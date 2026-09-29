import asyncio
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from bilim_neo.automation import due_bells, first_bell, quiet_now
from bilim_neo.attachments import AttachmentError, MAX_FILE_BYTES, StreamedAttachment, file_name, file_size, file_url
from bilim_neo.bot_store import BotStore
from bilim_neo.client import BilimClassClient
from bilim_neo.bot_views import schedule_view, marks_view, advice_view, dashboard_view, day, parse_day, week_view
from bilim_neo.bot_views import checklist_view, subject_marks_view
from bilim_neo.planner import tasks_for_day, task_progress
from bilim_neo import bot


SCHEDULE = {"days": [{"date": "28.09.2026", "day": "Понедельник", "subjects": [
    {"label": "Математика <сложно>", "timeslot": "08:00–08:45", "cabinet": "12", "homeworkBody": "№ 10 & 11"}
]}]}
LOCALIZED_SCHEDULE = {"date": "28.09.2026", "days": [
    {"date": "28 сентября", "day": "понедельник", "subjects": [
        {"label": "Математика", "timeslot": "08:30 - 09:15", "homeworkBody": "№ 10"},
        {"label": "История", "timeslot": "09:25 - 10:10", "homeworkBody": "", "hasFiles": True},
    ]},
    {"date": "29 сентября", "day": "вторник", "subjects": [
        {"label": "Литература", "timeslot": "08:30 - 09:15", "homeworkBody": "Прочитать главу"},
    ]},
]}


class ViewsTest(unittest.TestCase):
    def test_schedule_escapes_diary_data(self):
        view = schedule_view(SCHEDULE, date(2026, 9, 28))
        self.assertIn("&lt;сложно&gt;", view)
        self.assertNotIn("<сложно>", view)

    def test_homework_and_empty_day(self):
        self.assertIn("№ 10 &amp; 11", schedule_view(SCHEDULE, date(2026, 9, 28), "homework"))
        self.assertIn("не вернул расписание", schedule_view(SCHEDULE, date(2026, 9, 29)))
        long = {"days": [{"date": "28.09.2026", "subjects": [{"label": "Труд", "homeworkBody": "<&>" * 100}]}]}
        self.assertIn("<blockquote expandable>", schedule_view(long, date(2026, 9, 28), "homework"))

    def test_live_api_date_shape_powers_each_distinct_view(self):
        target = date(2026, 9, 28)
        self.assertIsNotNone(day(LOCALIZED_SCHEDULE, target))
        lessons = schedule_view(LOCALIZED_SCHEDULE, target)
        bells = schedule_view(LOCALIZED_SCHEDULE, target, "bells")
        homework = schedule_view(LOCALIZED_SCHEDULE, target, "homework")
        self.assertIn("Математика", lessons)
        self.assertIn("08:30–09:15", bells)
        self.assertIn("перемена 10 мин", bells)
        self.assertIn("№ 10", homework)
        self.assertIn("прикреплён файл", homework)
        self.assertNotEqual(lessons, bells)
        self.assertNotEqual(lessons, homework)
        self.assertIn("ДЗ: 2", week_view(LOCALIZED_SCHEDULE, target))
        self.assertIn("2 урока", dashboard_view(LOCALIZED_SCHEDULE, target, datetime(2026, 9, 28, 8, 40)))

    def test_localized_date_chooses_nearest_year_at_new_year(self):
        self.assertEqual(parse_day("01 января", date(2025, 12, 29)), date(2026, 1, 1))
        self.assertEqual(parse_day("31 декабря", date(2026, 1, 1)), date(2025, 12, 31))

    def test_week_and_day_buttons_open_distinct_destinations(self):
        tabs = [button.callback_data for button in bot.date_keyboard("day", 0).inline_keyboard[0]]
        self.assertEqual(tabs, ["view:day:0", "view:bells:0", "view:homework:0"])
        days = [button.callback_data for row in bot.week_keyboard(0).inline_keyboard for button in row
                if button.callback_data.startswith("view:day:")]
        self.assertEqual(len(days), 7)
        self.assertEqual(len(set(days)), 7)
        distant_days = [int(button.callback_data.split(":")[-1])
                        for row in bot.week_keyboard(8).inline_keyboard for button in row
                        if button.callback_data.startswith("view:day:")]
        self.assertTrue(all(abs(offset) <= bot.MAX_DAY_OFFSET for offset in distant_days))
        attachment = [button.callback_data for row in bot.date_keyboard("homework", 0).inline_keyboard for button in row]
        self.assertIn("files:list:0", attachment)

    def test_optional_planner_and_colored_buttons(self):
        hidden = bot.home_keyboard({"planner": False})
        self.assertNotIn("task:open:0", [button.callback_data for row in hidden.inline_keyboard for button in row])
        visible = bot.home_keyboard({"planner": True})
        today = visible.inline_keyboard[0][0]
        self.assertEqual(today.style, "primary")
        self.assertEqual(next(b.style for row in visible.inline_keyboard for b in row if b.callback_data == "task:open:0"), "success")
        hidden_day = bot.date_keyboard("homework", 0, {"planner": False})
        self.assertNotIn("task:open:0", [button.callback_data for row in hidden_day.inline_keyboard for button in row])
        self.assertIn("files:list:0", [button.callback_data for row in hidden_day.inline_keyboard for button in row])

    def test_color_preference_applies_across_navigation(self):
        on = {"planner": True, "button_colors": True}
        off = {"planner": True, "button_colors": False}
        prefs = {"marks": True, "homework": False, "schedule": True, "attendance": False,
                 "morning": False, "bell_reminders": False, "weekly": False,
                 "planner": True, "button_colors": True}
        self.assertEqual(bot.home_keyboard(on).inline_keyboard[1][0].style, "primary")
        self.assertEqual(bot.week_keyboard(0, on).inline_keyboard[1][0].style, "primary")
        self.assertEqual(bot.date_keyboard("homework", 0, on).inline_keyboard[1][-1].style, "primary")
        self.assertEqual(bot.settings_keyboard(prefs).inline_keyboard[0][0].style, "success")
        self.assertIsNone(bot.settings_keyboard(prefs).inline_keyboard[1][0].style)
        for markup in (bot.home_keyboard(off), bot.week_keyboard(0, off),
                       bot.date_keyboard("homework", 0, off),
                       bot.settings_keyboard({**prefs, "button_colors": False}),
                       bot.quiet_keyboard(off)):
            self.assertTrue(all(button.style is None for row in markup.inline_keyboard for button in row))

    def test_next_lesson_uses_telegram_relative_time(self):
        now = datetime(2026, 9, 28, 8, 10, tzinfo=ZoneInfo("Asia/Almaty"))
        screen = dashboard_view(LOCALIZED_SCHEDULE, now.date(), now)
        self.assertIn("<tg-time unix=", screen)
        self.assertIn('format="r"', screen)

    def test_callbacks_route_to_the_selected_view(self):
        fake_store = SimpleNamespace(user=lambda chat_id: {"profile": {"currentEduYear": 2026}, "prefs": {"planner": True}}, record_activity=lambda *_: None)
        for callback, expected in (("view:day:0", "day"), ("view:bells:0", "bells"),
                                   ("view:homework:0", "homework"), ("view:week:0", "week")):
            with self.subTest(callback=callback), patch.object(bot, "store", fake_store, create=True), \
                 patch.object(bot.asyncio, "to_thread", new_callable=AsyncMock, return_value="<b>Ответ</b>") as thread, \
                 patch.object(bot, "present", new_callable=AsyncMock) as present:
                message = SimpleNamespace(chat=SimpleNamespace(id=7, type="private"), answer=AsyncMock())
                query = SimpleNamespace(data=callback, message=message, from_user=SimpleNamespace(id=7), answer=AsyncMock())
                asyncio.run(bot.view(query))
                self.assertEqual(thread.call_args.args[2], expected)
                self.assertEqual(present.call_args.args[1], "<b>Ответ</b>")

    def test_marks_and_advice(self):
        marks = [{"subject": "Алгебра", "date": "28.09.2026", "regular_mark": 4, "regular_max": 10}]
        self.assertIn("4/10", marks_view(marks, 1))
        self.assertIn("Алгебра", advice_view(SCHEDULE, marks, date(2026, 9, 28)))

    def test_checklist_and_subject_history(self):
        target = date(2026, 9, 28)
        tasks = tasks_for_day(LOCALIZED_SCHEDULE, target)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(task_progress(tasks, {tasks[0]["key"]}), (1, 2))
        self.assertIn("1 из 2 выполнено", checklist_view(tasks, {tasks[0]["key"]}, target))
        keyboard = bot.plan_keyboard(tasks, set(), 0, target)
        self.assertIn(tasks[0]["key"], keyboard.inline_keyboard[0][0].callback_data)
        self.assertIn("20260928", keyboard.inline_keyboard[0][0].callback_data)
        marks = [{"subject": "Алгебра", "date": "28.09.2026", "regular_mark": 8},
                 {"subject": "История", "date": "28.09.2026", "regular_mark": 9}]
        subject = subject_marks_view(marks, "Алгебра", 1)
        self.assertIn("Алгебра", subject)
        self.assertNotIn("История", subject)

    def test_stale_checklist_button_does_not_toggle_another_day(self):
        target = date(2026, 9, 28)
        tasks = tasks_for_day(LOCALIZED_SCHEDULE, target)
        fake_store = SimpleNamespace(user=lambda _: {"login": "student", "prefs": {"planner": True}}, toggle_homework_done=AsyncMock(), record_activity=lambda *_: None)
        message = SimpleNamespace(chat=SimpleNamespace(id=7, type="private"), answer=AsyncMock())
        query = SimpleNamespace(data=f"task:toggle:0:0:20260929:{tasks[0]['key']}", message=message, from_user=SimpleNamespace(id=7), answer=AsyncMock())
        with patch.object(bot, "store", fake_store, create=True), \
             patch.object(bot.asyncio, "to_thread", new_callable=AsyncMock, return_value=(target, tasks)):
            asyncio.run(bot.task(query))
        fake_store.toggle_homework_done.assert_not_awaited()
        self.assertIn("измени", message.answer.call_args.args[0])

    def test_disabled_planner_rejects_old_button(self):
        fake_store = SimpleNamespace(user=lambda _: {"prefs": {"planner": False}}, toggle_homework_done=AsyncMock(), record_activity=lambda *_: None)
        message = SimpleNamespace(chat=SimpleNamespace(id=7, type="private"), answer=AsyncMock())
        query = SimpleNamespace(data="task:open:0", message=message, from_user=SimpleNamespace(id=7), answer=AsyncMock())
        with patch.object(bot, "store", fake_store, create=True):
            asyncio.run(bot.task(query))
        fake_store.toggle_homework_done.assert_not_awaited()
        self.assertIn("выключен", message.answer.call_args.args[0])

    def test_disabled_planner_omits_dashboard_progress(self):
        fake_client = SimpleNamespace(get_schedule=lambda _: LOCALIZED_SCHEDULE,
                                      get_profile=lambda: {"fio": None, "group": None, "schoolName": None, "schoolAddress": None, "region": None, "currentEduYear": None, "availableEduYears": None},
                                      session=SimpleNamespace(close=lambda: None))
        fake_store = SimpleNamespace(user=lambda _: {"prefs": {"planner": False}, "profile": fake_client.get_profile()},
                                     homework_done=lambda *_: self.fail("disabled planner read checklist"))
        with patch.object(bot, "store", fake_store, create=True), \
             patch.object(bot, "new_client", return_value=fake_client):
            screen = bot.fetch(7, "dashboard")
        self.assertNotIn("▰", screen)


class AutomationTest(unittest.TestCase):
    def test_quiet_interval_crosses_midnight(self):
        prefs = {"quiet_from": 22, "quiet_to": 7}
        self.assertTrue(quiet_now(prefs, 23))
        self.assertTrue(quiet_now(prefs, 6))
        self.assertFalse(quiet_now(prefs, 8))

    def test_bell_reminder_at_exact_minute(self):
        now = datetime(2026, 9, 28, 7, 50, 30, tzinfo=ZoneInfo("Asia/Almaty"))
        self.assertEqual(first_bell("08:00–08:45"), (8, 0))
        self.assertEqual(len(due_bells(SCHEDULE["days"][0]["subjects"], now)), 1)
        self.assertEqual(due_bells(SCHEDULE["days"][0]["subjects"], now.replace(minute=51)), [])


class AttachmentTest(unittest.TestCase):
    def test_client_reads_homework_file_shape(self):
        client = BilimClassClient()
        client.school_id = 7
        client.current_edu_year = 2026
        fixture = {"data": {"files": [{"name": "lesson.pdf", "sizeInBytes": 1200,
                                          "link": "https://storage.yandexcloud.kz/file"}], "books": []}}
        response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: fixture)
        with patch.object(client.session, "get", return_value=response) as get:
            self.assertEqual(client.get_homework_files("homework-uuid"), fixture["data"]["files"])
            self.assertEqual(get.call_args.kwargs["params"], {"homeworkUuid": "homework-uuid", "schoolId": 7, "eduYear": 2026})
        client.session.close()

    def test_metadata_is_validated_before_upload(self):
        metadata = {"name": "../задание", "extension": "pdf", "sizeInBytes": 1200,
                    "link": "https://storage.yandexcloud.kz/bucket/file?X-Amz-Signature=private"}
        self.assertEqual(file_name(metadata), "_задание.pdf")
        self.assertEqual(file_size(metadata), 1200)
        self.assertIsInstance(StreamedAttachment(metadata), StreamedAttachment)
        with self.assertRaises(AttachmentError):
            file_size({**metadata, "sizeInBytes": MAX_FILE_BYTES + 1})
        with self.assertRaises(AttachmentError):
            file_url({**metadata, "link": "http://127.0.0.1/file"})
        with self.assertRaises(AttachmentError):
            file_url({**metadata, "link": "https://storage.yandexcloud.kz.evil.test/file"})

    def test_stream_stops_when_remote_size_exceeds_limit(self):
        metadata = {"name": "a.pdf", "sizeInBytes": 100,
                    "link": "https://storage.yandexcloud.kz/bucket/a.pdf"}

        class Response:
            status = 200
            headers = {"Content-Length": str(MAX_FILE_BYTES + 1)}
            def raise_for_status(self): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        class Session:
            def get(self, *args, **kwargs):
                self.kwargs = kwargs
                return Response()

        session = Session()
        fake_bot = SimpleNamespace(session=SimpleNamespace(create_session=AsyncMock(return_value=session)))

        async def read():
            return [chunk async for chunk in StreamedAttachment(metadata).read(fake_bot)]

        with self.assertRaises(AttachmentError):
            asyncio.run(read())
        self.assertFalse(session.kwargs["allow_redirects"])


class StoreTest(unittest.TestCase):
    def test_notification_defaults_migrate_existing_users_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "neo.sqlite3")
            key = Fernet.generate_key().decode()
            store = BotStore(path, key)
            store.save_user(7, "student", "password", {})
            store.set_pref(7, "marks", False)
            store.set_pref(7, "planner", False)
            for kind in ("homework", "schedule", "attendance", "morning", "bell_reminders", "weekly"):
                store.set_pref(7, kind, True)
            for event_key in ("homework:1", "schedule:1", "attendance:1", "morning:1", "bell:1", "weekly:1", "referral:1"):
                store.enqueue(7, event_key, event_key)
            with store._db() as db:
                db.execute("DELETE FROM app_settings WHERE key='notification_defaults_v2'")

            migrated = BotStore(path, key)
            prefs = migrated.user(7)["prefs"]
            self.assertTrue(prefs["marks"])
            self.assertFalse(prefs["planner"])
            self.assertTrue(all(not prefs[k] for k in ("homework", "schedule", "attendance", "morning", "bell_reminders", "weekly")))
            self.assertEqual([text for _, text in migrated.pending(7)], ["referral:1"])
            migrated.set_pref(7, "schedule", True)
            self.assertTrue(BotStore(path, key).user(7)["prefs"]["schedule"])

    def test_only_marks_are_enabled_for_new_users_and_notice_has_footer(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BotStore(str(Path(tmp) / "neo.sqlite3"), Fernet.generate_key().decode())
            store.save_user(8, "student", "password", {})
            prefs = store.user(8)["prefs"]
            self.assertTrue(prefs["marks"])
            self.assertTrue(all(not prefs[k] for k in ("homework", "schedule", "attendance", "morning", "bell_reminders", "weekly")))
        text = bot.notification_text("<b>Новая оценка</b>")
        self.assertIn("<i>⚙️ Уведомления можно изменить в настройках.</i>", text)
        self.assertEqual(bot.notification_text(text), text)

    def test_existing_user_gets_planner_preference_and_can_disable_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BotStore(str(Path(tmp) / "neo.sqlite3"), Fernet.generate_key().decode())
            store.save_user(7, "student", "password", {})
            self.assertTrue(store.user(7)["prefs"]["planner"])
            self.assertTrue(store.user(7)["prefs"]["button_colors"])
            store.set_pref(7, "planner", False)
            store.set_pref(7, "button_colors", False)
            self.assertFalse(store.user(7)["prefs"]["planner"])
            self.assertFalse(store.user(7)["prefs"]["button_colors"])

    def test_homework_checklist_is_encrypted_and_toggles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neo.sqlite3"
            store = BotStore(str(path), Fernet.generate_key().decode())
            store.save_user(7, "student", "password", {})
            self.assertEqual(store.toggle_homework_done(7, "2026-09-28", "private-task-key"), {"private-task-key"})
            self.assertEqual(store.homework_done(7, "2026-09-28"), {"private-task-key"})
            self.assertNotIn(b"private-task-key", path.read_bytes())
            self.assertEqual(store.toggle_homework_done(7, "2026-09-28", "private-task-key"), set())
            store.delete_user(7)
            self.assertEqual(store.homework_done(7, "2026-09-28"), set())

    def test_private_data_encrypted_and_outbox_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "neo.sqlite3"
            store = BotStore(str(path), Fernet.generate_key().decode())
            store.save_user(123, "demo-login", "demo-password", {"fio": "Demo Student"})
            store.set_snapshot(123, "marks", {"Math": "secret grade 5"})
            store.enqueue(123, "event1", "private notice")
            raw = path.read_bytes()
            for secret in (b"demo-password", b"Demo Student", b"secret grade 5", b"private notice"):
                self.assertNotIn(secret, raw)
            self.assertEqual(store.user(123)["login"], "demo-login")
            self.assertEqual(store.snapshot(123, "marks"), {"Math": "secret grade 5"})
            self.assertEqual(len(store.pending(123)), 1)
            store.mark_sent(store.pending(123)[0][0])
            self.assertEqual(store.pending(123), [])
            store.enqueue(123, "marks:second", "mark notice")
            store.set_pref(123, "marks", False)
            self.assertEqual(store.pending(123), [])
            store.delete_user(123)
            self.assertIsNone(store.user(123))
            self.assertIsNone(store.snapshot(123, "marks"))

    def test_first_sync_is_baseline_then_grade_change_is_queued_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = BotStore(str(Path(tmp) / "neo.sqlite3"), Fernet.generate_key().decode())
            store.save_user(7, "student", "password", {"fio": "Student"})
            tomorrow = datetime.now(ZoneInfo("Asia/Almaty")).date() + timedelta(days=1)
            store.set_snapshot(7, "homework", {"date": tomorrow.isoformat(), "data": {"legacy": "wrong empty snapshot"}})
            scores = []

            class FakeClient:
                session = type("Session", (), {"close": lambda self: None})()

                def get_current_marks(self, period):
                    return scores.copy()

                def get_schedule(self, monday):
                    return {"days": []}

            with patch.object(bot, "store", store, create=True), patch.object(bot, "new_client", return_value=FakeClient()), patch.object(bot, "current_period", return_value=1):
                bot.collect_updates(7)
                self.assertEqual(store.pending(7), [])
                scores.append({"scheduleUuid": "lesson-1", "subject": "Алгебра", "date": "28.09.2026", "regular_mark": 8, "regular_max": 10})
                bot.collect_updates(7)
                self.assertEqual(len(store.pending(7)), 1)
                self.assertIn("Алгебра", store.pending(7)[0][1])
                bot.collect_updates(7)
                self.assertEqual(len(store.pending(7)), 1)


if __name__ == "__main__":
    unittest.main()
