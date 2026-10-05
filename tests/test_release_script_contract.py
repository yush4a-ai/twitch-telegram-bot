"""Контракт release-скрипта: удалённые проверки запускаются в контейнере."""

import unittest
from pathlib import Path

from scripts.production_release import HEALTH_SCRIPT

RELEASE_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "production_release.py"


class ReleaseRemoteCommandTests(unittest.TestCase):
    def test_bot_identity_runs_a_single_line_script_in_the_container(self):
        source = RELEASE_SCRIPT.read_text(encoding="utf-8")
        block = source[source.index("def bot_identity"):source.index("def post_checks")]

        self.assertIn('"python", "-c", HEALTH_SCRIPT', block)
        self.assertNotIn("sys.executable", block)
        # railway CLI не переносит переводы строк в удалённую команду.
        self.assertNotIn("\n", HEALTH_SCRIPT)
        self.assertIn("GETME_USERNAME=", HEALTH_SCRIPT)


if __name__ == "__main__":
    unittest.main()
