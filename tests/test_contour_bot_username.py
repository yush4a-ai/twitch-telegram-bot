import unittest

from aiohttp import web

from bot.config import contour_bot_username_allowed, public_site_bot_username_allowed
from bot.growth_site import install_growth_site


class ContourBotUsernameTests(unittest.TestCase):
    def test_all_contour_bots_are_accepted(self):
        for username in ("TwitchSignalBot", "TwitchSignalTestbot", "SignalStreamsBot"):
            with self.subTest(username=username):
                self.assertTrue(contour_bot_username_allowed(username))

    def test_username_comparison_ignores_case_and_spaces(self):
        self.assertTrue(contour_bot_username_allowed("  twitchsignaltestbot "))
        self.assertTrue(contour_bot_username_allowed("SIGNALSTREAMSBOT"))

    def test_foreign_bot_is_rejected(self):
        for value in ("", None, "OtherBot", "CigilBot", "TwitchSignal"):
            with self.subTest(value=value):
                self.assertFalse(contour_bot_username_allowed(value))

    def test_public_site_stays_on_the_test_contour(self):
        self.assertFalse(public_site_bot_username_allowed("TwitchSignalBot"))
        self.assertTrue(public_site_bot_username_allowed("TwitchSignalTestbot"))

    def test_growth_site_mounts_on_test_bots_only(self):
        for username in ("TwitchSignalTestbot", "SignalStreamsBot"):
            with self.subTest(username=username):
                install_growth_site(web.Application(), username, "https://example.test")
        for rejected in ("TwitchSignalBot", "OtherBot"):
            with self.subTest(rejected=rejected), self.assertRaises(ValueError):
                install_growth_site(web.Application(), rejected, "https://example.test")


if __name__ == "__main__":
    unittest.main()
