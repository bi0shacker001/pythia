//! `pythia._native.pad`: game controllers, read on Windows and Linux (gilrs),
//! turned into named actions for the handheld shell.
//!
//! UX review 2026-10-16, A7 (HH11): one input source. A thread reads every
//! controller, Steam Input's virtual pad as well as the raw device, and keeps
//! the actions in a short queue that Pythia's GUI thread drains with `poll()`.
//! Pythia drops what it drains while none of its windows has the focus, so a
//! press meant for another app never acts in Pythia (pythia/pad.py).
//!
//! Actions: `up`, `down`, `left`, `right` (the D-pad or the left stick; a held
//! direction repeats), `a`, `b`, `x`, `y`, `l1`, `r1`, `l2`, `r2`, `start`,
//! `select`. Face buttons are named by position, Xbox style: `a` is the bottom
//! one on every pad.

use std::collections::VecDeque;
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::AgentError;

pub fn fill(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(start, m)?)?;
    m.add_function(wrap_pyfunction!(poll, m)?)?;
    m.add_function(wrap_pyfunction!(pads, m)?)?;
    Ok(())
}

/// A held direction repeats after this...
const REPEAT_DELAY: Duration = Duration::from_millis(400);
/// ...every this long.
const REPEAT_EVERY: Duration = Duration::from_millis(120);
/// The same action from another pad within this long is the same press seen
/// twice (Steam Input's virtual pad and the device under it).
const SAME_PRESS: Duration = Duration::from_millis(40);
/// The stick counts as pushed past this, and as let go under `STICK_OFF`.
const STICK_ON: f32 = 0.6;
const STICK_OFF: f32 = 0.4;
/// Actions kept for the GUI thread; older ones go when it falls behind.
const QUEUE: usize = 64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Dir {
    Up,
    Down,
    Left,
    Right,
}

impl Dir {
    fn action(self) -> &'static str {
        match self {
            Dir::Up => "up",
            Dir::Down => "down",
            Dir::Left => "left",
            Dir::Right => "right",
        }
    }
}

/// What one controller reported, already named.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Input {
    /// A button went down (directions come as `Hold`).
    Press(&'static str),
    /// A direction is held (the D-pad, or the stick past `STICK_ON`), or let go (`None`).
    Hold(Option<Dir>),
    /// The left stick moved: (x, y), y up.
    Stick(Option<f32>, Option<f32>),
}

/// Turns inputs into actions: repeats held directions, reads the stick with
/// hysteresis, and drops a press another pad already sent.
#[derive(Default)]
pub(crate) struct Translator {
    held: Option<(usize, Dir, Instant, Instant)>,
    stick: std::collections::HashMap<usize, (f32, f32, Option<Dir>)>,
    last: Option<(usize, &'static str, Instant)>,
}

impl Translator {
    pub(crate) fn input(&mut self, pad: usize, input: Input, now: Instant) -> Vec<&'static str> {
        match input {
            Input::Press(action) => self.emit(pad, action, now).into_iter().collect(),
            Input::Hold(dir) => self.hold(pad, dir, now),
            Input::Stick(x, y) => {
                let entry = self.stick.entry(pad).or_insert((0.0, 0.0, None));
                if let Some(x) = x {
                    entry.0 = x;
                }
                if let Some(y) = y {
                    entry.1 = y;
                }
                let (x, y, was) = *entry;
                let pushed = |v: f32, on: bool| if on { v.abs() > STICK_OFF } else { v.abs() > STICK_ON };
                let dir = if x.abs() >= y.abs() {
                    pushed(x, matches!(was, Some(Dir::Left | Dir::Right))).then_some(if x > 0.0 { Dir::Right } else { Dir::Left })
                } else {
                    pushed(y, matches!(was, Some(Dir::Up | Dir::Down))).then_some(if y > 0.0 { Dir::Up } else { Dir::Down })
                };
                if dir == was {
                    return Vec::new();
                }
                self.stick.get_mut(&pad).expect("just inserted").2 = dir;
                self.hold(pad, dir, now)
            }
        }
    }

    /// The repeats due by `now` for a held direction.
    pub(crate) fn tick(&mut self, now: Instant) -> Vec<&'static str> {
        let mut out = Vec::new();
        if let Some((pad, dir, since, last)) = self.held {
            let due = if last == since { since + REPEAT_DELAY } else { last + REPEAT_EVERY };
            if now >= due {
                self.held = Some((pad, dir, since, now));
                out.push(dir.action());
            }
        }
        out
    }

    fn hold(&mut self, pad: usize, dir: Option<Dir>, now: Instant) -> Vec<&'static str> {
        match dir {
            Some(dir) => {
                if matches!(self.held, Some((p, d, _, _)) if p == pad && d == dir) {
                    return Vec::new();
                }
                let sent = self.emit(pad, dir.action(), now);
                if sent.is_some() {
                    self.held = Some((pad, dir, now, now));
                }
                sent.into_iter().collect()
            }
            None => {
                if matches!(self.held, Some((p, _, _, _)) if p == pad) {
                    self.held = None;
                }
                Vec::new()
            }
        }
    }

    fn emit(&mut self, pad: usize, action: &'static str, now: Instant) -> Option<&'static str> {
        if let Some((other, same, at)) = self.last {
            if other != pad && same == action && now.duration_since(at) < SAME_PRESS {
                return None;
            }
        }
        self.last = Some((pad, action, now));
        Some(action)
    }
}

struct Shared {
    queue: Mutex<VecDeque<String>>,
    pads: Mutex<Vec<(usize, String)>>,
}

fn shared() -> &'static Shared {
    static SHARED: OnceLock<Shared> = OnceLock::new();
    SHARED.get_or_init(|| Shared { queue: Mutex::new(VecDeque::new()), pads: Mutex::new(Vec::new()) })
}

fn push(actions: Vec<&'static str>) {
    if actions.is_empty() {
        return;
    }
    let mut queue = shared().queue.lock().unwrap_or_else(|e| e.into_inner());
    for action in actions {
        if queue.len() == QUEUE {
            queue.pop_front();
        }
        queue.push_back(action.to_string());
    }
}

fn named(button: gilrs::Button) -> Option<Input> {
    use gilrs::Button::*;
    Some(Input::Press(match button {
        South => "a",
        East => "b",
        West => "x",
        North => "y",
        LeftTrigger => "l1",
        RightTrigger => "r1",
        LeftTrigger2 => "l2",
        RightTrigger2 => "r2",
        Start => "start",
        Select => "select",
        DPadUp => return Some(Input::Hold(Some(Dir::Up))),
        DPadDown => return Some(Input::Hold(Some(Dir::Down))),
        DPadLeft => return Some(Input::Hold(Some(Dir::Left))),
        DPadRight => return Some(Input::Hold(Some(Dir::Right))),
        _ => return None,
    }))
}

fn read(mut gilrs: gilrs::Gilrs) {
    use gilrs::{Axis, Button, EventType};
    let mut translator = Translator::default();
    {
        let mut pads = shared().pads.lock().unwrap_or_else(|e| e.into_inner());
        *pads = gilrs.gamepads().map(|(id, pad)| (usize::from(id), pad.name().to_string())).collect();
    }
    loop {
        let event = gilrs.next_event_blocking(Some(Duration::from_millis(30)));
        let now = Instant::now();
        if let Some(event) = event {
            let pad = usize::from(event.id);
            let input = match event.event {
                EventType::ButtonPressed(button, _) => named(button),
                EventType::ButtonReleased(Button::DPadUp | Button::DPadDown | Button::DPadLeft | Button::DPadRight, _) => {
                    Some(Input::Hold(None))
                }
                EventType::AxisChanged(Axis::LeftStickX, value, _) => Some(Input::Stick(Some(value), None)),
                EventType::AxisChanged(Axis::LeftStickY, value, _) => Some(Input::Stick(None, Some(value))),
                EventType::Connected | EventType::Disconnected => {
                    let mut pads = shared().pads.lock().unwrap_or_else(|e| e.into_inner());
                    *pads = gilrs.gamepads().map(|(id, pad)| (usize::from(id), pad.name().to_string())).collect();
                    if matches!(event.event, EventType::Disconnected) {
                        Some(Input::Hold(None))
                    } else {
                        None
                    }
                }
                _ => None,
            };
            if let Some(input) = input {
                push(translator.input(pad, input, now));
            }
        }
        push(translator.tick(now));
    }
}

/// Starts reading controllers, once per process; later calls do nothing.
/// Raises AgentError when the system's controller support cannot start.
#[pyfunction]
fn start() -> PyResult<()> {
    static STARTED: OnceLock<Result<(), String>> = OnceLock::new();
    STARTED
        .get_or_init(|| {
            // Gilrs is made on the reading thread (it stays there); this waits for its answer.
            let (tx, rx) = std::sync::mpsc::channel();
            thread::Builder::new()
                .name("pythia-pad".into())
                .spawn(move || match gilrs::Gilrs::new() {
                    Ok(gilrs) => {
                        let _ = tx.send(Ok(()));
                        read(gilrs);
                    }
                    Err(gilrs::Error::NotImplemented(gilrs)) => {
                        let _ = tx.send(Err("controllers are not supported on this system".to_string()));
                        drop(gilrs);
                    }
                    Err(e) => {
                        let _ = tx.send(Err(e.to_string()));
                    }
                })
                .map_err(|e| e.to_string())?;
            rx.recv().map_err(|e| e.to_string())?
        })
        .clone()
        .map_err(AgentError::new_err)
}

/// The actions since the last call, oldest first.
#[pyfunction]
fn poll() -> Vec<String> {
    shared().queue.lock().unwrap_or_else(|e| e.into_inner()).drain(..).collect()
}

/// The controllers connected now: `id` and `name`.
#[pyfunction]
fn pads(py: Python<'_>) -> PyResult<Vec<Bound<'_, PyDict>>> {
    let pads = shared().pads.lock().unwrap_or_else(|e| e.into_inner()).clone();
    pads.into_iter()
        .map(|(id, name)| {
            let d = PyDict::new_bound(py);
            d.set_item("id", id)?;
            d.set_item("name", name)?;
            Ok(d)
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ms(base: Instant, n: u64) -> Instant {
        base + Duration::from_millis(n)
    }

    #[test]
    fn a_press_is_its_action() {
        let mut t = Translator::default();
        let now = Instant::now();
        assert_eq!(t.input(0, Input::Press("a"), now), vec!["a"]);
        assert_eq!(t.input(0, Input::Press("a"), ms(now, 200)), vec!["a"]);
    }

    #[test]
    fn a_held_direction_repeats_after_a_delay() {
        let mut t = Translator::default();
        let now = Instant::now();
        assert_eq!(t.input(0, Input::Hold(Some(Dir::Down)), now), vec!["down"]);
        assert!(t.tick(ms(now, 399)).is_empty());
        assert_eq!(t.tick(ms(now, 400)), vec!["down"]);
        assert!(t.tick(ms(now, 519)).is_empty());
        assert_eq!(t.tick(ms(now, 520)), vec!["down"]);
        t.input(0, Input::Hold(None), ms(now, 530));
        assert!(t.tick(ms(now, 2000)).is_empty());
    }

    #[test]
    fn the_stick_has_hysteresis_and_picks_its_main_axis() {
        let mut t = Translator::default();
        let now = Instant::now();
        assert!(t.input(0, Input::Stick(Some(0.5), None), now).is_empty());
        assert_eq!(t.input(0, Input::Stick(Some(0.7), None), now), vec!["right"]);
        // Still pushed until under STICK_OFF, so no second press.
        assert!(t.input(0, Input::Stick(Some(0.5), None), now).is_empty());
        assert!(t.input(0, Input::Stick(Some(0.3), None), now).is_empty());
        assert!(t.tick(ms(now, 1000)).is_empty());
        assert_eq!(t.input(0, Input::Stick(Some(0.1), Some(0.9)), ms(now, 1100)), vec!["up"]);
        assert_eq!(t.input(0, Input::Stick(None, Some(-0.9)), ms(now, 1200)), vec!["down"]);
    }

    #[test]
    fn the_same_press_from_steams_virtual_pad_counts_once() {
        let mut t = Translator::default();
        let now = Instant::now();
        assert_eq!(t.input(1, Input::Press("a"), now), vec!["a"]);
        assert!(t.input(2, Input::Press("a"), ms(now, 10)).is_empty());
        assert_eq!(t.input(2, Input::Press("a"), ms(now, 100)), vec!["a"]);
        assert_eq!(t.input(1, Input::Hold(Some(Dir::Left)), ms(now, 200)), vec!["left"]);
        assert!(t.input(2, Input::Hold(Some(Dir::Left)), ms(now, 205)).is_empty());
    }
}
