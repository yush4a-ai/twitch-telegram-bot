import unittest

from bot.viewer_filter import matches_viewer_filter, validate_viewer_filter


class ViewerFilterTests(unittest.TestCase):
    def test_game_title_and_exclusion_rules_are_deterministic(self):
        rule = validate_viewer_filter(["Minecraft"], ["speedrun", "record"], ["rerun"])
        self.assertTrue(matches_viewer_filter(rule, "minecraft", "New SpeedRun today"))
        self.assertTrue(matches_viewer_filter(rule, "MINECRAFT", "World record attempt"))
        self.assertFalse(matches_viewer_filter(rule, "Just Chatting", "Speedrun"))
        self.assertFalse(matches_viewer_filter(rule, "Minecraft", "Casual stream"))
        self.assertFalse(matches_viewer_filter(rule, "Minecraft", "Speedrun rerun"))
        self.assertTrue(matches_viewer_filter(validate_viewer_filter([], [], []), None, None))

    def test_lists_are_bounded_plain_text(self):
        invalid = (
            (["x"], [], []), (["x" * 41], [], []),
            (["ab"] * 6, [], []), (["ab", "AB"], [], []),
            ([], ["x\nwhy"], []), ([], [], ["x\x00y"]),
            ("game", [], []), ([], [7], []),
        )
        for games, keywords, exclude in invalid:
            with self.subTest(value=(games, keywords, exclude)), self.assertRaises(ValueError):
                validate_viewer_filter(games, keywords, exclude)
