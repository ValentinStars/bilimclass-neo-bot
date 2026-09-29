"""Onboarding, appearance preferences, identity refresh and two-way feedback."""

import asyncio
import os

from aiogram import BaseMiddleware, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from . import admin_tools, visuals

router = Router()


def core():
    from . import bot
    return bot


class IdentityMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        message = event.message if isinstance(event, CallbackQuery) else event
        actor = getattr(event, "from_user", None)
        if actor and getattr(getattr(message, "chat", None), "type", None) == "private":
            values = {"telegram_username": actor.username, "telegram_name": actor.first_name}
            saved = core().store.visitor(actor.id)
            if any(saved.get(k) != v for k, v in values.items()):
                core().store.update_visitor(actor.id, **values)
        return await handler(event, data)


def appearance_keyboard(chat_id):
    app = core()
    visitor = app.store.visitor(chat_id)
    user = app.store.user(chat_id)
    prefs = user["prefs"] if user else visitor
    theme = prefs.get("theme", "compact")
    return app.buttons(
        [("● Табло" if theme == "board" else "Табло", "look:set:board"),
         ("● Компактный" if theme == "compact" else "Компактный", "look:set:compact")],
        [("Посмотреть табло", "look:preview:board"), ("Пример текста", "look:preview:compact")],
        [("Анимация: вкл" if prefs.get("animations", True) else "Анимация: выкл", "look:motion")],
        [("Готово →" if user else "Выбрать и подключить →", "view:home" if user else "auth:start")],
        [("✉️ Обратная связь", "support:new")], prefs=prefs)


async def onboarding(message):
    app = core()
    name = app.h(message.from_user.first_name or "друг")
    content = f"<b>Привет, {name}! Как учёба?</b>\n\nВыбери, как будет выглядеть твой дневник.\n<b>Табло</b> — иллюстрированные экраны в одном стиле.\n<b>Компактный</b> — только текст и кнопки.\n\nОформление можно менять в настройках."
    await message.answer_photo(BufferedInputFile(await asyncio.to_thread(visuals.card_bytes, "welcome", "Выбери свой дневник\n01  Табло — картинки и движение\n02  Компактный — текст и кнопки"), filename="neo-welcome.png"), caption=content, reply_markup=appearance_keyboard(message.chat.id), protect_content=True)


@router.callback_query(F.data.startswith("look:"))
async def appearance(call: CallbackQuery):
    if call.message.chat.type != "private":
        await call.answer()
        return
    app = core()
    chat_id = call.from_user.id
    parts = call.data.split(":")
    await call.answer()
    if parts[1] == "set" and len(parts) == 3 and parts[2] in ("board", "compact"):
        app.store.update_visitor(chat_id, theme=parts[2], appearance_chosen=True)
        await call.message.edit_reply_markup(reply_markup=appearance_keyboard(chat_id))
        return
    if parts[1] == "motion":
        saved = app.store.visitor(chat_id)
        app.store.update_visitor(chat_id, animations=not saved.get("animations", True))
        await call.message.edit_reply_markup(reply_markup=appearance_keyboard(chat_id))
        return
    if parts[1] == "preview" and len(parts) == 3:
        sample = "<b>Среда · пример расписания</b>\n08:30  Алгебра · кабинет 204\n09:25  История · кабинет 112\n10:20  Английский · кабинет 306\n\n📝 ДЗ: прочитать § 12.\nЭто демонстрация оформления, не твой дневник."
        if parts[2] == "board":
            saved = app.store.visitor(chat_id)
            if saved.get("animations", True):
                await call.message.answer_animation(BufferedInputFile(await asyncio.to_thread(visuals.welcome_animation), filename="neo-motion.gif"), caption="Короткая заставка при запуске. Анимацию можно выключить.", protect_content=True)
            await call.message.answer_photo(BufferedInputFile(await asyncio.to_thread(visuals.card_bytes, "day", sample), filename="neo-preview.png"), caption=sample, reply_markup=appearance_keyboard(chat_id), protect_content=True)
        else:
            await call.message.answer(sample, reply_markup=appearance_keyboard(chat_id), protect_content=True)
        return
    await call.message.answer("<b>Оформление</b>\nВыбери режим и посмотри пример. Изменения сохраняются сразу.", reply_markup=appearance_keyboard(chat_id), protect_content=True)


class Feedback(StatesGroup):
    writing = State()
    replying = State()


@router.callback_query(F.data.startswith("support:"))
async def support(call: CallbackQuery, state: FSMContext):
    app = core()
    if call.message.chat.type != "private":
        await call.answer()
        return
    parts = call.data.split(":")
    if parts[1] == "new":
        await call.answer()
        await state.clear()
        await state.set_state(Feedback.writing)
        await call.message.answer("<b>Что улучшим?</b>\nРасскажи об ошибке, предложи функцию или просто поделись мыслью. Можно приложить скриншот, голосовое или файл одним сообщением.\n\nАдминистраторы увидят сообщение и твой Telegram ID и смогут ответить здесь. /cancel — отменить.", protect_content=True)
        return
    from .admin_panel import guard, keyboard
    if not await guard(call):
        return
    prefs = app.store.user(call.from_user.id)["prefs"]
    if parts[1] == "inbox":
        entries = app.store.feedback_list(20)
        rows = [[(f"#{i} · {'●' if status == 'open' else '✓'} {admin_tools.identity_label(chat, body)[:40]}", f"support:item:{i}")] for i, chat, body, _, status in entries]
        rows.append([("← Управление", "admin:home")])
        await app.present(call, "<b>Обратная связь</b>\nПоследние 20 обращений." if entries else "<b>Обратная связь</b>\nПока никто не написал.", app.buttons(*rows, prefs=prefs))
    elif parts[1] in ("item", "reply") and len(parts) == 3 and parts[2].isdigit():
        item_id = int(parts[2])
        item = app.store.feedback_item(item_id)
        if not item:
            await call.message.answer("Обращение удалено.")
            return
        chat_id, body, status = item
        if parts[1] == "reply":
            await state.set_state(Feedback.replying)
            await state.update_data(feedback_id=item_id)
            await call.message.answer(f"Ответ на обращение #{item_id}. Отправь одно сообщение. /cancel — отмена.")
            return
        excerpt = body.get('text') or 'Вложение'
        while len(app.h(excerpt)) > 3000:
            excerpt = excerpt[:len(excerpt)//2] + '…'
        text = f"<b>Обращение #{item_id}</b>\n{app.h(admin_tools.identity_label(chat_id, body))}\n\n{app.h(excerpt)}"
        await app.present(call, text, app.buttons([("Ответить", f"support:reply:{item_id}")], [("← Обращения", "support:inbox")], prefs=prefs))
        if body.get("media"):
            try:
                await call.bot.copy_message(call.from_user.id, chat_id, body["message_id"], protect_content=True)
            except Exception:
                await call.message.answer("Оригинал вложения больше недоступен.")
    elif parts[1] == "revoke":
        if not admin_tools.allowed(call.from_user.id):
            await call.message.answer("Сброс доступен только постоянному администратору.")
            return
        app.store.revoke_admin_sessions()
        await app.present(call, "Все запасные админские сессии завершены.", keyboard(call.from_user.id))


@router.message(Feedback.writing)
async def receive_feedback(message: Message, state: FSMContext):
    if message.chat.type != "private":
        return
    app = core()
    if not app.store.rate_limit(f"feedback:{message.chat.id}", 5, 3600):
        await state.clear()
        await message.answer("За час можно отправить пять обращений. Попробуй позже.")
        return
    body = {"text": (message.text or message.caption or "")[:1800], "message_id": message.message_id,
            "media": message.content_type != "text", "telegram_username": message.from_user.username}
    item_id = app.store.add_feedback(message.chat.id, body)
    await state.clear()
    admins = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}
    admins.update(i for i in app.store.users() if admin_tools.allowed(i, app.store))
    for ident in admins:
        try:
            user = app.store.user(ident)
            await message.bot.send_message(ident, f"<b>Новое обращение #{item_id}</b>\n{app.h(admin_tools.identity_label(message.chat.id, body))}\n{app.h(body['text'][:400] or 'Вложение')}", reply_markup=app.buttons([("Открыть обращение", f"support:item:{item_id}")], prefs=user['prefs'] if user else None), protect_content=True)
        except Exception:
            # Persisted inbox is the source of truth even if an admin blocked the bot.
            pass
    user = app.store.user(message.chat.id)
    await message.answer(f"Обращение #{item_id} сохранено для администраторов. Ответ придёт в этот чат.", reply_markup=app.buttons([("← Меню", "view:home")], prefs=user['prefs'] if user else app.store.visitor(message.chat.id)), protect_content=True)


@router.message(Feedback.replying)
async def reply_feedback(message: Message, state: FSMContext):
    from .admin_panel import admitted
    if message.chat.type != "private" or not admitted(message.from_user.id):
        await state.clear()
        return
    app = core()
    data = await state.get_data()
    item = app.store.feedback_item(data.get("feedback_id", -1))
    if not item:
        await state.clear()
        await message.answer("Обращение больше недоступно.")
        return
    try:
        await message.bot.send_message(item[0], f"Ответ администратора на обращение #{data['feedback_id']}:", protect_content=True)
        await message.bot.copy_message(item[0], message.chat.id, message.message_id, protect_content=True)
    except Exception:
        await message.answer("Не удалось доставить ответ. Можно попробовать ещё раз или /cancel.")
        return
    app.store.close_feedback(data["feedback_id"])
    await state.clear()
    await message.answer("Ответ доставлен.")
