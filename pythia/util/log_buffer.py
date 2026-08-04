"""Plain-Python replacement for Gtk.TextBuffer, used only for storing a
game's captured stdout/stderr (2026-08-04 GTK removal). Implements just the
handful of Gtk.TextBuffer methods real callers (game.py, monitored_command.py)
actually use -- not a general text-buffer, just enough to keep log capture
working without GTK. The eventual Qt log window reads .get_text() and renders
it itself; this holds no display/styling state (Gtk's "warning" red-foreground
tag is gone -- highlighting text is now the Qt log view's job, if it wants it).
"""


class _Iter:
    """Stand-in for Gtk.TextIter -- just an offset into the buffer's text."""

    def __init__(self, buffer: "SimpleLogBuffer", offset: int) -> None:
        self.buffer = buffer
        self.offset = offset


class SimpleLogBuffer:
    def __init__(self) -> None:
        self._text = ""

    def create_tag(self, _name: str, **_kwargs) -> None:
        """No-op -- text styling tags were a GTK/Pango concept; the Qt log
        view is responsible for its own highlighting, if any."""

    def get_start_iter(self) -> _Iter:
        return _Iter(self, 0)

    def get_end_iter(self) -> _Iter:
        return _Iter(self, len(self._text))

    def get_text(self, start: "_Iter | None" = None, end: "_Iter | None" = None, _include_hidden: bool = True) -> str:
        start_offset = start.offset if start else 0
        end_offset = end.offset if end else len(self._text)
        return self._text[start_offset:end_offset]

    def set_text(self, text: str) -> None:
        self._text = text or ""

    def insert(self, _iter: "_Iter", text: str, _length: int = -1) -> None:
        # Real Gtk.TextBuffer.insert() inserts at the iter's position; every
        # real caller here always passes get_end_iter(), so this just appends.
        self._text += text

    def delete(self, _start: "_Iter", _end: "_Iter") -> None:
        self._text = ""
