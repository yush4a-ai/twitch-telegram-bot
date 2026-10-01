import unittest

from scripts.r9_scale_validation import validate_r9_runtime


class R9GuardTests(unittest.TestCase):
    def test_only_pinned_staging_is_allowed(self):
        staging = {
            "RAILWAY_PROJECT_ID": "14282646-e318-4b80-b35d-4369270de255",
            "RAILWAY_ENVIRONMENT_ID": "7a873177-8ada-4b78-8732-a0bfdc1d519b",
            "RAILWAY_SERVICE_ID": "45e46f2a-dba3-4b18-bc5f-b6fafa260055",
            "RAILWAY_ENVIRONMENT_NAME": "staging",
            "NOTIFICATION_QUEUE_ENABLED": "1",
            "ADMIN_TELEGRAM_BOT_USERNAME": "TwitchSignalTestbot",
        }
        validate_r9_runtime(staging)
        for key, value in (
            ("RAILWAY_ENVIRONMENT_ID", "af6d873b-a2cf-45aa-be42-cd9efbd102a7"),
            ("RAILWAY_ENVIRONMENT_NAME", "production"),
            ("RAILWAY_SERVICE_ID", "other"),
            ("NOTIFICATION_QUEUE_ENABLED", "0"),
            ("ADMIN_TELEGRAM_BOT_USERNAME", "TwitchSignalBot"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_r9_runtime({**staging, key: value})


if __name__ == "__main__":
    unittest.main()
