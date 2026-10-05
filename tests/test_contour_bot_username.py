import unittest

from bot.config import contour_bot_username_allowed


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


if __name__ == "__main__":
    unittest.main()
