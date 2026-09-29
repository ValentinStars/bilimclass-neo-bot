import io
import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock
from types import SimpleNamespace

from cryptography.fernet import Fernet

from bilim_neo import admin_panel, admin_tools, bot, lord_bonus
from bilim_neo.bot_store import BotStore


class AdminFeaturesTest(unittest.TestCase):
    def test_referral_is_signed_and_paid_only_once_after_login(self):
        with patch.dict(os.environ, {"BOT_ENCRYPTION_KEY": Fernet.generate_key().decode()}):
            payload = bot.referral_payload(100)
            self.assertEqual(bot.referral_inviter(payload), 100)
            self.assertIsNone(bot.referral_inviter(payload[:-1] + ("0" if payload[-1] != "0" else "1")))
        with tempfile.TemporaryDirectory() as tmp:
            db = BotStore(str(Path(tmp) / "test.sqlite3"), Fernet.generate_key().decode())
            profile = {"fio": "А", "group": "8А", "schoolName": "ЛОРД"}
            db.save_user(100, "inviter", "secret", profile)
            self.assertTrue(db.set_pending_referral(200, 100))
            self.assertFalse(db.set_pending_referral(100, 100))
            self.assertEqual(db.referral_count(100), 0)
            self.assertTrue(db.save_user(200, "student", "pass", profile))
            self.assertEqual(db.complete_referral(200, "joined"), 100)
            self.assertIsNone(db.complete_referral(200, "again"))
            self.assertEqual(db.referral_count(100), 1)
            self.assertEqual(len(db.pending(100)), 1)
            db.set_pref(200, "planner", False)
            self.assertFalse(db.save_user(200, "student", "newpass", profile))
            self.assertFalse(db.user(200)["prefs"]["planner"])

    def test_admin_allowlist_and_audience_is_exact(self):
        with patch.dict(os.environ, {"ADMIN_IDS": "100, 300"}):
            self.assertTrue(admin_tools.allowed(100))
            self.assertFalse(admin_tools.allowed(200))
        users = [(1, {"profile": {"group": "8А", "schoolName": "ЛОРД", "region": "г. Алматы"}}),
                 (2, {"profile": {"group": "8Б", "schoolName": "ЛОРД", "region": "область"}}),
                 (3, {"profile": {"group": "8А", "schoolName": "Лицей", "schoolAddress": "город Кокшетау, улица 1"}})]
        self.assertEqual(admin_tools.recipients(users, "class", "8А"), [1, 3])
        self.assertEqual(admin_tools.recipients(users, "school", "ЛОРД"), [1, 2])
        self.assertEqual(admin_tools.recipients(users, "city", "Алматы"), [1])
        self.assertEqual(admin_tools.recipients(users, "student", "2"), [2])
        self.assertEqual(admin_tools.recipients(users, "city", "Северо-Казахстанская область"), [])

    def test_panel_requires_allowlist_and_enabled_preference(self):
        class FakeStore:
            def user(self, chat_id):
                return {"prefs": {"admin_enabled": chat_id == 100}}
        with patch.dict(os.environ, {"ADMIN_IDS": "100"}), patch.object(bot, "store", FakeStore(), create=True):
            self.assertTrue(admin_panel.admitted(100))
            self.assertFalse(admin_panel.admitted(200))
        with patch.dict(os.environ, {"ADMIN_IDS": "200"}), patch.object(bot, "store", FakeStore(), create=True):
            self.assertFalse(admin_panel.admitted(100))

    def test_bonus_scope_and_chart_are_real_png(self):
        self.assertTrue(lord_bonus.is_lord_school("Школа-лицей ЛОРД"))
        self.assertFalse(lord_bonus.is_lord_school("Лицей №2"))
        self.assertEqual(lord_bonus.class_key("8А класс"), "8А")
        self.assertEqual(lord_bonus.class_key("8A"), "8А")
        self.assertEqual(lord_bonus.class_key("3 класс"), "3")
        html = r'\"121367610\",[{\"1\":[[0,0,\"8А\"'
        self.assertEqual(lord_bonus.parse_sheets(html), {"8А": "121367610"})
        response = type("Response", (), {"content": b'"1,234"', "raise_for_status": lambda self: None})()
        with patch.object(lord_bonus, "sheets", return_value={"8А": "121367610"}), patch.object(lord_bonus.requests, "get", return_value=response) as get:
            self.assertEqual(lord_bonus.bonus_for_class("8А"), ("8А", 1.234))
            self.assertEqual(get.call_args.kwargs["params"]["range"], "A7")
            self.assertEqual(get.call_args.kwargs["params"]["gid"], "121367610")
        png = admin_tools.activity_chart({"daily": [("2026-09-29", 2)], "kinds": [], "users": []}, 12)
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertGreater(len(png), 10_000)
        classes = admin_tools.classes_chart([(1, {"profile": {"group": "8А"}})])
        self.assertTrue(classes.startswith(b"\x89PNG"))

    def test_photo_panel_navigation_sends_text_panel(self):
        message = SimpleNamespace(text=None, answer=AsyncMock(), edit_text=AsyncMock())
        asyncio.run(bot.present(SimpleNamespace(message=message), "Меню", None))
        message.answer.assert_awaited_once()
        message.edit_text.assert_not_awaited()

    def test_private_export_contains_exact_values(self):
        data = admin_tools.credentials_csv([(7, {"login": "student", "password": "abc123", "profile": {"group": "8А"}})])
        self.assertIn("login,password,class", data.decode("utf-8-sig"))
        self.assertIn("student,abc123,8А", data.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
