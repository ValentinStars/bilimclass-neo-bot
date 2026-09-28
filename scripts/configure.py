"""Interactive, dependency-free wizard for deployment secrets and polling settings."""

import base64
import binascii
import getpass
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / ".env"


def read_config(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip()
    return values


def new_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def valid_key(value):
    try:
        return len(base64.urlsafe_b64decode(value)) == 32
    except (ValueError, binascii.Error):
        return False


def save_config(path, values):
    content = "".join(f"{key}={values[key]}\n" for key in (
        "TELEGRAM_BOT_TOKEN", "BOT_ENCRYPTION_KEY", "BOT_DB_PATH", "POLL_INTERVAL_SECONDS"
    ))
    with tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".env.", delete=False) as handle:
        temporary = Path(handle.name)
        os.chmod(temporary, 0o600)
        handle.write(content)
    os.replace(temporary, path)
    os.chmod(path, 0o600)


def main():
    if not sys.stdin.isatty():
        raise SystemExit("Настройка требует терминал. Откройте ./manage.sh или создайте .env по образцу.")
    existing = read_config(CONFIG)
    print("\nНЭО · интерактивная настройка")
    print("Ввод токена скрыт. Пустой ввод сохраняет уже заданное значение.\n")

    while True:
        token = getpass.getpass("Токен от @BotFather" + (" [уже задан]" if existing.get("TELEGRAM_BOT_TOKEN") else "") + ": ").strip()
        token = token or existing.get("TELEGRAM_BOT_TOKEN", "")
        if re.fullmatch(r"\d{6,12}:[A-Za-z0-9_-]{30,}", token):
            break
        print("Похоже, это не токен бота. Проверьте его у @BotFather.")

    old_key = existing.get("BOT_ENCRYPTION_KEY", "")
    if old_key and not valid_key(old_key):
        raise SystemExit("Сохранённый ключ повреждён. Исправьте .env вручную, чтобы не потерять доступ к базе.")
    key = old_key or new_key()
    print("Ключ шифрования сохранён." if old_key else "Создан новый ключ шифрования.")

    while True:
        default_path = existing.get("BOT_DB_PATH", "data/neo.sqlite3")
        raw_path = input(f"Путь к базе [{default_path}]: ").strip() or default_path
        path = Path(raw_path)
        if not path.is_absolute() and ".." not in path.parts and raw_path and "\n" not in raw_path:
            break
        print("Укажите относительный путь внутри проекта, например data/neo.sqlite3.")

    while True:
        try:
            old_minutes = max(5, int(existing.get("POLL_INTERVAL_SECONDS", "1800")) // 60)
        except ValueError:
            old_minutes = 30
        raw_minutes = input(f"Проверять BilimClass каждые N минут [{old_minutes}]: ").strip()
        try:
            minutes = int(raw_minutes) if raw_minutes else old_minutes
            if 5 <= minutes <= 1440:
                break
        except ValueError:
            pass
        print("Введите число от 5 до 1440 минут.")

    save_config(CONFIG, {
        "TELEGRAM_BOT_TOKEN": token,
        "BOT_ENCRYPTION_KEY": key,
        "BOT_DB_PATH": raw_path,
        "POLL_INTERVAL_SECONDS": str(minutes * 60),
    })
    print("Настройки сохранены в .env (доступ только владельцу файла).")
    print("Если бот уже запущен, примените настройки командой ./restart.sh")


if __name__ == "__main__":
    main()
