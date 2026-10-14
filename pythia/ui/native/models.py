"""Data adapters + Qt model/view classes for the native library list. Kept
separate from any widget/backend-call code (summarize_game() is plain,
PySide6-model-free data shaping) so it can be unit tested without a
QApplication -- see aisync/CLAUDE-UI-RESEARCH.md for the pattern this
follows (Qt model/view over rebuilding widget trees, Lutris's stable-sort
convention).
"""

from __future__ import annotations

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSortFilterProxyModel, Qt

GAME_ID_ROLE = Qt.ItemDataRole.UserRole + 1
SUMMARY_ROLE = Qt.ItemDataRole.UserRole + 2


def summarize_game(row: dict, *, own_system_uuid: str | None = None) -> dict:
    """Flatten one Backend.games row ({"game_id", "entries": [...]}) into a
    single display-friendly summary. `entries` is the cross-system merged
    view (pythia.datadir.list_known_games()'s own docstring) -- this picks
    real, non-fabricated values only: the owning system's own entry when one
    exists (for notes/engine_override, which are only ever meaningfully
    editable on the system that owns them), falling back to the first known
    entry purely for display fields every entry should agree on
    (canonical_name). Never invents a value; `editable` tells a caller
    whether notes/engine_override actually have somewhere to be saved."""
    entries = row.get("entries") or []
    own_entry = (
        next((e for e in entries if e.get("system_uuid") == own_system_uuid), None) if own_system_uuid else None
    )
    first = own_entry or (entries[0] if entries else {})

    systems = sorted({e.get("system_uuid", "") for e in entries if e.get("system_uuid")})
    platform_ids: list[dict] = []
    seen_platforms: set[tuple] = set()
    for e in entries:
        for p in e.get("platform_ids") or []:
            key = (p.get("platform"), p.get("id"))
            if key not in seen_platforms:
                seen_platforms.add(key)
                platform_ids.append(p)

    version_count = 0
    variant_count = 0
    for e in entries:
        for group in e.get("versions") or []:
            version_count += 1
            variant_count += len(group.get("variants") or [])

    # Only the owning system's own variants are ever meaningfully selectable
    # for a direct launch from here (a variant's "path" is inherently
    # system-local -- see pythia.onboarding.find_candidates()'s own docstring
    # on why path matches stay own-system-only). Real variant `id`s only
    # (datadir._ensure_variant_ids()), never fabricated.
    variants = []
    if own_entry is not None:
        for group in own_entry.get("versions") or []:
            for variant in group.get("variants") or []:
                if variant.get("id"):
                    variants.append(
                        {
                            "variant_id": variant["id"],
                            "version": group.get("version", ""),
                            "path": variant.get("path", ""),
                        }
                    )

    # Cross-system detail rows for the Versions & Variants tree (2026-08-08,
    # native UI depth pass) -- every system's own view, informational only.
    # `editable`/`local` distinguish "this system's own, launchable variant"
    # from "another system's copy, shown for visibility, never launchable
    # from here" -- the tree renders both, but only `local` rows ever
    # correspond to an entry in `variants` above.
    detail_rows: list[dict] = []
    for e in entries:
        is_own = own_system_uuid is not None and e.get("system_uuid") == own_system_uuid
        for group in e.get("versions") or []:
            for variant in group.get("variants") or []:
                source = variant.get("source") or {}
                detail_rows.append(
                    {
                        "system_uuid": e.get("system_uuid", ""),
                        "local": is_own,
                        "version": group.get("version", ""),
                        "variant_id": variant.get("id", ""),
                        "source_kind": source.get("kind", ""),
                        "language": variant.get("language") or "",
                        "mods": variant.get("mods") or [],
                        "path": variant.get("path", ""),
                    }
                )

    # Updates are observations, not necessarily per-system -- surfaced from
    # every known entry, each tagged with which system's record reported it
    # so the UI never implies an update was found "here" when it wasn't.
    updates: list[dict] = []
    for e in entries:
        for u in e.get("available_updates") or []:
            updates.append({**u, "_reported_by": e.get("system_uuid", "")})

    # Timeline is inherently per-system (add_timeline_event() only ever
    # appends to the caller's own entry) -- only the owning system's own
    # history is real/meaningful to show here, most-recent first.
    timeline_events = sorted(
        (own_entry or {}).get("timeline_events") or [],
        key=lambda ev: ev.get("timestamp", ""),
        reverse=True,
    )

    return {
        "game_id": row.get("game_id", ""),
        "canonical_name": first.get("canonical_name") or row.get("game_id") or "Unknown game",
        "systems": systems,
        "platform_ids": platform_ids,
        "version_count": version_count,
        "variant_count": variant_count,
        "has_updates": any(e.get("available_updates") for e in entries),
        "notes": (own_entry or {}).get("notes", ""),
        "engine_override": (own_entry or {}).get("engine_override", ""),
        "editable": own_entry is not None,
        "variants": variants,
        "detail_rows": detail_rows,
        "updates": updates,
        "timeline_events": timeline_events,
        "entries": entries,
    }


class GameListModel(QAbstractListModel):
    """Wraps Backend.games. `refresh(rows)` is called whenever
    Backend.gamesChanged fires; nothing here talks to the backend directly
    (kept a plain data model, per the task's "keep presentation classes
    separate from data adapters/models and backend calls" requirement)."""

    def __init__(self, own_system_uuid: str | None = None, parent=None):
        super().__init__(parent)
        self._own_system_uuid = own_system_uuid
        self._rows: list[dict] = []

    def refresh(self, games: list[dict]) -> None:
        self.beginResetModel()
        self._rows = [summarize_game(row, own_system_uuid=self._own_system_uuid) for row in games]
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        summary = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return summary["canonical_name"]
        if role == GAME_ID_ROLE:
            return summary["game_id"]
        if role == SUMMARY_ROLE:
            return summary
        return None

    def summary_at(self, row: int) -> dict | None:
        return self._rows[row] if 0 <= row < len(self._rows) else None

    def summary_for_game_id(self, game_id: str) -> dict | None:
        return next((r for r in self._rows if r["game_id"] == game_id), None)

    def index_for_game_id(self, game_id: str) -> QModelIndex:
        """A real QModelIndex for re-selecting a game after a refresh, or an
        invalid index if it's no longer present -- lets a view honestly
        clear its selection instead of silently keeping a stale one
        (2026-08-08, native UI depth: "keep selection stable across refresh/
        filter/model resets, or clear the inspector honestly")."""
        for row, summary in enumerate(self._rows):
            if summary["game_id"] == game_id:
                return self.index(row, 0)
        return QModelIndex()


class GameFilterProxyModel(QSortFilterProxyModel):
    """Live text filter (name + platform) over GameListModel, stable-sorted
    by (name, game_id) -- the Lutris gui/views/store.py `sort_func` pattern
    (locale-lowercased primary key, a fixed secondary key so ties don't
    reshuffle between refreshes), expressed as a QSortFilterProxyModel
    instead of a hand-rolled GtkListStore sort function."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_text = ""
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str) -> None:
        self._search_text = (text or "").casefold().strip()
        # invalidateFilter()/invalidateRowsFilter() are both deprecated as of
        # this project's PySide6 version (6.11) -- invalidate() is the
        # current recommended call; re-running the sort too is harmless
        # since sort order never depends on the search text.
        self.invalidate()

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        left_summary = left.data(SUMMARY_ROLE) or {}
        right_summary = right.data(SUMMARY_ROLE) or {}
        left_key = (left_summary.get("canonical_name", "").casefold(), left_summary.get("game_id", ""))
        right_key = (right_summary.get("canonical_name", "").casefold(), right_summary.get("game_id", ""))
        return left_key < right_key

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        if not self._search_text:
            return True
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        summary = model.data(index, SUMMARY_ROLE) or {}
        haystack = summary.get("canonical_name", "").casefold()
        if self._search_text in haystack:
            return True
        return any(self._search_text in str(p.get("platform", "")).casefold() for p in summary.get("platform_ids", []))
