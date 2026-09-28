import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet

from bilim_neo.automation import due_bells, first_bell, quiet_now
from bilim_neo.bot_store import BotStore
from bilim_neo.bot_views import schedule_view, marks_view, advice_view
from bilim_neo import bot


SCHEDULE = {"days": [{"date": "28.09.2026", "day": "Понедельник", "subjects": [
    {"label": "Математика <сложно>", "timeslot": "08:00–08:45", "cabinet": "12", "homeworkBody": "№ 10 & 11"}
]}]}


class ViewsTest(unittest.TestCase):
    def test_schedule_escapes_diary_data(self):
        view = schedule_view(SCHEDULE, date(2026, 9, 28))
        self.assertIn("&lt;сложно&gt;", view)
        self.assertNotIn("<сложно>", view)

    def test_homework_and_empty_day(self):
        self.assertIn("№ 10 &amp; 11", schedule_view(SCHEDULE, date(2026, 9, 28), "homework"))
        self.assertIn("нет данных", schedule_view(SCHEDULE, date(2026, 9, 29)))

    def test_marks_and_advice(self):
        marks = [{"subject": "Алгебра", "date": "28.09.2026", "regular_mark": 4, "regular_max": 10}]
        self.assertIn("4/10", marks_view(marks, 1))
        self.assertIn("Алгебра", advice_view(SCHEDULE, marks, date(2026, 9, 28)))


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


class StoreTest(unittest.TestCase):
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
            scores = []

            class FakeClient:
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
