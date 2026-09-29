"""Small SQLite store for private bot chats. Credentials are encrypted at rest."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.fernet import Fernet


DEFAULT_PREFS = {
    "marks": True,
    "homework": True,
    "schedule": True,
    "attendance": True,
    "morning": False,
    "bell_reminders": False,
    "weekly": False,
    "planner": True,
    "button_colors": True,
    "admin_enabled": False,
    "lord_bonus": True,
    "quiet_from": 22,
    "quiet_to": 7,
}


class BotStore:
    def __init__(self, path: str, key: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(self.path.parent, 0o700)
        self.cipher = Fernet(key.encode())
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    chat_id INTEGER PRIMARY KEY, login BLOB NOT NULL, password BLOB NOT NULL,
                    prefs BLOB NOT NULL, profile BLOB NOT NULL
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    chat_id INTEGER NOT NULL, kind TEXT NOT NULL, content BLOB NOT NULL,
                    PRIMARY KEY(chat_id, kind),
                    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL,
                    event_key TEXT NOT NULL, content BLOB NOT NULL,
                    UNIQUE(chat_id, event_key),
                    FOREIGN KEY(chat_id) REFERENCES users(chat_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS pending_referrals (
                    invited_chat_id INTEGER PRIMARY KEY,
                    inviter_chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS referrals (
                    invited_chat_id INTEGER PRIMARY KEY REFERENCES users(chat_id) ON DELETE CASCADE,
                    inviter_chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    joined_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS activity_created_at_idx ON activity(created_at);
                CREATE INDEX IF NOT EXISTS activity_chat_id_idx ON activity(chat_id);
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    admin_chat_id INTEGER NOT NULL REFERENCES users(chat_id) ON DELETE CASCADE,
                    scope TEXT NOT NULL,
                    recipients INTEGER NOT NULL,
                    sent INTEGER NOT NULL,
                    failed INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
            """)
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _seal(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False, sort_keys=True).encode())

    def _open(self, value):
        return json.loads(self.cipher.decrypt(value))

    def save_user(self, chat_id: int, login: str, password: str, profile: dict):
        with self._db() as db:
            old = db.execute("SELECT login FROM users WHERE chat_id=?", (chat_id,)).fetchone()
            previous_login = self.cipher.decrypt(old[0]).decode() if old else None
            db.execute("""INSERT INTO users VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET login=excluded.login,
                password=excluded.password, profile=excluded.profile""", (
                    chat_id, self.cipher.encrypt(login.encode()),
                    self.cipher.encrypt(password.encode()),
                    self._seal(DEFAULT_PREFS), self._seal(profile),
                ))
            if previous_login is not None and previous_login != login:
                db.execute("DELETE FROM snapshots WHERE chat_id=?", (chat_id,))
                db.execute("DELETE FROM outbox WHERE chat_id=?", (chat_id,))
        return old is None

    def user(self, chat_id: int):
        with self._db() as db:
            row = db.execute("SELECT login,password,prefs,profile FROM users WHERE chat_id=?", (chat_id,)).fetchone()
        if row is None:
            return None
        return {
            "login": self.cipher.decrypt(row[0]).decode(),
            "password": self.cipher.decrypt(row[1]).decode(),
            "prefs": {**DEFAULT_PREFS, **self._open(row[2])},
            "profile": self._open(row[3]),
        }

    def users(self):
        with self._db() as db:
            return [row[0] for row in db.execute("SELECT chat_id FROM users")]

    def update_profile(self, chat_id: int, profile: dict):
        with self._db() as db:
            db.execute("UPDATE users SET profile=? WHERE chat_id=?", (self._seal(profile), chat_id))

    def set_pending_referral(self, invited_chat_id: int, inviter_chat_id: int):
        if invited_chat_id == inviter_chat_id:
            return False
        with self._db() as db:
            inviter = db.execute("SELECT 1 FROM users WHERE chat_id=?", (inviter_chat_id,)).fetchone()
            invited = db.execute("SELECT 1 FROM users WHERE chat_id=?", (invited_chat_id,)).fetchone()
            if not inviter or invited:
                return False
            db.execute("""INSERT INTO pending_referrals VALUES (?,?,?)
                ON CONFLICT(invited_chat_id) DO UPDATE SET inviter_chat_id=excluded.inviter_chat_id,
                created_at=excluded.created_at""", (invited_chat_id, inviter_chat_id, utc_now()))
        return True

    def complete_referral(self, invited_chat_id: int, notice: str):
        with self._db() as db:
            row = db.execute("SELECT inviter_chat_id FROM pending_referrals WHERE invited_chat_id=?", (invited_chat_id,)).fetchone()
            db.execute("DELETE FROM pending_referrals WHERE invited_chat_id=?", (invited_chat_id,))
            if not row or row[0] == invited_chat_id:
                return None
            inviter_chat_id = row[0]
            existing = db.execute("SELECT 1 FROM referrals WHERE invited_chat_id=?", (invited_chat_id,)).fetchone()
            if existing or not db.execute("SELECT 1 FROM users WHERE chat_id=?", (inviter_chat_id,)).fetchone():
                return None
            db.execute("INSERT INTO referrals VALUES (?,?,?)", (invited_chat_id, inviter_chat_id, utc_now()))
            db.execute("INSERT OR IGNORE INTO outbox(chat_id,event_key,content) VALUES (?,?,?)",
                       (inviter_chat_id, f"referral:{invited_chat_id}", self._seal(notice)))
            return inviter_chat_id

    def referral_count(self, inviter_chat_id: int):
        with self._db() as db:
            return db.execute("SELECT count(*) FROM referrals WHERE inviter_chat_id=?", (inviter_chat_id,)).fetchone()[0]

    def record_activity(self, chat_id: int, kind: str):
        if kind not in {"login", "menu", "diary", "marks", "files", "planner", "settings", "bonus", "admin", "broadcast"}:
            raise ValueError("Unknown activity kind")
        with self._db() as db:
            db.execute("INSERT INTO activity(chat_id,kind,created_at) SELECT chat_id,?,? FROM users WHERE chat_id=?",
                       (kind, utc_now(), chat_id))
            db.execute("DELETE FROM activity WHERE created_at<?", ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))

    def get_setting(self, key: str, default=None):
        with self._db() as db:
            row = db.execute("SELECT value FROM app_settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value):
        if key not in {"lord_bonus_enabled"}:
            raise ValueError("Unknown app setting")
        with self._db() as db:
            db.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, json.dumps(value)))

    def activity_counts(self, days: int = 14):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        with self._db() as db:
            daily = db.execute("SELECT substr(created_at,1,10),count(*) FROM activity WHERE created_at>=? GROUP BY 1 ORDER BY 1", (cutoff,)).fetchall()
            kinds = db.execute("SELECT kind,count(*) FROM activity WHERE created_at>=? GROUP BY kind ORDER BY 2 DESC", (cutoff,)).fetchall()
            users = db.execute("SELECT chat_id,count(*),max(created_at) FROM activity WHERE created_at>=? GROUP BY chat_id ORDER BY 2 DESC", (cutoff,)).fetchall()
        return {"daily": daily, "kinds": kinds, "users": users}

    def record_broadcast(self, admin_chat_id: int, scope: str, recipients: int, sent: int, failed: int):
        with self._db() as db:
            db.execute("INSERT INTO broadcasts(admin_chat_id,scope,recipients,sent,failed,created_at) VALUES (?,?,?,?,?,?)",
                       (admin_chat_id, scope, recipients, sent, failed, utc_now()))

    def broadcast_totals(self):
        with self._db() as db:
            return db.execute("SELECT count(*),coalesce(sum(recipients),0),coalesce(sum(sent),0),coalesce(sum(failed),0) FROM broadcasts").fetchone()

    def set_pref(self, chat_id: int, key: str, value):
        if key not in DEFAULT_PREFS:
            raise ValueError("Unknown preference")
        user = self.user(chat_id)
        user["prefs"][key] = value
        with self._db() as db:
            db.execute("UPDATE users SET prefs=? WHERE chat_id=?", (self._seal(user["prefs"]), chat_id))
            if value is False:
                prefix = "bell" if key == "bell_reminders" else key
                db.execute("DELETE FROM outbox WHERE chat_id=? AND event_key LIKE ?", (chat_id, prefix + ":%"))

    def snapshot(self, chat_id: int, kind: str):
        with self._db() as db:
            row = db.execute("SELECT content FROM snapshots WHERE chat_id=? AND kind=?", (chat_id, kind)).fetchone()
        return self._open(row[0]) if row else None

    def set_snapshot(self, chat_id: int, kind: str, content):
        with self._db() as db:
            db.execute("""INSERT INTO snapshots VALUES (?, ?, ?)
                ON CONFLICT(chat_id,kind) DO UPDATE SET content=excluded.content""",
                (chat_id, kind, self._seal(content)))

    def homework_done(self, chat_id: int, target: str):
        saved = self.snapshot(chat_id, "homework_done") or {}
        return set(saved.get(target, []))

    def toggle_homework_done(self, chat_id: int, target: str, task_key: str):
        """Toggle atomically; keep at most 30 dates of encrypted checklist state."""
        with self._db() as db:
            row = db.execute("SELECT content FROM snapshots WHERE chat_id=? AND kind='homework_done'", (chat_id,)).fetchone()
            saved = self._open(row[0]) if row else {}
            done = set(saved.get(target, []))
            if task_key in done:
                done.remove(task_key)
            else:
                done.add(task_key)
            if done:
                saved[target] = sorted(done)
            else:
                saved.pop(target, None)
            saved = {key: saved[key] for key in sorted(saved)[-30:]}
            db.execute("""INSERT INTO snapshots(chat_id,kind,content) VALUES (?, 'homework_done', ?)
                ON CONFLICT(chat_id,kind) DO UPDATE SET content=excluded.content""", (chat_id, self._seal(saved)))
        return done

    def enqueue(self, chat_id: int, event_key: str, content: str):
        with self._db() as db:
            db.execute("INSERT OR IGNORE INTO outbox(chat_id,event_key,content) VALUES (?,?,?)",
                       (chat_id, event_key, self._seal(content)))

    def pending(self, chat_id: int):
        with self._db() as db:
            rows = db.execute("SELECT id,content FROM outbox WHERE chat_id=? ORDER BY id LIMIT 10", (chat_id,)).fetchall()
        return [(row[0], self._open(row[1])) for row in rows]

    def mark_sent(self, item_id: int):
        with self._db() as db:
            db.execute("DELETE FROM outbox WHERE id=?", (item_id,))

    def delete_user(self, chat_id: int):
        with self._db() as db:
            db.execute("DELETE FROM users WHERE chat_id=?", (chat_id,))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
