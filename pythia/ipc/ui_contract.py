"""The backend contract (contract/README.md): every answer is ok, unavailable, error or pending.

Standard library only, so Pythia's backend and any tool can import it as it is.
"""

STATUSES = ("ok", "unavailable", "error", "pending")
NO_ANSWER = "There was no answer."
MALFORMED = "The answer could not be read."
_OWN = ("status", "slot", "message", "title", "code", "request")


def ok(slot=None, **fields):
    """An `ok` answer with the operation's own fields beside `status`."""
    return _answer("ok", slot, fields)


def unavailable(message, slot=None, title=None):
    """No backend for this here; `message` says why, in words a screen can show."""
    answer = _answer("unavailable", slot, {}, message=message)
    if title:
        answer["title"] = title
    return answer


def error(message, slot=None, code=None):
    """It was tried and failed; `message` is the reason in plain words."""
    answer = _answer("error", slot, {}, message=message)
    if code:
        answer["code"] = code
    return answer


def pending(request, slot=None):
    """The answer comes later, settled by an answer carrying the same `request`."""
    return _answer("pending", slot, {}, request=request)


def _answer(status, slot, fields, **own):
    answer = {"status": status, **own}
    if slot:
        answer["slot"] = slot
    answer.update(fields)
    return answer


def decode(value):
    """The one reading of what a backend sent: a dict with `status`, the contract's own
    fields when present and every other field under `fields`. Anything that is not a
    well-formed answer reads as an `error` (contract rule 1); a bare list is the legacy
    `ok` with the list under `fields["value"]`."""
    if value is None:
        return {"status": "error", "message": NO_ANSWER, "code": "no_answer", "fields": {}}
    if isinstance(value, list):
        return {"status": "ok", "fields": {"value": value}}
    if not isinstance(value, dict) or value.get("status") not in STATUSES:
        return _malformed()
    status = value["status"]
    for name in ("slot", "message", "title", "code", "request"):
        if name in value and not isinstance(value[name], str):
            return _malformed()
    if status in ("unavailable", "error") and not value.get("message"):
        return _malformed()
    if status == "pending" and not value.get("request"):
        return _malformed()
    decoded = {name: value[name] for name in _OWN if name in value}
    decoded["fields"] = {k: v for k, v in value.items() if k not in _OWN}
    return decoded


def _malformed():
    return {"status": "error", "message": MALFORMED, "code": "malformed", "fields": {}}


def guarded(slot, work, log=None):
    """Run `work()`; whatever it raises becomes an `error` answer, reported to `log` once
    (contract rule 1). `work` returns an answer."""
    try:
        return work()
    except Exception as exc:  # noqa: BLE001 -- the caller needs the reason, whatever it was
        if log:
            log(slot, exc)
        return error(str(exc) or exc.__class__.__name__, slot=slot)
