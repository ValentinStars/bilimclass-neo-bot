"""Upload the hand-drawn JPG as this bot's Telegram profile photo."""

import asyncio
import os
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InputProfilePhotoStatic
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]


async def main():
    load_dotenv(ROOT / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("Укажите TELEGRAM_BOT_TOKEN в .env")
    bot = Bot(token)
    try:
        await bot.set_my_profile_photo(
            photo=InputProfilePhotoStatic(photo=FSInputFile(ROOT / "assets/neo-avatar.jpg"))
        )
        me = await bot.get_me()
        print(f"Аватарка установлена для @{me.username}")
    except Exception as exc:
        raise SystemExit(f"Не удалось установить аватарку: {type(exc).__name__}") from None
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
