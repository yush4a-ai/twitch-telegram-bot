import unittest

from bot.config import environment_label


class EnvironmentLabelTests(unittest.TestCase):
    def test_railway_name_wins(self):
        self.assertEqual(environment_label("https://production.example", "production"), "production")
        self.assertEqual(environment_label("https://staging.example", "staging"), "staging")
        self.assertEqual(environment_label(None, "  production  "), "production")

    def test_public_address_is_only_a_fallback(self):
        self.assertEqual(environment_label("https://example.test", ""), "staging")
        self.assertEqual(environment_label("http://127.0.0.1:8080", ""), "local")
        self.assertEqual(environment_label(None, None), "local")


if __name__ == "__main__":
    unittest.main()
