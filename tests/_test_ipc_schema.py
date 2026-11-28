"""The schema is read from the Backend, so it cannot drift: the checked-in file must be what the Backend gives."""

import re
from pathlib import Path
from unittest import TestCase

from pythia.ipc import schema
from pythia.ipc.declarations import Event, method
from pythia.ipc.server import BUILTINS
from pythia.ui import backend as backend_module


def _later(fn):
    fn.answers_later = True
    return fn


class DescribeTest(TestCase):
    def test_slots_and_signals_become_methods_and_events(self):
        class Toy:
            changed = Event()
            said = Event("string", "any")

            @method("string", "boolean", returns="answer")
            def both(self, name: str, flag: bool):
                return {}

            @method(returns="string")
            def text(self) -> str:
                return ""

            @method("string")
            def plain(self, path: str):
                pass

            @method("integer", "number", "any", returns="answer")
            @_later
            def numbers(self, a, b, c):
                return {}

            @method("string")
            def with_extra_default(self, a: str, extra: str = ""):
                pass

            def not_a_slot(self):
                pass

        described = schema.describe(Toy)
        methods = {m["name"]: m for m in described["methods"]}
        self.assertEqual(
            methods["both"],
            {
                "name": "both",
                "params": [{"name": "name", "type": "string"}, {"name": "flag", "type": "boolean"}],
                "returns": "answer",
            },
        )
        self.assertEqual(methods["text"]["returns"], "string")
        self.assertEqual(methods["plain"]["returns"], "none")
        self.assertEqual([p["type"] for p in methods["numbers"]["params"]], ["integer", "number", "any"])
        self.assertTrue(methods["numbers"]["async"])
        self.assertNotIn("async", methods["both"])
        self.assertEqual(
            methods["with_extra_default"]["params"],
            [{"name": "a", "type": "string"}, {"name": "extra", "type": "string", "optional": True}],
        )
        self.assertNotIn("not_a_slot", methods)
        self.assertEqual(
            described["events"],
            [{"name": "changed", "params": []}, {"name": "said", "params": [{"type": "string"}, {"type": "any"}]}],
        )

    def test_the_list_is_sorted_so_the_file_does_not_churn(self):
        described = schema.backend_schema()
        names = [m["name"] for m in described["methods"]]
        self.assertEqual(names, sorted(names))


class BackendSchemaTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated = schema.backend_schema()

    def test_the_checked_in_schema_is_what_the_backend_gives(self):
        self.assertEqual(
            schema.render(self.generated),
            schema.SCHEMA_PATH.read_text("utf-8"),
            "pythia/ui/backend.py changed; run: python -m pythia.ipc.schema",
        )

    def test_every_declared_backend_method_is_included(self):
        source = Path(backend_module.__file__).read_text("utf-8")
        declared = len(re.findall(r"^    @method\(", source, flags=re.MULTILINE))
        self.assertEqual(len(self.generated["methods"]), declared)

    def test_every_method_can_be_called_on_a_backend(self):
        for entry in self.generated["methods"]:
            self.assertTrue(callable(getattr(backend_module.Backend, entry["name"])), entry["name"])

    def test_no_slot_is_named_like_a_built_in_method(self):
        self.assertFalse({m["name"] for m in self.generated["methods"]} & set(BUILTINS))

    def test_the_signals_are_the_ones_the_html_ui_listens_to(self):
        events = {e["name"] for e in self.generated["events"]}
        self.assertTrue({"gamesChanged", "uiLog", "asyncResult", "jobsChanged", "padAction"} <= events)

    def test_slow_slots_say_they_settle_later(self):
        by_name = {m["name"]: m for m in self.generated["methods"]}
        self.assertTrue(by_name["get_system_info"].get("async"))
        self.assertFalse(by_name["get_modes"].get("async"))

    def test_start_watch_has_its_optional_argument(self):
        by_name = {m["name"]: m for m in self.generated["methods"]}
        self.assertEqual(by_name["start_watch"]["params"][2], {"name": "plugin_id", "type": "string", "optional": True})
