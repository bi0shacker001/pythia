//! Turning droidtop-agent off while Pythia serves the handhelds, and back on.
//! Owner, 2026-10-16: the two cannot serve one computer together, so Pythia
//! stops a running agent and turns off its start at sign-in.
//!
//! The agent's own start entry (its systemd user unit or XDG autostart entry
//! on Linux, its LaunchAgent on macOS, its Run value on Windows; see
//! droidtop-agent's autostart.rs) is kept, as it was, in Pythia's folder
//! before it is removed, so turning the agent back on puts back exactly what
//! the person had: Pythia never writes an entry of its own for the agent.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use sysinfo::{ProcessesToUpdate, System};

/// The programs droidtop-agent runs as: the service and its window.
const PROGRAMS: &[&str] = &["droidtop-agent", "droidtop-agent-app"];

/// One start entry of the agent's, as it was.
#[derive(Serialize, Deserialize, Debug, Clone, PartialEq, Eq)]
pub struct Entry {
    /// "systemd", "desktop", "launchagent" or "run".
    pub kind: String,
    /// The file, or the registry value's name.
    pub at: String,
    pub content: String,
}

/// What Pythia turned off, kept in its folder.
#[derive(Serialize, Deserialize, Debug, Default, Clone)]
pub struct TurnedOff {
    pub entries: Vec<Entry>,
    /// When, in milliseconds since 1970.
    pub ms: i64,
}

pub fn record(folder: &Path) -> PathBuf {
    folder.join("droidtop-agent-turned-off.json")
}

/// The agent's processes now running, as (pid, name).
pub fn running() -> Vec<(u32, String)> {
    let mut system = System::new();
    system.refresh_processes(ProcessesToUpdate::All, true);
    let me = sysinfo::get_current_pid().ok();
    system
        .processes()
        .iter()
        .filter(|(pid, _)| Some(**pid) != me)
        .filter_map(|(pid, p)| {
            let name = p.name().to_string_lossy();
            let stem = name.strip_suffix(".exe").unwrap_or(&name);
            PROGRAMS.contains(&stem).then(|| (pid.as_u32(), stem.to_string()))
        })
        .collect()
}

/// Asks the agent's processes to stop, then waits up to `wait` for them to
/// go; returns the ones still running.
pub fn stop(wait: Duration) -> Vec<(u32, String)> {
    let mut system = System::new();
    system.refresh_processes(ProcessesToUpdate::All, true);
    for (pid, _) in running() {
        if let Some(p) = system.process(sysinfo::Pid::from_u32(pid)) {
            #[cfg(unix)]
            let _ = p.kill_with(sysinfo::Signal::Term);
            #[cfg(not(unix))]
            let _ = p.kill();
        }
    }
    let started = Instant::now();
    loop {
        let left = running();
        if left.is_empty() || started.elapsed() >= wait {
            return left;
        }
        std::thread::sleep(Duration::from_millis(200));
    }
}

#[cfg(target_os = "linux")]
mod system {
    use super::*;

    fn config() -> PathBuf {
        dirs::config_dir().unwrap_or_else(|| dirs::home_dir().unwrap_or_default().join(".config"))
    }
    fn places() -> [(&'static str, PathBuf); 2] {
        [("systemd", config().join("systemd/user/droidtop-agent.service")), ("desktop", config().join("autostart/droidtop-agent.desktop"))]
    }
    fn systemctl(args: &[&str]) {
        let _ = std::process::Command::new("systemctl").arg("--user").args(args).status();
    }

    pub fn entries() -> Vec<Entry> {
        places()
            .into_iter()
            .filter_map(|(kind, path)| {
                let content = std::fs::read_to_string(&path).ok()?;
                Some(Entry { kind: kind.into(), at: path.display().to_string(), content })
            })
            .collect()
    }

    pub fn remove(entries: &[Entry]) -> Result<(), String> {
        for e in entries {
            if e.kind == "systemd" {
                systemctl(&["stop", "droidtop-agent.service"]);
                systemctl(&["disable", "droidtop-agent.service"]);
            }
            std::fs::remove_file(&e.at).map_err(|err| format!("{}: {err}", e.at))?;
        }
        if entries.iter().any(|e| e.kind == "systemd") {
            systemctl(&["daemon-reload"]);
        }
        Ok(())
    }

    pub fn restore(entries: &[Entry]) -> Result<(), String> {
        for e in entries {
            write(Path::new(&e.at), &e.content)?;
        }
        if entries.iter().any(|e| e.kind == "systemd") {
            systemctl(&["daemon-reload"]);
            systemctl(&["enable", "droidtop-agent.service"]);
        }
        Ok(())
    }
}

#[cfg(target_os = "macos")]
mod system {
    use super::*;

    fn plist() -> PathBuf {
        dirs::home_dir().unwrap_or_default().join("Library/LaunchAgents/dev.droidtop.agent.plist")
    }

    pub fn entries() -> Vec<Entry> {
        std::fs::read_to_string(plist())
            .ok()
            .map(|content| Entry { kind: "launchagent".into(), at: plist().display().to_string(), content })
            .into_iter()
            .collect()
    }

    pub fn remove(entries: &[Entry]) -> Result<(), String> {
        for e in entries {
            std::fs::remove_file(&e.at).map_err(|err| format!("{}: {err}", e.at))?;
        }
        Ok(())
    }

    pub fn restore(entries: &[Entry]) -> Result<(), String> {
        for e in entries {
            write(Path::new(&e.at), &e.content)?;
        }
        Ok(())
    }
}

#[cfg(windows)]
mod system {
    use super::*;
    use winreg::enums::{HKEY_CURRENT_USER, KEY_READ, KEY_WRITE};
    use winreg::RegKey;

    const RUN: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    const VALUE: &str = "droidtop-agent";

    pub fn entries() -> Vec<Entry> {
        RegKey::predef(HKEY_CURRENT_USER)
            .open_subkey_with_flags(RUN, KEY_READ)
            .ok()
            .and_then(|k| k.get_value::<String, _>(VALUE).ok())
            .map(|content| Entry { kind: "run".into(), at: VALUE.into(), content })
            .into_iter()
            .collect()
    }

    pub fn remove(entries: &[Entry]) -> Result<(), String> {
        if entries.is_empty() {
            return Ok(());
        }
        let key = RegKey::predef(HKEY_CURRENT_USER).open_subkey_with_flags(RUN, KEY_WRITE).map_err(|e| e.to_string())?;
        key.delete_value(VALUE).map_err(|e| e.to_string())
    }

    pub fn restore(entries: &[Entry]) -> Result<(), String> {
        let (key, _) = RegKey::predef(HKEY_CURRENT_USER).create_subkey_with_flags(RUN, KEY_READ | KEY_WRITE).map_err(|e| e.to_string())?;
        for e in entries {
            key.set_value(&e.at, &e.content).map_err(|err| err.to_string())?;
        }
        Ok(())
    }
}

#[cfg(not(any(target_os = "linux", target_os = "macos", windows)))]
mod system {
    use super::*;
    pub fn entries() -> Vec<Entry> {
        Vec::new()
    }
    pub fn remove(_: &[Entry]) -> Result<(), String> {
        Ok(())
    }
    pub fn restore(_: &[Entry]) -> Result<(), String> {
        Ok(())
    }
}

#[cfg(not(windows))]
fn write(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(path, text).map_err(|e| format!("{}: {e}", path.display()))
}

/// The agent's start entries as they are now.
pub fn entries() -> Vec<Entry> {
    system::entries()
}

/// Turns the agent's start at sign-in off, keeping what was there in
/// `folder` (added to what an earlier turn-off kept, never replacing it).
pub fn turn_off_autostart(folder: &Path) -> Result<Vec<Entry>, String> {
    let now = entries();
    if now.is_empty() {
        return Ok(now);
    }
    let path = record(folder);
    let mut kept: TurnedOff = droidtop_agent_store::read_json(&path).map_err(|e| e.to_string())?;
    for e in &now {
        kept.entries.retain(|k| k.at != e.at);
        kept.entries.push(e.clone());
    }
    kept.ms = droidtop_agent_core::library::now_ms();
    // Kept before anything is removed: a failure leaves the entry, never loses it.
    droidtop_agent_store::write_json(&path, &kept).map_err(|e| e.to_string())?;
    system::remove(&now)?;
    Ok(now)
}

/// Puts back the start entries Pythia kept, and forgets them.
pub fn turn_on_autostart(folder: &Path) -> Result<Vec<Entry>, String> {
    let path = record(folder);
    let kept: TurnedOff = droidtop_agent_store::read_json(&path).map_err(|e| e.to_string())?;
    system::restore(&kept.entries)?;
    std::fs::remove_file(&path).or_else(|e| if e.kind() == std::io::ErrorKind::NotFound { Ok(()) } else { Err(e.to_string()) })?;
    Ok(kept.entries)
}

/// Whether Pythia turned the agent off and has its entries kept.
pub fn turned_off(folder: &Path) -> Option<TurnedOff> {
    let path = record(folder);
    path.is_file().then(|| droidtop_agent_store::read_json(&path).ok()).flatten()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_kept_entry_is_added_to_never_replaced() {
        let dir = std::env::temp_dir().join(format!("pythia-native-turned-off-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = record(&dir);
        let one = Entry { kind: "systemd".into(), at: "/a".into(), content: "one".into() };
        droidtop_agent_store::write_json(&path, &TurnedOff { entries: vec![one.clone()], ms: 1 }).unwrap();
        let kept = turned_off(&dir).unwrap();
        assert_eq!(kept.entries, vec![one]);
        assert!(turned_off(&dir.join("nowhere")).is_none());
    }

    #[test]
    fn this_process_is_never_the_agent() {
        let me = std::process::id();
        assert!(running().iter().all(|(pid, _)| *pid != me));
    }
}
