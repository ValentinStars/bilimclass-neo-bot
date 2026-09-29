"""Allowlisted private-chat administration. No broadcast is sent without preview and confirmation."""

import asyncio
import gzip
import html
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from . import admin_tools

router = Router()
log = logging.getLogger(__name__)


class Draft(StatesGroup):
    message = State()
    confirm = State()


def core():
    from . import bot
    return bot


def admitted(chat_id):
    app = core()
    user = app.store.user(chat_id)
    return bool(admin_tools.allowed(chat_id, app.store) and user and user["prefs"]["admin_enabled"])


async def guard(call):
    if call.message.chat.type != "private" or not admitted(call.from_user.id):
        await call.answer("Доступ к админ-панели закрыт", show_alert=True)
        return False
    await call.answer()
    return True


def keyboard(chat_id):
    app = core()
    prefs = app.store.user(chat_id)["prefs"]
    return app.buttons(
        [("🖥 Сервер", "admin:server"), ("📈 Активность", "admin:chart")],
        [("📊 Классы", "admin:classes")],
        [("✉️ Обратная связь", "support:inbox"), ("🔐 Сбросить запасные входы", "support:revoke")],
        [("👥 Пользователи", "admin:users:0"), ("📋 Активность", "admin:activity")],
        [("📜 Логи", "admin:logs"), ("📣 Объявление", "admin:broadcast")],
        [("🪙 BONUS LORD", "admin:bonus"), ("⬇️ Выгрузки", "admin:exports")],
        [("← Настройки", "view:settings")], prefs=prefs)


async def show(call):
    await core().present(call, "<b>НЭО · управление</b>\nСостояние, аудит и объявления в одном месте.", keyboard(call.from_user.id))


@router.message(Command("admin"))
async def admin_command(message: Message):
    if message.chat.type != "private" or not admitted(message.from_user.id):
        await message.answer("Доступ к админ-панели закрыт.")
        return
    core().store.record_activity(message.from_user.id, "admin")
    await message.answer("<b>НЭО · управление</b>\nВыбери раздел.", reply_markup=keyboard(message.from_user.id))


@router.callback_query(F.data.startswith("admin:"))
async def panel(call: CallbackQuery, state: FSMContext):
    if not await guard(call):
        return
    app = core()
    chat_id = call.from_user.id
    app.store.record_activity(chat_id, "admin")
    parts = call.data.split(":")
    action = parts[1]
    prefs = app.store.user(chat_id)["prefs"]
    back = app.buttons([("← Админ-панель", "admin:home")], prefs=prefs)
    if action == "home":
        await show(call)
    elif action == "server":
        data = await asyncio.to_thread(admin_tools.server_report, str(app.store.path))
        total = len(app.store.users())
        sent = app.store.broadcast_totals()
        report = (f"<b>🖥 Сервер</b>\nАптайм ОС: {data['uptime_h']} ч · бот: {data['bot_uptime_h']} ч\n"
                  f"Нагрузка 1/5/15 мин: {' / '.join(map(str, data['load']))}\n"
                  f"Процесс бота: {data['bot_memory_mb']} МБ памяти\n"
                  f"Память: свободно {data['ram_free_gb']} из {data['ram_total_gb']} ГБ\n"
                  f"Диск: свободно {data['disk_free_gb']} из {data['disk_total_gb']} ГБ\n"
                  f"База: {data['database_kb']} КБ · учеников: {total}\n"
                  f"Объявления: {sent[0]} кампаний, {sent[2]} доставлено, {sent[3]} ошибок")
        await app.present(call, report, back)
    elif action == "chart":
        counts = app.store.activity_counts()
        png = await asyncio.to_thread(admin_tools.activity_chart, counts, len(app.store.users()))
        await call.message.answer_photo(BufferedInputFile(png, filename="neo-activity.png"), caption="<b>Активность НЭО</b> · 14 дней, UTC. Учёт действий ведётся с обновления.", reply_markup=back, protect_content=True)
    elif action == "classes":
        users = [(i, app.store.user(i)) for i in app.store.users()]
        png = await asyncio.to_thread(admin_tools.classes_chart, users)
        await call.message.answer_photo(BufferedInputFile(png, filename="neo-classes.png"), caption="<b>Ученики по классам</b> · 8 крупнейших групп среди подключённых.", reply_markup=back, protect_content=True)
    elif action == "activity":
        counts = app.store.activity_counts()
        kinds = ", ".join(f"{html.escape(k)}: {n}" for k, n in counts["kinds"][:10]) or "пока нет данных"
        await app.present(call, f"<b>📋 Активность · 14 дней</b>\nСобытий: {sum(n for _, n in counts['kinds'])}\nАктивных учеников: {len(counts['users'])}\n{kinds}", back)
    elif action == "users":
        ids = app.store.users()
        page = max(0, min(int(parts[2]) if len(parts) > 2 else 0, max(0, (len(ids)-1)//10)))
        lines = []
        for ident in ids[page*10:(page+1)*10]:
            p = app.store.user(ident)["profile"]
            lines.append(f"• {app.h(admin_tools.identity_label(ident, p))} · {app.h(p.get('fio'))} · {app.h(p.get('group'))} · {app.h(p.get('schoolName'))}")
        rows = []
        if page:
            rows.append(("←", f"admin:users:{page-1}"))
        if (page+1)*10 < len(ids):
            rows.append(("→", f"admin:users:{page+1}"))
        markup = app.buttons(rows, [("← Админ-панель", "admin:home")], prefs=prefs) if rows else back
        await app.present(call, f"<b>👥 Ученики · {len(ids)}</b>\n" + "\n".join(lines), markup)
    elif action == "logs":
        try:
            raw = await asyncio.to_thread(admin_tools.journal, 50)
            raw = raw[-3000:] or "Журнал пуст."
            token = __import__("os").getenv("TELEGRAM_BOT_TOKEN", "")
            key = __import__("os").getenv("BOT_ENCRYPTION_KEY", "")
            for secret in (token, key):
                if secret:
                    raw = raw.replace(secret, "[REDACTED]")
            while len(html.escape(raw)) > 3500:
                raw = raw[len(raw)//3:]
            await app.present(call, "<b>📜 Последние строки журнала</b>\n<pre>" + html.escape(raw) + "</pre>", back)
        except Exception:
            await app.present(call, "Журнал сервиса недоступен.", back)
    elif action == "exports":
        await app.present(call, "<b>⬇️ Выгрузки</b>\nВыбери файл. Экспорт логинов и паролей требует отдельного подтверждения.", app.buttons(
            [("📜 Полный журнал", "admin:export:logs")],
            [("📊 Активность CSV", "admin:export:activity")],
            [("🔐 Логины и пароли", "admin:export:credentials")],
            [("← Админ-панель", "admin:home")], prefs=prefs))
    elif action == "export":
        kind = parts[2] if len(parts) > 2 else ""
        if kind == "credentials" and len(parts) == 3:
            await app.present(call, "<b>Выгрузка учётных данных</b>\nCSV содержит открытые логины и пароли всех подключённых учеников. Файл будет отправлен только в этот личный чат. Подтверди действие.", app.buttons(
                [("Подтверждаю выгрузку", "admin:export:credentials:confirm")],
                [("Отмена", "admin:exports")], prefs=prefs))
            return
        if kind == "activity":
            payload = admin_tools.activity_csv(app.store.activity_counts(), [(i, app.store.user(i)) for i in app.store.users()])
            name = "neo-activity-14d.csv"
        elif kind == "logs":
            try:
                raw = await asyncio.to_thread(admin_tools.journal)
            except Exception:
                await call.message.answer("Журнал недоступен.")
                return
            token = __import__("os").getenv("TELEGRAM_BOT_TOKEN", "")
            key = __import__("os").getenv("BOT_ENCRYPTION_KEY", "")
            for secret in (token, key):
                if secret:
                    raw = raw.replace(secret, "[REDACTED]")
            payload, name = raw.encode(), "neo-service.log"
            if len(payload) > 40_000_000:
                payload, name = gzip.compress(payload), "neo-service.log.gz"
        elif kind == "credentials" and len(parts) == 4 and parts[3] == "confirm":
            payload, name = admin_tools.credentials_csv([(i, app.store.user(i)) for i in app.store.users()]), "neo-users-private.csv"
        else:
            return
        if len(payload) > 45_000_000:
            await call.message.answer("Файл превышает лимит Telegram. Ограничь журнал на сервере.")
            return
        await call.message.answer_document(BufferedInputFile(payload, filename=name), protect_content=True)
    elif action == "bonus":
        if len(parts) > 2 and parts[2] == "toggle":
            app.store.set_setting("lord_bonus_enabled", not app.store.get_setting("lord_bonus_enabled", True))
        enabled = app.store.get_setting("lord_bonus_enabled", True)
        await app.present(call, f"<b>🪙 BONUS LORD</b>\nПоказ ученикам: {'включён' if enabled else 'выключен'}.\nДанные: публичная таблица, ячейка A7 листа класса.", app.buttons(
            [("✅ Выключить" if enabled else "⬜ Включить", "admin:bonus:toggle")],
            [("← Админ-панель", "admin:home")], prefs=prefs))
    elif action == "broadcast":
        await state.clear()
        await app.present(call, "<b>📣 Объявление</b>\nКому отправить? Выбор аудитории и текст будут показаны перед отправкой.", app.buttons(
            [("Всем", "admin:scope:all"), ("Классу", "admin:scope:class")],
            [("Школе", "admin:scope:school"), ("Городу", "admin:scope:city")],
            [("Ученику", "admin:scope:student")],
            [("← Админ-панель", "admin:home")], prefs=prefs))
    elif action == "scope":
        scope = parts[2] if len(parts) > 2 else ""
        users = [(i, app.store.user(i)) for i in app.store.users()]
        if scope == "all":
            await state.update_data(scope="all", value="")
            await state.set_state(Draft.message)
            await call.message.answer(f"Аудитория: все {len(users)} учеников. Отправь сообщение, фото, видео, аудио, файл, голосовое или стикер. /cancel — отмена.")
        elif scope in ("class", "school", "city", "student"):
            options = admin_tools.audiences(users)[scope]
            labels = {str(i): admin_tools.identity_label(i, u['profile']) for i, u in users}
            rows = [[(f"{(labels.get(value, value) if scope == 'student' else value)[:45]} · {len(admin_tools.recipients(users, scope, value))}", f"admin:pick:{scope}:{index}")] for index, value in enumerate(options[:50])]
            rows.append([("← Объявление", "admin:broadcast")])
            await app.present(call, "<b>Аудитория</b>\nВыбери точное значение. Неизвестный город не включается в рассылку.", app.buttons(*rows, prefs=prefs))
    elif action == "pick":
        scope = parts[2]
        options = admin_tools.audiences([(i, app.store.user(i)) for i in app.store.users()]).get(scope, [])
        index = int(parts[3])
        if not 0 <= index < min(len(options), 50):
            return
        value = options[index]
        await state.update_data(scope=scope, value=value)
        await state.set_state(Draft.message)
        label = admin_tools.identity_label(int(value), app.store.user(int(value))["profile"]) if scope == "student" else value
        await call.message.answer(f"Аудитория: {html.escape(label)}. Отправь содержимое объявления одним сообщением. /cancel — отмена.")
    elif action == "send":
        if await state.get_state() != Draft.confirm:
            await call.message.answer("Черновик устарел. Создай объявление заново.")
            return
        data = await state.get_data()
        users = [(i, app.store.user(i)) for i in app.store.users()]
        ids = admin_tools.recipients(users, data["scope"], data["value"])
        await state.clear()
        sent = failed = 0
        for target in ids:
            try:
                await call.bot.copy_message(target, chat_id, data["message_id"], protect_content=True)
                sent += 1
            except Exception:
                failed += 1
                log.warning("Broadcast delivery failed for chat %s", target)
            await asyncio.sleep(.055)
        app.store.record_broadcast(chat_id, data["scope"] + ":" + data["value"], len(ids), sent, failed)
        app.store.record_activity(chat_id, "broadcast")
        await call.message.answer(f"<b>Объявление отправлено</b>\nДоставлено: {sent}\nОшибок: {failed}", reply_markup=keyboard(chat_id))
    elif action == "cancel":
        await state.clear()
        await show(call)


@router.message(Draft.message)
async def draft_message(message: Message, state: FSMContext):
    if message.chat.type != "private" or not admitted(message.from_user.id):
        return
    if message.text and message.text.startswith("/"):
        return
    data = await state.get_data()
    users = [(i, core().store.user(i)) for i in core().store.users()]
    count = len(admin_tools.recipients(users, data["scope"], data["value"]))
    await state.update_data(message_id=message.message_id)
    await state.set_state(Draft.confirm)
    await message.answer(f"<b>Предпросмотр объявления</b>\nАудитория: {html.escape(data['scope'])} {html.escape(data['value'])}\nПолучателей: {count}\nСледующее сообщение — точная копия для получателей:", protect_content=True)
    await message.bot.copy_message(message.chat.id, message.chat.id, message.message_id, protect_content=True)
    await message.answer("Отправить объявление?", reply_markup=core().buttons(
        [("Отправить", "admin:send", "success"), ("Отмена", "admin:cancel")], prefs=core().store.user(message.from_user.id)["prefs"]))
