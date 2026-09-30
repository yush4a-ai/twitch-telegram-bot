import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from scripts.staging_go_live_e2e import run_smoke, validate_runtime


class StagingGoLiveE2ETests(unittest.IsolatedAsyncioTestCase):
    def test_runtime_guard_requires_exact_staging_and_confirmed_owner(self):
        staging = {
            "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
            "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
            "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "NOTIFICATION_QUEUE_ENABLED": "1",
            "OWNER_CHAT_ID": "425785231",
        }
        self.assertEqual(validate_runtime(staging), 425785231)
        for key, bad in (
            ("RAILWAY_ENVIRONMENT_NAME", "production"),
            ("RAILWAY_ENVIRONMENT_ID", "af6d873b-a2cf-45aa-be42-cd9efbd102a7"),
            ("OWNER_CHAT_ID", "12345"),
            ("NOTIFICATION_QUEUE_ENABLED", "0"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(ValueError):
                    validate_runtime({**staging, key: bad})

    async def test_fake_bot_receives_one_go_live_post_and_cleanup(self):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="TwitchSignalTestbot")),
            send_message=AsyncMock(return_value=SimpleNamespace(message_id=321)),
            edit_message_text=AsyncMock(return_value=True),
            delete_message=AsyncMock(return_value=True),
        )
        result = await run_smoke(bot, 425785231)
        self.assertEqual(result["job_status"], "done")
        self.assertEqual(result["attempt_count"], 1)
        self.assertEqual(result["offline_job_status"], "done")
        self.assertTrue(result["post_ended"])
        self.assertTrue(result["message_deleted"])
        bot.send_message.assert_awaited_once()
        bot.edit_message_text.assert_awaited_once()
        bot.delete_message.assert_awaited_once_with(425785231, 321)

    async def test_non_testbot_identity_never_sends(self):
        bot = SimpleNamespace(
            get_me=AsyncMock(return_value=SimpleNamespace(username="TwitchSignalBot")),
            send_message=AsyncMock(),
            delete_message=AsyncMock(),
        )
        with self.assertRaises(ValueError):
            await run_smoke(bot, 425785231)
        bot.send_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
