//! `pythia._native.tailscale`: the computer's own Tailscale node, read through tailscaled's LocalAPI with lib-net's
//! `net-tailscale` (Droidtop/tracker#645), the way ktailctl and the `tailscale` CLI read it. No account token and no
//! control server: when tailscaled is not running, there is no tailnet and nothing else changes.

use pyo3::prelude::*;
use serde_json::{json, Value};

use crate::run;

pub fn fill(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(status, m)?)?;
    Ok(())
}

fn peer(p: &net_tailscale::Peer) -> Value {
    json!({
        "host_name": p.host_name,
        "dns_name": p.dns_name,
        "os": p.os,
        "addresses": p.addresses,
        "online": p.online,
    })
}

/// `{"present": false}` when no tailscaled runs here; otherwise `{"present": true, "running": bool, "state": "Running",
/// "me": {...} | None, "peers": [{host_name, dns_name, os, addresses, online}, ...]}`. Raises AgentError when
/// tailscaled answers with an error. Asking does not hold the interpreter.
#[pyfunction]
fn status(py: Python<'_>) -> PyResult<PyObject> {
    run(py, || {
        let tailnet = net_tailscale::status().map_err(|e| e.to_string())?;
        let running = tailnet.running();
        Ok(match tailnet {
            net_tailscale::Tailnet::Absent => json!({"present": false}),
            net_tailscale::Tailnet::Present { state, me, peers } => json!({
                "present": true,
                "running": running,
                "state": state,
                "me": me.as_ref().map(peer),
                "peers": peers.iter().map(peer).collect::<Vec<_>>(),
            }),
        })
    })
}
