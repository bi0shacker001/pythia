//! Pythia's droidtop side as an extension module, `pythia._native`. While
//! Pythia runs it is this computer's agent for droidtop handhelds: it pairs,
//! serves sessions, keeps the library and saves in step, with droidtop-agent's
//! crates doing the work and Pythia's own folder holding the state.
//!
//! The agent's submodules (`pythia._native.store`, `.pair`, `.scan`, `.saves`,
//! `.remote` and `.service`) are droidtop-agent's Python bindings
//! (droidtop-agent `crates/python`, its docs/DESIGN.md section 16), built into
//! this module with `register` after `configure` names Pythia, its folder and
//! this module (Droidtop/tracker#867: one set of bindings, Pythia's own module
//! over them). Pythia's own submodules are `agent`, `pad` and `secure_screen`.
//! One module and not one per crate: each extension module links its own copy
//! of the Rust code, and one process must hold one agent.
//!
//! Pythia names its folder with `use_folder` before anything else. The
//! computer's identity stays in windowcast's host folder, the one
//! droidtop-agent and windowcast use too, so a handheld sees one computer
//! whichever of them serves it.
//!
//! Functions take keyword arguments and return dicts, lists and plain
//! values; a refusal raises `pythia._native.AgentError` with words a screen
//! can show. Every call releases the interpreter while Rust works, so a slow
//! one (a scan, a pairing wait) never stops Python's other threads.

// PyO3 0.22's #[pyfunction] expansion converts a PyErr into itself, which
// clippy calls a useless conversion; the code is PyO3's, not ours.
#![allow(clippy::useless_conversion)]

use std::path::PathBuf;
use std::sync::Mutex;

use droidtop_agent_service::Agent;
use droidtop_agent_store::Dirs;
use pyo3::prelude::*;
use serde_json::json;

mod agent;
mod pad;
mod secure;
mod takeover;

pub(crate) static FOLDER: Mutex<Option<PathBuf>> = Mutex::new(None);
// The bindings Pythia's module is built over, and what Pythia's own submodules use from them.
pub(crate) use droidtop_agent_python::{agent, run, AgentError};
use droidtop_agent_python::{submodule, Embedder};

/// Who embeds droidtop-agent's bindings here: Pythia, over its own folder.
const PYTHIA: Embedder = Embedder { program: "pythia", title: "Pythia", open: open_agent, module: "pythia._native" };

/// The settings folder (APPDATA on Windows). On Windows the dirs crate asks
/// the system for the profile's folder and ignores an APPDATA the caller
/// set; Pythia honours it, so a profile moved with APPDATA and LOCALAPPDATA
/// (a test profile, a portable install) is never written outside
/// (Droidtop/tracker#538, retest B3).
pub(crate) fn config_dir() -> Option<PathBuf> {
    env_dir("APPDATA").or_else(dirs::config_dir)
}

/// The local data folder (LOCALAPPDATA on Windows), as [`config_dir`].
pub(crate) fn data_local_dir() -> Option<PathBuf> {
    env_dir("LOCALAPPDATA").or_else(dirs::data_local_dir)
}

fn env_dir(name: &str) -> Option<PathBuf> {
    if !cfg!(windows) {
        return None;
    }
    std::env::var_os(name).filter(|v| !v.is_empty()).map(PathBuf::from)
}

/// The computer's identity folder: windowcast's host folder (it honours
/// APPDATA), or the one folder `DROIDTOP_AGENT_HOME` gives droidtop-agent.
fn computer_dir() -> PathBuf {
    match std::env::var_os("DROIDTOP_AGENT_HOME") {
        Some(home) => PathBuf::from(home),
        None => windowcast_identity::computer_dir(),
    }
}

/// Pythia's droidtop state over its folder.
fn pythia_dirs(folder: PathBuf) -> std::io::Result<Dirs> {
    let dirs = Dirs { config: folder.clone(), data: folder, computer: computer_dir() };
    std::fs::create_dir_all(&dirs.config)?;
    std::fs::create_dir_all(&dirs.computer)?;
    Ok(dirs)
}

/// The agent over Pythia's folder: the bindings call this the first time anything needs the agent.
fn open_agent() -> Result<Agent, String> {
    let folder = FOLDER.lock().unwrap().clone().ok_or("Pythia has not named its droidtop folder yet (use_folder)")?;
    let dirs = pythia_dirs(folder).map_err(|e| format!("Pythia's droidtop folder could not be made: {e}"))?;
    if let Some((agent_config, _)) = agent::agent_folders() {
        agent::adopt_legacy_identity(&agent_config, &dirs.config, &dirs.computer)
            .map_err(|e| format!("droidtop-agent's identity could not be taken in: {e}"))?;
    }
    Agent::open_in(dirs).map_err(|e| format!("Pythia's droidtop state could not be opened: {e}"))
}

/// Names the folder that holds Pythia's droidtop state (paired devices,
/// settings, the library, save copies). Only before first use: once the
/// state is open, another folder is refused.
#[pyfunction]
fn use_folder(py: Python<'_>, path: PathBuf) -> PyResult<PyObject> {
    run(py, move || {
        if let Some(a) = droidtop_agent_python::opened() {
            if a.store.dirs.config != path {
                return Err(format!("Pythia's droidtop state is already open in {}", a.store.dirs.config.display()));
            }
        }
        *FOLDER.lock().unwrap() = Some(path.clone());
        Ok(json!({ "folder": path, "computer": computer_dir() }))
    })
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    droidtop_agent_python::configure(PYTHIA).map_err(AgentError::new_err)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    droidtop_agent_python::register(m)?;
    m.add_function(wrap_pyfunction!(use_folder, m)?)?;
    submodule(m, "agent", agent::fill)?;
    submodule(m, "pad", pad::fill)?;
    submodule(m, "secure_screen", secure::fill)?;
    Ok(())
}

#[cfg(all(test, windows))]
mod tests {
    #[test]
    fn a_moved_profile_is_honoured() {
        let dir = std::env::temp_dir().join("pythia-profile-test");
        std::env::set_var("APPDATA", dir.join("Roaming"));
        std::env::set_var("LOCALAPPDATA", dir.join("Local"));
        assert_eq!(super::config_dir(), Some(dir.join("Roaming")));
        assert_eq!(super::data_local_dir(), Some(dir.join("Local")));
    }
}
