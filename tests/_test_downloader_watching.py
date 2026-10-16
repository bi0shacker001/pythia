"""Only the downloader that started a watch says Watching
(Droidtop/tracker#538 retest D4)."""

from unittest import TestCase

from pythia.ui.backend import downloader_watching


class DownloaderWatchingTest(TestCase):
    def test_only_the_downloader_that_started_it_is_watching(self):
        watcher = {"folder": "G:/Downloads", "program": "F95Checker.exe", "plugin": "f95checker"}
        self.assertEqual(downloader_watching(watcher, "f95checker"), "G:/Downloads")
        self.assertIsNone(downloader_watching(watcher, "steamcmd"))

    def test_a_watch_for_no_downloader_is_none_of_theirs(self):
        self.assertIsNone(downloader_watching({"folder": "G:/Downloads", "plugin": None}, "steamcmd"))
        self.assertIsNone(downloader_watching(None, "steamcmd"))
