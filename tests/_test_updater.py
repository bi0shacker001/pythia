"""pythia.updater: which lane a build is on, and what a lane offers (docs/SPEC.md decision 13)."""

import json
from unittest import TestCase, mock

from pythia import updater


def info(code):
    return {"versionCode": code, "versionName": f"0.5.23-dev.{code}", "assets": {}}


class UpdaterTest(TestCase):
    def test_unstable_is_the_highest_build_tag(self):
        releases = [
            {"tag_name": "stable", "assets": []},
            {"tag_name": "v0.5.23-dev.170", "assets": [{"name": "release-info.json", "browser_download_url": "u170"}]},
            {"tag_name": "v0.5.23-dev.181", "assets": [{"name": "release-info.json", "browser_download_url": "u181"}]},
            {"tag_name": "v0.5.23", "assets": []},
        ]

        def get(url):
            if "api.github.com" in url:
                return json.dumps(releases).encode()
            return json.dumps(info(int(url[1:]))).encode()

        with mock.patch.object(updater, "_get", side_effect=get):
            self.assertEqual(181, updater.release_info("unstable")["versionCode"])

    def test_the_lane_is_the_one_that_has_this_build(self):
        lanes = {"stable": info(150), "testing": info(170)}
        with (
            mock.patch.object(updater, "installed_build", return_value=170),
            mock.patch.object(updater, "release_info", side_effect=lambda lane: lanes.get(lane)),
        ):
            self.assertEqual("testing", updater.installed_lane())
        with (
            mock.patch.object(updater, "installed_build", return_value=175),
            mock.patch.object(updater, "release_info", side_effect=lambda lane: lanes.get(lane)),
        ):
            self.assertEqual("unstable", updater.installed_lane())

    def test_a_lane_it_cannot_ask_decides_nothing(self):
        with mock.patch.object(updater, "release_info", side_effect=OSError("offline")):
            self.assertIsNone(updater.installed_lane())

    def test_check_says_newer_current_or_none(self):
        with (
            mock.patch.object(updater, "updatable", return_value=None),
            mock.patch.object(updater, "lane", return_value="unstable"),
            mock.patch.object(updater, "installed_build", return_value=170),
        ):
            with mock.patch.object(updater, "release_info", return_value=info(181)):
                self.assertEqual("newer", updater.check()["state"])
            with mock.patch.object(updater, "release_info", return_value=info(170)):
                self.assertEqual("current", updater.check()["state"])
            with mock.patch.object(updater, "release_info", return_value=None):
                self.assertEqual("none", updater.check()["state"])

    def test_a_copy_from_the_repository_does_not_update_itself(self):
        with mock.patch.object(updater, "installed_build", return_value=0):
            self.assertEqual("unavailable", updater.check()["state"])
