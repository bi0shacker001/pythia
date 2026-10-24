"""Connecting GOG and Epic in the app (Droidtop/tracker#630): the store's own
sign-in page in the person's browser, then what it shows pasted back."""

from unittest import TestCase, mock

from pythia.plugins.library import egs, gog


class StoreConnectTest(TestCase):
    def test_both_say_what_to_paste_and_where_to_sign_in(self):
        for plugin, host in ((gog.GogPlugin(), "gog.com"), (egs.EgsPlugin(), "epicgames.com")):
            self.assertTrue(plugin.browser_connect["paste"])
            self.assertIn(host, plugin.browser_login_url())

    def test_gog_takes_the_code_from_the_pasted_address(self):
        token = {"refresh_token": "r", "access_token": "a"}
        with (
            mock.patch.object(gog, "_exchange", return_value=token) as exchange,
            mock.patch.object(gog.plugin, "set_credential"),
        ):
            self.assertTrue(
                gog.GogPlugin().connect_with("  https://embed.gog.com/on_login_success?origin=client&code=ABC  ")
            )
            self.assertFalse(gog.GogPlugin().connect_with("https://embed.gog.com/on_login_success?origin=client"))
        self.assertEqual(exchange.call_args.args[0]["code"], "ABC")

    def test_epic_takes_the_code_from_the_pasted_page(self):
        with mock.patch.object(egs, "_run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(egs.EgsPlugin().connect_with('{"redirectUrl": "x", "authorizationCode": "XYZ"}'))
            self.assertFalse(egs.EgsPlugin().connect_with("not json"))
        run.assert_called_once_with("auth", "--code", "XYZ")
