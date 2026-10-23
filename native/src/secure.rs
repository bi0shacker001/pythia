//! `pythia._native.secure_screen`: service mode, windowcast's secure-screen
//! helper (windowcast#467; docs/SPEC.md 1.10), through droidtop-agent's
//! `secure_screen` module so Pythia and the agent ask and switch alike.

use std::path::PathBuf;

use droidtop_agent_service::secure_screen::{self, Action};
use pyo3::prelude::*;
use serde_json::json;

use crate::run;

pub fn fill(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(state, m)?)?;
    m.add_function(wrap_pyfunction!(consent, m)?)?;
    m.add_function(wrap_pyfunction!(request, m)?)?;
    m.add_function(wrap_pyfunction!(perform, m)?)?;
    Ok(())
}

fn action(word: &str) -> Result<Action, String> {
    Action::parse(word).ok_or_else(|| format!("{word} is not enable, disable or uninstall"))
}

/// `not_available` (not Windows), `absent`, `disabled` or `enabled`.
#[pyfunction]
fn state() -> &'static str {
    secure_screen::state()
}

/// What service mode does, said before it is turned on.
#[pyfunction]
fn consent() -> &'static str {
    secure_screen::CONSENT
}

/// From Pythia itself: asks Windows to run `program args.. <action>
/// --confirmed [--grant-sid <sid>]` with administrator approval, waits for
/// it, and returns what happened in words. Slow: it waits for the person.
#[pyfunction]
fn request(py: Python<'_>, action: String, program: PathBuf, args: Vec<String>) -> PyResult<PyObject> {
    run(py, move || {
        let action = self::action(&action)?;
        let args: Vec<&str> = args.iter().map(String::as_str).collect();
        secure_screen::request(action, &program, &args).map(|done| json!(done))
    })
}

/// In the elevated copy: does the change, installing `helper` when the
/// helper is not installed yet. Returns the exit code for the copy that asked.
#[pyfunction]
#[pyo3(signature = (action, grant_sid = None, helper = None))]
fn perform(py: Python<'_>, action: String, grant_sid: Option<String>, helper: Option<PathBuf>) -> PyResult<PyObject> {
    run(py, move || {
        let action = self::action(&action)?;
        Ok(json!(match secure_screen::perform(action, grant_sid.as_deref(), helper.as_deref()) {
            Ok(()) => secure_screen::exit::DONE,
            Err((code, message)) => {
                eprintln!("{message}");
                code
            }
        }))
    })
}
