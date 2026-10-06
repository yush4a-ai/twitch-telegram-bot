"""Контракт сети и ошибок мини-аппа: таксономия, восстановление оплаты, клавиатура.

Тест исполняет ``bot/mini_app_ui/failures.js`` в Node (чистый модуль без DOM) и
дополнительно читает JS-файлы оболочки: проверяются тексты и решения о повторе,
хранение ключа запроса, восстановление операции, visualViewport и снятие
слушателей. Браузер для этого не нужен и живые платежи не затрагиваются.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "bot" / "mini_app_ui"

EXPECTED_KINDS = {
    "timeout", "network", "rate_limited", "access_denied", "auth_expired",
    "not_found", "conflict", "invalid_request", "unavailable", "payment_pending",
    "server_error", "unknown",
}

PROBE = """
const m = await import(process.argv[1]);
const fail = (cause, options) => m.describeFailure(cause, options);
process.stdout.write(JSON.stringify({
  kinds: m.FAILURE_KINDS,
  status_text: m.PAYMENT_STATUS_TEXT,
  availability_in_progress: m.availabilityText('payment_in_progress', ''),
  availability_active: m.availabilityText('already_active', ''),
  prepare_timeout: fail({status: 0, code: 'timeout'}, {context: 'prepare'}),
  order_timeout: fail({status: 0, code: 'timeout'}, {context: 'order'}),
  network: fail({status: 0, code: 'network'}, {context: 'prepare'}),
  access_denied: fail({status: 403, code: 'order_denied'}, {context: 'order'}),
  auth_expired: fail({status: 403, code: 'unauthorized'}, {context: 'action'}),
  rate_limited: fail({status: 429, code: 'too_many'}, {context: 'prepare'}),
  conflict: fail({status: 409, code: 'order_closed'}, {context: 'prepare'}),
  invalid: fail({status: 400, code: 'bad_request'}, {context: 'prepare'}),
  server_error: fail({status: 500, code: 'boom'}, {context: 'prepare'}),
  permanent: fail({status: 503, code: 'unavailable',
                   data: {state: 'unavailable', payment_request_created: false,
                          reason_code: 'payment_in_progress', message: 'ignored'}},
                  {context: 'prepare'}),
  permanent_active: fail({status: 503, code: 'unavailable',
                          data: {state: 'unavailable', payment_request_created: false,
                                 reason_code: 'already_active', message: 'ignored'}},
                         {context: 'prepare'}),
  payment_pending: fail({status: 503, code: 'unavailable',
                         data: {state: 'creation_unknown', order_id: 'a'.repeat(32)}},
                        {context: 'prepare'}),
}));
"""


def probe_taxonomy():
    node = shutil.which("node")
    if node is None:
        raise unittest.SkipTest("Node is required for the Mini App copy taxonomy probe")
    result = subprocess.run(
        [node, "--input-type=module", "-e", PROBE, (UI / "failures.js").as_uri()],
        text=True, encoding="utf-8", capture_output=True, timeout=30, check=True,
    )
    return json.loads(result.stdout)


class FailureTaxonomyBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.probe = probe_taxonomy()

    def test_taxonomy_covers_every_failure_class(self):
        self.assertEqual(set(self.probe["kinds"]), EXPECTED_KINDS)

    def test_prepare_timeout_is_not_a_connection_error(self):
        timeout = self.probe["prepare_timeout"]
        self.assertEqual(timeout["kind"], "timeout")
        self.assertEqual(timeout["message"], self.probe["status_text"])
        self.assertEqual(timeout["message"], "Проверяем статус платежа…")
        self.assertTrue(timeout["pending"], "после таймаута состояние нужно перепроверить")
        self.assertFalse(timeout["retry"], "повтор идёт через восстановление, а не кнопкой")
        self.assertNotIn("Нет связи", timeout["message"])

    def test_every_reason_has_its_own_text_and_retry_decision(self):
        rows = {
            name: self.probe[name] for name in
            ("network", "access_denied", "auth_expired", "rate_limited", "conflict",
             "invalid", "server_error", "permanent", "payment_pending", "order_timeout")
        }
        messages = [row["message"] for row in rows.values()]
        self.assertEqual(len(messages), len(set(messages)), "тексты причин должны различаться")
        for name, row in rows.items():
            with self.subTest(failure=name):
                self.assertIn(row["kind"], EXPECTED_KINDS)
                self.assertTrue(row["message"].strip())
                self.assertNotIn("Проверьте связь", row["message"])

    def test_temporary_failures_offer_a_retry_permanent_ones_do_not(self):
        for name in ("network", "server_error","order_timeout"):
            with self.subTest(failure=name):
                self.assertTrue(self.probe[name]["retry"])
        for name in ("access_denied", "auth_expired", "rate_limited", "conflict",
                     "invalid", "permanent"):
            with self.subTest(failure=name):
                self.assertFalse(self.probe[name]["retry"], "повторять нечего")

    def test_access_denied_is_not_reported_as_a_network_problem(self):
        denied = self.probe["access_denied"]
        self.assertEqual(denied["kind"], "access_denied")
        self.assertIn("доступ", denied["message"].lower())
        self.assertNotIn("связ", denied["message"].lower())
        self.assertIn("/paysupport", denied["message"])

    def test_server_created_nothing_allows_a_repeat_without_a_retry_button(self):
        permanent = self.probe["permanent"]
        self.assertEqual(permanent["kind"], "unavailable")
        self.assertFalse(permanent["retry"])
        self.assertFalse(permanent["pending"])
        self.assertEqual(permanent["message"], self.probe["availability_in_progress"])
        self.assertIn("неоплаченный счёт", permanent["message"])
        active = self.probe["permanent_active"]
        self.assertFalse(active["retry"])
        self.assertEqual(active["message"], self.probe["availability_active"])

    def test_unconfirmed_server_answer_asks_for_the_operation_state(self):
        pending = self.probe["payment_pending"]
        self.assertEqual(pending["kind"], "payment_pending")
        self.assertTrue(pending["pending"])
        self.assertEqual(pending["message"], self.probe["status_text"])
        self.assertNotIn("Нет связи", pending["message"])


class MiniAppPaymentRecoveryContractTests(unittest.TestCase):
    def source(self, name):
        return (UI / name).read_text(encoding="utf-8")

    def test_request_key_survives_attempts_in_session_storage_only(self):
        purchase = self.source("purchase.js")
        self.assertIn("sessionStorage", purchase)
        self.assertNotIn("localStorage", purchase, "ключ запроса не хранится в localStorage")
        self.assertIn("ts-app-purchase-operation", purchase)
        self.assertIn("request_key:current.requestKey", purchase)
        self.assertIn("current.requestKey=", purchase)
        self.assertEqual(purchase.count("crypto.randomUUID()"), 1,
                         "новый ключ создаётся только при первой попытке")

    def test_repeat_after_timeout_restores_the_same_order(self):
        purchase = self.source("purchase.js")
        self.assertIn("async function recover(", purchase)
        self.assertIn("PAYMENT_STATUS_TEXT", purchase)
        self.assertIn("link_opened", purchase, "повторное окно оплаты запрещено флагом")
        self.assertIn("rememberOrder", purchase)
        self.assertIn("describeFailure", purchase)

    def test_app_restores_the_last_operation_after_reload(self):
        app = self.source("app.js")
        self.assertIn("purchaseFeature.restoreOperation()", app)
        self.assertIn("pagehide", app)
        teardown = app.split("function teardown()", 1)[1].split("window.addEventListener('pagehide'", 1)[0]
        for listener in ("'online'", "'visibilitychange'", "'app-dialog-change'", "'click'"):
            with self.subTest(listener=listener):
                self.assertIn(f"removeEventListener({listener}", teardown,
                              "на pagehide снимаются все слушатели")

    def test_mobile_keyboard_is_measured_with_visual_viewport(self):
        telegram = self.source("telegram.js")
        self.assertIn("visualViewport", telegram)
        self.assertIn("--keyboard-inset", telegram)
        self.assertIn("--viewport-offset-top", telegram)
        self.assertNotIn("viewport-stable-height", telegram, "мёртвая переменная удалена")
        self.assertIn("removeEventListener", telegram.split("dispose()", 1)[1])
        css = self.source("app.css")
        self.assertNotIn("viewport-stable-height", css)
        self.assertIn("var(--keyboard-inset", css)
        for selector in (".tab-bar {", ".app-dialog {"):
            with self.subTest(selector=selector):
                rules = [part.split("}", 1)[0] for part in css.split(selector)[1:]]
                self.assertTrue(any("var(--keyboard-inset" in rule for rule in rules),
                                "клавиатура не перекрывает элемент")

    def test_operation_dates_use_the_bot_time_zone(self):
        subscription = self.source("subscription.js")
        self.assertIn("timeZone:'Europe/Moscow'", subscription)
        purchase = self.source("purchase.js")
        self.assertIn("dateText", purchase, "экран операции показывает даты через общий формат")

    def test_failure_module_is_served_to_the_app(self):
        web = (ROOT / "bot" / "mini_app_web.py").read_text(encoding="utf-8")
        self.assertIn('"failures.js": "application/javascript"', web)
        shell = (ROOT / "tests" / "test_mini_app_shell.py").read_text(encoding="utf-8")
        self.assertIn('"failures.js"', shell, "новый модуль попадает в проверку оболочки")


if __name__ == "__main__":
    unittest.main()
