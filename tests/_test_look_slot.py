"""A second Look does not have the first's progress or result written over
it (Droidtop/tracker#538 retest: "starting a second Look while one runs makes
the first's progress and result overwrite the page and the stored result")."""

import os
from unittest import TestCase, mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from pythia import look  # noqa: E402
from pythia.ui import backend  # noqa: E402

APP = QCoreApplication.instance() or QCoreApplication([])


class TwoLooksTest(TestCase):
    def test_the_newer_look_keeps_the_page(self):
        b = backend.Backend()
        workers, emitted = [], []
        b.lookChanged.connect(emitted.append)

        def fake_look(path, progress):
            progress({"folders": 1, "games": 1})
            return {"status": "ok", "root": path, "summary": f"Found in {path}", "proposals": []}

        with (
            mock.patch.object(
                backend.threading,
                "Thread",
                side_effect=lambda target, args, **kw: workers.append((target, args)) or mock.Mock(),
            ),
            mock.patch.object(look, "look", side_effect=fake_look),
        ):
            first = b.start_look("/games/first")["job"]
            second = b.start_look("/games/second")["job"]
            emitted.clear()
            workers[1][0](*workers[1][1])  # the second finishes first
            workers[0][0](*workers[0][1])  # then the first
        self.assertEqual(b.get_look()["root"], "/games/second")
        self.assertEqual(b.get_look()["job"], second)
        self.assertTrue(all(e.get("job") != first for e in emitted), emitted)
