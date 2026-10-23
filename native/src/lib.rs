//! Pythia's droidtop side as an extension module, `pythia._native`. While
//! Pythia runs it is this computer's agent for droidtop handhelds: it pairs,
//! serves sessions, keeps the library and saves in step, with droidtop-agent's
//! crates doing the work and Pythia's own folder holding the state.
//!
//! One submodule per agent crate: `pythia._native.store`, `.pair`, `.scan`,
//! `.saves` and `.service`. One module and not one per crate: each extension
//! module links its own copy of the Rust code, and one process must hold one
//! agent.
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
use std::sync::{Arc, Mutex, OnceLock};

use droidtop_agent_service::Agent;
use droidtop_agent_store::Dirs;
use pyo3::prelude::*;
use serde_json::{json, Value};

mod agent;
mod pad;
mod pair;
mod remote;
mod saves;
mod scan;
mod secure;
mod service;
mod store;
mod takeover;

// PyO3 0.22's create_exception! tests a `gil-refs` feature this crate does not
// declare, which rustc's cfg check reports.
#[allow(unexpected_cfgs)]
mod error {
    pyo3::create_exception!(
        pythia._native,
        AgentError,
        pyo3::exceptions::PyException,
        "A droidtop call was refused; the message is in words a screen can show."
    );
}
pub(crate) use error::AgentError;

pub(crate) static FOLDER: Mutex<Option<PathBuf>> = Mutex::new(None);
static AGENT: OnceLock<Arc<Agent>> = OnceLock::new();
static OPENING: Mutex<()> = Mutex::new(());

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

/// The agent over Pythia's folder, opened on first use.
pub(crate) fn agent() -> Result<Arc<Agent>, String> {
    if let Some(a) = AGENT.get() {
        return Ok(a.clone());
    }
    let _one = OPENING.lock().unwrap();
    if let Some(a) = AGENT.get() {
        return Ok(a.clone());
    }
    let folder = FOLDER.lock().unwrap().clone().ok_or("Pythia has not named its droidtop folder yet (use_folder)")?;
    let dirs = pythia_dirs(folder).map_err(|e| format!("Pythia's droidtop folder could not be made: {e}"))?;
    if let Some((agent_config, _)) = agent::agent_folders() {
        agent::adopt_legacy_identity(&agent_config, &dirs.config, &dirs.computer)
            .map_err(|e| format!("droidtop-agent's identity could not be taken in: {e}"))?;
    }
    let opened = Arc::new(Agent::open_in(dirs).map_err(|e| format!("Pythia's droidtop state could not be opened: {e}"))?);
    Ok(AGENT.get_or_init(|| opened).clone())
}

/// Names the folder that holds Pythia's droidtop state (paired devices,
/// settings, the library, save copies). Only before first use: once the
/// state is open, another folder is refused.
#[pyfunction]
fn use_folder(py: Python<'_>, path: PathBuf) -> PyResult<PyObject> {
    run(py, move || {
        if let Some(a) = AGENT.get() {
            if a.store.dirs.config != path {
                return Err(format!("Pythia's droidtop state is already open in {}", a.store.dirs.config.display()));
            }
        }
        *FOLDER.lock().unwrap() = Some(path.clone());
        Ok(json!({ "folder": path, "computer": computer_dir() }))
    })
}

/// Runs [`work`] without the interpreter and hands its result to Python.
pub(crate) fn run(py: Python<'_>, work: impl FnOnce() -> Result<Value, String> + Send) -> PyResult<PyObject> {
    match py.allow_threads(work) {
        Ok(value) => to_py(py, &value),
        Err(message) => Err(AgentError::new_err(message)),
    }
}

/// A JSON value as Python's own objects.
fn to_py(py: Python<'_>, value: &Value) -> PyResult<PyObject> {
    Ok(py.import_bound("json")?.call_method1("loads", (value.to_string(),))?.unbind())
}

/// A Python object (dicts, lists and plain values) as a JSON value.
pub(crate) fn from_py(obj: &Bound<'_, PyAny>) -> PyResult<Value> {
    let text: String = obj.py().import_bound("json")?.call_method1("dumps", (obj,))?.extract()?;
    serde_json::from_str(&text).map_err(|e| AgentError::new_err(e.to_string()))
}

/// Adds submodule [`name`], filled by [`fill`], as `pythia._native.<name>`,
/// importable as such.
fn submodule(parent: &Bound<'_, PyModule>, name: &str, fill: fn(&Bound<'_, PyModule>) -> PyResult<()>) -> PyResult<()> {
    let py = parent.py();
    let full = format!("pythia._native.{name}");
    let module = PyModule::new_bound(py, name)?;
    fill(&module)?;
    // Added under its short name first: add_submodule names the attribute after `__name__`.
    parent.add_submodule(&module)?;
    module.setattr("__name__", &full)?;
    py.import_bound("sys")?.getattr("modules")?.set_item(full, &module)?;
    Ok(())
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    m.add("PROTOCOL_VERSION", droidtop_agent_core::PROTOCOL_VERSION)?;
    m.add("AgentError", m.py().get_type_bound::<AgentError>())?;
    m.add_function(wrap_pyfunction!(use_folder, m)?)?;
    submodule(m, "store", store::fill)?;
    submodule(m, "agent", agent::fill)?;
    submodule(m, "pair", pair::fill)?;
    submodule(m, "scan", scan::fill)?;
    submodule(m, "saves", saves::fill)?;
    submodule(m, "remote", remote::fill)?;
    submodule(m, "service", service::fill)?;
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
