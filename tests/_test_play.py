"""The game page's primary slot (pythia.play; UX review A12 and the owner's
decisions 1 and 2 in docs/SPEC.md)."""

from unittest import TestCase, mock

from pythia import play


def entry(system="me", path="/games/Tiny", runner=None, ids=(), updates=()):
    return {
        "system_uuid": system,
        "game_id": "g1",
        "canonical_name": "Tiny Game",
        "platform_ids": [{"platform": p, "id": i} for p, i in ids],
        "available_updates": list(updates),
        "versions": [{"version": "1.0", "variants": [{"path": path, "runner": runner}]}],
    }


class StateTest(TestCase):
    def state(self, entries, steam_here=False, refused=None):
        with (
            mock.patch.object(play.datadir, "list_all_entries", return_value=entries),
            mock.patch.object(play, "get_system_uuid", return_value="me"),
            mock.patch.object(play, "_steam_here", return_value=steam_here),
            mock.patch.object(play, "_runner_check", return_value=refused),
        ):
            return play.state("g1")

    def test_not_installed_here_is_install_disabled_with_its_reason(self):
        s = self.state([entry(system="other")])
        self.assertEqual((s["primary"]["action"], s["primary"]["enabled"]), ("install", False))
        self.assertEqual(s["primary"]["reason"], play.NO_INSTALL)

    def test_steam_comes_first_when_steam_is_here(self):
        s = self.state([entry(runner="wine", ids=[("steam", "620")])], steam_here=True)
        self.assertEqual(
            (s["primary"]["action"], s["primary"]["enabled"], s["primary"]["via"]), ("play", True, "steam")
        )
        self.assertEqual(s["store_url"], "https://store.steampowered.com/app/620/")

    def test_without_steam_a_runner_plays_when_lutris_allows_it(self):
        s = self.state([entry(runner="wine", ids=[("steam", "620")])], steam_here=False)
        self.assertEqual((s["primary"]["enabled"], s["primary"]["via"]), (True, "runner"))
        refused = self.state([entry(runner="wine")], refused="Missing runner")
        self.assertEqual((refused["primary"]["enabled"], refused["primary"]["reason"]), (False, "Missing runner"))

    def test_nothing_to_start_it_says_so(self):
        s = self.state([entry()])
        self.assertEqual((s["primary"]["action"], s["primary"]["enabled"]), ("play", False))
        self.assertEqual(s["primary"]["reason"], play.NO_RUNNER)

    def test_an_update_is_never_hidden_and_never_takes_the_slot(self):
        s = self.state([entry(runner="wine", ids=[("f95", "123")], updates=[{"version": "1.1"}])])
        self.assertEqual(s["primary"]["action"], "play")
        self.assertEqual(s["update"], {"available": True, "version": "1.1", "enabled": False, "reason": play.NO_UPDATE})
        self.assertEqual(s["store_url"], "https://f95zone.to/threads/123/")


class PlayTest(TestCase):
    def test_steam_starts_by_its_link_and_a_runner_by_pythias_own_process(self):
        steam = {"status": "ok", "primary": {"action": "play", "enabled": True, "via": "steam"}}
        runner = {"status": "ok", "primary": {"action": "play", "enabled": True, "via": "runner"}}
        entries = [entry(runner="wine", ids=[("steam", "620")])]
        with mock.patch.object(play, "_entries", return_value=entries):
            with mock.patch.object(play, "state", return_value=steam):
                self.assertEqual(play.play("g1")["url"], "steam://rungameid/620")
            with (
                mock.patch.object(play, "state", return_value=runner),
                mock.patch("pythia.droidtop.launch._run_in_pythia", return_value={"result": "started"}) as run,
            ):
                self.assertEqual(play.play("g1"), {"status": "ok", "via": "runner"})
            run.assert_called_once_with("g1", "Tiny Game")

    def test_a_disabled_slot_does_not_start_anything(self):
        off = {"status": "ok", "primary": {"action": "install", "enabled": False, "reason": play.NO_INSTALL}}
        with mock.patch.object(play, "state", return_value=off):
            self.assertEqual(play.play("g1"), {"status": "error", "message": play.NO_INSTALL})


class InstallTest(TestCase):
    """Install hands the game to its store's own installer (Droidtop/tracker#630)."""

    def entries(self, ids=(), owned=()):
        e = entry(ids=ids)
        e["versions"] = []
        e["ownership"] = [{"platform": p} for p in owned]
        return [e]

    def test_the_store_it_is_owned_on_installs_it(self):
        with mock.patch("sys.platform", "win32"):
            self._owned_store()

    def _owned_store(self):
        primary = play.install_state(self.entries(ids=[("steam", "440"), ("gog", "1207658924")], owned=["gog"]))
        self.assertEqual((primary["enabled"], primary["via"]), (True, "gog"))
        self.assertEqual(primary["url"], "goggalaxy://openGameView/1207658924")
        primary = play.install_state(self.entries(ids=[("egs", "Fortnite")]))
        self.assertEqual(primary["url"], "com.epicgames.launcher://apps/Fortnite?action=install&silent=false")

    def test_without_a_store_it_stays_disabled_with_why(self):
        primary = play.install_state(self.entries(ids=[("f95", "1")]))
        self.assertEqual((primary["enabled"], primary["reason"]), (False, play.NO_INSTALL))

    def test_install_answers_the_stores_link(self):
        entries = self.entries(ids=[("steam", "440")])
        with (
            mock.patch.object(play.datadir, "list_all_entries", return_value=entries),
            mock.patch.object(play, "get_system_uuid", return_value="me"),
        ):
            self.assertEqual(
                play.play("g1"), {"status": "ok", "via": "steam", "url": "steam://install/440", "install": True}
            )

    def test_on_linux_gog_and_epic_say_what_works_instead(self):
        with mock.patch("sys.platform", "linux"):
            primary = play.install_state(self.entries(ids=[("gog", "1"), ("egs", "Fortnite")], owned=["gog"]))
            self.assertEqual((primary["enabled"], primary["reason"]), (False, play.NO_INSTALL_LINUX))
            primary = play.install_state(self.entries(ids=[("gog", "1"), ("steam", "440")], owned=["gog"]))
            self.assertEqual((primary["enabled"], primary["via"]), (True, "steam"))
