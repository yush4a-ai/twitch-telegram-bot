"""Ссылка на сайт продукта: только из настроек и только когда адрес задан.

Адрес публичного сайта не должен быть зашит в код: это контакт, который владелец
меняет в окружении. Тест держит оба свойства: без настройки ссылки нет, с
настройкой она появляется и в тексте, и кнопкой.
"""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.config import ConfigError, load_config
from bot.handlers import telegram_help


class PublicSiteLinkTests(unittest.TestCase):
    def config(self, url=None):
        return SimpleNamespace(public_site_url=url, oauth_public_base_url="https://example.test")

    def buttons(self, keyboard):
        return {button.url for row in keyboard.inline_keyboard for button in row if button.url}

    def test_no_link_without_the_setting(self):
        text, keyboard = telegram_help.help_screen(self.config(None))
        self.assertNotIn("Подробные инструкции", text)
        self.assertNotIn("https://botsignal.stream", self.buttons(keyboard))

    def test_link_appears_when_the_setting_is_present(self):
        text, keyboard = telegram_help.help_screen(self.config("https://botsignal.stream"))
        self.assertIn("Подробные инструкции", text)
        self.assertIn("https://botsignal.stream", text)
        self.assertIn("https://botsignal.stream", self.buttons(keyboard))

    def test_setting_requires_https(self):
        with patch.dict(os.environ, {"PUBLIC_SITE_URL": "http://botsignal.stream"}, clear=False):
            with self.assertRaises(ConfigError):
                load_config()

    def test_setting_trailing_slash_is_removed(self):
        with patch.dict(os.environ, {"PUBLIC_SITE_URL": "https://botsignal.stream/"}, clear=False):
            config = load_config()
        self.assertEqual(config.public_site_url, "https://botsignal.stream")


if __name__ == "__main__":
    unittest.main()
