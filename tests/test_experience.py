import asyncio
import io
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cryptography.fernet import Fernet
from PIL import Image
from aiogram import Bot, Dispatcher
from aiogram.client.session.base import BaseSession
from aiogram.types import Message, Update, User, Chat, CallbackQuery, PhotoSize, MessageId
from datetime import datetime, timezone

from bilim_neo import admin_panel, admin_tools, bot, experience, recovery, visuals
from bilim_neo.bot_store import BotStore


class ExperienceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = BotStore(str(Path(self.tmp.name) / "test.sqlite3"), Fernet.generate_key().decode())
        self.env = patch.dict(os.environ, {"ADMIN_IDS": "1", "RECOVERY_ADMIN_LOGIN": "reserve-test",
            "RECOVERY_ADMIN_HASH": recovery.password_hash("test-long-password"),
            "RECOVERY_ADMIN_PROFILE": '{"schoolName":"Example school","group":"10A","region":"г. Example"}'})
        self.env.start()
        self.app = patch.object(bot, "store", self.store, create=True)
        self.app.start()

    def tearDown(self):
        self.app.stop()
        self.env.stop()
        self.tmp.cleanup()

    def test_local_recovery_throttle_expiry_rotation_and_no_diary_access(self):
        for _ in range(5):
            self.assertFalse(recovery.login(self.store, 2, "reserve-test", "wrong"))
        self.assertFalse(recovery.login(self.store, 2, "reserve-test", "test-long-password"))
        self.assertIsNone(self.store.user(2))
        self.assertTrue(recovery.login(self.store, 3, "reserve-test", "test-long-password"))
        user = self.store.user(3)
        self.assertEqual(user["password"], "")
        self.assertEqual(user["profile"]["schoolName"], "Example school")
        self.assertTrue(user["prefs"]["local_admin"])
        self.assertTrue(admin_panel.admitted(3))
        with self.assertRaises(RuntimeError), patch.object(bot, "BilimClassClient") as client:
            bot.new_client(user)
        client.assert_not_called()
        with patch("time.time", return_value=time.time()+43201):
            self.assertFalse(admin_panel.admitted(3))
        with patch.dict(os.environ, {"RECOVERY_ADMIN_HASH": "rotated"}):
            self.assertFalse(admin_panel.admitted(3))
        self.store.revoke_admin_sessions()
        self.assertFalse(admin_panel.admitted(3))

    def test_backup_access_preserves_existing_student_and_theme_survives_login(self):
        self.store.update_visitor(4, theme="board", animations=False, telegram_username="student_name")
        self.store.save_user(4, "student", "diary-secret", {"group": "8А"})
        self.assertTrue(recovery.login(self.store, 4, "reserve-test", "test-long-password"))
        user = self.store.user(4)
        self.assertEqual(user["password"], "diary-secret")
        self.assertFalse(user["prefs"]["local_admin"])
        self.assertEqual(user["prefs"]["theme"], "board")
        self.assertFalse(user["prefs"]["animations"])
        self.assertEqual(admin_tools.identity_label(4, user["profile"]), "4 · @student_name")
        self.store.update_visitor(4, telegram_username=None)
        self.assertEqual(admin_tools.identity_label(4, self.store.user(4)["profile"]), "4")

    def test_feedback_is_encrypted_rate_limited_and_deleted_on_logout(self):
        item_id = self.store.add_feedback(5, {"text": "private-feedback", "message_id": 50})
        self.assertEqual(self.store.feedback_item(item_id)[1]["text"], "private-feedback")
        self.assertNotIn(b"private-feedback", self.store.path.read_bytes())
        for _ in range(5):
            self.assertTrue(self.store.rate_limit("feedback:5", 5, 3600))
        self.assertFalse(self.store.rate_limit("feedback:5", 5, 3600))
        self.store.delete_user(5)
        self.assertIsNone(self.store.feedback_item(item_id))

    def test_feedback_reply_uses_saved_recipient_and_checks_admin(self):
        self.store.save_user(1, "owner", "pass", {})
        self.store.set_pref(1, "admin_enabled", True)
        item_id = self.store.add_feedback(7, {"text": "idea"})
        state = SimpleNamespace(get_data=AsyncMock(return_value={"feedback_id": item_id}), clear=AsyncMock())
        api = SimpleNamespace(send_message=AsyncMock(), copy_message=AsyncMock())
        message = SimpleNamespace(chat=SimpleNamespace(id=1, type="private"), from_user=SimpleNamespace(id=1), message_id=99, bot=api, answer=AsyncMock())
        asyncio.run(experience.reply_feedback(message, state))
        api.copy_message.assert_awaited_once_with(7, 1, 99, protect_content=True)
        self.assertEqual(self.store.feedback_item(item_id)[2], "answered")
        api.copy_message.reset_mock()
        message.from_user.id = 99
        asyncio.run(experience.reply_feedback(message, state))
        api.copy_message.assert_not_awaited()

    def test_visual_panels_preserve_long_text_and_compact_has_no_image(self):
        message = SimpleNamespace(answer=AsyncMock(), answer_photo=AsyncMock(), answer_animation=AsyncMock())
        content = "<b>Homework</b>\n" + "Full content " * 120
        asyncio.run(bot.send_screen(message, content, None, {"theme": "board"}, "homework"))
        message.answer_photo.assert_not_awaited()
        self.assertEqual(message.answer.call_args.args[0], content)
        message.answer_photo.reset_mock()
        asyncio.run(bot.send_screen(message, "plain", None, {"theme": "compact"}))
        message.answer_photo.assert_not_awaited()
        png = visuals.card_bytes("day", "Example")
        self.assertEqual(Image.open(io.BytesIO(png)).size, (960, 160))
        gif = Image.open(io.BytesIO(visuals.welcome_animation()))
        self.assertGreater(gif.n_frames, 1)

    def test_compact_grade_table_uses_real_subject_scores_and_thresholds(self):
        source = [
            {"subject": "Каз. язык", "date": "29.09", "regular_mark": 3, "regular_max": 10},
            {"subject": "История", "date": "29.09", "sor_mark": 12, "sor_max": 15},
            {"subject": "Каз. язык", "date": "30.09", "regular_mark": 10, "regular_max": 10},
        ]
        rows = visuals.grade_rows(source)
        kaz = next(row for row in rows if row["subject"] == "Каз. язык")
        self.assertEqual([(mark["type"], mark["score"], mark["max"]) for mark in kaz["marks"]],
                         [("ФО", "10", 10), ("ФО", "3", 10)])
        self.assertEqual(visuals.grade_color(10, 10), visuals.GREEN)
        self.assertEqual(visuals.grade_color(3, 10), visuals.RED)
        self.assertEqual(visuals.grade_color(7, 10), visuals.YELLOW)
        self.assertEqual(visuals.grade_color(3, None), visuals.MUTED)
        image = visuals.grade_card(rows, 1)
        self.assertEqual(image.width, 960)
        self.assertLess(image.height, 400)
        self.assertTrue(visuals.card_bytes("marks", "ignored", rows, 1).startswith(b"\x89PNG"))

    def test_marks_visual_fetch_reuses_the_same_diary_request(self):
        self.store.save_user(11, "student", "password", {})
        marks = [{"subject": "Каз. язык", "date": "30.09", "regular_mark": 10, "regular_max": 10}]
        client = SimpleNamespace(session=SimpleNamespace(close=lambda: None), get_profile=lambda: {},
                                 get_current_marks=Mock(return_value=marks))
        with patch.object(bot, "new_client", return_value=client), patch.object(bot, "current_period", return_value=1):
            text, visual = bot.fetch(11, "marks", with_visual=True)
        self.assertIn("Каз. язык", text)
        self.assertEqual(visual["rows"][0]["subject"], "Каз. язык")
        self.assertEqual(client.get_current_marks.call_count, 1)

    def test_dispatcher_onboarding_recovery_and_feedback_roundtrip(self):
        class FakeTelegram(BaseSession):
            def __init__(self):
                super().__init__()
                self.calls = []
                self.latest = {}

            async def close(self):
                pass

            async def stream_content(self, *args, **kwargs):
                yield b""

            async def make_request(self, api_bot, method, timeout=None):
                self.calls.append(method)
                name = method.__api_method__
                if name in ("answerCallbackQuery", "deleteMessage"):
                    return True
                if name == "copyMessage":
                    return MessageId(message_id=len(self.calls)+100)
                chat_id = int(method.chat_id)
                photo = [PhotoSize(file_id="example", file_unique_id="example", width=1200, height=680)] if name in ("sendPhoto", "editMessageMedia") else None
                response = Message(message_id=len(self.calls)+100, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"), from_user=User(id=123456, is_bot=True, first_name="NEO"), text=getattr(method, "text", None), photo=photo)
                self.latest[chat_id] = response
                return response

        async def scenario():
            session = FakeTelegram()
            api = Bot("123456:" + "A"*35, session=session)
            dp = Dispatcher()
            dp.message.outer_middleware(experience.IdentityMiddleware())
            dp.callback_query.outer_middleware(experience.IdentityMiddleware())
            for route in (bot.router, admin_panel.router, experience.router):
                dp.include_router(route)
            counter = 0

            async def event(chat_id, text=None, callback=None):
                nonlocal counter
                counter += 1
                actor = User(id=chat_id, is_bot=False, first_name="Аня", username=f"student_{chat_id}")
                if callback:
                    msg = session.latest[chat_id]
                    update = Update(update_id=counter, callback_query=CallbackQuery(id=str(counter), from_user=actor, chat_instance="test", message=msg, data=callback))
                else:
                    msg = Message(message_id=counter, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type="private"), from_user=actor, text=text)
                    update = Update(update_id=counter, message=msg)
                await dp.feed_update(api, update)

            try:
                await event(2, "/start")
                await event(2, callback="look:set:board")
                await event(2, callback="auth:start")
                await event(2, "reserve-test")
                await event(2, "test-long-password")
                self.assertTrue(admin_panel.admitted(2))
                self.assertEqual(self.store.user(2)["prefs"]["theme"], "board")
                await event(2, callback="admin:home")
                await event(7, "/start")
                await event(7, callback="support:new")
                await event(7, "Добавьте напоминание")
                item_id = self.store.feedback_list()[0][0]
                await event(2, callback="support:inbox")
                await event(2, callback=f"support:reply:{item_id}")
                await event(2, "Спасибо, записали")
                copies = [method for method in session.calls if method.__api_method__ == "copyMessage"]
                self.assertEqual(copies[-1].chat_id, 7)
                self.assertEqual(self.store.feedback_item(item_id)[2], "answered")
                self.assertEqual(self.store.visitor(7)["telegram_username"], "student_7")
            finally:
                for route in tuple(dp.sub_routers):
                    route._parent_router = None
                dp.sub_routers.clear()
                await dp.storage.close()
                await api.session.close()

        with patch.object(bot, "BilimClassClient", side_effect=AssertionError("Local recovery must never call BilimClass")):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
