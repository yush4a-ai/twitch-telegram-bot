import unittest

from bot import deep_links


class GrowthLinksTests(unittest.TestCase):
    def test_source_and_referral_payload_are_bounded_and_literal(self):
        self.assertEqual(deep_links.parse_growth_start_payload("src_site"), ("site", "site"))
        self.assertEqual(
            deep_links.parse_growth_start_payload("ref_AbC012_-xyz9"),
            ("referral", "AbC012_-xyz9"),
        )
        for payload in (
            "src_unknown", "src_site_more", "ref_short", "ref_AbC012_-xyz!",
            "ref_AbC012_-xyz9?chat=1", "ref_" + "A" * 65, "track_alpha", "",
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(deep_links.parse_growth_start_payload(payload))

    def test_builder_uses_explicit_testbot_and_rejects_unsafe_inputs(self):
        self.assertEqual(
            deep_links.build_growth_deep_link("TwitchSignalTestbot", "src_site"),
            "https://t.me/TwitchSignalTestbot?start=src_site",
        )
        self.assertEqual(
            deep_links.build_track_deep_link("PaverPapa", bot_username="TwitchSignalTestbot"),
            "https://t.me/TwitchSignalTestbot?start=track_paverpapa",
        )
        for username, payload in (
            ("evil.example/path", "src_site"), ("Testbot", "src_other"),
            ("TwitchSignalTestbot", "ref_short"),
        ):
            with self.subTest(username=username, payload=payload):
                with self.assertRaises(ValueError):
                    deep_links.build_growth_deep_link(username, payload)


if __name__ == "__main__":
    unittest.main()
