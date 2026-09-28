"""Private-chat Telegram companion powered by BilimClassClient."""

import asyncio
import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from .bot_store import BotStore
from .automation import due_bells, quiet_now
from .bot_views import (advice_view, attendance_view, day, grades_view, h,
                        marks_view, schedule_view, week_view, parse_day)
from .client import BilimClassClient


TZ = ZoneInfo("Asia/Almaty")
router = Router()
store: BotStore
logger = logging.getLogger(__name__)


class Login(StatesGroup):
    username = State()
    password = State()


def buttons(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
        for row in rows
    ])


def home_keyboard():
    return buttons(
        [("📅 Сегодня", "view:day:0"), ("🌅 Завтра", "view:day:1")],
        [("🗓 Неделя", "view:week"), ("🔔 Звонки", "view:bells:0")],
        [("📝 Домашка", "view:homework:1"), ("📊 Оценки", "view:marks")],
        [("📈 Табель", "view:grades"), ("🏃 Посещаемость", "view:attendance")],
        [("💡 Советы", "view:advice"), ("⚙️ Настройки", "view:settings")],
    )


def back_keyboard():
    return buttons([("← Главное меню", "view:home")])


async def present(call: CallbackQuery, content: str, markup):
    """Reuse the current panel; keep delivered alerts as a readable history."""
    alert = (call.message.text or "").startswith(("📊 Новые", "🏃 Новые", "📝 Обновилась", "📅 Изменилось", "Доброе утро", "Скоро урок"))
    if alert:
        await call.message.answer(content, reply_markup=markup, protect_content=True)
        return
    try:
        await call.message.edit_text(content, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def date_keyboard(mode, offset):
    return buttons(
        [("← День", f"view:{mode}:{offset-1}"), ("День →", f"view:{mode}:{offset+1}")],
        [("🏠 Меню", "view:home"), ("🗓 Неделя", "view:week")],
    )


def settings_keyboard(prefs):
    names = [("marks", "Новые оценки"), ("homework", "Новая домашка"),
             ("schedule", "Изменения расписания"), ("attendance", "Пропуски"),
             ("morning", "Утренний план"), ("bell_reminders", "Перед уроком"),
             ("weekly", "План недели")]
    rows = [[(f"{'✅' if prefs[key] else '⬜'} {name}", f"toggle:{key}")] for key, name in names]
    rows += [[("🌙 Тихие часы", "view:quiet")], [("🔐 Профиль", "view:profile"), ("🏠 Меню", "view:home")]]
    return buttons(*rows)


def new_client(user):
    client = BilimClassClient(user["login"], user["password"])
    client.login()
    return client


def current_period(client):
    today = datetime.now(TZ).date()
    periods = client.get_periods()
    for period in periods:
        start, end = parse_day(period.get("periodStart")), parse_day(period.get("periodEnd"))
        if start and end and start <= today <= end:
            return int(period["period"])
    past = [p for p in periods if parse_day(p.get("periodStart")) and parse_day(p["periodStart"]) <= today]
    return int(past[-1]["period"]) if past else 1


def fetch(chat_id, kind, offset=0, year=None):
    user = store.user(chat_id)
    client = new_client(user)
    today = datetime.now(TZ).date()
    target = today + timedelta(days=offset)
    if kind in ("day", "bells", "homework", "week", "advice"):
        monday = target - timedelta(days=target.weekday())
        schedule = client.get_schedule(monday.strftime("%d.%m.%Y"))
        if kind == "week":
            return week_view(schedule)
        if kind == "advice":
            marks = client.get_current_marks(current_period(client))
            return advice_view(schedule, marks, today + timedelta(days=1))
        return schedule_view(schedule, target, {"day": "lessons"}.get(kind, kind))
    if kind == "marks":
        period = current_period(client)
        return marks_view(client.get_current_marks(period), period)
    if kind == "grades":
        y = year or client.current_edu_year
        return grades_view(client.get_year_grades(y), y)
    if kind == "attendance":
        return attendance_view(client.get_current_marks(current_period(client)), client.get_year_grades())
    raise ValueError("Unknown view")


def private(message):
    return message.chat.type == "private"


@router.message(CommandStart())
@router.message(Command("menu"))
async def start(message: Message, state: FSMContext):
    if not private(message):
        return
    await state.clear()
    user = store.user(message.chat.id)
    if user:
        await message.answer(f"<b>НЭО · твой школьный день</b>\n{h(user['profile'].get('group'))} · {h(user['profile'].get('schoolName'))}\n\nЧто посмотрим?", reply_markup=home_keyboard(), protect_content=True)
    else:
        await message.answer("<b>Привет, я НЭО.</b>\nСоберу расписание, задания и оценки в одном месте и вовремя напомню о важном.\n\nПодключи дневник в личном чате:", reply_markup=buttons([("🔐 Подключить дневник", "auth:start")]), protect_content=True)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Ввод отменён.", reply_markup=back_keyboard())


@router.message(Command("logout"))
async def logout(message: Message, state: FSMContext):
    if not private(message):
        return
    await state.clear()
    store.delete_user(message.chat.id)
    await message.answer("Дневник отключён. Сохранённые данные удалены.", reply_markup=buttons([("Подключить снова", "auth:start")]))


@router.callback_query(F.data == "auth:start")
async def auth_start(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if call.message.chat.type != "private":
        return
    await state.set_state(Login.username)
    await call.message.answer("Отправь <b>логин BilimClass</b>. Я удалю сообщение после получения. /cancel — отмена.", protect_content=True)


@router.message(Login.username)
async def auth_username(message: Message, state: FSMContext):
    if not private(message) or not message.text:
        return
    await state.update_data(username=message.text.strip())
    try:
        await message.delete()
    except Exception:
        pass
    await state.set_state(Login.password)
    await message.answer("Теперь отправь <b>пароль</b>. Он будет зашифрован на сервере.", protect_content=True)


@router.message(Login.password)
async def auth_password(message: Message, state: FSMContext):
    if not private(message) or not message.text:
        return
    password = message.text
    try:
        await message.delete()
    except Exception:
        pass
    data = await state.get_data()
    await state.clear()
    try:
        def verify():
            client = BilimClassClient(data["username"], password)
            client.login()
            return client.get_profile()
        profile = await asyncio.to_thread(verify)
    except Exception:
        await message.answer("Не получилось войти. Проверь логин и пароль и попробуй ещё раз через кнопку.", reply_markup=buttons([("Попробовать снова", "auth:start")]), protect_content=True)
        return
    profile = {key: profile.get(key) for key in ("fio", "group", "schoolName", "currentEduYear", "availableEduYears")}
    store.save_user(message.chat.id, data["username"], password, profile)
    await message.answer(f"Готово, <b>{h(profile.get('fio'))}</b>! Дневник подключён.\nУведомления можно настроить отдельно для каждого события.", reply_markup=home_keyboard(), protect_content=True)


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(call: CallbackQuery):
    user = store.user(call.message.chat.id)
    if not user:
        await call.answer("Сначала подключи дневник", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key not in ("marks", "homework", "schedule", "attendance", "morning", "bell_reminders", "weekly"):
        await call.answer()
        return
    store.set_pref(call.message.chat.id, key, not user["prefs"][key])
    await call.answer("Настройка сохранена")
    await call.message.edit_reply_markup(reply_markup=settings_keyboard(store.user(call.message.chat.id)["prefs"]))


@router.callback_query(F.data.startswith("quiet:"))
async def quiet(call: CallbackQuery):
    user = store.user(call.message.chat.id)
    if not user:
        await call.answer("Сначала подключи дневник", show_alert=True)
        return
    try:
        start, end = map(int, call.data.split(":")[1:])
        if not 0 <= start <= 23 or not 0 <= end <= 23:
            raise ValueError
    except ValueError:
        await call.answer()
        return
    store.set_pref(call.message.chat.id, "quiet_from", start)
    store.set_pref(call.message.chat.id, "quiet_to", end)
    await call.answer("Тихие часы сохранены")
    await call.message.edit_text(f"<b>Тихие часы</b> · {start:02d}:00–{end:02d}:00\nУведомления придут после окончания паузы.", reply_markup=quiet_keyboard())


def quiet_keyboard():
    return buttons([("22:00–07:00", "quiet:22:7"), ("21:00–08:00", "quiet:21:8")], [("00:00–00:00 · выкл.", "quiet:0:0")], [("← Настройки", "view:settings")])


@router.callback_query(F.data.startswith("view:"))
async def view(call: CallbackQuery):
    await call.answer()
    if call.message.chat.type != "private":
        return
    user = store.user(call.message.chat.id)
    if not user:
        await call.message.answer("Подключи дневник, чтобы открыть раздел.", reply_markup=buttons([("🔐 Подключить", "auth:start")]))
        return
    parts = call.data.split(":")
    kind = parts[1]
    if kind == "home":
        await present(call, "<b>НЭО · меню</b>\nЧто посмотрим?", home_keyboard())
        return
    if kind == "settings":
        p = user["prefs"]
        await present(call, f"<b>Уведомления</b>\nНажми на пункт, чтобы переключить.\n🌙 Тихие часы: {p['quiet_from']:02d}:00–{p['quiet_to']:02d}:00", settings_keyboard(p))
        return
    if kind == "quiet":
        await present(call, "<b>Тихие часы</b>\nВыбери удобный режим по времени Алматы.", quiet_keyboard())
        return
    if kind == "profile":
        p = user["profile"]
        await present(call, f"<b>Профиль</b>\n{h(p.get('fio'))}\n{h(p.get('group'))} · {h(p.get('schoolName'))}\nУчебный год: {h(p.get('currentEduYear'))}\n\nОтключить дневник и удалить данные: /logout", back_keyboard())
        return
    if kind not in ("day", "bells", "homework", "week", "advice", "marks", "grades", "attendance"):
        return
    try:
        offset = max(-14, min(14, int(parts[2]))) if len(parts) > 2 else 0
        year = int(parts[2]) if kind == "grades" and len(parts) > 2 else None
        if year is not None:
            current = int(user["profile"].get("currentEduYear") or datetime.now(TZ).year)
            available = user["profile"].get("availableEduYears") or list(range(current, current - 5, -1))
            if year not in available:
                await call.message.answer("Этот учебный год недоступен в профиле.", reply_markup=back_keyboard())
                return
        content = await asyncio.to_thread(fetch, call.message.chat.id, kind, offset, year)
    except Exception:
        logger.exception("BilimClass view failed for chat %s", call.message.chat.id)
        await call.message.answer("Дневник временно недоступен. Попробуй чуть позже. Если пароль изменился — /logout и подключи дневник снова.", reply_markup=back_keyboard())
        return
    if kind in ("day", "bells", "homework"):
        markup = date_keyboard(kind, offset)
    elif kind == "grades":
        current = int(user["profile"].get("currentEduYear") or datetime.now(TZ).year)
        years = user["profile"].get("availableEduYears") or list(range(current, current - 5, -1))
        rows = [[(f"{y}/{y+1}", f"view:grades:{y}") for y in years[i:i+2]] for i in range(0, len(years), 2)]
        markup = buttons(*rows, [("← Меню", "view:home")])
    else:
        markup = back_keyboard()
    await present(call, content, markup)


def collect_updates(chat_id):
    user = store.user(chat_id)
    client = new_client(user)
    period = current_period(client)
    marks = client.get_current_marks(period)
    now = datetime.now(TZ)
    today = now.date()
    tomorrow = today + timedelta(days=1)
    monday = tomorrow - timedelta(days=tomorrow.weekday())
    schedule = client.get_schedule(monday.strftime("%d.%m.%Y"))
    lessons = (day(schedule, tomorrow) or {}).get("subjects", [])
    payloads = {
        "marks": {f"{period}:{m.get('scheduleUuid')}:{key}": {"subject": m.get("subject"), "date": m.get("date"), "score": m.get(key), "max": m.get(key.replace("_mark", "_max"))} for m in marks for key in ("regular_mark", "sor_mark", "soch_mark", "po_mark") if m.get(key) is not None},
        "attendance": {f"{period}:{m.get('scheduleUuid')}": m.get("attendance") for m in marks if m.get("attendance") and m["attendance"] != "was_in_class"},
        "homework": {f"{tomorrow}:{i}": s.get("homeworkBody") for i, s in enumerate(lessons) if s.get("homeworkBody")},
        "schedule": {f"{tomorrow}:{i}": (s.get("label"), s.get("timeslot"), s.get("cabinet")) for i, s in enumerate(lessons)},
    }
    today_monday = today - timedelta(days=today.weekday())
    today_schedule = schedule if today_monday == monday else client.get_schedule(today_monday.strftime("%d.%m.%Y"))
    today_lessons = (day(today_schedule, today) or {}).get("subjects", [])
    store.set_snapshot(chat_id, "today_lessons", {"date": today.isoformat(), "lessons": today_lessons})
    for kind, current in payloads.items():
        saved = store.snapshot(chat_id, kind)
        old = saved.get("data") if kind in ("homework", "schedule") and saved and saved.get("date") == tomorrow.isoformat() else (None if kind in ("homework", "schedule") else saved)
        if old is not None and user["prefs"].get(kind):
            keys = set(current) | (set(old) if kind in ("homework", "schedule") else set())
            changed = sorted(key for key in keys if old.get(key) != current.get(key))
            if changed:
                title = {"marks": "📊 Новые оценки", "attendance": "🏃 Новые отметки посещаемости", "homework": "📝 Обновилась домашка на завтра", "schedule": "📅 Изменилось расписание на завтра"}[kind]
                if kind == "marks":
                    text = "\n".join(f"• {h(current[key]['subject'])} · {h(current[key]['date'])}: {h(key.split(':')[-1].replace('_mark', '').upper())} {h(current[key]['score'])}/{h(current[key]['max'])}" for key in changed[:8])
                    low = []
                    for key in changed:
                        try:
                            if float(current[key]["score"]) / float(current[key]["max"]) < .6:
                                low.append(current[key]["subject"])
                        except (TypeError, ValueError, ZeroDivisionError):
                            pass
                    if low:
                        text += "\n💡 Повтори темы по: " + h(", ".join(dict.fromkeys(low[:3])))
                else:
                    text = f"Изменений: {len(changed)}. Открой раздел и проверь детали."
                digest = hashlib.sha256(json.dumps({key: current.get(key) for key in changed}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:20]
                store.enqueue(chat_id, f"{kind}:{digest}", f"<b>{title}</b>\n{text}")
        store.set_snapshot(chat_id, kind, {"date": tomorrow.isoformat(), "data": current} if kind in ("homework", "schedule") else current)
    if user["prefs"]["morning"]:
        last = store.snapshot(chat_id, "morning")
        if now.hour >= 7 and last != today.isoformat():
            count = len(today_lessons)
            store.enqueue(chat_id, f"morning:{today}", f"<b>Доброе утро ☀️</b>\nСегодня {count} уроков. Удачного дня!")
            store.set_snapshot(chat_id, "morning", today.isoformat())
    if user["prefs"]["weekly"] and today.weekday() == 0 and now.hour >= 7:
        last = store.snapshot(chat_id, "weekly")
        if last != today.isoformat():
            store.enqueue(chat_id, f"weekly:{today}", week_view(today_schedule))
            store.set_snapshot(chat_id, "weekly", today.isoformat())


async def notifications(bot: Bot, interval: int):
    last_sync = None
    slots = asyncio.Semaphore(4)

    async def process(chat_id, now, sync_due):
        async with slots:
            try:
                user = store.user(chat_id)
                if quiet_now(user["prefs"], now.hour):
                    return
                if sync_due:
                    try:
                        await asyncio.to_thread(collect_updates, chat_id)
                    except Exception:
                        logger.exception("BilimClass sync failed for chat %s", chat_id)
                cached = store.snapshot(chat_id, "today_lessons")
                if user["prefs"]["bell_reminders"] and cached and cached.get("date") == now.date().isoformat():
                    for number, lesson, start in due_bells(cached["lessons"], now):
                        key = f"bell:{now.date()}:{number}"
                        store.enqueue(chat_id, key, f"<b>Скоро урок · {number:02d}</b>\n{h(lesson.get('label') or 'Урок')} в {start:%H:%M} · каб. {h(lesson.get('cabinet'))}")
                for item_id, notice in store.pending(chat_id):
                    await bot.send_message(chat_id, notice, protect_content=True, reply_markup=home_keyboard())
                    store.mark_sent(item_id)
            except Exception:
                logger.exception("Notification delivery failed for chat %s", chat_id)

    while True:
        now = datetime.now(TZ)
        sync_due = last_sync is None or (now - last_sync).total_seconds() >= interval
        await asyncio.gather(*(process(chat_id, now, sync_due) for chat_id in store.users()))
        if sync_due:
            last_sync = now
        await asyncio.sleep(30)


async def main():
    global store
    load_dotenv()
    token, key = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("BOT_ENCRYPTION_KEY")
    if not token or not key:
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and BOT_ENCRYPTION_KEY in .env")
    store = BotStore(os.getenv("BOT_DB_PATH", "data/bot.sqlite3"), key)
    logging.basicConfig(level=logging.INFO)
    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in (
        ("start", "Открыть дневник"), ("menu", "Главное меню"),
        ("logout", "Отключить дневник"), ("cancel", "Отменить ввод"))])
    task = asyncio.create_task(notifications(bot, max(300, int(os.getenv("POLL_INTERVAL_SECONDS", "1800")))))
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        task.cancel()
        await bot.session.close()


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
