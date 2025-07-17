import asyncio
import aiohttp
import logging
import os
import sys
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp_retry import RetryClient
from markup import *

# Инициализация бота и диспетчера
TOKEN = "YOUR_BOT_TOKEN"  # Замените на ваш токен
bot = Bot(token=TOKEN, parse_mode="HTML")
dp = Dispatcher(bot)
logger = logging.getLogger(__name__)
BOT_ENABLED = True
DEVELOPER_IDS = {123456789}  # Замените на реальные ID разработчиков
DATABASE_FILE = "bot_mirrozz_database.db"
start_time = time.time()
callback_cache = {}

async def init_database():
    # Инициализация базы данных (предполагается, что функция уже определена)
    pass

async def execute_with_retry_async(query, params=()):
    # Функция для выполнения SQL-запросов с повторными попытками (предполагается, что уже определена)
    pass

async def send_message_safe(bot, chat_id, text, reply_markup=None):
    try:
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)
    except Exception as e:
        logger.error(f"Failed to send message to chat {chat_id}: {str(e)}")

async def edit_message_if_changed(callback, text, reply_markup=None):
    try:
        if callback.message.text != text or callback.message.reply_markup != reply_markup:
            await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to edit message for callback {callback.data}: {str(e)}")
        await callback.answer("⚠️ Не удалось обновить сообщение. Попробуйте снова.", show_alert=True)

async def is_banned(user_id):
    # Проверка статуса бана (предполагается, что уже определена)
    return False

async def update_user_activity(user_id):
    # Обновление активности пользователя (предполагается, что уже определена)
    pass

async def check_subscriptions(user_id, check_all_functions):
    # Проверка подписок (предполагается, что уже определена)
    return []

async def check_subgram_subscription(user_id, chat_id, first_name, language_code, is_premium, gender=None):
    # Проверка подписки Subgram (предполагается, что уже определена)
    return "ok", []

async def update_user_subscription(user_id, total_fixed_link, gender):
    # Обновление подписки пользователя (предполагается, что уже определена)
    pass

async def read_stats():
    # Чтение статистики (предполагается, что уже определена)
    return 0, 0, 0

async def update_stats(total_links):
    # Обновление статистики (предполагается, что уже определена)
    pass

async def is_admin(user_id):
    # Проверка статуса администратора (предполагается, что уже определена)
    return user_id in DEVELOPER_IDS

async def scope_text(check_scope):
    # Получение текста области проверки (предполагается, что уже определена)
    return "Links only" if check_scope == "links_only" else "All functions"

async with RetryClient() as client:
    try:
        if content_type == "text":
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=content_data
            )
        elif content_type == "photo":
            async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", json={
                "chat_id": message.chat.id,
                "photo": content_data,
                "caption": caption,
                "parse_mode": "HTML"
            }) as response:
                if not (await response.json()).get('ok'):
                    await send_message_safe(
                        bot=bot,
                        chat_id=message.chat.id,
                        text="⚠️ Не удалось отправить фото."
                    )
                    logger.warning(f"Failed to send photo for link_id {link_id} to user {message.from_user.id}")
        elif content_type == "document":
            async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendDocument", json={
                "chat_id": message.chat.id,
                "document": content_data,
                "caption": caption,
                "parse_mode": "HTML"
            }) as response:
                if not (await response.json()).get('ok'):
                    await send_message_safe(
                        bot=bot,
                        chat_id=message.chat.id,
                        text="⚠️ Не удалось отправить документ."
                    )
                    logger.warning(f"Failed to send document for link_id {link_id} to user {message.from_user.id}")
        else:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неподдерживаемый тип контента."
            )
            logger.error(f"Unsupported content type {content_type} for link_id {link_id}")
            return

        # Update subscription fixed link count
        await execute_with_retry_async(
            "UPDATE subscriptions SET total_fixed_link = total_fixed_link + 1 WHERE user_id = ?",
            (message.from_user.id,)
        )
        logger.info(f"Link {link_id} visited by user {message.from_user.id}, content_type: {content_type}")

    except Exception as e:
        logger.error(f"Error processing link {link_id} for user {message.from_user.id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Произошла ошибка при обработке ссылки."
        )

@dp.callback_query_handler(lambda c: c.data.startswith("check_local_subscription_"))
async def check_local_subscription(callback: types.CallbackQuery):
    if not BOT_ENABLED and callback.from_user.id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="🔴 Бот временно отключен. Только панель разработчика доступна.",
            reply_markup=None
        )
        return
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    action = callback.data.split("_")[-1]
    check_all_functions = action in ["start", "help", "stats", "report"]
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions)
    
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_local_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к {'скрипту' if action not in ['start', 'help', 'stats', 'report'] else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    
    if action == "start":
        await handle_start(callback.message)
    elif action == "help":
        await handle_help(callback.message)
    elif action == "stats":
        await handle_user_stats(callback.message)
    elif action == "report":
        await handle_report(callback.message)
    else:
        await handle_link_visit(callback.message, action)

@dp.callback_query_handler(lambda c: c.data.startswith("check_subgram_subscription_"))
async def check_subgram_subscription(callback: types.CallbackQuery):
    if not BOT_ENABLED and callback.from_user.id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="🔴 Бот временно отключен. Только панель разработчика доступна.",
            reply_markup=None
        )
        return
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    action = callback.data.split("_")[-1]
    try:
        status, result = await check_subgram_subscription(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            first_name=callback.from_user.first_name,
            language_code=callback.from_user.language_code,
            is_premium=callback.from_user.is_premium
        )
    except Exception as e:
        logger.error(f"Error checking Subgram subscription for user {user_id}: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка при проверке подписки. Попробуйте снова.",
            reply_markup=await create_back_keyboard()
        )
        return
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_subgram_subscription_{action}"))
        try:
            await edit_message_if_changed(
                callback=callback,
                text=(
                    f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                    f"Для продолжения подпишитесь на каналы:\n\n"
                    f"После подписки нажмите кнопку ниже:"
                ),
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to edit message for callback check_subgram_subscription_{action}: {str(e)}")
            await callback.answer("⚠️ Не удалось обновить сообщение. Попробуйте снова.", show_alert=True)
        return
    elif status == 'need_gender':
        try:
            await edit_message_if_changed(
                callback=callback,
                text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
                reply_markup=await create_gender_selection_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to edit message for gender selection: {str(e)}")
            await callback.answer("⚠️ Не удалось обновить сообщение. Попробуйте снова.", show_alert=True)
        return
    
    try:
        if action == "start":
            await handle_start(callback.message)
        elif action == "help":
            await handle_help(callback.message)
        elif action == "stats":
            await handle_user_stats(callback.message)
        elif action == "report":
            await handle_report(callback.message)
        else:
            await handle_link_visit(callback.message, action)
    except Exception as e:
        logger.error(f"Error handling action {action} for user {user_id}: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Произошла ошибка при выполнении действия.",
            reply_markup=await create_back_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data.startswith("subgram_gender_"))
async def handle_gender_selection(callback: types.CallbackQuery):
    if not BOT_ENABLED and callback.from_user.id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="🔴 Бот временно отключен. Только панель разработчика доступна.",
            reply_markup=None
        )
        return
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    gender = "male" if callback.data == "subgram_gender_male" else "female"
    await update_user_subscription(user_id, total_fixed_link=0, gender=gender)
    
    try:
        status, result = await check_subgram_subscription(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            first_name=callback.from_user.first_name,
            language_code=callback.from_user.language_code,
            is_premium=callback.from_user.is_premium,
            gender=gender
        )
    except Exception as e:
        logger.error(f"Error checking Subgram subscription after gender selection for user {user_id}: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка при проверке подписки. Попробуйте снова.",
            reply_markup=await create_back_keyboard()
        )
        return
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_start"))
        try:
            await edit_message_if_changed(
                callback=callback,
                text=(
                    f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                    f"Для продолжения подпишитесь на каналы:\n\n"
                    f"После подписки нажмите кнопку ниже:"
                ),
                reply_markup=keyboard
            )
        except Exception as e:
            logger.error(f"Failed to edit message for callback check_subgram_subscription_start: {str(e)}")
            await callback.answer("⚠️ Не удалось обновить сообщение. Попробуйте снова.", show_alert=True)
        return
    elif status == 'ok':
        await handle_start(callback.message)
    else:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Произошла ошибка при обработке подписки.",
            reply_markup=await create_back_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    total_users, link_visits, total_links = await read_stats()
    text = (
        f"📊 <b>Статистика бота</b>\n\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🔗 Переходов по ссылкам: <b>{link_visits}</b>\n"
        f"📜 Создано ссылок: <b>{total_links}</b>\n"
        f"⏳ Время работы: <b>{int((time.time() - start_time) / 3600)} часов</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_reports")
async def section_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="📩 <b>Список репортов</b>",
        reply_markup=await create_reports_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("no_checked_reports_"))
async def no_checked_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    page = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text="📩 <b>Непросмотренные репорты</b>",
        reply_markup=await create_reports_keyboard(page=page, checked=False)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("all_reports_"))
async def all_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    page = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text="📩 <b>Все репорты</b>",
        reply_markup=await create_reports_keyboard(page=page, checked=True)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("report_info_"))
async def report_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    report_id = callback.data.split("_")[2]
    report = await execute_with_retry_async("SELECT user_id, message, created_at FROM reports WHERE report_id = ?", (report_id,))
    if not report:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Репорт не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    reporter_id, message_text, created_at = report[0]
    user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)) else f"Пользователь {reporter_id}"
    date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    
    await execute_with_retry_async("UPDATE reports SET is_checked = 1 WHERE report_id = ?", (report_id,))
    text = (
        f"📩 <b>Информация о репорте</b>\n\n"
        f"🆔 ID репорта: <b>{report_id}</b>\n"
        f"👤 Пользователь: <b>{user_name}</b> (ID: {reporter_id})\n"
        f"📅 Дата: <b>{date}</b>\n"
        f"💬 Сообщение: <b>{message_text}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_report_actions_keyboard(report_id, reporter_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_report_"))
async def delete_report(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    report_id = callback.data.split("_")[2]
    await execute_with_retry_async("DELETE FROM reports WHERE report_id = ?", (report_id,))
    await edit_message_if_changed(
        callback=callback,
        text="🗑 Репорт удален.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_users")
async def section_users(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="👥 <b>Управление пользователями</b>",
        reply_markup=await create_users_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "search_user")
async def search_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_search_user"})
    await edit_message_if_changed(
        callback=callback,
        text="🔎 <b>Поиск пользователя</b>\n\nВведите ID пользователя или @username:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("banned_users_"))
async def banned_users(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    page = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text="🚫 <b>Забаненные пользователи</b>",
        reply_markup=await create_banned_users_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("banned_user_info_"))
async def banned_user_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    banned_user_id = int(callback.data.split("_")[-1])
    ban_info = await execute_with_retry_async("SELECT admin_id, reason, banned_at FROM bans WHERE user_id = ?", (banned_user_id,))
    if not ban_info:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Информация о бане не найдена.",
            reply_markup=await create_back_keyboard()
        )
        return
    admin_id, reason, banned_at = ban_info[0]
    user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (banned_user_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (banned_user_id,)) else f"Пользователь {banned_user_id}"
    admin_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)) else f"Админ {admin_id}"
    date = datetime.fromtimestamp(banned_at).strftime("%d.%m.%Y %H:%M")
    
    text = (
        f"🚫 <b>Информация о бане</b>\n\n"
        f"👤 Пользователь: <b>{user_name}</b> (ID: {banned_user_id})\n"
        f"👑 Админ: <b>{admin_name}</b> (ID: {admin_id})\n"
        f"📅 Дата: <b>{date}</b>\n"
        f"💬 Причина: <b>{reason}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(banned_user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "section_links")
async def section_links(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="🔗 <b>Управление ссылками</b>",
        reply_markup=await create_links_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "create_link")
async def create_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_link_content"})
    await edit_message_if_changed(
        callback=callback,
        text="🔗 <b>Создание ссылки</b>\n\nОтправьте контент (текст, фото или файл до 2 МБ):",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "delete_link")
async def delete_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="🗑 <b>Удаление ссылки</b>",
        reply_markup=await create_links_list_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("list_links_"))
async def list_links(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    page = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text="📜 <b>Список ссылок</b>",
        reply_markup=await create_links_list_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("link_info_"))
async def link_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    link_id = callback.data.split("_")[2]
    link = await execute_with_retry_async("SELECT creator_id, content_type, content_data, caption, created_at, visits FROM links WHERE link_id = ?", (link_id,))
    if not link:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ссылка не найдена.",
            reply_markup=await create_back_keyboard()
        )
        return
    creator_id, content_type, content_data, caption, created_at, visits = link[0]
    creator_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)) else f"Админ {creator_id}"
    date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    bot_username = (await bot.get_me()).username
    link_url = f"https://t.me/{bot_username}?start={link_id}"
    
    text = (
        f"🔗 <b>Информация о ссылке</b>\n\n"
        f"🆔 ID ссылки: <b>{link_id}</b>\n"
        f"👤 Создатель: <b>{creator_name}</b> (ID: {creator_id})\n"
        f"📅 Дата создания: <b>{date}</b>\n"
        f"📊 Переходов: <b>{visits}</b>\n"
        f"📋 Тип контента: <b>{content_type}</b>\n"
        f"🔗 Ссылка: {link_url}\n"
        f"💬 Подпись: <b>{caption or 'Нет'}</b>\n"
        f"📄 Контент: <b>{content_data}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_link_actions_keyboard(link_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_delete_"))
async def confirm_delete_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    link_id = callback.data.split("_")[2]
    await edit_message_if_changed(
        callback=callback,
        text=f"🗑 <b>Подтвердите удаление ссылки {link_id}</b>",
        reply_markup=await create_confirm_delete_keyboard(link_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_link_"))
async def delete_link_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    link_id = callback.data.split("_")[2]
    await execute_with_retry_async("DELETE FROM links WHERE link_id = ?", (link_id,))
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    await update_stats(total_links=total_links)
    
    await edit_message_if_changed(
        callback=callback,
        text="🗑 Ссылка удалена.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_ads")
async def section_ads(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="📢 <b>Управление рекламой</b>",
        reply_markup=await create_ads_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_channel")
async def add_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_channel_id"})
    await edit_message_if_changed(
        callback=callback,
        text="📢 <b>Добавление канала</b>\n\nВведите ID канала (@username или -100123456789):",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("scope_"))
async def set_channel_scope(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    state = await dp.storage.get_data(user=user_id)
    if state.get("state") != "awaiting_channel_scope":
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Неверное состояние.",
            reply_markup=await create_back_keyboard()
        )
        return
    channel_id = state.get("channel_id")
    check_scope = "links_only" if callback.data == "scope_links_only" else "all_functions"
    
    title = channel_id if channel_id.startswith("@") else f"Канал {channel_id}"
    await execute_with_retry_async(
        "INSERT INTO channels (channel_id, title, check_scope) VALUES (?, ?, ?)",
        (channel_id, title, check_scope)
    )
    await dp.storage.set_data(user=user_id, data={})
    await edit_message_if_changed(
        callback=callback,
        text=f"📢 Канал {title} добавлен с проверкой: {await scope_text(check_scope)}",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "remove_channel")
async def remove_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="📢 <b>Удаление канала</b>",
        reply_markup=await create_channels_keyboard(action="confirm_remove_channel")
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_remove_channel_"))
async def confirm_remove_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    channel_id = callback.data.split("_")[-1]
    title = (await execute_with_retry_async("SELECT title FROM channels WHERE channel_id = ?", (channel_id,)))[0][0] if await execute_with_retry_async("SELECT title FROM channels WHERE channel_id = ?", (channel_id,)) else channel_id
    await edit_message_if_changed(
        callback=callback,
        text=f"📢 <b>Подтвердите удаление канала {title}</b>",
        reply_markup=await create_confirm_channel_delete_keyboard(channel_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_channel_"))
async def delete_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    channel_id = callback.data.split("_")[2]
    title = (await execute_with_retry_async("SELECT title FROM channels WHERE channel_id = ?", (channel_id,)))[0][0] if await execute_with_retry_async("SELECT title FROM channels WHERE channel_id = ?", (channel_id,)) else channel_id
    await execute_with_retry_async("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
    await edit_message_if_changed(
        callback=callback,
        text=f"📢 Канал {title} удален.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "channel_list")
async def channel_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    channels = await execute_with_retry_async("SELECT channel_id, title, check_scope FROM channels")
    if not channels:
        await edit_message_if_changed(
            callback=callback,
            text="📢 <b>Список каналов</b>\n\nНет добавленных каналов.",
            reply_markup=await create_back_keyboard()
        )
        return
    text = "📢 <b>Список каналов</b>\n\n"
    for channel_id, title, check_scope in channels:
        text += f"👉 {title} ({await scope_text(check_scope)})\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_admins")
async def section_admins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="👑 <b>Управление администраторами</b>",
        reply_markup=await create_admins_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def add_admin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_admin_id"})
    await edit_message_if_changed(
        callback=callback,
        text="👑 <b>Добавление администратора</b>\n\nВведите ID пользователя или @username:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("remove_admin_"))
async def remove_admin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    admin_id = int(callback.data.split("_")[-1])
    if admin_id == user_id:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Вы не можете удалить самого себя.",
            reply_markup=await create_back_keyboard()
        )
        return
    if admin_id in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Нельзя удалить разработчика из администраторов.",
            reply_markup=await create_back_keyboard()
        )
        return
    await execute_with_retry_async("DELETE FROM admins WHERE user_id = ?", (admin_id,))
    await send_message_safe(
        bot=bot,
        chat_id=admin_id,
        text="👑 Вы были удалены из администраторов бота Mirrozz Scripts."
    )
    admin_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)) else f"Админ {admin_id}"
    await edit_message_if_changed(
        callback=callback,
        text=f"👑 {admin_name} (ID: {admin_id}) удален из администраторов.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("list_admins_"))
async def list_admins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    page = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text="👑 <b>Список администраторов</b>",
        reply_markup=await create_admins_list_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("admin_info_"))
async def admin_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    admin_id = int(callback.data.split("_")[-1])
    admin = await execute_with_retry_async("SELECT first_name, username FROM users WHERE user_id = ?", (admin_id,))
    if not admin:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Администратор не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    first_name, username = admin[0]
    text = (
        f"👑 <b>Информация об администраторе</b>\n\n"
        f"👤 Имя: <b>{first_name}</b>\n"
        f"🔗 Username: <b>@{username if username else 'Нет'}</b>\n"
        f"🆔 ID: <b>{admin_id}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_admin_actions_keyboard(admin_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("send_message_"))
async def send_message_to_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    target_user_id = int(callback.data.split("_")[-1])
    await dp.storage.set_data(user=user_id, data={"state": f"awaiting_message_{target_user_id}"})
    await edit_message_if_changed(
        callback=callback,
        text="📩 <b>Отправка сообщения</b>\n\nВведите текст сообщения:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("ban_user_"))
async def ban_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    target_user_id = int(callback.data.split("_")[-1])
    if target_user_id == user_id:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Вы не можете забанить самого себя.",
            reply_markup=await create_back_keyboard()
        )
        return
    if target_user_id in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Нельзя забанить разработчика.",
            reply_markup=await create_back_keyboard()
        )
        return
    await dp.storage.set_data(user=user_id, data={"state": f"awaiting_ban_reason_{target_user_id}"})
    await edit_message_if_changed(
        callback=callback,
        text="🚫 <b>Блокировка пользователя</b>\n\nВведите причину бана:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("unban_user_"))
async def unban_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    target_user_id = int(callback.data.split("_")[-1])
    await execute_with_retry_async("DELETE FROM bans WHERE user_id = ?", (target_user_id,))
    await execute_with_retry_async("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_user_id,))
    await send_message_safe(
        bot=bot,
        chat_id=target_user_id,
        text="✅ Вы были разблокированы в боте."
    )
    user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (target_user_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (target_user_id,)) else f"Пользователь {target_user_id}"
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ {user_name} (ID: {target_user_id}) разблокирован.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("user_info_"))
async def user_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    target_user_id = int(callback.data.split("_")[-1])
    user = await execute_with_retry_async("SELECT first_name, username, join_date, last_activity FROM users WHERE user_id = ?", (target_user_id,))
    if not user:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Пользователь не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    first_name, username, join_date, last_activity = user[0]
    visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links WHERE creator_id = ?", (target_user_id,)))[0][0] or 0
    join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
    last_activity_str = datetime.fromtimestamp(last_activity).strftime("%d.%m.%Y %H:%M") if last_activity else "Нет данных"
    
    text = (
        f"ℹ️ <b>Информация о пользователе</b>\n\n"
        f"👤 Имя: <b>{first_name}</b>\n"
        f"🔗 Username: <b>@{username if username else 'Нет'}</b>\n"
        f"🆔 ID: <b>{target_user_id}</b>\n"
        f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
        f"🕒 Последняя активность: <b>{last_activity_str}</b>\n"
        f"🔗 Переходов по созданным ссылкам: <b>{visits}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(target_user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "admin_developer")
async def admin_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="🛠 <b>Панель разработчика</b>",
        reply_markup=await create_developer_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "developer_server")
async def developer_server(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="🖥 <b>Управление сервером</b>",
        reply_markup=await create_developer_server_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "disable_bot")
async def disable_bot(callback: types.CallbackQuery):
    global BOT_ENABLED
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    cache_key = f"{user_id}_{callback.id}"
    if cache_key in callback_cache:
        await callback.answer("⚠️ Пожалуйста, подождите перед повторным действием.")
        return
    callback_cache[cache_key] = True
    
    BOT_ENABLED = False
    await edit_message_if_changed(
        callback=callback,
        text="🔴 Бот отключен.",
        reply_markup=await create_developer_server_keyboard()
    )
    logger.info(f"Bot disabled by developer {user_id}")

@dp.callback_query_handler(lambda c: c.data == "enable_bot")
async def enable_bot(callback: types.CallbackQuery):
    global BOT_ENABLED
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    cache_key = f"{user_id}_{callback.id}"
    if cache_key in callback_cache:
        await callback.answer("⚠️ Пожалуйста, подождите перед повторным действием.")
        return
    callback_cache[cache_key] = True
    
    BOT_ENABLED = True
    await edit_message_if_changed(
        callback=callback,
        text="🟢 Бот включен.",
        reply_markup=await create_developer_server_keyboard()
    )
    logger.info(f"Bot enabled by developer {user_id}")

@dp.callback_query_handler(lambda c: c.data == "emergency_shutdown")
async def emergency_shutdown(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    cache_key = f"{user_id}_{callback.id}"
    if cache_key in callback_cache:
        await callback.answer("⚠️ Пожалуйста, подождите перед повторным действием.")
        return
    callback_cache[cache_key] = True
    
    await edit_message_if_changed(
        callback=callback,
        text="🛑 Бот завершает работу...",
        reply_markup=None
    )
    logger.info(f"Emergency shutdown initiated by developer {user_id}")
    await bot.close()
    sys.exit(0)

@dp.callback_query_handler(lambda c: c.data == "restart_bot")
async def restart_bot(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    cache_key = f"{user_id}_{callback.id}"
    if cache_key in callback_cache:
        await callback.answer("⚠️ Пожалуйста, подождите перед повторным действием.")
        return
    callback_cache[cache_key] = True
    
    await edit_message_if_changed(
        callback=callback,
        text="🔄 Бот перезапускается...",
        reply_markup=None
    )
    logger.info(f"Bot restart initiated by developer {user_id}")
    await bot.close()
    os.execv(sys.executable, ['python'] + sys.argv)

@dp.callback_query_handler(lambda c: c.data == "server_status")
async def server_status(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    total_users, link_visits, total_links = await read_stats()
    uptime_hours = int((time.time() - start_time) / 3600)
    bot_status = "🟢 Включен" if BOT_ENABLED else "🔴 Отключен"
    
    text = (
        f"🖥 <b>Статус сервера</b>\n\n"
        f"📅 Текущее время: <b>{datetime.now().strftime('%d.%m.%Y %H:%M:%S')}</b>\n"
        f"⏳ Время работы: <b>{uptime_hours} часов</b>\n"
        f"🔄 Статус бота: <b>{bot_status}</b>\n"
        f"👥 Активных пользователей: <b>{total_users}</b>\n"
        f"🔗 Переходов по ссылкам: <b>{link_visits}</b>\n"
        f"📜 Всего ссылок: <b>{total_links}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developer_server_keyboard()
    )
    logger.info(f"Server status checked by developer {user_id}")

@dp.callback_query_handler(lambda c: c.data == "developer_database")
async def developer_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="🗄 <b>Управление базой данных</b>",
        reply_markup=await create_developer_database_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "download_database")
async def download_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    try:
        async with aiohttp.ClientSession() as session:
            with open(DATABASE_FILE, "rb") as f:
                await bot.send_document(
                    chat_id=callback.message.chat.id,
                    document=types.InputFile(f, filename="bot_mirrozz_database.db"),
                    caption="📥 База данных"
                )
        await edit_message_if_changed(
            callback=callback,
            text="📥 База данных отправлена.",
            reply_markup=await create_back_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to download database for user {user_id}: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Не удалось отправить базу данных.",
            reply_markup=await create_back_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "upload_database")
async def upload_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_database_upload"})
    await edit_message_if_changed(
        callback=callback,
        text="📤 <b>Загрузка базы данных</b>\n\nОтправьте файл базы данных (.db):",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "reset_database")
async def reset_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    cache_key = f"{user_id}_{callback.id}"
    if cache_key in callback_cache:
        await callback.answer("⚠️ Пожалуйста, подождите перед повторным действием.")
        return
    callback_cache[cache_key] = True
    
    try:
        os.remove(DATABASE_FILE)
        await init_database()
        await edit_message_if_changed(
            callback=callback,
            text="🗑 База данных сброшена и инициализирована заново.",
            reply_markup=await create_back_keyboard()
        )
        logger.info(f"Database reset by developer {user_id}")
    except Exception as e:
        logger.error(f"Failed to reset database for user {user_id}: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Не удалось сбросить базу данных.",
            reply_markup=await create_back_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "developer_messages")
async def developer_messages(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="📢 <b>Управление системными сообщениями</b>",
        reply_markup=await create_developer_messages_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "send_system_message")
async def send_system_message(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_system_message"})
    await edit_message_if_changed(
        callback=callback,
        text="📩 <b>Отправка системного сообщения</b>\n\nВведите текст сообщения:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "message_history")
async def message_history(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    messages = await execute_with_retry_async("SELECT message_id, message_text, sent_at, sender_id FROM system_messages ORDER BY sent_at DESC LIMIT 5")
    if not messages:
        await edit_message_if_changed(
            callback=callback,
            text="📜 <b>История системных сообщений</b>\n\nНет отправленных сообщений.",
            reply_markup=await create_back_keyboard()
        )
        return
    text = "📜 <b>История системных сообщений</b>\n\n"
    for message_id, message_text, sent_at, sender_id in messages:
        sender_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)) else f"Админ {sender_id}"
        date = datetime.fromtimestamp(sent_at).strftime("%d.%m.%Y %H:%M")
        text += f"📩 {date} от {sender_name}: {message_text}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "developer_management")
async def developer_management(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="👨‍💻 <b>Управление разработчиками</b>",
        reply_markup=await create_developers_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_developer")
async def add_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={"state": "awaiting_developer_id"})
    await edit_message_if_changed(
        callback=callback,
        text="👨‍💻 <b>Добавление разработчика</b>\n\nВведите ID пользователя или @username:",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "remove_developer")
async def remove_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    developers = await execute_with_retry_async("SELECT user_id, username FROM developers")
    if not developers:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Нет добавленных разработчиков.",
            reply_markup=await create_back_keyboard()
        )
        return
    keyboard = InlineKeyboardMarkup(row_width=1)
    for dev_id, username in developers:
        dev_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (dev_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (dev_id,)) else f"Пользователь {dev_id}"
        keyboard.add(InlineKeyboardButton(text=f"🗑 {dev_name} (@{username or 'Нет'})", callback_data=f"confirm_remove_developer_{dev_id}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_management"))
    await edit_message_if_changed(
        callback=callback,
        text="🗑 <b>Удаление разработчика</b>",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_remove_developer_"))
async def confirm_remove_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    developer_id = int(callback.data.split("_")[-1])
    if developer_id == user_id:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Вы не можете удалить самого себя.",
            reply_markup=await create_back_keyboard()
        )
        return
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delete_developer_{developer_id}"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data="developer_management")
    )
    dev_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (developer_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (developer_id,)) else f"Пользователь {developer_id}"
    await edit_message_if_changed(
        callback=callback,
        text=f"🗑 <b>Подтвердите удаление разработчика {dev_name} (ID: {developer_id})</b>",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_developer_"))
async def delete_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    developer_id = int(callback.data.split("_")[-1])
    await execute_with_retry_async("DELETE FROM developers WHERE user_id = ?", (developer_id,))
    DEVELOPER_IDS.discard(developer_id)
    dev_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (developer_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (developer_id,)) else f"Пользователь {developer_id}"
    await send_message_safe(
        bot=bot,
        chat_id=developer_id,
        text="👨‍💻 Вы были удалены из разработчиков бота Mirrozz Scripts."
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"🗑 {dev_name} (ID: {developer_id}) удален из разработчиков.",
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "list_developers")
async def list_developers(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав разработчика.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    developers = await execute_with_retry_async("SELECT user_id, username, added_at FROM developers")
    if not developers:
        await edit_message_if_changed(
            callback=callback,
            text="📜 <b>Список разработчиков</b>\n\nНет добавленных разработчиков.",
            reply_markup=await create_back_keyboard()
        )
        return
    text = "📜 <b>Список разработчиков</b>\n\n"
    for dev_id, username, added_at in developers:
        dev_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (dev_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (dev_id,)) else f"Пользователь {dev_id}"
        text += f"👨‍💻 {dev_name} (@{username or 'Нет'}, ID: {dev_id}, добавлен: {added_at})\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={})
    await edit_message_if_changed(
        callback=callback,
        text="🛠 <b>Панель администратора</b>\n\nВыберите раздел:",
        reply_markup=await create_admin_main_keyboard(user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "user_back_to_start")
async def user_back_to_start(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await dp.storage.set_data(user=user_id, data={})
    await handle_start(callback.message)

async def create_developer_server_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🟢 Включить бота" if not BOT_ENABLED else "🔴 Отключить бота", callback_data="enable_bot" if not BOT_ENABLED else "disable_bot"),
        InlineKeyboardButton(text="🛑 Сброс", callback_data="emergency_shutdown"),
        InlineKeyboardButton(text="🔄 Перезагрузить", callback_data="restart_bot"),
        InlineKeyboardButton(text="📊 Статус", callback_data="server_status"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer")
    )
    return keyboard

async def on_startup(_):
    await init_database()
    logger.info("Bot started")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)