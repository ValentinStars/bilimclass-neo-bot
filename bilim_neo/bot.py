"""Private-chat Telegram companion powered by BilimClassClient."""

import asyncio
import hashlib
import hmac
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
from aiogram.types import BotCommand, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, BufferedInputFile, InputMediaPhoto
from aiogram.client.default import DefaultBotProperties
from dotenv import load_dotenv

from .bot_store import BotStore
from .attachments import AttachmentError, StreamedAttachment, file_name, file_size, size_label
from .automation import due_bells, quiet_now
from .bot_views import (advice_view, attendance_view, dashboard_view, day,
                        grades_view, h, marks_view, parse_day, schedule_view,
                        week_view, checklist_view, subject_marks_view)
from .client import BilimClassClient
from .planner import tasks_for_day, task_progress
from .lord_bonus import bonus_for_class, is_lord_school
from . import admin_tools, recovery, visuals


TZ = ZoneInfo("Asia/Almaty")
MAX_DAY_OFFSET = 63
MAX_WEEK_OFFSET = 8
router = Router()
store: BotStore
logger = logging.getLogger(__name__)
NOTIFICATION_FOOTER = "<i>⚙️ Уведомления можно изменить в настройках.</i>"


def notification_text(content):
    """Use a quiet visual footnote; Telegram has no per-line font-size control."""
    if content.endswith(NOTIFICATION_FOOTER):
        return content
    if len(content) > 3900:
        plain = visuals.plain(content)
        while len(h(plain)) > 3800:
            plain = plain[:len(plain) // 2].rstrip()
        content = h(plain) + "\n…"
    return content + "\n\n" + NOTIFICATION_FOOTER


class Login(StatesGroup):
    username = State()
    password = State()


def action_style(label, action):
    """Keep navigation calm; color destinations and positive actions consistently."""
    if label.startswith(("←", "🏠")):
        return None
    if action.startswith("toggle:"):
        return "success" if label.startswith("✅") else None
    if action.startswith(("task:open:", "task:toggle:", "auth:start")):
        return "success"
    if action.startswith(("view:day:", "view:week:", "view:bells:",
                          "view:homework:", "view:marks", "view:grades",
                          "view:attendance", "view:advice", "view:settings",
                          "view:quiet", "view:profile", "marks:subject",
                          "files:list:", "files:send:", "quiet:")):
        return "primary"
    return None


def buttons(*rows, prefs=None):
    colored = (prefs or {}).get("button_colors", True)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=item[0], callback_data=item[1],
                              style=(item[2] if len(item) > 2 else action_style(item[0], item[1])) if colored else None)
         for item in row]
        for row in rows
    ])


def home_keyboard(prefs=None):
    if (prefs or {}).get("local_admin"):
        return buttons([("Управление", "admin:home"), ("Обращения", "support:inbox")],
                       [("Оформление", "look:open"), ("Настройки", "view:settings")],
                       [("Войти заново", "auth:start")], prefs=prefs)
    planner = (prefs or {}).get("planner", True)
    rows = [
        [("📅 Сегодня", "view:day:0", "primary"), ("🌅 Завтра", "view:day:1")],
        [("🗓 Неделя", "view:week:0"), ("🔔 Звонки", "view:bells:0")],
        [("📝 ДЗ сегодня", "view:homework:0"), ("📊 Оценки", "view:marks")],
        [("📈 Табель", "view:grades"), ("🏃 Посещаемость", "view:attendance")],
    ]
    if planner:
        rows.append([("✅ План ДЗ", "task:open:0", "success"), ("💡 Советы", "view:advice")])
        rows.append([("⚙️ Настройки", "view:settings")])
    else:
        rows.append([("💡 Советы", "view:advice"), ("⚙️ Настройки", "view:settings")])
    rows.append([("🎟 Пригласить друга", "ref:link"), ("✉️ Обратная связь", "support:new")])
    return buttons(*rows, prefs=prefs)


def back_keyboard(prefs=None):
    return buttons([("← Главное меню", "view:home")], prefs=prefs)


async def send_screen(message, content, markup, prefs=None, section="home", animate=False, visual=None):
    prefs = prefs or {}
    if prefs.get("theme") != "board" or section not in visuals.VISUAL_SECTIONS:
        return await message.answer(content, reply_markup=markup, protect_content=True)
    if visual is None and len(content) > 1000:
        return await message.answer(content, reply_markup=markup, protect_content=True)
    caption = ("Последние оценки. Полный список — кнопкой ниже." if visual.get("rows") else content) if visual is not None else content
    image_args = (section, content, visual.get("rows"), visual.get("period")) if visual else (section, content)
    if animate and prefs.get("animations", True):
        await message.answer_animation(BufferedInputFile(await asyncio.to_thread(visuals.welcome_animation), filename="neo.gif"), caption=caption, reply_markup=markup, protect_content=True)
    else:
        await message.answer_photo(BufferedInputFile(await asyncio.to_thread(visuals.card_bytes, *image_args), filename="neo-panel.png"), caption=caption, reply_markup=markup, protect_content=True)


async def present(call: CallbackQuery, content: str, markup, visual=None, force_text=False):
    """Reuse the current panel; keep delivered alerts as a readable history."""
    current_store = globals().get("store")
    chat_id = getattr(getattr(call.message, "chat", None), "id", None)
    user = current_store.user(chat_id) if current_store and chat_id else None
    prefs = user["prefs"] if user else {}
    section = visuals.section_for(getattr(call, "data", None))
    if prefs.get("theme") == "board" and section in visuals.VISUAL_SECTIONS and not force_text:
        if getattr(call.message, "photo", None) and (len(content) <= 1000 or visual is not None):
            caption = ("Последние оценки. Полный список — кнопкой ниже." if visual.get("rows") else content) if visual is not None else content
            image_args = (section, content, visual.get("rows"), visual.get("period")) if visual else (section, content)
            media = InputMediaPhoto(media=BufferedInputFile(await asyncio.to_thread(visuals.card_bytes, *image_args), filename="neo-panel.png"), caption=caption)
            try:
                await call.message.edit_media(media, reply_markup=markup)
            except TelegramBadRequest as exc:
                if "message is not modified" not in str(exc).lower():
                    raise
            return
        return await send_screen(call.message, content, markup, prefs, section, visual=visual)
    if call.message.text is None:
        await call.message.answer(content, reply_markup=markup, protect_content=True)
        return
    alert = (call.message.text or "").startswith(("📊 Новые", "🏃 Новые", "📝 Обновилась", "📅 Изменилось", "Доброе утро", "Скоро урок", "План недели"))
    if alert:
        await call.message.answer(content, reply_markup=markup, protect_content=True)
        return
    try:
        await call.message.edit_text(content, reply_markup=markup)
    except TelegramBadRequest as exc:
        if "message is not modified" not in str(exc).lower():
            raise


def date_keyboard(mode, offset, prefs=None):
    week_offset = (datetime.now(TZ).date().weekday() + offset) // 7
    modes = (("day", "Уроки"), ("bells", "Звонки"), ("homework", "ДЗ"))
    move = []
    if offset > -MAX_DAY_OFFSET:
        move.append(("← День", f"view:{mode}:{offset-1}"))
    if offset < MAX_DAY_OFFSET:
        move.append(("День →", f"view:{mode}:{offset+1}"))
    rows = [[(f"{'• ' if mode == key else ''}{label}", f"view:{key}:{offset}",
              "primary" if mode == key else None) for key, label in modes]]
    if mode == "homework":
        actions = []
        if (prefs or {}).get("planner", True):
            actions.append(("✅ План ДЗ", f"task:open:{offset}", "success"))
        actions.append(("📎 Файлы", f"files:list:{offset}"))
        rows.append(actions)
    rows.extend((move, [("🏠 Меню", "view:home"), ("🗓 Неделя", f"view:week:{week_offset}")]))
    return buttons(*rows, prefs=prefs)


def week_keyboard(week_offset, prefs=None):
    today = datetime.now(TZ).date()
    monday = today - timedelta(days=today.weekday()) + timedelta(weeks=week_offset)
    dates = [monday + timedelta(days=index) for index in range(7)]
    change_week = []
    if week_offset > -MAX_WEEK_OFFSET:
        change_week.append(("← Неделя", f"view:week:{week_offset-1}"))
    if week_offset < MAX_WEEK_OFFSET:
        change_week.append(("Неделя →", f"view:week:{week_offset+1}"))
    rows = [change_week]
    labels = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    for index in range(0, 7, 2):
        rows.append([(f"{labels[position]} {dates[position]:%d.%m}",
                      f"view:day:{(dates[position]-today).days}")
                     for position in range(index, min(index + 2, 7))])
    rows.append([("🏠 Меню", "view:home")])
    return buttons(*rows, prefs=prefs)


def settings_keyboard(prefs, chat_id=None, profile=None, bonus_visible=True):
    names = [("marks", "Новые оценки"), ("homework", "Новая домашка"),
             ("schedule", "Изменения расписания"), ("attendance", "Пропуски"),
             ("morning", "Утренний план"), ("bell_reminders", "Перед уроком"),
             ("weekly", "План недели")]
    rows = [[(f"{'✅' if prefs[key] else '⬜'} {name}", f"toggle:{key}")] for key, name in names]
    rows += [[(f"{'✅' if prefs['planner'] else '⬜'} План ДЗ", "toggle:planner")],
             [(f"{'✅' if prefs['button_colors'] else '⬜'} Цветные кнопки", "toggle:button_colors")],
             [("🌙 Тихие часы", "view:quiet")],
             [("🔐 Профиль", "view:profile"), ("🏠 Меню", "view:home")]]
    rows.insert(-1, [("🎨 Оформление", "look:open"), ("✉️ Обратная связь", "support:new")])
    if bonus_visible and profile and is_lord_school(profile.get("schoolName")):
        rows.insert(-1, [(f"{'✅' if prefs['lord_bonus'] else '⬜'} BONUS LORD", "toggle:lord_bonus"), ("🪙 Открыть", "view:bonus")])
    if chat_id is not None and admin_tools.allowed(chat_id, globals().get("store")):
        rows.insert(-1, [(f"{'✅' if prefs['admin_enabled'] else '⬜'} Админ-панель", "toggle:admin_enabled")])
        if prefs["admin_enabled"]:
            rows.insert(-1, [("🛠 Открыть админ-панель", "admin:home")])
    return buttons(*rows, prefs=prefs)


def referral_payload(chat_id):
    identifier = str(chat_id)
    digest = hmac.new(os.environ["BOT_ENCRYPTION_KEY"].encode(), identifier.encode(), hashlib.sha256).hexdigest()[:16]
    return f"r_{identifier}_{digest}"


def referral_inviter(payload):
    parts = payload.split("_")
    if len(parts) != 3 or parts[0] != "r" or not parts[1].isdigit():
        return None
    return int(parts[1]) if hmac.compare_digest(referral_payload(int(parts[1])), payload) else None


def new_client(user):
    if user["profile"].get("account_kind") == "local_admin":
        raise RuntimeError("Локальный администратор не подключён к BilimClass")
    client = BilimClassClient(user["login"], user["password"])
    try:
        client.login()
    except Exception:
        client.session.close()
        raise
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


def fetch(chat_id, kind, offset=0, year=None, with_visual=False):
    user = store.user(chat_id)
    client = new_client(user)
    try:
        latest = client.get_profile()
        profile = {key: latest.get(key) for key in ("fio", "group", "schoolName", "schoolAddress", "region", "currentEduYear", "availableEduYears")}
        if any(user["profile"].get(k) != v for k, v in profile.items()):
            store.update_profile(chat_id, profile)
            user["profile"] = profile
        now = datetime.now(TZ)
        today = now.date()
        target = today + timedelta(days=1 if kind == "advice" else offset)
        if kind in ("dashboard", "day", "bells", "homework", "week", "advice"):
            monday = target - timedelta(days=target.weekday())
            schedule = client.get_schedule(monday.strftime("%d.%m.%Y"))
            if kind == "dashboard":
                if user["prefs"]["planner"]:
                    tasks = tasks_for_day(schedule, today)
                    done = store.homework_done(chat_id, today.isoformat())
                    return dashboard_view(schedule, today, now, task_progress(tasks, done))
                return dashboard_view(schedule, today, now)
            if kind == "week":
                return week_view(schedule, monday)
            if kind == "advice":
                marks = client.get_current_marks(current_period(client))
                return advice_view(schedule, marks, target)
            return schedule_view(schedule, target, {"day": "lessons"}.get(kind, kind))
        if kind == "marks":
            period = current_period(client)
            marks = client.get_current_marks(period)
            content = marks_view(marks, period)
            return (content, {"rows": visuals.grade_rows(marks), "period": period}) if with_visual else content
        if kind == "grades":
            y = year or client.current_edu_year
            return grades_view(client.get_year_grades(y), y)
        if kind == "attendance":
            return attendance_view(client.get_current_marks(current_period(client)), client.get_year_grades())
        raise ValueError("Unknown view")
    finally:
        client.session.close()


def homework_plan(chat_id, offset):
    user = store.user(chat_id)
    client = new_client(user)
    try:
        target = datetime.now(TZ).date() + timedelta(days=offset)
        monday = target - timedelta(days=target.weekday())
        schedule = client.get_schedule(monday.strftime("%d.%m.%Y"))
        return target, tasks_for_day(schedule, target)
    finally:
        client.session.close()


def plan_keyboard(tasks, done, offset, target, prefs=None):
    rows = [[(f"{'✅' if task['key'] in done else '○'} {task['lesson'].get('label') or 'Урок'}"[:60],
              f"task:toggle:{offset}:{task['index']}:{target:%Y%m%d}:{task['key']}",
              None if task["key"] in done else "success")] for task in tasks[:30]]
    navigation = ([("← День", f"task:open:{offset-1}")] if offset > -MAX_DAY_OFFSET else []) + \
                 ([("День →", f"task:open:{offset+1}")] if offset < MAX_DAY_OFFSET else [])
    rows.extend((navigation, [("📝 Откры ДЗ", f"view:homework:{offset}"), ("🏠 Меню", "view:home")]))
    return buttons(*rows, prefs=prefs)


@router.callback_query(F.data.startswith("task:"))
async def task(call: CallbackQuery):
    await call.answer()
    if call.message.chat.type != "private":
        return
    user = store.user(call.message.chat.id)
    if not user:
        return
    store.record_activity(call.from_user.id, "planner")
    if not user["prefs"]["planner"]:
        await call.message.answer("План ДЗ выключен. Его можно включить в настройках.", reply_markup=buttons([("⚙️ Настройки", "view:settings")], prefs=user["prefs"]), protect_content=True)
        return
    try:
        parts = call.data.split(":")
        action, offset = parts[1], int(parts[2])
        if not -MAX_DAY_OFFSET <= offset <= MAX_DAY_OFFSET or action not in ("open", "toggle"):
            return
        target, tasks = await asyncio.to_thread(homework_plan, call.message.chat.id, offset)
        if action == "toggle":
            index = int(parts[3])
            selected = next((item for item in tasks if item["index"] == index), None)
            if selected is None or len(parts) != 6 or parts[4] != target.strftime("%Y%m%d") or selected["key"] != parts[5]:
                await call.message.answer("Задание изменилось. Открой план заново.", protect_content=True)
                return
            done = store.toggle_homework_done(call.message.chat.id, target.isoformat(), selected["key"])
        else:
            done = store.homework_done(call.message.chat.id, target.isoformat())
        await present(call, checklist_view(tasks, done, target), plan_keyboard(tasks, done, offset, target, user["prefs"]))
    except Exception:
        logger.exception("Homework plan failed for chat %s", call.message.chat.id)
        await call.message.answer("План ДЗ пока недоступен. Попробуй позже.", reply_markup=back_keyboard(user["prefs"]), protect_content=True)


def subject_marks(chat_id, subject_index=None, expected_key=None):
    user = store.user(chat_id)
    client = new_client(user)
    try:
        period = current_period(client)
        marks = client.get_current_marks(period)
        names = sorted({row["subject"] for row in marks if row.get("subject")})
        if subject_index is None:
            return names
        if not 0 <= subject_index < len(names):
            return None
        if expected_key != hashlib.sha256(names[subject_index].encode()).hexdigest()[:12]:
            return None
        return subject_marks_view(marks, names[subject_index], period)
    finally:
        client.session.close()


@router.callback_query(F.data.startswith("marks:"))
async def marks_detail(call: CallbackQuery):
    await call.answer()
    if call.message.chat.type != "private":
        return
    user = store.user(call.message.chat.id)
    if not user:
        return
    store.record_activity(call.from_user.id, "marks")
    try:
        parts = call.data.split(":")
        if parts[1] == "subjects":
            names = await asyncio.to_thread(subject_marks, call.message.chat.id)
            rows = [[(name[:60], f"marks:subject:{index}:{hashlib.sha256(name.encode()).hexdigest()[:12]}")]
                    for index, name in enumerate(names[:40])]
            rows.append([("← Все оценки", "view:marks")])
            content = "<b>📚 Оценки по предметам</b>\nВыбери предмет." if names else "<b>📚 Оценки по предметам</b>\nПока оценок нет."
            await present(call, content, buttons(*rows, prefs=user["prefs"]), force_text=True)
        elif parts[1] == "subject":
            index = int(parts[2])
            content = await asyncio.to_thread(subject_marks, call.message.chat.id, index, parts[3] if len(parts) == 4 else None)
            if content is None:
                await call.message.answer("Список предметов изменился. Открой его заново.", reply_markup=buttons([("← Предметы", "marks:subjects")], prefs=user["prefs"]), protect_content=True)
                return
            await present(call, content, buttons([("← Предметы", "marks:subjects"), ("🏠 Меню", "view:home")], prefs=user["prefs"]), force_text=True)
    except Exception:
        logger.exception("Subject marks failed for chat %s", call.message.chat.id)
        await call.message.answer("Оценки пока недоступны. Попробуй позже.", reply_markup=back_keyboard(user["prefs"]), protect_content=True)


def homework_attachments(chat_id, offset, lesson_index=None, file_index=None):
    """Resolve callback indexes afresh; signed links are never kept in SQLite or callbacks."""
    user = store.user(chat_id)
    client = new_client(user)
    try:
        target = datetime.now(TZ).date() + timedelta(days=offset)
        monday = target - timedelta(days=target.weekday())
        selected = day(client.get_schedule(monday.strftime("%d.%m.%Y")), target) or {}
        lessons = selected.get("subjects") or []
        if lesson_index is not None:
            if not 0 <= lesson_index < len(lessons):
                raise AttachmentError("Урок больше не найден. Открой ДЗ заново.")
            lesson = lessons[lesson_index]
            files = client.get_homework_files(lesson.get("homeworkUuid"))
            if file_index is None or not 0 <= file_index < len(files):
                raise AttachmentError("Файл больше не найден. Открой список заново.")
            return lesson.get("label") or "Урок", files[file_index]
        result = []
        for index, lesson in enumerate(lessons):
            if lesson.get("hasFiles") and lesson.get("homeworkUuid"):
                for position, metadata in enumerate(client.get_homework_files(lesson["homeworkUuid"])):
                    result.append((index, position, lesson.get("label") or "Урок", metadata))
        return result
    finally:
        client.session.close()


@router.callback_query(F.data.startswith("files:"))
async def files(call: CallbackQuery):
    if call.message.chat.type != "private":
        await call.answer()
        return
    user = store.user(call.message.chat.id)
    if not user:
        await call.answer("Сначала подключи дневник", show_alert=True)
        return
    store.record_activity(call.from_user.id, "files")
    offset = 0
    try:
        parts = call.data.split(":")
        action = parts[1]
        offset = int(parts[2])
        if not -MAX_DAY_OFFSET <= offset <= MAX_DAY_OFFSET:
            raise ValueError
        if action == "list" and len(parts) == 3:
            await call.answer("Ищу файлы…")
            entries = await asyncio.to_thread(homework_attachments, call.message.chat.id, offset)
            if not entries:
                await present(call, "<b>📎 Файлы к ДЗ</b>\nНа этот день вложений нет.", buttons([("← К ДЗ", f"view:homework:{offset}")], prefs=user["prefs"]))
                return
            rows = []
            for lesson_index, file_index, subject, metadata in entries[:40]:
                try:
                    label = f"{subject} · {file_name(metadata)} · {size_label(file_size(metadata))}"
                except AttachmentError:
                    label = f"{subject} · файл без размера"
                rows.append([(label[:60], f"files:send:{offset}:{lesson_index}:{file_index}")])
            rows.append([("← К ДЗ", f"view:homework:{offset}")])
            extra = "\nПоказаны первые 40 файлов." if len(entries) > 40 else ""
            await present(call, f"<b>📎 Файлы к ДЗ</b>\nНажми на файл — отправлю его сюда. На сервере файлы не сохраняются.{extra}", buttons(*rows, prefs=user["prefs"]))
            return
        if action == "send" and len(parts) == 5:
            lesson_index, file_index = int(parts[3]), int(parts[4])
            if not 0 <= lesson_index < 100 or not 0 <= file_index < 100:
                raise ValueError
            await call.answer("Отправляю файл…")
            subject, metadata = await asyncio.to_thread(homework_attachments, call.message.chat.id, offset, lesson_index, file_index)
            document = StreamedAttachment(metadata)
            await call.message.answer_document(document, caption=f"📎 <b>{h(subject)}</b>\n{h(document.filename)}", protect_content=True)
            return
        raise ValueError
    except AttachmentError as exc:
        await call.message.answer(h(str(exc)), reply_markup=buttons([("← К ДЗ", f"view:homework:{offset}")], prefs=user["prefs"]), protect_content=True)
    except Exception as exc:
        # aiohttp errors may include a signed storage URL; never log its traceback.
        logger.warning("Attachment delivery failed for chat %s (%s)", call.message.chat.id, type(exc).__name__)
        await call.message.answer("Не удалось отправить файл. Открой список заново или попробуй позже.", reply_markup=buttons([("← К ДЗ", f"view:homework:{offset}")], prefs=user["prefs"]), protect_content=True)


def private(message):
    return message.chat.type == "private"


@router.message(CommandStart())
@router.message(Command("menu"))
async def start(message: Message, state: FSMContext):
    if not private(message):
        return
    await state.clear()
    if message.text and message.text.startswith("/start "):
        inviter = referral_inviter(message.text.split(maxsplit=1)[1].strip())
        if inviter:
            store.set_pending_referral(message.chat.id, inviter)
    user = store.user(message.chat.id)
    if user:
        store.record_activity(message.chat.id, "menu")
        if user["profile"].get("account_kind") == "local_admin":
            content = "<b>Запасной администратор</b>\n" + ("Сессия активна до 12 часов после входа. /logout — выйти." if admin_tools.allowed(message.chat.id, store) else "Сессия истекла. /recovery — войти снова.")
        else:
            try:
                content = await asyncio.to_thread(fetch, message.chat.id, "dashboard")
            except Exception:
                content = "<b>НЭО · твой школьный день</b>\nРасписание пока не загрузилось. Разделы доступны ниже."
        content = f"<b>Привет, {h(message.from_user.first_name or 'друг')}! Как учёба?</b>\n\n" + content
        await send_screen(message, content, home_keyboard(user["prefs"]), user["prefs"])
    else:
        from .experience import onboarding
        await onboarding(message)


@router.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    user = store.user(message.chat.id) if private(message) else None
    await message.answer("Ввод отменён.", reply_markup=back_keyboard(user["prefs"] if user else None))


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
    if not store.user(call.from_user.id) and not store.visitor(call.from_user.id).get("appearance_chosen"):
        store.update_visitor(call.from_user.id, theme="compact", appearance_chosen=True)
    await state.clear()
    await state.set_state(Login.username)
    await call.message.answer("Отправь <b>логин BilimClass</b>. Я удалю сообщение после получения. /cancel — отмена.", protect_content=True)


@router.message(Command("recovery"))
async def recovery_start(message: Message, state: FSMContext):
    if not private(message):
        return
    await state.clear()
    await state.update_data(auth_mode="recovery")
    await state.set_state(Login.username)
    await message.answer("<b>Запасной вход администратора</b>\nВведи локальный логин. /cancel — отмена.", protect_content=True)


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
    if data.get("auth_mode") == "recovery" or recovery.matches_login(data.get("username", "")):
        success = await asyncio.to_thread(recovery.login, store, message.chat.id, data["username"], password)
        if not success:
            await message.answer("Не удалось войти. Проверь данные. После пяти попыток вход приостанавливается на 15 минут.", reply_markup=buttons([("Повторить", "auth:start")]), protect_content=True)
            return
        user = store.user(message.chat.id)
        store.record_activity(message.chat.id, "login")
        for raw_id in os.getenv("ADMIN_IDS", "").split(","):
            if raw_id.strip().isdigit():
                owner_id = int(raw_id.strip())
                if owner_id != message.chat.id and store.user(owner_id):
                    store.enqueue(owner_id, f"recovery:{message.chat.id}:{message.message_id}", f"<b>Запасной администратор вошёл</b>\n{h(admin_tools.identity_label(message.chat.id, user['profile']))}\nДоступ действует 12 часов. Сбросить все запасные входы можно в админ-панели.")
        await send_screen(message, "<b>Запасной администратор подключён</b>\nДоступ действует 12 часов. /logout — завершить вход.", home_keyboard(user["prefs"]), user["prefs"], "admin")
        return
    try:
        def verify():
            client = BilimClassClient(data["username"], password)
            try:
                client.login()
                return client.get_profile()
            finally:
                client.session.close()
        profile = await asyncio.to_thread(verify)
    except Exception:
        await message.answer("Не получилось войти. Проверь логин и пароль и попробуй ещё раз через кнопку.", reply_markup=buttons([("Попробовать снова", "auth:start")]), protect_content=True)
        return
    profile = {key: profile.get(key) for key in ("fio", "group", "schoolName", "schoolAddress", "region", "currentEduYear", "availableEduYears")}
    is_new = store.save_user(message.chat.id, data["username"], password, profile)
    store.record_activity(message.chat.id, "login")
    if is_new:
        store.complete_referral(message.chat.id, f"<b>🎟 Друг подключился по твоей ссылке</b>\n{h(profile.get('fio'))} · {h(profile.get('group'))}. Спасибо за приглашение!")
    prefs = store.user(message.chat.id)["prefs"]
    await send_screen(message, f"Готово, <b>{h(profile.get('fio'))}</b>! Дневник подключён.\nУведомления можно настроить отдельно для каждого события.", home_keyboard(prefs), prefs)


@router.callback_query(F.data.startswith("toggle:"))
async def toggle(call: CallbackQuery):
    if call.message.chat.type != "private":
        await call.answer()
        return
    user = store.user(call.message.chat.id)
    if not user:
        await call.answer("Сначала подключи дневник", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    if key == "admin_enabled" and not admin_tools.allowed(call.from_user.id, store):
        await call.answer("Доступ закрыт", show_alert=True)
        return
    if key == "lord_bonus" and not is_lord_school(user["profile"].get("schoolName")):
        await call.answer("Доступно только ученикам ЛОРД", show_alert=True)
        return
    if key not in ("marks", "homework", "schedule", "attendance", "morning", "bell_reminders", "weekly", "planner", "button_colors", "admin_enabled", "lord_bonus"):
        await call.answer()
        return
    store.set_pref(call.message.chat.id, key, not user["prefs"][key])
    store.record_activity(call.from_user.id, "settings")
    await call.answer("Настройка сохранена")
    updated = store.user(call.message.chat.id)
    await call.message.edit_reply_markup(reply_markup=settings_keyboard(updated["prefs"], call.from_user.id, updated["profile"], store.get_setting("lord_bonus_enabled", True)))


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
    await present(call, f"<b>Тихие часы</b> · {start:02d}:00–{end:02d}:00\nУведомления придут после окончания паузы.", quiet_keyboard(user["prefs"]))


def quiet_keyboard(prefs=None):
    return buttons([("22:00–07:00", "quiet:22:7"), ("21:00–08:00", "quiet:21:8")], [("00:00–00:00 · выкл.", "quiet:0:0")], [("← Настройки", "view:settings")], prefs=prefs)


@router.callback_query(F.data == "ref:link")
async def referral_link(call: CallbackQuery):
    if call.message.chat.type != "private":
        await call.answer()
        return
    user = store.user(call.from_user.id)
    if not user:
        await call.answer("Сначала подключи дневник", show_alert=True)
        return
    await call.answer()
    bot_info = await call.bot.get_me()
    url = f"https://t.me/{bot_info.username}?start={referral_payload(call.from_user.id)}"
    count = store.referral_count(call.from_user.id)
    await present(call, f"<b>🎟 Пригласи друга</b>\nОтправь ему ссылку. Когда он подключит дневник, я сообщу тебе здесь.\n\n<code>{h(url)}</code>\n\nПодключились по ссылке: {count}", back_keyboard(user["prefs"]))


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
    if kind == "marks" and len(parts) == 3 and parts[2] == "text":
        try:
            content = await asyncio.to_thread(fetch, call.message.chat.id, "marks")
        except Exception:
            await call.message.answer("Оценки пока недоступны. Попробуй позже.", reply_markup=back_keyboard(user["prefs"]), protect_content=True)
            return
        await present(call, content, buttons([("← Оценки", "view:marks"), ("🏠 Меню", "view:home")], prefs=user["prefs"]), force_text=True)
        return
    if kind == "home":
        store.record_activity(call.from_user.id, "menu")
        if user["profile"].get("account_kind") == "local_admin":
            content = "<b>Запасной администратор</b>\n" + ("Управление ботом и обращения учеников." if admin_tools.allowed(call.from_user.id, store) else "Сессия истекла. /recovery — войти снова.")
            await present(call, content, home_keyboard(user["prefs"]))
            return
        try:
            content = await asyncio.to_thread(fetch, call.message.chat.id, "dashboard")
        except Exception:
            content = "<b>НЭО · меню</b>\nРасписание пока не загрузилось. Выбери нужный раздел."
        await present(call, content, home_keyboard(user["prefs"]))
        return
    if kind == "settings":
        store.record_activity(call.from_user.id, "settings")
        p = user["prefs"]
        await present(call, f"<b>⚙️ Настройки</b>\nПо умолчанию включены только новые оценки. Остальные уведомления можно включить здесь.\n🌙 Тихие часы: {p['quiet_from']:02d}:00–{p['quiet_to']:02d}:00", settings_keyboard(p, call.from_user.id, user["profile"], store.get_setting("lord_bonus_enabled", True)))
        return
    if kind == "bonus":
        if not is_lord_school(user["profile"].get("schoolName")) or not store.get_setting("lord_bonus_enabled", True) or not user["prefs"]["lord_bonus"]:
            await call.message.answer("BONUS LORD недоступен для этого профиля.")
            return
        try:
            group, amount = await asyncio.to_thread(bonus_for_class, user["profile"].get("group"))
            content = f"<b>🪙 BONUS LORD · {h(group)}</b>\nСумма класса: <b>{amount:g}</b>\nИсточник: публичная таблица класса, ячейка A7." if amount is not None else "<b>BONUS LORD</b>\nДля твоего класса лист пока не найден."
        except Exception:
            logger.warning("BONUS LORD unavailable for chat %s", call.from_user.id)
            content = "<b>BONUS LORD</b>\nТаблица сейчас недоступна. Попробуй позже."
        store.record_activity(call.from_user.id, "bonus")
        await present(call, content, back_keyboard(user["prefs"]))
        return
    if kind == "quiet":
        await present(call, "<b>Тихие часы</b>\nВыбери удобный режим по времени Алматы.", quiet_keyboard(user["prefs"]))
        return
    if kind == "profile":
        p = user["profile"]
        await present(call, f"<b>Профиль</b>\n{h(p.get('fio'))}\n{h(admin_tools.identity_label(call.from_user.id, p))}\n{h(p.get('group'))} · {h(p.get('schoolName'))}\nУчебный год: {h(p.get('currentEduYear'))}\n\nОтключить дневник и удалить данные: /logout", back_keyboard(user["prefs"]))
        return
    if kind not in ("day", "bells", "homework", "week", "advice", "marks", "grades", "attendance"):
        return
    store.record_activity(call.from_user.id, "marks" if kind in ("marks", "grades") else "diary")
    try:
        raw_offset = int(parts[2]) if len(parts) > 2 else 0
        week_offset = max(-MAX_WEEK_OFFSET, min(MAX_WEEK_OFFSET, raw_offset)) if kind == "week" else 0
        offset = week_offset * 7 if kind == "week" else max(-MAX_DAY_OFFSET, min(MAX_DAY_OFFSET, raw_offset))
        if kind == "grades":
            offset = 0
        year = int(parts[2]) if kind == "grades" and len(parts) > 2 else None
        if year is not None:
            current = int(user["profile"].get("currentEduYear") or datetime.now(TZ).year)
            available = user["profile"].get("availableEduYears") or list(range(current, current - 5, -1))
            if year not in available:
                await call.message.answer("Этот учебный год недоступен в профиле.", reply_markup=back_keyboard(user["prefs"]))
                return
        payload = await asyncio.to_thread(fetch, call.message.chat.id, kind, offset, year,
                                          user["prefs"].get("theme") == "board" and kind == "marks")
        content, visual = payload if isinstance(payload, tuple) else (payload, None)
    except Exception:
        logger.exception("BilimClass view failed for chat %s", call.message.chat.id)
        await call.message.answer("Дневник временно недоступен. Попробуй чуть позже. Если пароль изменился — /logout и подключи дневник снова.", reply_markup=back_keyboard(user["prefs"]))
        return
    if kind in ("day", "bells", "homework"):
        markup = date_keyboard(kind, offset, user["prefs"])
    elif kind == "week":
        markup = week_keyboard(week_offset, user["prefs"])
    elif kind == "grades":
        current = int(user["profile"].get("currentEduYear") or datetime.now(TZ).year)
        years = user["profile"].get("availableEduYears") or list(range(current, current - 5, -1))
        rows = [[(f"{y}/{y+1}", f"view:grades:{y}") for y in years[i:i+2]] for i in range(0, len(years), 2)]
        markup = buttons(*rows, [("← Меню", "view:home")], prefs=user["prefs"])
    elif kind == "marks":
        rows = [[("📚 По предметам", "marks:subjects")]]
        if visual is not None:
            rows.append([("📋 Все оценки", "view:marks:text")])
        rows.append([("🏠 Меню", "view:home")])
        markup = buttons(*rows, prefs=user["prefs"])
    else:
        markup = back_keyboard(user["prefs"])
    await present(call, content, markup, visual=visual)


def collect_updates(chat_id):
    user = store.user(chat_id)
    client = new_client(user)
    try:
        return _collect_updates(chat_id, user, client)
    finally:
        client.session.close()


def _collect_updates(chat_id, user, client):
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
        old = saved.get("data") if kind in ("homework", "schedule") and saved and saved.get("schema") == 2 and saved.get("date") == tomorrow.isoformat() else (None if kind in ("homework", "schedule") else saved)
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
        store.set_snapshot(chat_id, kind, {"schema": 2, "date": tomorrow.isoformat(), "data": current} if kind in ("homework", "schedule") else current)
    if user["prefs"]["morning"]:
        last = store.snapshot(chat_id, "morning")
        if now.hour >= 7 and last != today.isoformat():
            count = len(today_lessons)
            store.enqueue(chat_id, f"morning:{today}", f"<b>Доброе утро ☀️</b>\nСегодня {count} уроков. Удачного дня!")
            store.set_snapshot(chat_id, "morning", today.isoformat())
    if user["prefs"]["weekly"] and today.weekday() == 0 and now.hour >= 7:
        last = store.snapshot(chat_id, "weekly")
        if last != today.isoformat():
            store.enqueue(chat_id, f"weekly:{today}", "<b>План недели</b>\n" + week_view(today_schedule, today_monday))
            store.set_snapshot(chat_id, "weekly", today.isoformat())


async def notifications(bot: Bot, interval: int):
    last_sync = None
    slots = asyncio.Semaphore(4)

    async def process(chat_id, now, sync_due):
        async with slots:
            try:
                user = store.user(chat_id)
                if user["profile"].get("account_kind") == "local_admin":
                    return
                if quiet_now(user["prefs"], now.hour):
                    return
                if sync_due:
                    try:
                        await asyncio.to_thread(collect_updates, chat_id)
                    except RuntimeError as exc:
                        if "авторизации (403)" in str(exc):
                            store.enqueue(chat_id, "auth:reconnect", "<b>🔐 Дневник требует повторного входа</b>\nBilimClass больше не принимает сохранённые данные. Отправь /logout и подключи дневник снова.")
                        else:
                            logger.warning("BilimClass sync temporarily unavailable for chat %s (%s)", chat_id, type(exc).__name__)
                    except Exception:
                        logger.warning("BilimClass sync temporarily unavailable for chat %s", chat_id)
                cached = store.snapshot(chat_id, "today_lessons")
                if user["prefs"]["bell_reminders"] and cached and cached.get("date") == now.date().isoformat():
                    for number, lesson, start in due_bells(cached["lessons"], now):
                        key = f"bell:{now.date()}:{number}"
                        store.enqueue(chat_id, key, f"<b>Скоро урок · {number:02d}</b>\n{h(lesson.get('label') or 'Урок')} в {start:%H:%M} · каб. {h(lesson.get('cabinet'))}")
                for item_id, notice in store.pending(chat_id):
                    await bot.send_message(chat_id, notification_text(notice), protect_content=True, reply_markup=home_keyboard(user["prefs"]))
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
    from .experience import router as experience_router, IdentityMiddleware
    dp.message.outer_middleware(IdentityMiddleware())
    dp.callback_query.outer_middleware(IdentityMiddleware())
    dp.include_router(router)
    from .admin_panel import router as admin_router
    dp.include_router(admin_router)
    dp.include_router(experience_router)
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in (
        ("start", "Открыть дневник"), ("menu", "Главное меню"),
        ("logout", "Отключить дневник"), ("cancel", "Отменить ввод"), ("admin", "Панель администратора"))])
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
