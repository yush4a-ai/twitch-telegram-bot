"""Восстановление входа в Mini App: истёкшая подпись не должна превращать экран в тупик.

Проверки статические: браузерного прогона в этой среде нет (playwright не
установлен), поэтому тест фиксирует сам механизм — единый таймаут, обработку
отказа входа в одном месте, однократную перезагрузку и понятное сообщение.
"""
import unittest
from pathlib import Path

UI = Path(__file__).resolve().parents[1] / "bot" / "mini_app_ui"


class SessionRecoveryTests(unittest.TestCase):
    def test_api_has_one_timeout_and_reports_expired_session(self):
        source = (UI / "api.js").read_text(encoding="utf-8")
        self.assertIn("response.status === 401", source)
        self.assertIn("onAuthExpired", source)
        self.assertIn("timeoutMs=10000", source)
        self.assertIn("clearTimeout(timer)", source)

    def test_app_reloads_once_and_then_explains(self):
        source = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("onAuthExpired:handleAuthExpired", source)
        self.assertIn("AUTH_RELOAD_FLAG", source)
        self.assertIn("window.location.reload()", source)
        self.assertIn("Сессия истекла", source)
        # Флаг снимается после успешного входа, иначе следующая сессия не восстановится.
        self.assertIn("removeItem(AUTH_RELOAD_FLAG)", source)

    def test_app_waits_longer_and_reacts_to_returning_connection(self):
        source = (UI / "app.js").read_text(encoding="utf-8")
        self.assertIn("addEventListener('online'", source)
        self.assertIn("controller.abort(),15000", source)
        self.assertNotIn("controller.abort(),5000", source)

    def test_action_buttons_do_not_stick_forever(self):
        source = (UI / "components.js").read_text(encoding="utf-8")
        self.assertIn("Долго нет ответа", source)
        self.assertIn("15000", source)


if __name__ == "__main__":
    unittest.main()
