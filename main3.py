import asyncio
import logging
import sqlite3
import json
import aiosqlite
from datetime import datetime
from uuid import uuid4
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher.filters import Command, RegexpCommandsFilter
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.utils.markdown import hcode
from aiogram.utils.exceptions import BotBlocked, InvalidQueryID, MessageNotModified, NetworkError
import time
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
from cachetools import TTLCache
from telegram.helpers import escape_md
import sys
import os
import shutil

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.propagate = False

# Конфигурация бота
TOKEN = "8178374718:AAHvyoBH5Ty2VKwNyfdWeOez9XLSflNQtaM"
SUBGRAM_API_KEY = "8a1994b006b02e4e126dae69f8ce9832f87d005a77480d0a40854c4b592947ad"
SUBGRAM_API_URL = "https://api.subgram.ru/request-op/"
SUBGRAM_MAX_OP = 5
ADMINS = [7057452528, 7236484299]
DEVELOPER_IDS = {7057452528}
DATABASE_FILE = "bot_mirrozz_database.db"
BOT_ENABLED = True
start_time = time.time()

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Кэши
subgram_cache = TTLCache(maxsize=1000, ttl=300)
callback_cache = TTLCache(maxsize=1000, ttl=5)

# Состояния для FSM
class AdminStates(StatesGroup):
    RemoveDeveloper = State()
    SystemMessage = State()
    Report = State()

# Асинхронное выполнение SQL-запросов с обработкой блокировки базы данных
async def execute_with_retry_async(query, params=(), retries=5, delay=0.1):
    for attempt in range(retries):
        async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
            try:
                cursor = await conn.execute(query, params)
                if query.strip().upper().startswith("SELECT"):
                    result = await cursor.fetchall()
                else:
                    await conn.commit()
                    result = None
                return result
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"Database locked, retry {attempt + 1}/{retries}")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Database error: {str(e)}")
                    raise
            finally:
                await cursor.close()
    logger.error("Database is locked after maximum retries")
    raise sqlite3.OperationalError("Database is locked after maximum retries")

async def is_admin(user_id):
    result = await execute_with_retry_async("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return bool(result)

async def is_banned(user_id):
    result = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
    return bool(result)

async def send_message_safe(bot, chat_id, text, reply_markup=None, parse_mode="HTML"):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )
    except BotBlocked:
        logger.info(f"Cannot send message to {chat_id}: Bot is blocked by the user")
    except NetworkError as e:
        logger.error(f"Network error sending message to {chat_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Failed to send message to {chat_id}: {str(e)}")

async def edit_message_if_changed(callback, text, reply_markup):
    current_text = callback.message.text or ""
    current_markup = callback.message.reply_markup.inline_keyboard if callback.message.reply_markup else []
    new_markup = reply_markup.inline_keyboard if reply_markup else []

    if current_text == text and current_markup == new_markup:
        logger.debug("Skipping message edit: No changes detected")
        try:
            await callback.answer()
        except InvalidQueryID:
            logger.info(f"Callback query {callback.id} is too old or invalid")
        return

    serialized_markup = []
    if reply_markup:
        for row in reply_markup.inline_keyboard:
            serialized_row = []
            for button in row:
                button_dict = {
                    "text": button.text,
                    "callback_data": button.callback_data
                }
                if button.url:
                    button_dict["url"] = button.url
                serialized_row.append(button_dict)
            serialized_markup.append(serialized_row)

    try:
        async with RetryClient() as client:
            async with client.post(f"https://api.telegram.org/bot{TOKEN}/editMessageText", json={
                "chat_id": callback.message.chat.id,
                "message_id": callback.message.message_id,
                "text": text,
                "reply_markup": {"inline_keyboard": serialized_markup} if reply_markup else {},
                "parse_mode": "HTML"
            }) as response:
                if not (await response.json()).get('ok'):
                    logger.warning(f"Failed to edit message for callback {callback.data}")
    except MessageNotModified:
        logger.debug("Message edit skipped: Content not modified")
    except NetworkError as e:
        logger.error(f"Network error editing message for callback {callback.data}: {str(e)}")
    except Exception as e:
        logger.error(f"Error editing message for callback {callback.data}: {str(e)}")
    try:
        await callback.answer()
    except InvalidQueryID:
        logger.info(f"Callback query {callback.id} is too old or invalid")

async def check_subgram_subscription(user_id, chat_id, first_name=None, language_code="ru", is_premium=False, gender=None):
    cache_key = f"{user_id}_{gender or ''}"
    if cache_key in subgram_cache:
        logger.debug(f"SubGram: Using cached response for user {user_id}")
        return subgram_cache[cache_key]

    headers = {'Auth': SUBGRAM_API_KEY}
    data = {
        "UserId": str(user_id),
        "ChatId": str(chat_id),
        "MaxOP": SUBGRAM_MAX_OP,
        "action": "subscribe"
    }
    
    if first_name:
        data["first_name"] = first_name
    if language_code:
        data["language_code"] = language_code
    if is_premium is not None:
        data["Premium"] = is_premium
    if gender:
        data["Gender"] = gender

    retry_options = ExponentialRetry(attempts=5, start_timeout=1.0, factor=2.0)
    try:
        async with RetryClient() as client:
            logger.info(f"SubGram request data: {data}")
            async with client.post(SUBGRAM_API_URL, headers=headers, json=data, retry_options=retry_options) as response:
                result = await response.json()
                logger.info(f"SubGram response for user {user_id}: {result}")
                
                if response.status == 200:
                    if result.get('status') == 'ok':
                        logger.info(f"SubGram: Пользователь {user_id} подписан на все каналы")
                        await execute_with_retry_async(
                            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                            (json.dumps([]), int(time.time()), user_id)
                        )
                        subgram_cache[cache_key] = ('ok', None)
                        return 'ok', None
                    elif result.get('status') == 'warning':
                        links = result.get('links', [])
                        logger.info(f"SubGram: Пользователь {user_id} должен подписаться на {len(links)} каналов")
                        await execute_with_retry_async(
                            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                            (json.dumps(links), int(time.time()), user_id)
                        )
                        subgram_cache[cache_key] = ('need_subscribe', links)
                        return 'need_subscribe', links
                    elif result.get('status') == 'gender':
                        logger.info(f"SubGram: Для пользователя {user_id} требуется указать пол")
                        subgram_cache[cache_key] = ('need_gender', "Пожалуйста, укажите ваш пол для продолжения")
                        return 'need_gender', "Пожалуйста, укажите ваш пол для продолжения"
                    else:
                        logger.error(f"SubGram: Неизвестный статус ответа: {result}")
                        subgram_cache[cache_key] = ('ok', None)
                        await execute_with_retry_async(
                            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                            (json.dumps([]), int(time.time()), user_id)
                        )
                        return 'ok', None
                elif response.status == 404 and result.get('message') == 'Нет подходящих рекламодателей для данного пользователя':
                    logger.info(f"SubGram: Нет подходящих каналов для пользователя {user_id}")
                    await execute_with_retry_async(
                        "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                        (json.dumps([]), int(time.time()), user_id)
                    )
                    subgram_cache[cache_key] = ('ok', None)
                    return 'ok', None
                else:
                    logger.error(f"SubGram API error: HTTP {response.status}, {result}")
                    subgram_cache[cache_key] = ('ok', None)
                    await execute_with_retry_async(
                        "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                        (json.dumps([]), int(time.time()), user_id)
                    )
                    return 'ok', None
    except aiohttp.ClientConnectorError as e:
        logger.error(f"SubGram connection error: {str(e)}")
        subgram_cache[cache_key] = ('ok', None)
        await execute_with_retry_async(
            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
            (json.dumps([]), int(time.time()), user_id)
        )
        return 'ok', None
    except Exception as e:
        logger.error(f"SubGram API error for user {user_id}: {str(e)}")
        subgram_cache[cache_key] = ('ok', None)
        await execute_with_retry_async(
            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
            (json.dumps([]), int(time.time()), user_id)
        )
        return 'ok', None

async def init_database():
    if hasattr(init_database, "completed"):
        logger.debug("Database already initialized, skipping")
        return
    
    logger.info("Starting database initialization")
    queries = [
        '''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            join_date INTEGER,
            last_activity INTEGER,
            is_banned BOOLEAN DEFAULT 0
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS links (
            link_id TEXT PRIMARY KEY,
            content_type TEXT,
            content_data TEXT,
            caption TEXT,
            creator_id INTEGER,
            created_at INTEGER,
            visits INTEGER DEFAULT 0
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS reports (
            report_id TEXT PRIMARY KEY,
            user_id INTEGER,
            message TEXT,
            created_at INTEGER,
            is_checked BOOLEAN DEFAULT 0
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            title TEXT,
            check_scope TEXT DEFAULT 'links_only'
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS subscriptions (
            user_id INTEGER PRIMARY KEY,
            total_fixed_link INTEGER DEFAULT 0,
            subgram_links TEXT DEFAULT '[]',
            subgram_timestamp INTEGER,
            gender TEXT
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            total_users INTEGER DEFAULT 0,
            link_visits INTEGER DEFAULT 0,
            total_links INTEGER DEFAULT 0
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER PRIMARY KEY,
            admin_id INTEGER,
            reason TEXT,
            banned_at INTEGER
        )
        ''',
        '''
        CREATE TABLE IF NOT EXISTS developers (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            added_at TEXT
        )
        '''
    ]
    for query in queries:
        try:
            await execute_with_retry_async(query)
            logger.info(f"Executed query: {query.strip().splitlines()[0]}")
        except sqlite3.OperationalError as e:
            logger.error(f"Failed to execute query: {query.strip().splitlines()[0]}, Error: {str(e)}")
            raise
    
    await execute_with_retry_async("INSERT OR IGNORE INTO stats (id, total_users, link_visits, total_links) VALUES (1, 0, 0, 0)")
    for admin_id in ADMINS:
        await execute_with_retry_async("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
    for dev_id in DEVELOPER_IDS:
        await execute_with_retry_async(
            "INSERT OR IGNORE INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
            (dev_id, "Unknown", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
    
    async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
        cursor = await conn.execute("PRAGMA table_info(channels)")
        columns = [info[1] for info in await cursor.fetchall()]
        if 'check_scope' not in columns:
            logger.info("Adding check_scope column to channels table")
            await conn.execute("ALTER TABLE channels ADD COLUMN check_scope TEXT DEFAULT 'links_only'")
            await conn.commit()
    
    async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
        cursor = await conn.execute("PRAGMA table_info(subscriptions)")
        columns = [info[1] for info in await cursor.fetchall()]
        if 'subgram_links' not in columns:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN subgram_links TEXT DEFAULT '[]'")
            await conn.commit()
        if 'subgram_timestamp' not in columns:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN subgram_timestamp INTEGER")
            await conn.commit()
        if 'gender' not in columns:
            await conn.execute("ALTER TABLE subscriptions ADD COLUMN gender TEXT")
            await conn.commit()
    
    logger.info("Database initialization completed")
    init_database.completed = True

async def read_stats():
    result = await execute_with_retry_async("SELECT total_users, link_visits, total_links FROM stats WHERE id = 1")
    return result[0] if result else (0, 0, 0)

async def update_stats(total_users=None, link_visits=None, total_links=None):
    if total_users is not None:
        await execute_with_retry_async("UPDATE stats SET total_users = ? WHERE id = 1", (total_users,))
    if link_visits is not None:
        await execute_with_retry_async("UPDATE stats SET link_visits = ? WHERE id = 1", (link_visits,))
    if total_links is not None:
        await execute_with_retry_async("UPDATE stats SET total_links = ? WHERE id = 1", (total_links,))

async def add_user(user_id, first_name, username, join_date):
    await execute_with_retry_async(
        "INSERT OR REPLACE INTO users (user_id, first_name, username, join_date, last_activity) VALUES (?, ?, ?, ?, ?)",
        (user_id, first_name, username, join_date, join_date)
    )
    await execute_with_retry_async(
        "INSERT OR IGNORE INTO subscriptions (user_id, total_fixed_link, subgram_links) VALUES (?, 0, '[]')",
        (user_id,)
    )
    total_users = (await execute_with_retry_async("SELECT COUNT(*) FROM users"))[0][0]
    await update_stats(total_users=total_users)

async def update_user_activity(user_id):
    await execute_with_retry_async(
        "UPDATE users SET last_activity = ? WHERE user_id = ?",
        (int(datetime.now().timestamp()), user_id)
    )

async def update_user_subscription(user_id, total_fixed_link, subgram_links=None, gender=None):
    if subgram_links is not None:
        await execute_with_retry_async(
            "UPDATE subscriptions SET total_fixed_link = ?, subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
            (total_fixed_link, json.dumps(subgram_links), int(time.time()), user_id)
        )
    if gender is not None:
        await execute_with_retry_async(
            "UPDATE subscriptions SET gender = ? WHERE user_id = ?",
            (gender, user_id)
        )

async def check_subscriptions(user_id, check_all_functions=False):
    try:
        channels = await execute_with_retry_async("SELECT channel_id, title, check_scope FROM channels")
        unsubscribed = []
        async with RetryClient() as client:
            for channel_id, title, check_scope in channels:
                scope = check_scope if check_scope else 'links_only'
                if scope == 'all_functions' or (scope == 'links_only' and not check_all_functions):
                    try:
                        async with client.get(f"https://api.telegram.org/bot{TOKEN}/getChatMember?chat_id={channel_id}&user_id={user_id}") as response:
                            member = await response.json()
                            if not member.get('ok') or member.get('result', {}).get('status') not in ["member", "administrator", "creator"]:
                                unsubscribed.append(title)
                    except Exception as e:
                        logger.warning(f"Failed to check subscription for channel {channel_id}: {str(e)}")
                        unsubscribed.append(title)
        return unsubscribed
    except sqlite3.OperationalError as e:
        if "no such column: check_scope" in str(e):
            logger.warning("check_scope column missing, falling back to all channels")
            channels = await execute_with_retry_async("SELECT channel_id, title FROM channels")
            unsubscribed = []
            async with RetryClient() as client:
                for channel_id, title in channels:
                    try:
                        async with client.get(f"https://api.telegram.org/bot{TOKEN}/getChatMember?chat_id={channel_id}&user_id={user_id}") as response:
                            member = await response.json()
                            if not member.get('ok') or member.get('result', {}).get('status') not in ["member", "administrator", "creator"]:
                                unsubscribed.append(title)
                    except Exception as e:
                        logger.warning(f"Failed to check subscription for channel {channel_id}: {str(e)}")
                        unsubscribed.append(title)
            return unsubscribed
        else:
            raise

async def create_admin_main_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton(text="📩 Репорты", callback_data="section_reports"),
        InlineKeyboardButton(text="👥 Пользователи", callback_data="section_users"),
        InlineKeyboardButton(text="🔗 Ссылки", callback_data="section_links"),
        InlineKeyboardButton(text="📢 Реклама", callback_data="section_ads"),
        InlineKeyboardButton(text="👑 Админы", callback_data="section_admins")
    )
    if user_id in DEVELOPER_IDS:
        keyboard.add(InlineKeyboardButton(text="🛠 Разработчик", callback_data="admin_developer"))
    return keyboard

async def create_reports_keyboard(page=0, per_page=5, checked=False):
    reports = await execute_with_retry_async(
        "SELECT report_id, user_id, created_at FROM reports WHERE is_checked = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (0 if not checked else 1, per_page, page * per_page)
    )
    total_reports = (await execute_with_retry_async("SELECT COUNT(*) FROM reports WHERE is_checked = ?", (0 if not checked else 1,)))[0][0]
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for report_id, user_id, created_at in reports:
        user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)) else f"Пользователь {user_id}"
        date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
        keyboard.add(InlineKeyboardButton(text=f"📩 {date} by {user_name}", callback_data=f"report_info_{report_id}"))
    
    total_pages = (total_reports + per_page - 1) // per_page
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{'no_checked_reports' if not checked else 'all_reports'}_{page-1}"))
    if (page + 1) < total_pages:
        pagination.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{'no_checked_reports' if not checked else 'all_reports'}_{page+1}"))
    if pagination:
        keyboard.row(*pagination)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return keyboard

async def create_report_actions_keyboard(report_id, user_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🗑 Удалить репорт", callback_data=f"delete_report_{report_id}"),
        InlineKeyboardButton(text="📩 Ответить пользователю", callback_data=f"send_message_{user_id}"),
        InlineKeyboardButton(text="🚫 Заблокировать пользователя", callback_data=f"ban_user_{user_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="section_reports")
    )
    return keyboard

async def create_users_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🔎 Поиск пользователя", callback_data="search_user"),
        InlineKeyboardButton(text="🚫 Забаненные пользователи", callback_data="banned_users_0"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_links_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🔗 Создать ссылку", callback_data="create_link"),
        InlineKeyboardButton(text="🗑 Удалить ссылку", callback_data="delete_link"),
        InlineKeyboardButton(text="📜 Список ссылок", callback_data="list_links_0"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_ads_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="📢 Добавить канал", callback_data="add_channel"),
        InlineKeyboardButton(text="✖️ Удалить канал", callback_data="remove_channel"),
        InlineKeyboardButton(text="📋 Список каналов", callback_data="channel_list"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_admins_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="👑 Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton(text="🚫 Удалить админа", callback_data="remove_admin"),
        InlineKeyboardButton(text="📜 Список админов", callback_data="list_admins_0"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_developers_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="👨‍💻 Добавить разработчика", callback_data="add_developer"),
        InlineKeyboardButton(text="🗑 Удалить разработчика", callback_data="remove_developer"),
        InlineKeyboardButton(text="📜 Список разработчиков", callback_data="list_developers"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer")
    )
    return keyboard

async def create_channel_scope_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🔗 Только для ссылок", callback_data="scope_links_only"),
        InlineKeyboardButton(text="🌐 Для всех функций", callback_data="scope_all_functions"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return keyboard

async def create_user_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="user_back_to_start"))
    return keyboard

async def create_channels_keyboard(action="remove_channel"):
    channels = await execute_with_retry_async("SELECT channel_id, title FROM channels")
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for channel_id, title in channels:
        keyboard.add(InlineKeyboardButton(text=f"📢 {title}", callback_data=f"{action}_{channel_id}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return keyboard

async def create_confirm_channel_delete_keyboard(channel_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"confirm_remove_channel_{channel_id}"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data="channel_list")
    )
    return keyboard

async def create_links_list_keyboard(page=0, per_page=5):
    links = await execute_with_retry_async(
        "SELECT link_id, creator_id, created_at FROM links ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (per_page, page * per_page)
    )
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for link_id, creator_id, created_at in links:
        creator_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)) else f"Админ {creator_id}"
        date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
        keyboard.add(InlineKeyboardButton(text=f"🔗 {date} by {creator_name}", callback_data=f"link_info_{link_id}"))
    
    total_pages = (total_links + per_page - 1) // per_page
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list_links_{page-1}"))
    if (page + 1) < total_pages:
        pagination.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"list_links_{page+1}"))
    if pagination:
        keyboard.row(*pagination)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main"))
    return keyboard

async def create_admins_list_keyboard(page=0, per_page=5):
    admins = await execute_with_retry_async(
        "SELECT user_id FROM admins ORDER BY user_id LIMIT ? OFFSET ?",
        (per_page, page * per_page)
    )
    total_admins = (await execute_with_retry_async("SELECT COUNT(*) FROM admins"))[0][0]
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for (admin_id,) in admins:
        admin_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)) else f"Админ {admin_id}"
        keyboard.add(InlineKeyboardButton(text=f"👑 {admin_name} (ID: {admin_id})", callback_data=f"admin_info_{admin_id}"))
    
    total_pages = (total_admins + per_page - 1) // per_page
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list_admins_{page-1}"))
    if (page + 1) < total_pages:
        pagination.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"list_admins_{page+1}"))
    if pagination:
        keyboard.row(*pagination)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="section_admins"))
    return keyboard

async def create_admin_actions_keyboard(admin_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🚫 Удалить админа", callback_data=f"remove_admin_{admin_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="list_admins_0")
    )
    return keyboard

async def create_link_actions_keyboard(link_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="🗑 Удалить ссылку", callback_data=f"confirm_delete_{link_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="list_links_0")
    )
    return keyboard

async def create_confirm_delete_keyboard(link_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delete_link_{link_id}"),
        InlineKeyboardButton(text="❌ Нет, отмена", callback_data=f"link_info_{link_id}")
    )
    return keyboard

async def create_banned_users_keyboard(page=0, per_page=5):
    bans = await execute_with_retry_async(
        "SELECT user_id, admin_id, banned_at FROM bans ORDER BY banned_at DESC LIMIT ? OFFSET ?",
        (per_page, page * per_page)
    )
    total_bans = (await execute_with_retry_async("SELECT COUNT(*) FROM bans"))[0][0]
    
    keyboard = InlineKeyboardMarkup(row_width=1)
    for user_id, admin_id, banned_at in bans:
        user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)) else f"Пользователь {user_id}"
        date = datetime.fromtimestamp(banned_at).strftime("%d.%m.%Y %H:%M")
        keyboard.add(InlineKeyboardButton(text=f"🚫 {date} {user_name}", callback_data=f"banned_user_info_{user_id}"))
    
    total_pages = (total_bans + per_page - 1) // per_page
    pagination = []
    if page > 0:
        pagination.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"banned_users_{page-1}"))
    if (page + 1) < total_pages:
        pagination.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"banned_users_{page+1}"))
    if pagination:
        keyboard.row(*pagination)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="section_users"))
    return keyboard

async def create_user_actions_keyboard(user_id):
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="📩 Отправить сообщение", callback_data=f"send_message_{user_id}"),
        InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}"),
        InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_user_{user_id}"),
        InlineKeyboardButton(text="ℹ️ Информация", callback_data=f"user_info_{user_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="section_users")
    )
    return keyboard

async def create_gender_selection_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="♂ Мужской", callback_data="subgram_gender_male"),
        InlineKeyboardButton(text="♀ Женский", callback_data="subgram_gender_female"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="user_back_to_start")
    )
    return keyboard

async def create_developer_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🖥 Сервер", callback_data="developer_server"),
        InlineKeyboardButton(text="🗄 База данных", callback_data="developer_database"),
        InlineKeyboardButton(text="📢 Системные сообщения", callback_data="developer_messages"),
        InlineKeyboardButton(text="👨‍💻 Разработчики", callback_data="developer_management"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back_to_main")
    )
    return keyboard

async def create_developer_server_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🔴 Отключить" if BOT_ENABLED else "🟢 Включить", callback_data="disable_bot" if BOT_ENABLED else "enable_bot"),
        InlineKeyboardButton(text="🛑 Сброс", callback_data="emergency_shutdown"),
        InlineKeyboardButton(text="🔄 Перезагрузка", callback_data="restart_bot"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer")
    )
    return keyboard

async def create_developer_database_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📥 Скачать БД", callback_data="download_database"),
        InlineKeyboardButton(text="📤 Загрузить БД", callback_data="upload_database"),
        InlineKeyboardButton(text="🗑 Сбросить БД", callback_data="reset_database"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer")
    )
    return keyboard

async def create_developer_messages_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📩 Отправить сообщение", callback_data="send_system_message"),
        InlineKeyboardButton(text="📜 История сообщений", callback_data="message_history"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer")
    )
    return keyboard

async def scope_text(check_scope):
    return "Только для ссылок" if check_scope == "links_only" or check_scope is None else "Все функции"

@dp.message_handler(RegexpCommandsFilter(regexp_commands=['start (.+)']))
async def handle_start_deeplink(message: types.Message, regexp_command):
    if not BOT_ENABLED and message.from_user.id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    link_id = regexp_command.group(1)
    await handle_start(message, link_id)

@dp.message_handler(commands=["start"])
async def handle_start(message: types.Message, link_id=None):
    if not BOT_ENABLED and message.from_user.id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    username = message.from_user.username
    join_date = int(datetime.now().timestamp())
    
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    
    await add_user(user_id, first_name, username, join_date)
    
    check_all_functions = not link_id
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions)
    
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        callback_data = f"check_local_subscription_{link_id}" if link_id else "check_local_subscription_start"
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к {'скрипту' if link_id else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    
    status, result = await check_subgram_subscription(
        user_id=user_id,
        chat_id=message.chat.id,
        first_name=first_name,
        language_code=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        callback_data = f"check_subgram_subscription_{link_id}" if link_id else "check_subgram_subscription_start"
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы </b>\n\n"
                f"Для продолжения подпишитесь на каналы:\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'need_gender':
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
        )
        return
    
    if link_id:
        await handle_link_visit(message, link_id)
    else:
        welcome_text = (
            f"👋 <b>Привет, {first_name}!</b>\n\n"
            f"Я <b>Mirrozz Scripts</b> — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀\n\n"
            f"<b>Почему я лучший?</b>\n"
            f"• <b>Актуальные скрипты</b> — база обновляется регулярно!\n"
            f"• <b>Мгновенный доступ</b> — получай скрипты в пару кликов!\n"
            f"• <b>Надежное хранение</b> — твои скрипты всегда под рукой!\n"
            f"• <b>Стабильная работа</b> — бот на мощном сервере, без сбоев!\n\n"
            f"Напиши /help, чтобы узнать все команды!"
        )
        if await is_admin(user_id):
            welcome_text += "\n\n👑 Ты администратор! Используй /admin для доступа к панели управления."
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=welcome_text
        )

@dp.message_handler(commands=["help"])
async def handle_help(message: types.Message):
    if not BOT_ENABLED and message.from_user.id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    await update_user_activity(user_id)
    
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions=True)
    
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_local_subscription_help"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к функциям бота подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    
    status, result = await check_subgram_subscription(
        user_id=user_id,
        chat_id=message.chat.id,
        first_name=message.from_user.first_name,
        language_code=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_help"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Для продолжения подпишитесь на каналы:\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'need_gender':
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
        )
        return
    
    text = (
        f"📚 <b>Команды Mirrozz Scripts</b>\n\n"
        f"👋 /start — Запустить бота и получить приветствие\n"
        f"📊 /user_stats — Посмотреть статистику твоего профиля\n"
        f"📩 /report [сообщение] — Отправить жалобу админам\n"
    )
    if await is_admin(user_id):
        text += f"👑 /admin — Открыть панель администратора"
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=text
    )

@dp.message_handler(commands=["user_stats"])
async def handle_user_stats(message: types.Message):
    if not BOT_ENABLED and message.from_user.id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    await update_user_activity(user_id)
    
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions=True)
    
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_local_subscription_stats"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к функциям бота подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    
    status, result = await check_subgram_subscription(
        user_id=user_id,
        chat_id=message.chat.id,
        first_name=message.from_user.first_name,
        language_code=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_stats"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Для продолжения подпишитесь на каналы:\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'need_gender':
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
        )
        return
    
    user = await execute_with_retry_async("SELECT first_name, username, join_date FROM users WHERE user_id = ?", (user_id,))
    if not user:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пользователь не найден."
        )
        return
    first_name, username, join_date = user[0]
    visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links WHERE creator_id = ?", (user_id,)))[0][0] or 0
    
    join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
    text = (
        f"📊 <b>Статистика пользователя</b>\n\n"
        f"👤 Имя: <b>{first_name}</b>\n"
        f"🔗 Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
        f"🆔 ID: <b>{user_id}</b>\n"
        f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
        f"🔗 Переходов по созданным ссылкам: <b>{visits}</b>"
    )
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=text
    )

@dp.message_handler(commands=["report"])
async def handle_report(message: types.Message):
    if not BOT_ENABLED and message.from_user.id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    await update_user_activity(user_id)
    
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions=True)
    
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_local_subscription_report"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к функциям бота подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    
    status, result = await check_subgram_subscription(
        user_id=user_id,
        chat_id=message.chat.id,
        first_name=message.from_user.first_name,
        language_code=message.from_user.language_code,
        is_premium=message.from_user.is_premium
    )
    
    if status == 'need_subscribe':
        keyboard = InlineKeyboardMarkup()
        for link in result:
            channel_name = link.get("resource_name", "Channel")
            url = link.get("link", "#")
            button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
            keyboard.add(button)
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_report"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Для продолжения подпишитесь на каналы:\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'need_gender':
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
        )
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="📩 <b>Отправка жалобы</b>\n\nПожалуйста, укажите сообщение жалобы: /report [сообщение]"
        )
        return
    
    report_id = str(uuid4())
    message_text = args[1]
    created_at = int(datetime.now().timestamp())
    
    await execute_with_retry_async(
        "INSERT INTO reports (report_id, user_id, message, created_at, is_checked) VALUES (?, ?, ?, ?, ?)",
        (report_id, user_id, message_text, created_at, 0)
    )
    
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text="✅ Жалоба отправлена администрации."
    )
    async with RetryClient() as client:
        for admin_id in ADMINS:
            try:
                async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={
                    "chat_id": admin_id,
                    "text": f"📩 <b>Новая жалоба</b>\n\nID: {report_id}\nПользователь: {message.from_user.first_name} (@{escape_md(message.from_user.username) if message.from_user.username else 'Нет'})\nСообщение: {message_text}",
                    "parse_mode": "HTML"
                }) as response:
                    if not (await response.json()).get('ok'):
                        logger.warning(f"Failed to send report notification to admin {admin_id}")
            except Exception as e:
                logger.warning(f"Failed to send report notification to admin {admin_id}: {str(e)}")

@dp.message_handler(commands=["admin"])
async def handle_admin(message: types.Message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    await update_user_activity(user_id)
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        return
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text="🛠 <b>Панель администратора</b>\n\nВыберите раздел:",
        reply_markup=await create_admin_main_keyboard(user_id)
    )

@dp.message_handler(content_types=['text', 'photo', 'document'])
async def handle_content(message: types.Message):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    await update_user_activity(user_id)
    
    state = await dp.storage.get_data(user=user_id)
    state_value = state.get("state")
    
    if state_value == "awaiting_link_content" and await is_admin(user_id):
        link_id = str(uuid4())[:8]
        created_at = int(datetime.now().timestamp())
        
        if message.content_type == "text":
            content_type = "text"
            content_data = message.text.replace("'", "''")
            caption = None
        elif message.content_type == "photo":
            content_type = "photo"
            content_data = message.photo[-1].file_id
            caption = message.caption.replace("'", "''") if message.caption else ""
        elif message.content_type == "document":
            if message.document.file_size > 2 * 1024 * 1024:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Файл слишком большой (макс. 2 МБ)."
                )
                return
            content_type = "document"
            content_data = message.document.file_id
            caption = message.caption.replace("'", "''") if message.caption else ""
        else:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Этот тип контента не поддерживается."
            )
            return
        
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (link_id, content_type, content_data, caption, user_id, created_at)
        )
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        
        bot_username = (await bot.get_me()).username
        link = f"https://t.me/{bot_username}?start={link_id}"
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="section_links"))
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"🔗 <b>Ссылка создана!</b>\n\nВаша ссылка: {hcode(link)}",
            reply_markup=keyboard
        )
        await dp.storage.set_data(user=user_id, data={})
    
    elif state_value == "awaiting_admin_id" and await is_admin(user_id):
        text = message.text.strip()
        try:
            if text.startswith("@"):
                result = await execute_with_retry_async("SELECT user_id FROM users WHERE username = ?", (text[1:],))
                if not result:
                    await send_message_safe(
                        bot=bot,
                        chat_id=message.chat.id,
                        text="⚠️ Пользователь не найден."
                    )
                    return
                new_admin_id = result[0][0]
            else:
                new_admin_id = int(text)
            
            if await execute_with_retry_async("SELECT 1 FROM admins WHERE user_id = ?", (new_admin_id,)):
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь уже администратор."
                )
                return
            await execute_with_retry_async("INSERT INTO admins (user_id) VALUES (?)", (new_admin_id,))
            
            user_info = await execute_with_retry_async("SELECT first_name, username FROM users WHERE user_id = ?", (new_admin_id,))
            user_name = user_info[0][0] if user_info else f"Пользователь {new_admin_id}"
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ {user_name} (ID: {new_admin_id}) теперь администратор.",
                reply_markup=await create_back_keyboard()
            )
            await send_message_safe(
                bot=bot,
                chat_id=new_admin_id,
                text="👑 Вы назначены администратором бота Mirrozz Scripts! Используйте /admin для доступа к панели управления."
            )
            await dp.storage.set_data(user=user_id, data={})
        
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат. Укажите ID или @username."
            )
    
    elif state_value == "awaiting_developer_id" and user_id in DEVELOPER_IDS:
        text = message.text.strip()
        try:
            if text.startswith("@"):
                result = await execute_with_retry_async("SELECT user_id FROM users WHERE username = ?", (text[1:],))
                if not result:
                    await send_message_safe(
                        bot=bot,
                        chat_id=message.chat.id,
                        text="⚠️ Пользователь не найден."
                    )
                    return
                new_developer_id = result[0][0]
            else:
                new_developer_id = int(text)
            
            if new_developer_id in DEVELOPER_IDS:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь уже разработчик."
                )
                return
            username = (await execute_with_retry_async("SELECT username FROM users WHERE user_id = ?", (new_developer_id,)))[0][0] if await execute_with_retry_async("SELECT username FROM users WHERE user_id = ?", (new_developer_id,)) else "Unknown"
            added_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            await execute_with_retry_async(
                "INSERT INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
                (new_developer_id, username, added_at)
            )
            DEVELOPER_IDS.add(new_developer_id)
            user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (new_developer_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (new_developer_id,)) else f"Пользователь {new_developer_id}"
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ {user_name} (ID: {new_developer_id}) добавлен как разработчик.",
                reply_markup=await create_back_keyboard()
            )
            await send_message_safe(
                bot=bot,
                chat_id=new_developer_id,
                text="👨‍💻 Вы назначены разработчиком бота Mirrozz Scripts! Используйте /admin для доступа к панели разработчика."
            )
            await dp.storage.set_data(user=user_id, data={})
        
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат. Укажите ID или @username."
            )
    
    elif state_value == "awaiting_system_message" and user_id in DEVELOPER_IDS:
        message_text = message.text.strip()
        await execute_with_retry_async(
            "INSERT INTO system_messages (message_id, message_text, sent_at, sender_id) VALUES (?, ?, ?, ?)",
            (str(uuid4()), message_text, int(time.time()), user_id)
        )
        users = await execute_with_retry_async("SELECT user_id FROM users WHERE is_banned = 0")
        sent_count = 0
        async with RetryClient() as client:
            for (target_user_id,) in users:
                try:
                    async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={
                        "chat_id": target_user_id,
                        "text": message_text,
                        "parse_mode": "HTML"
                    }) as response:
                        if (await response.json()).get('ok'):
                            sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to send system message to {target_user_id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Системное сообщение отправлено {sent_count} пользователям.",
            reply_markup=await create_back_keyboard()
        )
        await dp.storage.set_data(user=user_id, data={})
    
    elif state_value == "awaiting_database_upload" and user_id in DEVELOPER_IDS:
        if message.content_type != "document" or not message.document.file_name.endswith(".db"):
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пожалуйста, загрузите файл базы данных (.db).",
                reply_markup=await create_back_keyboard()
            )
            return
        try:
            file_info = await bot.get_file(message.document.file_id)
            file_path = file_info.file_path
            async with aiohttp.ClientSession() as session:
                async with session.get(f"https://api.telegram.org/file/bot{TOKEN}/{file_path}") as response:
                    if response.status != 200:
                        await send_message_safe(
                            bot=bot,
                            chat_id=message.chat.id,
                            text="⚠️ Не удалось скачать файл базы данных.",
                            reply_markup=await create_back_keyboard()
                        )
                        return
                    temp_file = f"temp_{message.document.file_id}.db"
                    with open(temp_file, "wb") as f:
                        f.write(await response.read())
            
            async with aiosqlite.connect(temp_file, timeout=10) as conn:
                cursor = await conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = set(row[0] for row in await cursor.fetchall())
            required_tables = {'users', 'links', 'reports', 'channels', 'subscriptions', 'stats', 'admins', 'bans', 'developers'}
            if not required_tables.issubset(tables):
                os.remove(temp_file)
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Неверная структура базы данных.",
                    reply_markup=await create_back_keyboard()
                )
                return
            
            shutil.copy(temp_file, DATABASE_FILE)
            os.remove(temp_file)
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="✅ База данных успешно загружена.",
                reply_markup=await create_back_keyboard()
            )
            await dp.storage.set_data(user=user_id, data={})
        
        except Exception as e:
            logger.error(f"Database upload error: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при загрузке базы данных: {str(e)}",
                reply_markup=await create_back_keyboard()
            )
            await dp.storage.set_data(user=user_id, data={})
    
    elif state_value == "awaiting_channel_id" and await is_admin(user_id):
        channel_info = message.text.strip()
        if channel_info.startswith("@") or (channel_info.startswith("-100") and channel_info[4:].isdigit()):
            if await execute_with_retry_async("SELECT 1 FROM channels WHERE channel_id = ?", (channel_info,)):
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот канал уже добавлен."
                )
                return
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_channel_scope", "channel_id": channel_info})
            title = channel_info if channel_info.startswith("@") else f"Канал {channel_info}"
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"📢 <b>Добавление канала {title}</b>\n\nВыберите, когда требуется проверка подписки:",
                reply_markup=await create_channel_scope_keyboard()
            )
        else:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат. Используйте @username или -100123456789."
            )
    
    elif state_value == "awaiting_search_user" and await is_admin(user_id):
        query = message.text.strip()
        if query.startswith("@"):
            user = await execute_with_retry_async("SELECT user_id, first_name, username FROM users WHERE username = ?", (query[1:],))
        else:
            try:
                user_id_query = int(query)
                user = await execute_with_retry_async("SELECT user_id, first_name, username FROM users WHERE user_id = ?", (user_id_query,))
            except ValueError:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Неверный формат. Укажите ID или @username."
                )
                return
        if not user:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не найден."
            )
            return
        user_id, first_name, username = user[0]
        text = (
            f"ℹ️ <b>Пользователь найден</b>\n\n"
            f"👤 Имя: <b>{first_name}</b>\n"
            f"🔗 Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
            f"🆔 ID: <b>{user_id}</b>"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=text,
            reply_markup=await create_user_actions_keyboard(user_id)
        )
        await dp.storage.set_data(user=user_id, data={})
    
    elif state_value and state_value.startswith("awaiting_message_") and await is_admin(user_id):
        target_user_id = int(state_value.split("_")[2])
        try:
            async with RetryClient() as client:
                async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={
                    "chat_id": target_user_id,
                    "text": message.text,
                    "parse_mode": "HTML"
                }) as response:
                    if not (await response.json()).get('ok'):
                        await send_message_safe(
                            bot=bot,
                            chat_id=message.chat.id,
                            text="⚠️ Не удалось отправить сообщение.",
                            reply_markup=await create_back_keyboard()
                        )
                        return
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="✅ Сообщение отправлено.",
                reply_markup=await create_back_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to send message to {target_user_id}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Не удалось отправить сообщение.",
                reply_markup=await create_back_keyboard()
            )
        await dp.storage.set_data(user=user_id, data={})
    
    elif state_value and state_value.startswith("awaiting_ban_reason_") and await is_admin(user_id):
        target_user_id = int(state_value.split("_")[3])
        reason = message.text.strip()
        banned_at = int(datetime.now().timestamp())
        await execute_with_retry_async(
            "INSERT OR REPLACE INTO bans (user_id, admin_id, reason, banned_at) VALUES (?, ?, ?, ?)",
            (target_user_id, user_id, reason, banned_at)
        )
        await execute_with_retry_async(
            "UPDATE users SET is_banned = 1 WHERE user_id = ?",
            (target_user_id,)
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"🚫 Пользователь {target_user_id} забанен.\nПричина: {reason}",
            reply_markup=await create_back_keyboard()
        )
        await send_message_safe(
            bot=bot,
            chat_id=target_user_id,
            text=f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
        )
        await dp.storage.set_data(user=user_id, data={})

async def handle_link_visit(message: types.Message, link_id: str):
    user_id = message.from_user.id
    await update_user_activity(user_id)
    
    link = await execute_with_retry_async("SELECT content_type, content_data, caption FROM links WHERE link_id = ?", (link_id,))
    if not link:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ссылка недействительна или удалена."
        )
        return
    content_type, content_data, caption = link[0]
    
    await execute_with_retry_async("UPDATE links SET visits = visits + 1 WHERE link_id = ?", (link_id,))
    total_visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links"))[0][0]
    await update_stats(link_visits=total_visits)
    
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
        except Exception as e:
            logger.error(f"Failed to send content: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Ошибка при отправке контента."
            )

@dp.callback_query_handler()
async def handle_callback_query(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=callback.message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        try:
            await callback.answer()
        except InvalidQueryID:
            logger.info(f"Callback query {callback.id} is too old or invalid")
        return
    await update_user_activity(user_id)
    data = callback.data
    
    if user_id in callback_cache:
        await callback.answer("⏳ Пожалуйста, подождите немного перед повторным нажатием.")
        return
    callback_cache[user_id] = True

    try:
        if data.startswith("check_local_subscription_"):
            link_id = data.split("_")[-1] if data != "check_local_subscription_start" else None
            check_all_functions = not link_id
            unsubscribed_local = await check_subscriptions(user_id, check_all_functions)
            if unsubscribed_local:
                channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
                keyboard = InlineKeyboardMarkup()
                callback_data = f"check_local_subscription_{link_id}" if link_id else "check_local_subscription_start"
                keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
                await edit_message_if_changed(
                    callback,
                    text=(
                        f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                        f"Для доступа к {'скрипту' if link_id else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
                        f"После подписки нажмите кнопку ниже:"
                    ),
                    reply_markup=keyboard
                )
                return
            
            status, result = await check_subgram_subscription(
                user_id=user_id,
                chat_id=callback.message.chat.id,
                first_name=callback.from_user.first_name,
                language_code=callback.from_user.language_code,
                is_premium=callback.from_user.is_premium
            )
            
            if status == 'need_subscribe':
                keyboard = InlineKeyboardMarkup()
                for link in result:
                    channel_name = link.get("resource_name", "Channel")
                    url = link.get("link", "#")
                    button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
                    keyboard.add(button)
                callback_data = f"check_subgram_subscription_{link_id}" if link_id else "check_subgram_subscription_start"
                keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
                await edit_message_if_changed(
                    callback,
                    text=(
                        f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                        f"Для продолжения подпишитесь на каналы:\n\n"
                        f"После подписки нажмите кнопку ниже:"
                    ),
                    reply_markup=keyboard
                )
                return
            elif status == 'need_gender':
                await edit_message_if_changed(
                    callback,
                    text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
                    reply_markup=await create_gender_selection_keyboard()
                )
                return
            
            if link_id:
                await handle_link_visit(callback.message, link_id)
            else:
                welcome_text = (
                    f"👋 <b>Привет, {callback.from_user.first_name}!</b>\n\n"
                    f"Я <b>Mirrozz Scripts</b> — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀\n\n"
                    f"<b>Почему я лучший?</b>\n"
                    f"• <b>Актуальные скрипты</b> — база обновляется регулярно!\n"
                    f"• <b>Мгновенный доступ</b> — получай скрипты в пару кликов!\n"
                    f"• <b>Надежное хранение</b> — твои скрипты всегда под рукой!\n"
                    f"• <b>Стабильная работа</b> — бот на мощном сервере, без сбоев!\n\n"
                    f"Напиши /help, чтобы узнать все команды!"
                )
                if await is_admin(user_id):
                    welcome_text += "\n\n👑 Ты администратор! Используй /admin для доступа к панели управления."
                await edit_message_if_changed(
                    callback,
                    text=welcome_text,
                    reply_markup=None
                )

        elif data.startswith("check_subgram_subscription_"):
            link_id = data.split("_")[-1] if data != "check_subgram_subscription_start" else None
            status, result = await check_subgram_subscription(
                user_id=user_id,
                chat_id=callback.message.chat.id,
                first_name=callback.from_user.first_name,
                language_code=callback.from_user.language_code,
                is_premium=callback.from_user.is_premium
            )
            
            if status == 'need_subscribe':
                keyboard = InlineKeyboardMarkup()
                for link in result:
                    channel_name = link.get("resource_name", "Channel")
                    url = link.get("link", "#")
                    button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
                    keyboard.add(button)
                callback_data = f"check_subgram_subscription_{link_id}" if link_id else "check_subgram_subscription_start"
                keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
                await edit_message_if_changed(
                    callback,
                    text=(
                        f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                        f"Для продолжения подпишитесь на каналы:\n\n"
                        f"После подписки нажмите кнопку ниже:"
                    ),
                    reply_markup=keyboard
                )
                return
            elif status == 'need_gender':
                await edit_message_if_changed(
                    callback,
                    text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
                    reply_markup=await create_gender_selection_keyboard()
                )
                return
            
            if link_id:
                await handle_link_visit(callback.message, link_id)
            else:
                welcome_text = (
                    f"👋 <b>Привет, {callback.from_user.first_name}!</b>\n\n"
                    f"Я <b>Mirrozz Scripts</b> — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀\n\n"
                    f"<b>Почему я лучший?</b>\n"
                    f"• <b>Актуальные скрипты</b> — база обновляется регулярно!\n"
                    f"• <b>Мгновенный доступ</b> — получай скрипты в пару кликов!\n"
                    f"• <b>Надежное хранение</b> — твои скрипты всегда под рукой!\n"
                    f"• <b>Стабильная работа</b> — бот на мощном сервере, без сбоев!\n\n"
                    f"Напиши /help, чтобы узнать все команды!"
                )
                if await is_admin(user_id):
                    welcome_text += "\n\n👑 Ты администратор! Используй /admin для доступа к панели управления."
                await edit_message_if_changed(
                    callback,
                    text=welcome_text,
                    reply_markup=None
                )

        elif data.startswith("subgram_gender_"):
            gender = data.split("_")[-1]
            await update_user_subscription(user_id, total_fixed_link=0, gender=gender)
            status, result = await check_subgram_subscription(
                user_id=user_id,
                chat_id=callback.message.chat.id,
                first_name=callback.from_user.first_name,
                language_code=callback.from_user.language_code,
                is_premium=callback.from_user.is_premium,
                gender=gender
            )
            
            if status == 'need_subscribe':
                keyboard = InlineKeyboardMarkup()
                for link in result:
                    channel_name = link.get("resource_name", "Channel")
                    url = link.get("link", "#")
                    button = InlineKeyboardButton(text=f"Подписаться на {channel_name}", url=url)
                    keyboard.add(button)
                callback_data = "check_subgram_subscription_start"
                keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
                await edit_message_if_changed(
                    callback,
                    text=(
                        f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                        f"Для продолжения подпишитесь на каналы:\n\n"
                        f"После подписки нажмите кнопку ниже:"
                    ),
                    reply_markup=keyboard
                )
                return
            else:
                welcome_text = (
                    f"👋 <b>Привет, {callback.from_user.first_name}!</b>\n\n"
                    f"Я <b>Mirrozz Scripts</b> — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀\n\n"
                    f"<b>Почему я лучший?</b>\n"
                    f"• <b>Актуальные скрипты</b> — база обновляется регулярно!\n"
                    f"• <b>Мгновенный доступ</b> — получай скрипты в пару кликов!\n"
                    f"• <b>Надежное хранение</b> — твои скрипты всегда под рукой!\n"
                    f"• <b>Стабильная работа</b> — бот на мощном сервере, без сбоев!\n\n"
                    f"Напиши /help, чтобы узнать все команды!"
                )
                if await is_admin(user_id):
                    welcome_text += "\n\n👑 Ты администратор! Используй /admin для доступа к панели управления."
                await edit_message_if_changed(
                    callback,
                    text=welcome_text,
                    reply_markup=None
                )

        elif data == "user_back_to_start":
            await handle_start(callback.message)

        elif data == "admin_stats" and await is_admin(user_id):
            total_users, link_visits, total_links = await read_stats()
            active_users = (await execute_with_retry_async("SELECT COUNT(*) FROM users WHERE last_activity >= ?", (int(time.time()) - 86400 * 7,)))[0][0]
            text = (
                f"📊 <b>Статистика бота</b>\n\n"
                f"👥 Всего пользователей: <b>{total_users}</b>\n"
                f"📅 Активных за неделю: <b>{active_users}</b>\n"
                f"🔗 Всего ссылок: <b>{total_links}</b>\n"
                f"👀 Переходов по ссылкам: <b>{link_visits}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_admin_main_keyboard(user_id)
            )

        elif data == "section_reports" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="📩 <b>Репорты</b>\n\nВыберите непросмотренные репорты:",
                reply_markup=await create_reports_keyboard()
            )

        elif data.startswith("no_checked_reports_") and await is_admin(user_id):
            page = int(data.split("_")[-1])
            await edit_message_if_changed(
                callback,
                text="📩 <b>Непросмотренные репорты</b>",
                reply_markup=await create_reports_keyboard(page=page, checked=False)
            )

        elif data.startswith("all_reports_") and await is_admin(user_id):
            page = int(data.split("_")[-1])
            await edit_message_if_changed(
                callback,
                text="📩 <b>Все репорты</b>",
                reply_markup=await create_reports_keyboard(page=page, checked=True)
            )

        elif data.startswith("report_info_") and await is_admin(user_id):
            report_id = data.split("_")[-1]
            report = await execute_with_retry_async(
                "SELECT user_id, message, created_at, is_checked FROM reports WHERE report_id = ?",
                (report_id,)
            )
            if not report:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Репорт не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            user_id, message_text, created_at, is_checked = report[0]
            user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (user_id,)) else f"Пользователь {user_id}"
            date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
            text = (
                f"📩 <b>Репорт</b>\n\n"
                f"ID: <b>{report_id}</b>\n"
                f"Пользователь: <b>{user_name}</b>\n"
                f"Дата: <b>{date}</b>\n"
                f"Статус: <b>{'Проверен' if is_checked else 'Не проверен'}</b>\n"
                f"Сообщение: <b>{message_text}</b>"
            )
            await execute_with_retry_async("UPDATE reports SET is_checked = 1 WHERE report_id = ?", (report_id,))
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_report_actions_keyboard(report_id, user_id)
            )

        elif data.startswith("delete_report_") and await is_admin(user_id):
            report_id = data.split("_")[-1]
            await execute_with_retry_async("DELETE FROM reports WHERE report_id = ?", (report_id,))
            await edit_message_if_changed(
                callback,
                text="✅ Репорт удален.",
                reply_markup=await create_back_keyboard()
            )

        elif data == "section_users" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="👥 <b>Пользователи</b>\n\nВыберите действие:",
                reply_markup=await create_users_keyboard()
            )

        elif data == "section_links" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="🔗 <b>Ссылки</b>\n\nВыберите действие:",
                reply_markup=await create_links_keyboard()
            )

        elif data == "section_ads" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="📢 <b>Реклама</b>\n\nВыберите действие:",
                reply_markup=await create_ads_keyboard()
            )

        elif data == "section_admins" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="👑 <b>Админы</b>\n\nВыберите действие:",
                reply_markup=await create_admins_keyboard()
            )

        elif data == "admin_developer" and user_id in DEVELOPER_IDS:
            await edit_message_if_changed(
                callback,
                text="🛠 <b>Панель разработчика</b>\n\nВыберите раздел:",
                reply_markup=await create_developer_keyboard()
            )

        elif data == "developer_server" and user_id in DEVELOPER_IDS:
            uptime = int(time.time() - start_time)
            uptime_str = f"{uptime // 3600}ч {(uptime % 3600) // 60}м {uptime % 60}с"
            text = (
                f"🖥 <b>Управление сервером</b>\n\n"
                f"Статус: <b>{'🟢 Работает' if BOT_ENABLED else '🔴 Отключен'}</b>\n"
                f"Время работы: <b>{uptime_str}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_developer_server_keyboard()
            )

        elif data == "developer_database" and user_id in DEVELOPER_IDS:
            db_size = os.path.getsize(DATABASE_FILE) / (1024 * 1024) if os.path.exists(DATABASE_FILE) else 0
            text = (
                f"🗄 <b>Управление базой данных</b>\n\n"
                f"Размер БД: <b>{db_size:.2f} МБ</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_developer_database_keyboard()
            )

        elif data == "developer_messages" and user_id in DEVELOPER_IDS:
            await edit_message_if_changed(
                callback,
                text="📢 <b>Системные сообщения</b>\n\nВыберите действие:",
                reply_markup=await create_developer_messages_keyboard()
            )

        elif data == "developer_management" and user_id in DEVELOPER_IDS:
            await edit_message_if_changed(
                callback,
                text="👨‍💻 <b>Управление разработчиками</b>\n\nВыберите действие:",
                reply_markup=await create_developers_keyboard()
            )

        elif data == "create_link" and await is_admin(user_id):
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_link_content"})
            await edit_message_if_changed(
                callback,
                text="🔗 <b>Создание ссылки</b>\n\nОтправьте текст, фото или документ (до 2 МБ).",
                reply_markup=await create_back_keyboard()
            )

        elif data == "delete_link" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="🗑 <b>Удаление ссылки</b>\n\nВыберите ссылку для удаления:",
                reply_markup=await create_links_list_keyboard()
            )

        elif data.startswith("list_links_") and await is_admin(user_id):
            page = int(data.split("_")[-1])
            await edit_message_if_changed(
                callback,
                text="📜 <b>Список ссылок</b>",
                reply_markup=await create_links_list_keyboard(page=page)
            )

        elif data.startswith("link_info_") and await is_admin(user_id):
            link_id = data.split("_")[-1]
            link = await execute_with_retry_async(
                "SELECT content_type, content_data, caption, creator_id, created_at, visits FROM links WHERE link_id = ?",
                (link_id,)
            )
            if not link:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Ссылка не найдена.",
                    reply_markup=await create_back_keyboard()
                )
                return
            content_type, content_data, caption, creator_id, created_at, visits = link[0]
            creator_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)) else f"Админ {creator_id}"
            date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
            bot_username = (await bot.get_me()).username
            link_url = f"https://t.me/{bot_username}?start={link_id}"
            content_preview = content_data[:50] + "..." if len(content_data) > 50 else content_data
            text = (
                f"🔗 <b>Информация о ссылке</b>\n\n"
                f"ID: <b>{link_id}</b>\n"
                f"Тип: <b>{content_type}</b>\n"
                f"Содержимое: <b>{content_preview}</b>\n"
                f"Подпись: <b>{caption or 'Нет'}</b>\n"
                f"Создатель: <b>{creator_name}</b>\n"
                f"Дата создания: <b>{date}</b>\n"
                f"Переходы: <b>{visits}</b>\n"
                f"Ссылка: <b>{hcode(link_url)}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_link_actions_keyboard(link_id)
            )

        elif data.startswith("confirm_delete_") and await is_admin(user_id):
            link_id = data.split("_")[-1]
            await edit_message_if_changed(
                callback,
                text=f"🗑 <b>Подтвердите удаление ссылки {link_id}</b>",
                reply_markup=await create_confirm_delete_keyboard(link_id)
            )

        elif data.startswith("delete_link_") and await is_admin(user_id):
            link_id = data.split("_")[-1]
            await execute_with_retry_async("DELETE FROM links WHERE link_id = ?", (link_id,))
            total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
            await update_stats(total_links=total_links)
            await edit_message_if_changed(
                callback,
                text="✅ Ссылка удалена.",
                reply_markup=await create_back_keyboard()
            )

        elif data == "add_channel" and await is_admin(user_id):
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_channel_id"})
            await edit_message_if_changed(
                callback,
                text="📢 <b>Добавление канала</b>\n\nОтправьте ID канала (например, @username или -100123456789):",
                reply_markup=await create_back_keyboard()
            )

        elif data.startswith("scope_") and await is_admin(user_id):
            scope = data.split("_")[-1]
            state = await dp.storage.get_data(user=user_id)
            channel_id = state.get("channel_id")
            if not channel_id:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Ошибка: ID канала не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            title = channel_id if channel_id.startswith("@") else f"Канал {channel_id}"
            await execute_with_retry_async(
                "INSERT INTO channels (channel_id, title, check_scope) VALUES (?, ?, ?)",
                (channel_id, title, scope)
            )
            await edit_message_if_changed(
                callback,
                text=f"✅ Канал {title} добавлен с проверкой: {await scope_text(scope)}",
                reply_markup=await create_back_keyboard()
            )
            await dp.storage.set_data(user=user_id, data={})

        elif data == "remove_channel" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="✖️ <b>Удаление канала</b>\n\nВыберите канал для удаления:",
                reply_markup=await create_channels_keyboard(action="confirm_remove_channel")
            )

        elif data == "channel_list" and await is_admin(user_id):
            channels = await execute_with_retry_async("SELECT channel_id, title, check_scope FROM channels")
            if not channels:
                await edit_message_if_changed(
                    callback,
                    text="📢 <b>Список каналов</b>\n\nКаналы отсутствуют.",
                    reply_markup=await create_back_keyboard()
                )
                return
            channel_list = "\n".join([f"📢 {title} ({await scope_text(check_scope)})" for _, title, check_scope in channels])
            await edit_message_if_changed(
                callback,
                text=f"📢 <b>Список каналов</b>\n\n{channel_list}",
                reply_markup=await create_back_keyboard()
            )

        elif data.startswith("confirm_remove_channel_") and await is_admin(user_id):
            channel_id = data.split("_")[-1]
            channel = await execute_with_retry_async("SELECT title FROM channels WHERE channel_id = ?", (channel_id,))
            if not channel:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Канал не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            title = channel[0][0]
            await edit_message_if_changed(
                callback,
                text=f"✖️ <b>Подтвердите удаление канала {title}</b>",
                reply_markup=await create_confirm_channel_delete_keyboard(channel_id)
            )

        elif data.startswith("confirm_remove_channel_") and await is_admin(user_id):
            channel_id = data.split("_")[-1]
            await execute_with_retry_async("DELETE FROM channels WHERE channel_id = ?", (channel_id,))
            await edit_message_if_changed(
                callback,
                text="✅ Канал удален.",
                reply_markup=await create_back_keyboard()
            )

        elif data == "add_admin" and await is_admin(user_id):
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_admin_id"})
            await edit_message_if_changed(
                callback,
                text="👑 <b>Добавление админа</b>\n\nОтправьте ID или @username нового администратора:",
                reply_markup=await create_back_keyboard()
            )

        elif data == "remove_admin" and await is_admin(user_id):
            await edit_message_if_changed(
                callback,
                text="🚫 <b>Удаление админа</b>\n\nВыберите администратора для удаления:",
                reply_markup=await create_admins_list_keyboard()
            )

        elif data.startswith("list_admins_") and await is_admin(user_id):
            page = int(data.split("_")[-1])
            await edit_message_if_changed(
                callback,
                text="📜 <b>Список админов</b>",
                reply_markup=await create_admins_list_keyboard(page=page)
            )

        elif data.startswith("admin_info_") and await is_admin(user_id):
            admin_id = int(data.split("_")[-1])
            admin = await execute_with_retry_async("SELECT first_name, username FROM users WHERE user_id = ?", (admin_id,))
            if not admin:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Админ не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            first_name, username = admin[0]
            text = (
                f"👑 <b>Информация об админе</b>\n\n"
                f"👤 Имя: <b>{first_name}</b>\n"
                f"🔗 Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
                f"🆔 ID: <b>{admin_id}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_admin_actions_keyboard(admin_id)
            )

        elif data.startswith("remove_admin_") and await is_admin(user_id):
            admin_id = int(data.split("_")[-1])
            if admin_id == user_id:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Вы не можете удалить себя.",
                    reply_markup=await create_back_keyboard()
                )
                return
            await execute_with_retry_async("DELETE FROM admins WHERE user_id = ?", (admin_id,))
            await edit_message_if_changed(
                callback,
                text=f"✅ Админ {admin_id} удален.",
                reply_markup=await create_back_keyboard()
            )

        elif data == "search_user" and await is_admin(user_id):
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_search_user"})
            await edit_message_if_changed(
                callback,
                text="🔎 <b>Поиск пользователя</b>\n\nОтправьте ID или @username:",
                reply_markup=await create_back_keyboard()
            )

        elif data.startswith("banned_users_") and await is_admin(user_id):
            page = int(data.split("_")[-1])
            await edit_message_if_changed(
                callback,
                text="🚫 <b>Забаненные пользователи</b>",
                reply_markup=await create_banned_users_keyboard(page=page)
            )

        elif data.startswith("banned_user_info_") and await is_admin(user_id):
            banned_user_id = int(data.split("_")[-1])
            ban_info = await execute_with_retry_async("SELECT admin_id, reason, banned_at FROM bans WHERE user_id = ?", (banned_user_id,))
            if not ban_info:
                await edit_message_if_changed(
                    callback,
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
                f"👤 Пользователь: <b>{user_name}</b>\n"
                f"🆔 ID: <b>{banned_user_id}</b>\n"
                f"👑 Админ: <b>{admin_name}</b>\n"
                f"📅 Дата бана: <b>{date}</b>\n"
                f"📜 Причина: <b>{reason}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_user_actions_keyboard(banned_user_id)
            )

        elif data.startswith("send_message_") and await is_admin(user_id):
            target_user_id = int(data.split("_")[-1])
            await dp.storage.set_data(user=user_id, data={"state": f"awaiting_message_{target_user_id}"})
            await edit_message_if_changed(
                callback,
                text=f"📩 <b>Отправка сообщения пользователю {target_user_id}</b>\n\nВведите текст сообщения:",
                reply_markup=await create_back_keyboard()
            )

        elif data.startswith("ban_user_") and await is_admin(user_id):
            target_user_id = int(data.split("_")[-1])
            await dp.storage.set_data(user=user_id, data={"state": f"awaiting_ban_reason_{target_user_id}"})
            await edit_message_if_changed(
                callback,
                text=f"🚫 <b>Бан пользователя {target_user_id}</b>\n\nУкажите причину бана:",
                reply_markup=await create_back_keyboard()
            )

        elif data.startswith("unban_user_") and await is_admin(user_id):
            target_user_id = int(data.split("_")[-1])
            await execute_with_retry_async("DELETE FROM bans WHERE user_id = ?", (target_user_id,))
            await execute_with_retry_async("UPDATE users SET is_banned = 0 WHERE user_id = ?", (target_user_id,))
            await edit_message_if_changed(
                callback,
                text=f"✅ Пользователь {target_user_id} разбанен.",
                reply_markup=await create_back_keyboard()
            )
            await send_message_safe(
                bot=bot,
                chat_id=target_user_id,
                text="✅ Вы были разблокированы в боте."
            )

        elif data.startswith("user_info_") and await is_admin(user_id):
            target_user_id = int(data.split("_")[-1])
            user = await execute_with_retry_async("SELECT first_name, username, join_date, last_activity FROM users WHERE user_id = ?", (target_user_id,))
            if not user:
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Пользователь не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            first_name, username, join_date, last_activity = user[0]
            visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links WHERE creator_id = ?", (target_user_id,)))[0][0] or 0
            join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
            last_activity_str = datetime.fromtimestamp(last_activity).strftime("%d.%m.%Y %H:%M")
            is_banned_status = await is_banned(target_user_id)
            text = (
                f"ℹ️ <b>Информация о пользователе</b>\n\n"
                f"👤 Имя: <b>{first_name}</b>\n"
                f"🔗 Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
                f"🆔 ID: <b>{target_user_id}</b>\n"
                f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
                f"🕒 Последняя активность: <b>{last_activity_str}</b>\n"
                f"🔗 Переходов по созданным ссылкам: <b>{visits}</b>\n"
                f"🚫 Статус: <b>{'Забанен' if is_banned_status else 'Активен'}</b>"
            )
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_user_actions_keyboard(target_user_id)
            )

        elif data == "add_developer" and user_id in DEVELOPER_IDS:
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_developer_id"})
            await edit_message_if_changed(
                callback,
                text="👨‍💻 <b>Добавление разработчика</b>\n\nОтправьте ID или @username нового разработчика:",
                reply_markup=await create_back_keyboard()
            )

        elif data == "remove_developer" and user_id in DEVELOPER_IDS:
            await dp.storage.set_state(user=user_id, state=AdminStates.RemoveDeveloper)
            await edit_message_if_changed(
                callback,
                text="🗑 <b>Удаление разработчика</b>\n\nОтправьте ID или @username разработчика для удаления:",
                reply_markup=await create_back_keyboard()
            )

        elif data == "list_developers" and user_id in DEVELOPER_IDS:
            developers = await execute_with_retry_async("SELECT user_id, username, added_at FROM developers")
            if not developers:
                await edit_message_if_changed(
                    callback,
                    text="👨‍💻 <b>Список разработчиков</b>\n\nРазработчики отсутствуют.",
                    reply_markup=await create_back_keyboard()
                )
                return
            dev_list = "\n".join([f"👨‍💻 @{escape_md(username) if username else 'Нет'} (ID: {user_id}, Добавлен: {added_at})" for user_id, username, added_at in developers])
            await edit_message_if_changed(
                callback,
                text=f"👨‍💻 <b>Список разработчиков</b>\n\n{dev_list}",
                reply_markup=await create_back_keyboard()
            )

        elif data == "disable_bot" and user_id in DEVELOPER_IDS:
            BOT_ENABLED = False
            await edit_message_if_changed(
                callback,
                text="🔴 Бот отключен.",
                reply_markup=await create_developer_server_keyboard()
            )

        elif data == "enable_bot" and user_id in DEVELOPER_IDS:
            BOT_ENABLED = True
            await edit_message_if_changed(
                callback,
                text="🟢 Бот включен.",
                reply_markup=await create_developer_server_keyboard()
            )

        elif data == "restart_bot" and user_id in DEVELOPER_IDS:
            await edit_message_if_changed(
                callback,
                text="🔄 Перезагрузка бота начата...",
                reply_markup=await create_back_keyboard()
            )
            for dev_id in DEVELOPER_IDS:
                await send_message_safe(
                    bot=bot,
                    chat_id=dev_id,
                    text="🔄 Бот перезагружается..."
                )
            os._exit(0)

        elif data == "emergency_shutdown" and user_id in DEVELOPER_IDS:
            await edit_message_if_changed(
                callback,
                text="🛑 Экстренное выключение...",
                reply_markup=await create_back_keyboard()
            )
            for dev_id in DEVELOPER_IDS:
                await send_message_safe(
                    bot=bot,
                    chat_id=dev_id,
                    text="🛑 Бот экстренно выключен."
                )
            sys.exit(0)

        elif data == "download_database" and user_id in DEVELOPER_IDS:
            if not os.path.exists(DATABASE_FILE):
                await edit_message_if_changed(
                    callback,
                    text="⚠️ База данных не найдена.",
                    reply_markup=await create_back_keyboard()
                )
                return
            try:
                await bot.send_document(
                    chat_id=callback.message.chat.id,
                    document=types.InputFile(DATABASE_FILE),
                    caption="📥 База данных"
                )
                await edit_message_if_changed(
                    callback,
                    text="✅ База данных отправлена.",
                    reply_markup=await create_developer_database_keyboard()
                )
            except Exception as e:
                logger.error(f"Failed to send database: {str(e)}")
                await edit_message_if_changed(
                    callback,
                    text="⚠️ Ошибка при отправке базы данных.",
                    reply_markup=await create_back_keyboard()
                )

        elif data == "upload_database" and user_id in DEVELOPER_IDS:
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_database_upload"})
            await edit_message_if_changed(
                callback,
                text="📤 <b>Загрузка базы данных</b>\n\nОтправьте файл базы данных (.db):",
                reply_markup=await create_back_keyboard()
            )

        elif data == "reset_database" and user_id in DEVELOPER_IDS:
            try:
                if os.path.exists(DATABASE_FILE):
                    os.remove(DATABASE_FILE)
                await init_database()
                await edit_message_if_changed(
                    callback,
                    text="✅ База данных сброшена.",
                    reply_markup=await create_developer_database_keyboard()
                )
            except Exception as e:
                logger.error(f"Database reset error: {str(e)}")
                await edit_message_if_changed(
                    callback,
                    text=f"⚠️ Ошибка при сбросе базы данных: {str(e)}",
                    reply_markup=await create_back_keyboard()
                )

        elif data == "send_system_message" and user_id in DEVELOPER_IDS:
            await dp.storage.set_data(user=user_id, data={"state": "awaiting_system_message"})
            await edit_message_if_changed(
                callback,
                text="📩 <b>Отправка системного сообщения</b>\n\nВведите текст сообщения:",
                reply_markup=await create_back_keyboard()
            )

        elif data == "message_history" and user_id in DEVELOPER_IDS:
            messages = await execute_with_retry_async("SELECT message_id, message_text, sent_at, sender_id FROM system_messages ORDER BY sent_at DESC LIMIT 10")
            if not messages:
                await edit_message_if_changed(
                    callback,
                    text="📜 <b>История системных сообщений</b>\n\nСообщения отсутствуют.",
                    reply_markup=await create_back_keyboard()
                )
                return
            message_list = []
            for message_id, message_text, sent_at, sender_id in messages:
                sender_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)) else f"Админ {sender_id}"
                date = datetime.fromtimestamp(sent_at).strftime("%d.%m.%Y %H:%M")
                message_list.append(f"📩 {date} от {sender_name}\n{message_text[:50]}{'...' if len(message_text) > 50 else ''}")
            text = f"📜 <b>История системных сообщений</b>\n\n" + "\n\n".join(message_list)
            await edit_message_if_changed(
                callback,
                text=text,
                reply_markup=await create_back_keyboard()
            )

        elif data == "back_to_main" and await is_admin(user_id):
            await dp.storage.set_data(user=user_id, data={})
            await edit_message_if_changed(
                callback,
                text="🛠 <b>Панель администратора</b>\n\nВыберите раздел:",
                reply_markup=await create_admin_main_keyboard(user_id)
            )

    except Exception as e:
        logger.error(f"Callback error: {str(e)}")
        await edit_message_if_changed(
            callback,
            text="⚠️ Произошла ошибка при обработке запроса.",
            reply_markup=await create_back_keyboard()
        )

    finally:
        callback_cache.pop(user_id, None)

@dp.message_handler(state=AdminStates.RemoveDeveloper)
async def handle_remove_developer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        await state.finish()
        return
    
    text = message.text.strip()
    try:
        if text.startswith("@"):
            result = await execute_with_retry_async("SELECT user_id FROM users WHERE username = ?", (text[1:],))
            if not result:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Пользователь не найден.",
                    reply_markup=await create_back_keyboard()
                )
                return
            target_developer_id = result[0][0]
        else:
            target_developer_id = int(text)
        
        if target_developer_id == user_id:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Вы не можете удалить себя.",
                reply_markup=await create_back_keyboard()
            )
            return
        
        if target_developer_id not in DEVELOPER_IDS:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Этот пользователь не является разработчиком.",
                reply_markup=await create_back_keyboard()
            )
            return
        
        await execute_with_retry_async("DELETE FROM developers WHERE user_id = ?", (target_developer_id,))
        DEVELOPER_IDS.remove(target_developer_id)
        user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (target_developer_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (target_developer_id,)) else f"Пользователь {target_developer_id}"
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ {user_name} (ID: {target_developer_id}) удален из разработчиков.",
            reply_markup=await create_back_keyboard()
        )
        await send_message_safe(
            bot=bot,
            chat_id=target_developer_id,
            text="👨‍💻 Вы были удалены из списка разработчиков бота Mirrozz Scripts."
        )
        await state.finish()
    
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверный формат. Укажите ID или @username.",
            reply_markup=await create_back_keyboard()
        )

async def on_startup(_):
    await init_database()
    logger.info("Bot started")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)