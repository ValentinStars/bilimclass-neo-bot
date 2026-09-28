"""Small SQLite store for private bot chats. Credentials are encrypted at rest."""

import json
import os
import sqlite3
from contextlib import contextmanager
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
            db.execute("""INSERT INTO users VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET login=excluded.login,
                password=excluded.password, profile=excluded.profile""", (
                    chat_id, self.cipher.encrypt(login.encode()),
                    self.cipher.encrypt(password.encode()),
                    self._seal(DEFAULT_PREFS), self._seal(profile),
                ))
            db.execute("DELETE FROM snapshots WHERE chat_id=?", (chat_id,))
            db.execute("DELETE FROM outbox WHERE chat_id=?", (chat_id,))

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
