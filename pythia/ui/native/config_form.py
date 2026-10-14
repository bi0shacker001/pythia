"""Generic global/game/instance config-cascade editor, shared by the
Settings page (global scope) and the per-game Advanced tab (game/instance
scope) -- 2026-08-08, native UI depth: "do not duplicate cascade logic in
the UI." Every value shown and written goes through the real
`Backend.get_effective_config()`/`set_config_value()`, which themselves are
thin wrappers over `pythia.config_cascade`'s own real storage tiers -- this
module never reads/writes config_cascade directly.

`ADVANCED_CONFIG_FIELDS` is the field list used by both contexts (see its
own comment for why each field is here). Field type handling:
"bool"/"text"/"path"/"select" behave the same as plugins_view.py's schema-
driven form; "keyvalue" (multi-line KEY=VALUE -> dict, for `environment`)
and "json" (raw JSON text, for `monitoring`, whose shape config_cascade
itself never constrains) are new here.
"""

from __future__ import annotations

import json

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Real keys pythia.launch.resolve_launch_plan()/launch_native() actually
# consume (backend/environment/command_prefix/pre_launch_script/
# wait_pre_launch/post_exit_script/monitoring -- see
# pythia/core/sync/... no, see pythia/launch.py and
# pythia/ui/backend.py's get_effective_launch_plan()'s own key list) plus
# the Wine/Proton controls consumed by resolve_launch_plan().
ADVANCED_CONFIG_FIELDS = [
    {
        "key": "backend",
        "label": "Execution backend",
        "type": "select",
        "options": ["native", "wine", "proton"],
        "help": "Wine and Proton run internally when their executable is installed or configured below.",
    },
    {"key": "wine_prefix", "label": "Wine prefix path", "type": "path"},
    {"key": "wine_version", "label": "Wine/Proton executable", "type": "text"},
    {"key": "wine_arch", "label": "Wine architecture", "type": "select", "options": ["win32", "win64"]},
    {"key": "dxvk", "label": "DXVK", "type": "bool"},
    {"key": "vkd3d", "label": "VKD3D", "type": "bool"},
    {"key": "esync", "label": "esync", "type": "bool"},
    {"key": "fsync", "label": "fsync", "type": "bool"},
    {"key": "environment", "label": "Environment variables (one KEY=VALUE per line)", "type": "keyvalue"},
    {"key": "command_prefix", "label": "Command prefix", "type": "text"},
    {"key": "pre_launch_script", "label": "Pre-launch script", "type": "path"},
    {"key": "wait_pre_launch", "label": "Wait for pre-launch script to exit", "type": "bool"},
    {"key": "post_exit_script", "label": "Post-exit script", "type": "path"},
    {"key": "monitoring", "label": "Monitoring (JSON)", "type": "json"},
]


def _keyvalue_getter(text_edit: QPlainTextEdit):
    def getter():
        text = text_edit.toPlainText().strip()
        if not text:
            return None
        result = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError(f"Invalid line (expected KEY=VALUE): {line!r}")
            key, value = line.split("=", 1)
            result[key.strip()] = value.strip()
        return result

    return getter


def _keyvalue_setter(text_edit: QPlainTextEdit):
    def setter(value):
        if isinstance(value, dict):
            text_edit.setPlainText("\n".join(f"{k}={v}" for k, v in value.items()))
        else:
            text_edit.setPlainText("")

    return setter


def _json_getter(text_edit: QPlainTextEdit):
    def getter():
        text = text_edit.toPlainText().strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}") from e

    return getter


def _json_setter(text_edit: QPlainTextEdit):
    def setter(value):
        text_edit.setPlainText(json.dumps(value, indent=2) if value is not None else "")

    return setter


def build_field_widget(field: dict):
    """One (widget, getter, setter) triple per field. `getter` may raise
    ValueError for input that doesn't parse (keyvalue/json) -- callers must
    catch this per field so one bad value doesn't block saving every other
    field (2026-08-08 requirement: "invalid ... values must be identified
    without discarding the user's other edits")."""
    field_type = field.get("type", "text")

    if field_type == "bool":
        widget = QCheckBox()
        return widget, (lambda: widget.isChecked()), (lambda v: widget.setChecked(bool(v)))

    if field_type == "select":
        widget = QComboBox()
        widget.addItem("(unset)", None)
        for option in field.get("options", []):
            widget.addItem(option, option)

        def getter():
            return widget.currentData()

        def setter(value):
            index = widget.findData(value)
            widget.setCurrentIndex(index if index >= 0 else 0)

        return widget, getter, setter

    if field_type == "path":
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        line_edit = QLineEdit()

        def _pick():
            path = QFileDialog.getExistingDirectory(container, field.get("label", "Select a folder"))
            if path:
                line_edit.setText(path)

        browse = QPushButton("Browse...")
        browse.clicked.connect(_pick)
        layout.addWidget(line_edit)
        layout.addWidget(browse)
        return container, (lambda: line_edit.text() or None), (lambda v: line_edit.setText(v or "" if v is not None else ""))

    if field_type == "keyvalue":
        widget = QPlainTextEdit()
        widget.setMaximumHeight(80)
        return widget, _keyvalue_getter(widget), _keyvalue_setter(widget)

    if field_type == "json":
        widget = QPlainTextEdit()
        widget.setMaximumHeight(80)
        return widget, _json_getter(widget), _json_setter(widget)

    # "text" and anything unrecognized -- honest plain text fallback.
    widget = QLineEdit()
    return widget, (lambda: widget.text() or None), (lambda v: widget.setText(str(v) if v is not None else ""))


class CascadeConfigForm(QWidget):
    """Renders `fields` (a subset of ADVANCED_CONFIG_FIELDS or the full
    list) for a given `(game_id, variant_id)` scope, sourcing every value
    from the real cascade and writing edits back to `edit_scope`
    ("global"/"game"/"instance"). `game_id`/`variant_id` must be real
    `None` when not applicable -- NEVER an empty string:
    datadir.find_entry_file()'s glob-prefix matching (`f"{uuid}-{game_id}-"`)
    would match every entry for this system if game_id were "", silently
    reading/writing the wrong game's config."""

    def __init__(self, backend, fields: list, *, game_id: str | None, variant_id: str | None, edit_scope: str, parent=None):
        super().__init__(parent)
        if edit_scope not in ("global", "game", "instance"):
            raise ValueError(f"unknown edit_scope: {edit_scope!r}")
        self.backend = backend
        self.fields = fields
        self.game_id = game_id
        self.variant_id = variant_id
        self.edit_scope = edit_scope
        self._rows: dict[str, dict] = {}

        root = QVBoxLayout(self)
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        for field in fields:
            widget, getter, setter = build_field_widget(field)
            source_label = QLabel("")
            source_label.setMinimumWidth(90)
            reset_button = QPushButton("Reset")
            reset_button.setToolTip(f"Clear this {edit_scope}-scope override.")
            reset_button.clicked.connect(lambda _checked=False, k=field["key"]: self._on_reset(k))

            row_container = QWidget()
            row_layout = QHBoxLayout(row_container)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(widget, 1)
            row_layout.addWidget(source_label)
            row_layout.addWidget(reset_button)

            label_text = field["label"]
            if field.get("help"):
                label_text += " *"
            form.addRow(label_text, row_container)
            self._rows[field["key"]] = {
                "widget": widget,
                "getter": getter,
                "setter": setter,
                "source_label": source_label,
                "reset_button": reset_button,
            }
        root.addLayout(form)

        help_lines = [f["help"] for f in fields if f.get("help")]
        if help_lines:
            help_label = QLabel("* " + " | ".join(help_lines))
            help_label.setWordWrap(True)
            root.addWidget(help_label)

        self.refresh()

    def refresh(self) -> None:
        keys = [f["key"] for f in self.fields]
        effective = self.backend.get_effective_config(keys, self.game_id, self.variant_id)
        for key, row in self._rows.items():
            info = effective.get(key) or {"value": None, "source_scope": "default", "raw": {}}
            row["setter"](info.get("value"))
            row["source_label"].setText(f"({info.get('source_scope', 'default')})")
            raw_here = (info.get("raw") or {}).get(self.edit_scope)
            row["reset_button"].setEnabled(raw_here is not None)

    def save(self) -> dict:
        """Writes every field's current widget value to `edit_scope`,
        independently -- a field that fails to parse or fails to save does
        NOT stop the others from being attempted. Returns {"status": "ok"}
        or {"status": "error", "errors": {key: message}}."""
        errors: dict[str, str] = {}
        for key, row in self._rows.items():
            try:
                value = row["getter"]()
            except ValueError as e:
                errors[key] = str(e)
                continue
            result = self.backend.set_config_value(self.edit_scope, self.game_id, self.variant_id, key, value)
            if result.get("status") != "ok":
                errors[key] = result.get("message", "Unknown error")
        self.refresh()
        if errors:
            return {"status": "error", "errors": errors}
        return {"status": "ok"}

    def _on_reset(self, key: str) -> None:
        result = self.backend.set_config_value(self.edit_scope, self.game_id, self.variant_id, key, None)
        if not result or result.get("status") != "ok":
            QMessageBox.warning(self, "Reset setting", (result or {}).get("message", f"Could not reset {key}."))
            return
        self.refresh()
