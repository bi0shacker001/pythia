"""The vendored backend contract (pythia/ipc/ui_contract.py) reads every case of mytop-ui's fixtures as written,
and the server maps what a slot returns onto it."""

import json
from pathlib import Path
from unittest import TestCase

from pythia.ipc import CONTRACT_PIN, ui_contract
from pythia.ipc.server import to_answer

CASES = json.loads((Path(__file__).parent / "fixtures" / "ui_contract_answers.json").read_text("utf-8"))["cases"]


class ContractFixturesTest(TestCase):
    def test_every_fixture_decodes_as_written(self):
        for case in CASES:
            with self.subTest(case["name"]):
                self.assertEqual(ui_contract.decode(case["input"]), case["decoded"])

    def test_the_pin_names_the_file_it_copies(self):
        self.assertIn("contract/python/ui_contract.py", CONTRACT_PIN)


class ToAnswerTest(TestCase):
    def test_an_answer_is_kept_as_it_is(self):
        answer = {"status": "ok", "slot": "get_modes", "manager": True}
        self.assertIs(to_answer("get_modes", answer), answer)
        for answer in (
            {"status": "unavailable", "message": "Not built yet."},
            {"status": "error", "message": "No."},
            {"status": "pending", "request": "x-1"},
        ):
            self.assertEqual(to_answer("x", answer), answer)

    def test_a_bare_value_is_the_value_of_an_ok(self):
        self.assertEqual(to_answer("get_system_uuid", "abc"), {"status": "ok", "value": "abc"})
        self.assertEqual(to_answer("get_all_plugins", [{"id": "a"}]), {"status": "ok", "value": [{"id": "a"}]})

    def test_nothing_is_a_bare_ok(self):
        self.assertEqual(to_answer("stop_watch", None), {"status": "ok"})

    def test_something_that_is_not_an_answer_is_an_error_not_passed_on(self):
        for bad in ({"no": "status"}, {"status": "maybe"}, {"status": "error"}, {"status": "pending"}):
            with self.subTest(bad=bad):
                answer = to_answer("x", bad)
                self.assertEqual(answer["status"], "error")
                self.assertEqual(answer["code"], "malformed")
