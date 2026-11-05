//! `pythia._native.kdeconnect`: this computer as a KDE Connect device (Droidtop/tracker#645), the same device
//! droidtop runs, through lib-kdeconnect's one operation table (`kdeconnect_service::ops`). Phones and computers with
//! the official KDE Connect apps, and droidtop handhelds, find it, pair with it, share files, links and text with it,
//! share the clipboard and type into it.

use pyo3::prelude::*;
use serde_json::Value;

use crate::{from_py, run};

pub fn fill(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(call, m)?)?;
    Ok(())
}

/// Runs KDE Connect operation `op` with keyword arguments, for example
/// `call("start", state_dir=..., receive_dir=..., name="Living room PC", type="desktop")`,
/// `call("devices")`, `call("event", timeout_ms=30000)`, `call("pair", id=...)`,
/// `call("share_file", id=..., path=...)`, `call("clipboard_set", text=...)`.
/// The operations and their replies are lib-kdeconnect's (`crates/service/src/ops.rs`). A refusal raises
/// AgentError with words a screen can show. `event` waits without holding the interpreter.
#[pyfunction]
#[pyo3(signature = (op, **kwargs))]
fn call(py: Python<'_>, op: String, kwargs: Option<&Bound<'_, pyo3::types::PyDict>>) -> PyResult<PyObject> {
    let args = match kwargs {
        Some(k) => from_py(k.as_any())?,
        None => Value::Object(Default::default()),
    };
    run(py, move || {
        let reply = kdeconnect_service::ops::call(&op, &args);
        match reply.get("error").and_then(Value::as_str) {
            Some(e) => Err(e.to_string()),
            None => Ok(reply),
        }
    })
}
