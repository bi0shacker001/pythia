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
//! - save copies (the archive) and plugin adapters: a file one side has and
//!   the other lacks is copied over; none is ever deleted here.
//!
//! Owner, 2026-10-16: "pythia imports the droidtop agent context when it
//! takes the agent role, too, so data shouldn't be lost". The first time,
//! every whole file the agent has comes in whatever its age, the paired
//! devices merged with any Pythia has; an older agent's own identity is
//! taken before Pythia's state is opened (`adopt_legacy_identity`).
//!
//! Importing (agent to Pythia) always happens; writing back (Pythia to the
//! agent) only when Pythia was told the agent is off, so two running
//! programs never write each other's files. The computer's identity and its
//! trusted devices are shared: both use windowcast's host folder.

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

/// The files kept whole, as (name, in config or data). settings.json holds
/// the plugin adapters' approvals; adapter-offers.json the offers waiting.
const WHOLE: &[(&str, bool)] = &[
    ("devices.json", true),
    ("settings.json", true),
    ("saves.json", true),
    ("manager-games.json", false),
    ("launcher-marks.json", false),
    ("adapter-offers.json", false),
];

/// In Pythia's folder once Pythia has taken in droidtop-agent's state the
/// first time. Before that, the agent's files win whatever their age.
const IMPORTED_MARK: &str = "agent-imported";

/// An older droidtop-agent's own identity and trusted devices, from before
/// it shared windowcast's host folder (droidtop-agent-store `migrate`).
const LEGACY_IDENTITY: &[&str] = &["identity.key", "trusted-devices", "previous-identity.key"];

/// Before Pythia opens its droidtop state: an older agent's own identity and
/// trusted devices, copied into Pythia's folder when Pythia has none, so
/// opening moves them into the shared folder as the agent itself would
/// (`migrate`) rather than making a new identity the handhelds do not know.
/// The agent's folder is only read. Returns the files taken.
pub(crate) fn adopt_legacy_identity(agent_config: &Path, pythia_config: &Path, computer: &Path) -> io::Result<Vec<&'static str>> {
    let mut taken = Vec::new();
    if agent_config == pythia_config || agent_config == computer {
        return Ok(taken);
    }
    for &name in LEGACY_IDENTITY {
        let (theirs, ours) = (agent_config.join(name), pythia_config.join(name));
        // `migrate` renames what it moved to *.moved: taken once, not again.
        let moved = pythia_config.join(format!("{name}.moved"));
        let moved_key = ours.with_extension("key.moved");
        if theirs.is_file() && !ours.exists() && !moved.exists() && !moved_key.exists() {
            fs::create_dir_all(pythia_config)?;
            fs::copy(&theirs, &ours)?;
            taken.push(name);
        }
    }
    Ok(taken)
}

/// The paired devices of both: every device either has, with the agent's
/// entry where both have one.
fn merge_devices(agent: &Path, pythia: &Path) -> io::Result<String> {
    let read = |p: &Path| -> io::Result<Vec<serde_json::Value>> {
        match fs::read(p) {
            Ok(bytes) => {
                serde_json::from_slice(&bytes).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, format!("{}: {e}", p.display())))
            }
            Err(e) if e.kind() == io::ErrorKind::NotFound => Ok(Vec::new()),
            Err(e) => Err(e),
        }
    };
    let mut all = read(agent)?;
    for d in read(pythia)? {
        if !all.iter().any(|a| a.get("id") == d.get("id")) {
            all.push(d);
        }
    }
    Ok(serde_json::to_string_pretty(&all).expect("JSON values serialize"))
}

/// The whole files between Pythia's (`pythia`: config, data) and the
/// agent's folders. The first time (`first`), every file the agent has comes
/// in, the paired devices merged, so nothing it had is lost to a copy
/// Pythia made itself; after that the newer copy wins, and Pythia's go back
/// only with `write_back`. Returns the files `imported` and `written`.
fn whole_files(
    pythia: (&Path, &Path),
    agent: (&Path, &Path),
    first: bool,
    write_back: bool,
) -> Result<(Vec<&'static str>, Vec<&'static str>), String> {
    let err = |what: String, e: io::Error| format!("{what}: {e}");
    let (mut imported, mut written) = (Vec::new(), Vec::new());
    for &(name, in_config) in WHOLE {
        let (ours, theirs) = if in_config { (pythia.0.join(name), agent.0.join(name)) } else { (pythia.1.join(name), agent.1.join(name)) };
        if first && theirs.is_file() {
            if name == "devices.json" && ours.is_file() {
                let merged = merge_devices(&theirs, &ours).map_err(|e| err(format!("merging droidtop-agent's {name}"), e))?;
                fs::write(&ours, merged).map_err(|e| err(format!("taking droidtop-agent's {name}"), e))?;
            } else {
                copy_over(&theirs, &ours).map_err(|e| err(format!("taking droidtop-agent's {name}"), e))?;
            }
            imported.push(name);
            continue;
        }
        match direction(&ours, &theirs) {
            Some(true) => {
                copy_over(&theirs, &ours).map_err(|e| err(format!("taking droidtop-agent's {name}"), e))?;
                imported.push(name);
            }
            Some(false) if write_back => {
                copy_over(&ours, &theirs).map_err(|e| err(format!("writing droidtop-agent's {name}"), e))?;
                written.push(name);
            }
            _ => {}
        }
    }
    Ok((imported, written))
}

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
        let mark = dirs.config.join(IMPORTED_MARK);
        let first = !mark.exists();
        let (imported, written) = whole_files((&dirs.config, &dirs.data), (&agent_config, &agent_data), first, write_back)?;
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
        // The plugin adapters the person approved: programs kept per context.
        let adapters_imported =
            copy_missing(&agent_data.join("adapters"), &dirs.adapters()).map_err(|e| err("taking plugin adapters", e))?;
        if write_back {
            copy_missing(&dirs.adapters(), &agent_data.join("adapters")).map_err(|e| err("writing plugin adapters", e))?;
        }
        if first {
            fs::write(&mark, b"").map_err(|e| err("marking droidtop-agent's state taken in", e))?;
        }
        Ok(json!({
            "imported": imported,
            "written": written,
            "from_agent": from_agent,
            "to_agent": to_agent,
            "copies_imported": copies_imported,
            "copies_written": copies_written,
            "adapters_imported": adapters_imported,
            "first": first,
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

    /// Owner, 2026-10-16: "pythia imports the droidtop agent context when it
    /// takes the agent role, too, so data shouldn't be lost".
    #[test]
    fn the_first_takeover_takes_in_everything_the_agent_had_whatever_its_age() {
        let dir = temp("first");
        let (pc, pd, ac, ad) = (dir.join("p"), dir.join("p"), dir.join("a/config"), dir.join("a/data"));
        // The agent's files are older than copies Pythia made itself.
        write_at(&ac.join("devices.json"), r#"[{"id":"handheld-a","name":"Deck"}]"#, 1_000);
        write_at(&ac.join("settings.json"), r#"{"name":"Agent's name"}"#, 1_000);
        write_at(&ac.join("saves.json"), r#"{"games":{"x":true}}"#, 1_000);
        write_at(&ad.join("manager-games.json"), "[1]", 1_000);
        write_at(&ad.join("launcher-marks.json"), r#"{"m":1}"#, 1_000);
        write_at(&ad.join("adapter-offers.json"), r#"[{"id":"f95"}]"#, 1_000);
        write_at(&pc.join("devices.json"), r#"[{"id":"handheld-b","name":"Win 4"}]"#, 5_000);
        write_at(&pc.join("settings.json"), "{}", 5_000);

        let (imported, written) = whole_files((&pc, &pd), (&ac, &ad), true, true).unwrap();
        assert_eq!(imported.len(), WHOLE.len());
        assert!(written.is_empty());
        let devices: Vec<serde_json::Value> = serde_json::from_str(&fs::read_to_string(pc.join("devices.json")).unwrap()).unwrap();
        let ids: Vec<_> = devices.iter().map(|d| d["id"].as_str().unwrap()).collect();
        assert_eq!(ids, ["handheld-a", "handheld-b"]);
        assert_eq!(fs::read_to_string(pc.join("settings.json")).unwrap(), r#"{"name":"Agent's name"}"#);
        assert_eq!(fs::read_to_string(pd.join("adapter-offers.json")).unwrap(), r#"[{"id":"f95"}]"#);
        // The agent's own files are only read.
        assert_eq!(fs::read_to_string(ac.join("devices.json")).unwrap(), r#"[{"id":"handheld-a","name":"Deck"}]"#);

        // After the first time, the newer copy wins as before.
        write_at(&pc.join("settings.json"), r#"{"name":"Renamed in Pythia"}"#, 9_000);
        let (imported, written) = whole_files((&pc, &pd), (&ac, &ad), false, true).unwrap();
        assert!(!imported.contains(&"settings.json"));
        assert!(written.contains(&"settings.json"));
        assert_eq!(fs::read_to_string(ac.join("settings.json")).unwrap(), r#"{"name":"Renamed in Pythia"}"#);
    }

    #[test]
    fn an_older_agents_own_identity_becomes_this_computers() {
        let dir = temp("legacy");
        let (agent_config, pythia, computer) = (dir.join("agent"), dir.join("pythia"), dir.join("computer"));
        fs::create_dir_all(&agent_config).unwrap();
        let old = droidtop_agent_store::Dirs { config: agent_config.clone(), data: agent_config.clone(), computer: agent_config.clone() };
        // The identity an older agent kept in its own folder, under its own name.
        let seed = fs::read({
            droidtop_agent_store::Store::open_in(old).unwrap();
            agent_config.join(windowcast_identity::HOST_IDENTITY_FILE)
        })
        .unwrap();
        fs::rename(agent_config.join(windowcast_identity::HOST_IDENTITY_FILE), agent_config.join("identity.key")).unwrap();

        assert_eq!(adopt_legacy_identity(&agent_config, &pythia, &computer).unwrap(), ["identity.key"]);
        fs::create_dir_all(&computer).unwrap();
        let dirs = droidtop_agent_store::Dirs { config: pythia.clone(), data: pythia.clone(), computer: computer.clone() };
        droidtop_agent_store::Store::open_in(dirs).unwrap();
        assert_eq!(fs::read(computer.join(windowcast_identity::HOST_IDENTITY_FILE)).unwrap(), seed);
        assert!(agent_config.join("identity.key").is_file(), "the agent's folder is only read");
        assert!(adopt_legacy_identity(&agent_config, &pythia, &computer).unwrap().is_empty(), "taken once");
    }

    #[test]
    fn plugin_adapters_come_across_like_save_copies() {
        let dir = temp("adapters");
        write_at(&dir.join("a/adapters/f95/adapter"), "#!/bin/sh", 10);
        assert_eq!(copy_missing(&dir.join("a/adapters"), &dir.join("p/adapters")).unwrap(), 1);
        assert!(dir.join("p/adapters/f95/adapter").is_file());
    }
}
