//! `pythia._native.agent`: droidtop-agent, the separate program, seen from
//! Pythia. Owner, 2026-10-16: Pythia "can import, and if the agent is
//! disabled, will update the agent's storage folder". So both keep the same
//! pairings, settings, library and save copies, whichever served last:
//!
//! - whole files (the paired devices, settings, the saves choices, the games
//!   Pythia handed over, launcher marks): the newer copy wins, and the copy
//!   made takes the other's modified time, so the two compare equal after;
//! - the library: both are the same change log, so each side's changes are
//!   applied to the other (nothing is lost, a removal stays removed);
//! - save copies (the archive): a copy one side has and the other lacks is
//!   copied over; none is ever deleted here.
//!
//! Importing (agent to Pythia) always happens; writing back (Pythia to the
//! agent) only when Pythia was told the agent is off, so two running
//! programs never write each other's files. The computer's identity and its
//! trusted devices need nothing: both use windowcast's host folder.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::time::Duration;

use droidtop_agent_core::library::Library;
use pyo3::prelude::*;
use serde_json::json;

use crate::{agent, run, takeover, FOLDER};

pub fn fill(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(folders, m)?)?;
    m.add_function(wrap_pyfunction!(sync, m)?)?;
    m.add_function(wrap_pyfunction!(status, m)?)?;
    m.add_function(wrap_pyfunction!(turn_off, m)?)?;
    m.add_function(wrap_pyfunction!(turn_on, m)?)?;
    Ok(())
}

/// droidtop-agent's settings and data folders, as its `Dirs::locate` finds
/// them, without making them.
pub(crate) fn agent_folders() -> Option<(PathBuf, PathBuf)> {
    match std::env::var_os("DROIDTOP_AGENT_HOME") {
        Some(home) => Some((PathBuf::from(&home), PathBuf::from(home))),
        None => Some((crate::config_dir()?.join("droidtop-agent"), crate::data_local_dir()?.join("droidtop-agent"))),
    }
}

/// Where droidtop-agent keeps its files (`config`, `data`), and whether it
/// has any there yet (`exists`).
#[pyfunction]
fn folders(py: Python<'_>) -> PyResult<PyObject> {
    run(py, || {
        let (config, data) = agent_folders().ok_or("this system has no settings folder for droidtop-agent")?;
        let exists = config.join("devices.json").is_file() || data.join("library.json").is_file();
        Ok(json!({ "config": config, "data": data, "exists": exists }))
    })
}

/// The files kept whole, as (name, in config or data).
const WHOLE: &[(&str, bool)] =
    &[("devices.json", true), ("settings.json", true), ("saves.json", true), ("manager-games.json", false), ("launcher-marks.json", false)];

fn modified(path: &Path) -> Option<std::time::SystemTime> {
    fs::metadata(path).ok()?.modified().ok()
}

/// Copies `from` over `to` (through a temporary file, so a reader never sees
/// half of it) and gives `to` the modified time of `from`.
fn copy_over(from: &Path, to: &Path) -> io::Result<()> {
    if let Some(parent) = to.parent() {
        fs::create_dir_all(parent)?;
    }
    let tmp = to.with_extension("pythia-tmp");
    fs::copy(from, &tmp)?;
    if let Some(when) = modified(from) {
        fs::File::options().write(true).open(&tmp)?.set_modified(when)?;
    }
    fs::rename(&tmp, to)
}

/// Which way a whole file goes: Some(true) agent to Pythia, Some(false)
/// Pythia to the agent, None when they are the same age (or both missing).
fn direction(pythia: &Path, agent: &Path) -> Option<bool> {
    match (modified(pythia), modified(agent)) {
        (None, Some(_)) => Some(true),
        (Some(_), None) => Some(false),
        (Some(p), Some(a)) if a > p => Some(true),
        (Some(p), Some(a)) if p > a => Some(false),
        _ => None,
    }
}

/// The files under `root`, as paths relative to it.
fn files_under(root: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    let mut todo = vec![PathBuf::new()];
    while let Some(rel) = todo.pop() {
        let Ok(entries) = fs::read_dir(root.join(&rel)) else { continue };
        for entry in entries.flatten() {
            let rel = rel.join(entry.file_name());
            match entry.file_type() {
                Ok(t) if t.is_dir() => todo.push(rel),
                Ok(t) if t.is_file() => out.push(rel),
                _ => {}
            }
        }
    }
    out
}

/// Copies the files under `from` that `to` lacks; returns how many.
fn copy_missing(from: &Path, to: &Path) -> io::Result<usize> {
    let mut copied = 0;
    for rel in files_under(from) {
        let target = to.join(&rel);
        if !target.exists() {
            copy_over(&from.join(&rel), &target)?;
            copied += 1;
        }
    }
    Ok(copied)
}

/// Applies `from`'s changes to `into`; returns how many changed it.
fn merge(into: &mut Library, from: &Library) -> usize {
    let (changes, _) = from.changes_since(0);
    changes.into_iter().filter(|c| into.apply(c.clone())).count()
}

/// Brings Pythia's droidtop state and droidtop-agent's together: what the
/// agent has newer comes into Pythia's folder, and, with `write_back`,
/// what Pythia has newer goes into the agent's (see the module's words).
/// Returns the whole files `imported` and `written`, the library changes
/// taken `from_agent` and given `to_agent`, and the save copies
/// `copies_imported` and `copies_written`. Without any agent files there is
/// nothing to import, and with `write_back` Pythia's are written there.
#[pyfunction]
#[pyo3(signature = (write_back = false))]
fn sync(py: Python<'_>, write_back: bool) -> PyResult<PyObject> {
    run(py, move || {
        let a = agent()?;
        let (agent_config, agent_data) = agent_folders().ok_or("this system has no settings folder for droidtop-agent")?;
        let dirs = &a.store.dirs;
        let err = |what: &str, e: io::Error| format!("{what}: {e}");
        let (mut imported, mut written) = (Vec::new(), Vec::new());
        for &(name, in_config) in WHOLE {
            let (pythia, theirs) =
                if in_config { (dirs.config.join(name), agent_config.join(name)) } else { (dirs.data.join(name), agent_data.join(name)) };
            match direction(&pythia, &theirs) {
                Some(true) => {
                    copy_over(&theirs, &pythia).map_err(|e| err(&format!("taking droidtop-agent's {name}"), e))?;
                    imported.push(name);
                }
                Some(false) if write_back => {
                    copy_over(&pythia, &theirs).map_err(|e| err(&format!("writing droidtop-agent's {name}"), e))?;
                    written.push(name);
                }
                _ => {}
            }
        }
        if imported.contains(&"settings.json") {
            a.store.reload_settings();
        }

        let theirs_path = agent_data.join("library.json");
        let mut theirs = if theirs_path.is_file() {
            Library::load(&theirs_path).map_err(|e| err("reading droidtop-agent's library", e))?
        } else {
            Library::default()
        };
        let from_agent = a.store.edit_library(|ours| {
            let n = merge(ours, &theirs);
            (n, n > 0)
        });
        let mut to_agent = 0;
        if write_back {
            to_agent = merge(&mut theirs, &a.store.library_now());
            if to_agent > 0 || !theirs_path.is_file() {
                theirs.save(&theirs_path).map_err(|e| err("writing droidtop-agent's library", e))?;
            }
        }

        let copies_imported = copy_missing(&agent_data.join("archive"), &dirs.archive()).map_err(|e| err("taking save copies", e))?;
        let copies_written = if write_back {
            copy_missing(&dirs.archive(), &agent_data.join("archive")).map_err(|e| err("writing save copies", e))?
        } else {
            0
        };
        Ok(json!({
            "imported": imported,
            "written": written,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "copies_imported": copies_imported,
            "copies_written": copies_written,
        }))
    })
}

/// Pythia's droidtop folder, named or not yet opened.
fn pythia_folder() -> Result<PathBuf, String> {
    FOLDER.lock().unwrap().clone().ok_or_else(|| "Pythia has not named its droidtop folder yet (use_folder)".into())
}

/// droidtop-agent on this computer: `running` (its processes, each with
/// `pid` and `name`), `starts_at_sign_in` (its own start entry is there),
/// `turned_off` (Pythia turned it off and keeps its start entry: `since`,
/// in milliseconds since 1970, and the `entries` kinds), and its `folders`.
#[pyfunction]
fn status(py: Python<'_>) -> PyResult<PyObject> {
    run(py, || {
        let folder = pythia_folder()?;
        let running: Vec<_> = takeover::running().into_iter().map(|(pid, name)| json!({ "pid": pid, "name": name })).collect();
        let turned_off = takeover::turned_off(&folder)
            .map(|t| json!({ "since": t.ms, "entries": t.entries.iter().map(|e| e.kind.clone()).collect::<Vec<_>>() }));
        let (config, data) = agent_folders().unzip();
        Ok(json!({
            "running": running,
            "starts_at_sign_in": !takeover::entries().is_empty(),
            "turned_off": turned_off,
            "folders": { "config": config, "data": data },
        }))
    })
}

/// Turns droidtop-agent off so Pythia can serve: keeps its start entry in
/// Pythia's folder and removes it, then asks its processes to stop and
/// waits up to `wait` seconds. Returns the `entries` turned off and the
/// processes `still_running` (none when it worked).
#[pyfunction]
#[pyo3(signature = (wait = 10.0))]
fn turn_off(py: Python<'_>, wait: f64) -> PyResult<PyObject> {
    run(py, move || {
        let folder = pythia_folder()?;
        let entries =
            takeover::turn_off_autostart(&folder).map_err(|e| format!("droidtop-agent's start at sign-in could not be turned off: {e}"))?;
        let left = takeover::stop(Duration::try_from_secs_f64(wait).unwrap_or(Duration::from_secs(10)));
        Ok(json!({
            "entries": entries.iter().map(|e| e.kind.clone()).collect::<Vec<_>>(),
            "still_running": left.into_iter().map(|(pid, name)| json!({ "pid": pid, "name": name })).collect::<Vec<_>>(),
        }))
    })
}

/// Puts droidtop-agent's start at sign-in back as it was before Pythia
/// turned it off; it serves from the next sign-in, or once this Pythia no
/// longer does. Returns the `entries` put back (none when Pythia had not
/// turned it off).
#[pyfunction]
fn turn_on(py: Python<'_>) -> PyResult<PyObject> {
    run(py, || {
        let folder = pythia_folder()?;
        let entries =
            takeover::turn_on_autostart(&folder).map_err(|e| format!("droidtop-agent's start at sign-in could not be put back: {e}"))?;
        Ok(json!({ "entries": entries.iter().map(|e| e.kind.clone()).collect::<Vec<_>>() }))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, SystemTime};

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("pythia-native-{name}-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn write_at(path: &Path, text: &str, secs: u64) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
        fs::File::options().write(true).open(path).unwrap().set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(secs)).unwrap();
    }

    #[test]
    fn the_newer_whole_file_wins_and_then_both_are_the_same_age() {
        let dir = temp("whole");
        let (pythia, agent) = (dir.join("p/devices.json"), dir.join("a/devices.json"));
        assert_eq!(direction(&pythia, &agent), None);
        write_at(&agent, "[1]", 2_000_000);
        assert_eq!(direction(&pythia, &agent), Some(true));
        write_at(&pythia, "[2]", 1_000_000);
        assert_eq!(direction(&pythia, &agent), Some(true));
        copy_over(&agent, &pythia).unwrap();
        assert_eq!(fs::read_to_string(&pythia).unwrap(), "[1]");
        assert_eq!(direction(&pythia, &agent), None);
        write_at(&pythia, "[3]", 3_000_000);
        assert_eq!(direction(&pythia, &agent), Some(false));
    }

    #[test]
    fn save_copies_one_side_lacks_are_copied_and_none_replaced() {
        let dir = temp("archive");
        let (from, to) = (dir.join("from"), dir.join("to"));
        write_at(&from.join("PC/game/one.sav"), "new", 10);
        write_at(&from.join("PC/game/two.sav"), "theirs", 10);
        write_at(&to.join("PC/game/two.sav"), "ours", 10);
        assert_eq!(copy_missing(&from, &to).unwrap(), 1);
        assert_eq!(fs::read_to_string(to.join("PC/game/one.sav")).unwrap(), "new");
        assert_eq!(fs::read_to_string(to.join("PC/game/two.sav")).unwrap(), "ours");
        assert_eq!(copy_missing(&from, &to).unwrap(), 0);
    }

    #[test]
    fn libraries_merge_both_ways() {
        use droidtop_agent_core::keys::DeviceKey;
        use droidtop_agent_core::library::{Install, ScannedGame};
        let game = |key: &str| ScannedGame {
            key: key.into(),
            title: key.into(),
            platform: None,
            install: Install { installed: true, size: 1, ..Default::default() },
        };
        let (pc, hh) = (DeviceKey::generate().peer_id(), DeviceKey::generate().peer_id());
        let (mut ours, mut theirs) = (Library::default(), Library::default());
        ours.update_device(&pc, "PC", vec![game("pythia:1")]);
        theirs.update_device(&hh, "Handheld", vec![game("steam:2")]);
        assert!(merge(&mut ours, &theirs) > 0);
        assert!(merge(&mut theirs, &ours) > 0);
        assert_eq!(merge(&mut ours, &theirs), 0);
        let keys = |l: &Library| l.games.keys().cloned().collect::<Vec<_>>();
        assert_eq!(keys(&ours), vec!["pythia:1", "steam:2"]);
        assert_eq!(keys(&ours), keys(&theirs));
    }
}
