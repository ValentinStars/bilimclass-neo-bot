import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import configure


class ConfigureTest(unittest.TestCase):
    def test_interactive_wizard_keeps_existing_secret_and_private_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / ".env"
            token = "123456789:" + "A" * 35
            with patch.object(configure, "CONFIG", config), \
                 patch.object(configure.sys, "stdin", SimpleNamespace(isatty=lambda: True)), \
                 patch.object(configure.getpass, "getpass", side_effect=[token, ""]), \
                 patch("builtins.input", side_effect=[""] * 8), \
                 contextlib.redirect_stdout(io.StringIO()):
                configure.main()
                first = configure.read_config(config)
                first.update(RECOVERY_ADMIN_LOGIN="reserve-test", RECOVERY_ADMIN_HASH="pbkdf2:test-hash", RECOVERY_ADMIN_PROFILE="'{}'")
                configure.save_config(config, first)
                configure.main()
            second = configure.read_config(config)
            self.assertEqual(first["TELEGRAM_BOT_TOKEN"], token)
            self.assertEqual(second["TELEGRAM_BOT_TOKEN"], token)
            self.assertEqual(second["BOT_ENCRYPTION_KEY"], first["BOT_ENCRYPTION_KEY"])
            self.assertTrue(configure.valid_key(second["BOT_ENCRYPTION_KEY"]))
            self.assertEqual(second["POLL_INTERVAL_SECONDS"], "1800")
            self.assertEqual(second["RECOVERY_ADMIN_HASH"], first["RECOVERY_ADMIN_HASH"])
            self.assertEqual(second["RECOVERY_ADMIN_PROFILE"], first["RECOVERY_ADMIN_PROFILE"])
            self.assertEqual(os.stat(config).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
