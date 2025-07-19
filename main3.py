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
from aiogram.utils.markdown import hcode, hbold, escape_md
from aiogram.utils.exceptions import BotBlocked, InvalidQueryID, MessageNotModified, NetworkError
import time
import aiohttp
from aiohttp_retry import RetryClient, ExponentialRetry
from cachetools import TTLCache
import sys
import os
import shutil
import fcntl
import psutil

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
TOKEN = os.getenv("BOT_TOKEN", "8178374718:AAHvyoBH5Ty2VKwNyfdWeOez9XLSflNQtaM")
SUBGRAM_API_KEY = os.getenv("SUBGRAM_API_KEY", "8a1994b006b02e4e126dae69f8ce9832f87d005a77480d0a40854c4b592947ad")
SUBGRAM_API_URL = "https://api.subgram.ru/request-op/"
SUBGRAM_MAX_OP = 6
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
subgram_cache = TTLCache(maxsize=1000, ttl=30)  # Увеличено с 3 до 30 секунд
callback_cache = TTLCache(maxsize=1000, ttl=10)
subscription_cache = TTLCache(maxsize=1000, ttl=30)  # Кэш для результатов проверки подписки

# Состояния для FSM
class AdminStates(StatesGroup):
    RemoveDeveloper = State()
    SystemMessage = State()
    Report = State()
    AddDeveloper = State()

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
                    await send_message_safe(bot, callback.message.chat.id, text, reply_markup)
    except (MessageNotModified, InvalidQueryID):
        logger.debug(f"Message edit skipped for callback {callback.data}: Content not modified or invalid query")
        await send_message_safe(bot, callback.message.chat.id, text, reply_markup)
    except NetworkError as e:
        logger.error(f"Network error editing message for callback {callback.data}: {str(e)}")
        await send_message_safe(bot, callback.message.chat.id, text, reply_markup)
    except Exception as e:
        logger.error(f"Error editing message for callback {callback.data}: {str(e)}")
        await send_message_safe(bot, callback.message.chat.id, text, reply_markup)
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

    retry_options = ExponentialRetry(attempts=7, start_timeout=2.0, factor=2.0)
    start_time = time.time()
    try:
        async with RetryClient() as client:
            logger.info(f"SubGram request data for user {user_id}: {data}")
            async with client.post(SUBGRAM_API_URL, headers=headers, json=data, retry_options=retry_options) as response:
                result = await response.json()
                logger.info(f"SubGram response for user {user_id}: {result}, took {time.time() - start_time:.2f} seconds")
                
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
                        subgram_cache.pop(cache_key, None)
                        await execute_with_retry_async(
                            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                            (json.dumps([]), int(time.time()), user_id)
                        )
                        return 'ok', None
                elif response.status == 429:
                    logger.warning(f"SubGram API rate limit exceeded for user {user_id}")
                    await asyncio.sleep(1)
                    return await check_subgram_subscription(user_id, chat_id, first_name, language_code, is_premium, gender)
                elif response.status == 404 and result.get('message') == 'Нет подходящих рекламодателей для данного пользователя':
                    logger.info(f"SubGram: Нет подходящих каналов для пользователя {user_id}")
                    await execute_with_retry_async(
                        "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                        (json.dumps([]), int(time.time()), user_id)
                    )
                    subgram_cache[cache_key] = ('ok', None)
                    return 'ok', None
                else:
                    logger.error(f"SubGram API error for user {user_id}: HTTP {response.status}, {result}")
                    subgram_cache.pop(cache_key, None)
                    await execute_with_retry_async(
                        "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
                        (json.dumps([]), int(time.time()), user_id)
                    )
                    return 'ok', None
    except aiohttp.ClientConnectorError as e:
        logger.error(f"SubGram connection error for user {user_id}: {str(e)}, took {time.time() - start_time:.2f} seconds")
        subgram_cache.pop(cache_key, None)
        await execute_with_retry_async(
            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
            (json.dumps([]), int(time.time()), user_id)
        )
        return 'ok', None
    except Exception as e:
        logger.error(f"SubGram API error for user {user_id}: {str(e)}, took {time.time() - start_time:.2f} seconds")
        subgram_cache.pop(cache_key, None)
        await execute_with_retry_async(
            "UPDATE subscriptions SET subgram_links = ?, subgram_timestamp = ? WHERE user_id = ?",
            (json.dumps([]), int(time.time()), user_id)
        )
        return 'ok', None

async def init_database():
    lock_file = f"{DATABASE_FILE}.lock"
    try:
        with open(lock_file, 'a') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
                cursor = await conn.execute("PRAGMA table_info(stats)")
                if await cursor.fetchall():
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
                ''',
                '''
                CREATE TABLE IF NOT EXISTS system_messages (
                    message_id TEXT PRIMARY KEY,
                    message_text TEXT,
                    sent_at INTEGER,
                    sender_id INTEGER
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
            
            # Добавление индексов для оптимизации запросов
            index_queries = [
                "CREATE INDEX IF NOT EXISTS idx_users_user_id ON users(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_links_link_id ON links(link_id)",
                "CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_bans_user_id ON bans(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id)",
                "CREATE INDEX IF NOT EXISTS idx_developers_user_id ON developers(user_id)"
            ]
            for query in index_queries:
                try:
                    await execute_with_retry_async(query)
                    logger.info(f"Executed index query: {query}")
                except sqlite3.OperationalError as e:
                    logger.error(f"Failed to execute index query: {query}, Error: {str(e)}")
            
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
    finally:
        if os.path.exists(lock_file):
            os.remove(lock_file)

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
    cache_key = f"{user_id}_{check_all_functions}"
    if cache_key in subscription_cache:
        logger.debug(f"Subscriptions: Using cached response for user {user_id}")
        return subscription_cache[cache_key]

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
                        await asyncio.sleep(0.1)  # Пауза 100 мс между запросами
                    except Exception as e:
                        logger.warning(f"Failed to check subscription for channel {channel_id} for user {user_id}: {str(e)}")
                        unsubscribed.append(title)
        subscription_cache[cache_key] = unsubscribed
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
                        await asyncio.sleep(0.1)  # Пауза 100 мс между запросами
                    except Exception as e:
                        logger.warning(f"Failed to check subscription for channel {channel_id} for user {user_id}: {str(e)}")
                        unsubscribed.append(title)
            subscription_cache[cache_key] = unsubscribed
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
        InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"delete_channel_{channel_id}"),
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
        InlineKeyboardButton(text="📊 Статус", callback_data="server_status"),
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

@dp.message_handler(commands=["logs"])
async def view_logs(message: types.Message):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        return
    try:
        with open("bot.log", "r") as f:
            logs = f.readlines()[-10:]  # Последние 10 строк
        text = "📜 <b>Последние логи</b>\n\n" + "".join(logs)
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=text
        )
    except Exception as e:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Не удалось прочитать логи: {str(e)}"
        )

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

async def handle_link_visit(message: types.Message, link_id: str):
    link = await execute_with_retry_async(
        "SELECT content_type, content_data, caption, visits FROM links WHERE link_id = ?",
        (link_id,)
    )
    if not link:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ссылка не найдена или удалена."
        )
        return
    
    content_type, content_data, caption, visits = link[0]
    await execute_with_retry_async(
        "UPDATE links SET visits = visits + 1 WHERE link_id = ?",
        (link_id,)
    )
    total_visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links"))[0][0] or 0
    await update_stats(link_visits=total_visits)

    try:
        if content_type == "text":
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=content_data + (f"\n\n{caption}" if caption else ""),
                reply_markup=await create_user_back_keyboard()
            )
        elif content_type == "photo":
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=content_data,
                caption=caption or None,
                reply_markup=await create_user_back_keyboard()
            )
        elif content_type == "document":
            await bot.send_document(
                chat_id=message.chat.id,
                document=content_data,
                caption=caption or None,
                reply_markup=await create_user_back_keyboard()
            )
    except Exception as e:
        logger.error(f"Failed to send link content to {message.chat.id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Не удалось отправить контент ссылки."
        )

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
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к {'скрипту' if link_id else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=callback_data))
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
                f"📢 <b>Подпишитесь на дополнительные resoluzioni aggiuntive каналы</b>\n\n"
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
                        logger.warning(f"Failed to notify admin {admin_id} about report {report_id}")
            except Exception as e:
                logger.error(f"Error notifying admin {admin_id} about report {report_id}: {str(e)}")

@dp.message_handler(commands=["admin"])
async def handle_admin(message: types.Message):
    user_id = message.from_user.id
    if not BOT_ENABLED and user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🔴 Бот временно отключен. Только панель разработчика доступна."
        )
        return
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        return
    await update_user_activity(user_id)
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text="👑 <b>Панель администратора</b>\n\nВыберите раздел:",
        reply_markup=await create_admin_main_keyboard(user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        f"👥 Всего пользователей: <b>{total_users}</b>\n"
        f"🔗 Переходов по ссылкам: <b>{link_visits}</b>\n"
        f"📜 Создано ссылок: <b>{total_links}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_back_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "back_to_main")
async def back_to_main(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👑 <b>Панель администратора</b>\n\nВыберите раздел:",
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
    await handle_start(callback.message)

@dp.callback_query_handler(lambda c: c.data.startswith("check_local_subscription_"))
async def check_local_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="⏳ Проверяем подписку на каналы...",
        reply_markup=None
    )
    
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
    
    await edit_message_if_changed(
        callback=callback,
        text="⏳ Проверяем подписку на дополнительные каналы...",
        reply_markup=None
    )
    
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_subgram_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
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
            callback=callback,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
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
    elif action != "start":
        await handle_start(callback.message, link_id=action)

@dp.callback_query_handler(lambda c: c.data.startswith("check_subgram_subscription_"))
async def check_subgram_subscription_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text="⏳ Проверяем подписку на дополнительные каналы...",
        reply_markup=None
    )
    
    action = callback.data.split("_")[-1]
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_subgram_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
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
            callback=callback,
            text="👤 <b>Пожалуйста, укажите ваш пол:</b>",
            reply_markup=await create_gender_selection_keyboard()
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
    elif action != "start":
        await handle_start(callback.message, link_id=action)

@dp.callback_query_handler(lambda c: c.data.startswith("subgram_gender_"))
async def set_subgram_gender(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    gender = callback.data.split("_")[-1]
    await update_user_subscription(user_id, None, gender=gender)
    
    await edit_message_if_changed(
        callback=callback,
        text="⏳ Проверяем подписку на дополнительные каналы...",
        reply_markup=None
    )
    
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_start"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Для продолжения подпишитесь на каналы:\n\n"
                f"После подписки нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'ok':
        await handle_start(callback.message)

@dp.callback_query_handler(lambda c: c.data == "section_reports")
async def section_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📥 <b>Непроверенные репорты</b>\n\nВыберите репорт для просмотра:",
        reply_markup=await create_reports_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("no_checked_reports_"))
async def no_checked_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📥 <b>Непроверенные репорты</b>\n\nВыберите репорт для просмотра:",
        reply_markup=await create_reports_keyboard(page=page, checked=False)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("all_reports_"))
async def all_reports(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📜 <b>Все репорты</b>\n\nВыберите репорт для просмотра:",
        reply_markup=await create_reports_keyboard(page=page, checked=True)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("report_info_"))
async def report_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    report_id = callback.data.split("_")[-1]
    report = await execute_with_retry_async(
        "SELECT user_id, message, created_at, is_checked FROM reports WHERE report_id = ?",
        (report_id,)
    )
    if not report:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Репорт не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    reporter_id, message_text, created_at, is_checked = report[0]
    user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)) else f"Пользователь {reporter_id}"
    date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    text = (
        f"📩 <b>Информация о репорте</b>\n\n"
        f"🆔 ID: <b>{report_id}</b>\n"
        f"👤 Пользователь: <b>{user_name}</b> (ID: {reporter_id})\n"
        f"📅 Дата: <b>{date}</b>\n"
        f"📝 Сообщение: <b>{message_text}</b>\n"
        f"✅ Проверено: <b>{'Да' if is_checked else 'Нет'}</b>"
    )
    if not is_checked:
        await execute_with_retry_async(
            "UPDATE reports SET is_checked = 1 WHERE report_id = ?",
            (report_id,)
        )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_report_actions_keyboard(report_id, reporter_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_report_"))
async def delete_report(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    report_id = callback.data.split("_")[-1]
    await execute_with_retry_async(
        "DELETE FROM reports WHERE report_id = ?",
        (report_id,)
    )
    await edit_message_if_changed(
        callback=callback,
        text="✅ Репорт успешно удален.",
        reply_markup=await create_reports_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_users")
async def section_users(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👥 <b>Управление пользователями</b>\n\nВыберите действие:",
        reply_markup=await create_users_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "search_user")
async def search_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="🔎 Введите ID пользователя для поиска:",
        reply_markup=await create_back_keyboard()
    )
    await AdminStates.SystemMessage.set()

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_send_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    
    if not target_user_id:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка: ID пользователя не найден."
        )
        await state.finish()
        return
    
    try:
        # Отправляем сообщение пользователю
        await send_message_safe(
            bot=bot,
            chat_id=target_user_id,
            text=f"📩 Ответ от администратора:\n\n{message.text}"
        )
        
        # Отправляем подтверждение админу
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Сообщение отправлено пользователю {target_user_id}."
        )
    except Exception as e:
        logger.error(f"Error sending message to user {target_user_id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Не удалось отправить сообщение пользователю {target_user_id}."
        )
    
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("banned_users_"))
async def banned_users(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="🚫 <b>Заблокированные пользователи</b>\n\nВыберите пользователя для просмотра:",
        reply_markup=await create_banned_users_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("banned_user_info_"))
async def banned_user_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    banned_user_id = int(callback.data.split("_")[-1])
    user = await execute_with_retry_async(
        "SELECT first_name, username, join_date FROM users WHERE user_id = ?",
        (banned_user_id,)
    )
    ban_info = await execute_with_retry_async(
        "SELECT reason, banned_at, admin_id FROM bans WHERE user_id = ?",
        (banned_user_id,)
    )
    if not user or not ban_info:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Пользователь или информация о бане не найдены.",
            reply_markup=await create_back_keyboard()
        )
        return
    first_name, username, join_date = user[0]
    reason, banned_at, admin_id = ban_info[0]
    join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
    banned_at_str = datetime.fromtimestamp(banned_at).strftime("%d.%m.%Y %H:%M")
    admin_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)) else f"Админ {admin_id}"
    text = (
        f"🚫 <b>Информация о заблокированном пользователе</b>\n\n"
        f"🆔 ID: <b>{banned_user_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
        f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
        f"📅 Дата бана: <b>{banned_at_str}</b>\n"
        f"👑 Админ: <b>{admin_name}</b>\n"
        f"📝 Причина: <b>{reason or 'Не указана'}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(banned_user_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("send_message_"))
async def send_message_to_user(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    target_user_id = int(callback.data.split("_")[-1])
    
    # Сохраняем target_user_id в состоянии
    await state.update_data(target_user_id=target_user_id)
    
    await edit_message_if_changed(
        callback=callback,
        text=f"📩 Введите сообщение для пользователя {target_user_id}:",
        reply_markup=await create_back_keyboard()
    )
    await AdminStates.SystemMessage.set()

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_send_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "send_message":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    target_user_id = data.get("target_user_id")
    if not target_user_id:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка: ID пользователя не найден."
        )
        await state.finish()
        return
    await send_message_safe(
        bot=bot,
        chat_id=target_user_id,
        text=f"📩 Ответ от администратора:\n\n{message.text}"
    )
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=f"✅ Сообщение отправлено пользователю {target_user_id}."
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("ban_user_"))
async def ban_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    target_user_id = int(callback.data.split("_")[-1])
    await edit_message_if_changed(
        callback=callback,
        text=f"🚫 Введите причину бана для пользователя {target_user_id} (или 'Без причины' для бана без указания причины):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(target_user_id=target_user_id, action="ban")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_ban_user(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    target_user_id = data.get("target_user_id")
    action = data.get("action")
    if action != "ban" or not target_user_id:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка: Некорректное действие или ID пользователя."
        )
        await state.finish()
        return
    reason = message.text if message.text != "Без причины" else None
    await execute_with_retry_async(
        "INSERT OR REPLACE INTO bans (user_id, admin_id, reason, banned_at) VALUES (?, ?, ?, ?)",
        (target_user_id, user_id, reason, int(datetime.now().timestamp()))
    )
    await send_message_safe(
        bot=bot,
        chat_id=target_user_id,
        text=f"🚫 Вы были заблокированы администрацией.\nПричина: {reason or 'Не указана'}"
    )
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=f"✅ Пользователь {target_user_id} заблокирован."
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("unban_user_"))
async def unban_user(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    target_user_id = int(callback.data.split("_")[-1])
    await execute_with_retry_async(
        "DELETE FROM bans WHERE user_id = ?",
        (target_user_id,)
    )
    await send_message_safe(
        bot=bot,
        chat_id=target_user_id,
        text="✅ Вы были разблокированы администрацией."
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Пользователь {target_user_id} разблокирован.",
        reply_markup=await create_user_actions_keyboard(target_user_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("user_info_"))
async def user_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    target_user_id = int(callback.data.split("_")[-1])
    user = await execute_with_retry_async(
        "SELECT first_name, username, join_date FROM users WHERE user_id = ?",
        (target_user_id,)
    )
    if not user:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Пользователь не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    first_name, username, join_date = user[0]
    join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
    is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (target_user_id,))
    text = (
        f"👤 <b>Информация о пользователе</b>\n\n"
        f"🆔 ID: <b>{target_user_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
        f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
        f"🚫 Статус: <b>{'Заблокирован' if is_banned else 'Активен'}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(target_user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "section_links")
async def section_links(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="🔗 <b>Управление ссылками</b>\n\nВыберите действие:",
        reply_markup=await create_links_keyboard()
    )

class AdminStates(StatesGroup):
    RemoveDeveloper = State()
    SystemMessage = State()
    Report = State()
    AddDeveloper = State()
    CreateLink = State()  # Новое состояние для создания ссылок

@dp.callback_query_handler(lambda c: c.data == "create_link")
async def create_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text=(
            "🔗 <b>Создание новой ссылки</b>\n\n"
            "Отправьте контент для ссылки одним из следующих способов:\n\n"
            "1. Просто текст - будет создана текстовая ссылка\n"
            "2. Фото с подписью - будет создана ссылка на фото\n"
            "3. Документ с подписью - будет создана ссылка на файл\n\n"
            "Подпись будет использована как описание ссылки."
        ),
        reply_markup=await create_back_keyboard()
    )
    await AdminStates.CreateLink.set()

@dp.message_handler(state=AdminStates.CreateLink, content_types=types.ContentType.TEXT)
async def process_create_text_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    
    try:
        # Генерируем короткий ID из 8 символов
        link_id = str(uuid4())[:8]
        content_type = "text"
        content_data = message.text
        caption = None  # Для текстовых ссылок подпись не используется
        
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        
        bot_username = (await bot.get_me()).username
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"✅ Текстовая ссылка создана!\n\n"
                f"🆔 ID: <code>{link_id}</code>\n"
                f"🔗 Ссылка: https://t.me/{bot_username}?start={link_id}\n\n"
                f"📝 Содержимое: {content_data[:100]}..."
            )
        )
    except Exception as e:
        logger.error(f"Error creating text link: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка при создании текстовой ссылки. Попробуйте позже."
        )
    
    await state.finish()

@dp.message_handler(state=AdminStates.CreateLink, content_types=types.ContentType.PHOTO)
async def process_create_photo_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    
    try:
        # Генерируем короткий ID из 8 символов
        link_id = str(uuid4())[:8]
        content_type = "photo"
        
        # Получаем самое большое доступное фото
        photo = message.photo[-1]
        content_data = photo.file_id
        caption = message.caption
        
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        
        bot_username = (await bot.get_me()).username
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"✅ Фото-ссылка создана!\n\n"
                f"🆔 ID: <code>{link_id}</code>\n"
                f"🔗 Ссылка: https://t.me/{bot_username}?start={link_id}\n\n"
                f"📷 Подпись: {caption[:100] if caption else 'Нет'}"
            )
        )
    except Exception as e:
        logger.error(f"Error creating photo link: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка при создании фото-ссылки. Попробуйте позже."
        )
    
    await state.finish()

@dp.message_handler(state=AdminStates.CreateLink, content_types=types.ContentType.DOCUMENT)
async def process_create_document_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    
    try:
        # Генерируем короткий ID из 8 символов
        link_id = str(uuid4())[:8]
        content_type = "document"
        content_data = message.document.file_id
        caption = message.caption
        
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        
        bot_username = (await bot.get_me()).username
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=(
                f"✅ Файловая ссылка создана!\n\n"
                f"🆔 ID: <code>{link_id}</code>\n"
                f"🔗 Ссылка: https://t.me/{bot_username}?start={link_id}\n\n"
                f"📄 Имя файла: {message.document.file_name}\n"
                f"📝 Подпись: {caption[:100] if caption else 'Нет'}"
            )
        )
    except Exception as e:
        logger.error(f"Error creating document link: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка при создании файловой ссылки. Попробуйте позже."
        )
    
    await state.finish()

@dp.message_handler(state=AdminStates.CreateLink)
async def process_create_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    try:
        logger.info(f"Received input for link creation: {message.text}")
        content_type, content_data, caption = message.text.split("|", 2)
        content_type = content_type.strip().lower()
        content_data = content_data.strip()
        caption = caption.strip() or None
        if content_type not in ["text", "photo", "document"]:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Некорректный тип контента. Используйте: text, photo, document."
            )
            return
        link_id = str(uuid4())
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        logger.info(f"Link created: ID={link_id}, type={content_type}, creator={user_id}")
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Ссылка создана!\nID: {link_id}\nСсылка: https://t.me/{(await bot.get_me()).username}?start={link_id}"
        )
    except ValueError as e:
        logger.error(f"Invalid input format for link creation: {message.text}, error: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректный формат. Используйте: Тип_контента | Данные| Подпись"
        )
    except sqlite3.OperationalError as e:
        logger.error(f"Database error during link creation: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка базы данных при создании ссылки. Попробуйте позже."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "delete_link")
async def delete_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="🗑 Введите ID ссылки для удаления:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="delete_link")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_delete_link(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "delete_link":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    link_id = message.text.strip()
    link = await execute_with_retry_async(
        "SELECT 1 FROM links WHERE link_id = ?",
        (link_id,)
    )
    if not link:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ссылка не найдена."
        )
        await state.finish()
        return
    await execute_with_retry_async(
        "DELETE FROM links WHERE link_id = ?",
        (link_id,)
    )
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    await update_stats(total_links=total_links)
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=f"✅ Ссылка {link_id} удалена."
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("list_links_"))
async def list_links(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="🔗 <b>Список ссылок</b>\n\nВыберите ссылку для просмотра:",
        reply_markup=await create_links_list_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("link_info_"))
async def link_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    link_id = callback.data.split("_")[-1]
    link = await execute_with_retry_async(
        "SELECT content_type, content_data, caption, creator_id, created_at, visits FROM links WHERE link_id = ?",
        (link_id,)
    )
    if not link:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ссылка не найдена.",
            reply_markup=await create_back_keyboard()
        )
        return
    content_type, content_data, caption, creator_id, created_at, visits = link[0]
    creator_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)) else f"Админ {creator_id}"
    created_at_str = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    text = (
        f"🔗 <b>Информация о ссылке</b>\n\n"
        f"🆔 ID: <b>{link_id}</b>\n"
        f"Тип: <b>{content_type}</b>\n"
        f"Данные: <b>{content_data}</b>\n"
        f"Подпись: <b>{caption or 'Нет'}</b>\n"
        f"👑 Создатель: <b>{creator_name}</b>\n"
        f"📅 Дата создания: <b>{created_at_str}</b>\n"
        f"🔗 Переходов: <b>{visits}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_link_actions_keyboard(link_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_delete_"))
async def confirm_delete_link(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    link_id = callback.data.split("_")[-1]
    await edit_message_if_changed(
        callback=callback,
        text=f"🗑 Вы уверены, что хотите удалить ссылку {link_id}?",
        reply_markup=await create_confirm_delete_keyboard(link_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_link_"))
async def delete_link_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    link_id = callback.data.split("_")[-1]
    await execute_with_retry_async(
        "DELETE FROM links WHERE link_id = ?",
        (link_id,)
    )
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    await update_stats(total_links=total_links)
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Ссылка {link_id} удалена.",
        reply_markup=await create_links_list_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_ads")
async def section_ads(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📢 <b>Управление рекламой</b>\n\nВыберите действие:",
        reply_markup=await create_ads_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_channel")
async def add_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📢 Введите ID канала и его название в формате: ID|Название",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_channel")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_add_channel(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "add_channel":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    try:
        channel_id, title = message.text.split("|", 1)
        channel_id = channel_id.strip()
        title = title.strip()
        await edit_message_if_changed(
            callback=message,
            text="📢 Выберите область проверки подписки для канала:",
            reply_markup=await create_channel_scope_keyboard()
        )
        await state.update_data(channel_id=channel_id, title=title)
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректный формат. Используйте: ID|Название"
        )
        await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("scope_"))
async def set_channel_scope(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    channel_id = data.get("channel_id")
    title = data.get("title")
    if not channel_id or not title:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка: данные канала не найдены.",
            reply_markup=await create_back_keyboard()
        )
        await state.finish()
        return
    scope = callback.data.split("_")[-1]
    check_scope = "links_only" if scope == "links_only" else "all_functions"
    await execute_with_retry_async(
        "INSERT OR REPLACE INTO channels (channel_id, title, check_scope) VALUES (?, ?, ?)",
        (channel_id, title, check_scope)
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Канал {title} добавлен с областью проверки: {await scope_text(check_scope)}.",
        reply_markup=await create_ads_keyboard()
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "remove_channel")
async def remove_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="📢 Выберите канал для удаления:",
        reply_markup=await create_channels_keyboard(action="confirm_remove_channel")
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_remove_channel_"))
async def confirm_remove_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    channel_id = callback.data.split("_")[-1]
    channel = await execute_with_retry_async(
        "SELECT title FROM channels WHERE channel_id = ?",
        (channel_id,)
    )
    if not channel:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Канал не найден.",
            reply_markup=await create_ads_keyboard()
        )
        return
    title = channel[0][0]
    await edit_message_if_changed(
        callback=callback,
        text=f"🗑 Вы уверены, что хотите удалить канал {title}?",
        reply_markup=await create_confirm_channel_delete_keyboard(channel_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("delete_channel_"))
async def delete_channel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    channel_id = callback.data.split("_")[-1]
    channel = await execute_with_retry_async(
        "SELECT title FROM channels WHERE channel_id = ?",
        (channel_id,)
    )
    if not channel:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Канал не найден.",
            reply_markup=await create_ads_keyboard()
        )
        return
    title = channel[0][0]
    await execute_with_retry_async(
        "DELETE FROM channels WHERE channel_id = ?",
        (channel_id,)
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Канал {title} удален.",
        reply_markup=await create_ads_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "channel_list")
async def channel_list(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
            text="📢 Список каналов пуст.",
            reply_markup=await create_ads_keyboard()
        )
        return
    text = "📢 <b>Список каналов</b>\n\n"
    for channel_id, title, check_scope in channels:
        text += f"🆔 {channel_id}\nНазвание: {title}\nОбласть проверки: {await scope_text(check_scope)}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_ads_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "section_admins")
async def section_admins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👑 <b>Управление администраторами</b>\n\nВыберите действие:",
        reply_markup=await create_admins_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def add_admin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👑 Введите ID пользователя для добавления в администраторы:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_admin")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_add_admin(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "add_admin":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    try:
        new_admin_id = int(message.text)
        user = await execute_with_retry_async(
            "SELECT first_name FROM users WHERE user_id = ?",
            (new_admin_id,)
        )
        if not user:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не найден."
            )
            await state.finish()
            return
        await execute_with_retry_async(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)",
            (new_admin_id,)
        )
        await send_message_safe(
            bot=bot,
            chat_id=new_admin_id,
            text="👑 Вы были назначены администратором!"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Пользователь {new_admin_id} добавлен в администраторы."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID пользователя (число)."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "remove_admin")
async def remove_admin(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👑 Введите ID администратора для удаления:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="remove_admin")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_remove_admin(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if await is_banned(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🚫 Вы заблокированы в боте."
        )
        await state.finish()
        return
    if not await is_admin(user_id):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав администратора."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "remove_admin":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    try:
        admin_id = int(message.text)
        if admin_id in DEVELOPER_IDS:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⛔ Нельзя удалить разработчика через эту команду."
            )
            await state.finish()
            return
        admin = await execute_with_retry_async(
            "SELECT 1 FROM admins WHERE user_id = ?",
            (admin_id,)
        )
        if not admin:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Администратор не найден."
            )
            await state.finish()
            return
        await execute_with_retry_async(
            "DELETE FROM admins WHERE user_id = ?",
            (admin_id,)
        )
        await send_message_safe(
            bot=bot,
            chat_id=admin_id,
            text="🚫 Вы были удалены из администраторов."
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Администратор {admin_id} удален."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID администратора (число)."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("list_admins_"))
async def list_admins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
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
        text="👑 <b>Список администраторов</b>\n\nВыберите администратора для просмотра:",
        reply_markup=await create_admins_list_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("admin_info_"))
async def admin_info(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    admin_id = int(callback.data.split("_")[-1])
    admin = await execute_with_retry_async(
        "SELECT first_name, username FROM users WHERE user_id = ?",
        (admin_id,)
    )
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
        f"🆔 ID: <b>{admin_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_admin_actions_keyboard(admin_id)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("remove_admin_"))
async def remove_admin_confirm(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    if not await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="⛔ У вас нет прав администратора.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    admin_id = int(callback.data.split("_")[-1])
    if admin_id in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Нельзя удалить разработчика через эту команду.",
            reply_markup=await create_admins_list_keyboard()
        )
        return
    await execute_with_retry_async(
        "DELETE FROM admins WHERE user_id = ?",
        (admin_id,)
    )
    await send_message_safe(
        bot=bot,
        chat_id=admin_id,
        text="🚫 Вы были удалены из администраторов."
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Администратор {admin_id} удален.",
        reply_markup=await create_admins_list_keyboard()
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
        text="🛠 <b>Панель разработчика</b>\n\nВыберите раздел:",
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
        text="🖥 <b>Управление сервером</b>\n\nВыберите действие:",
        reply_markup=await create_developer_server_keyboard()
    )

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
    uptime = time.time() - start_time
    uptime_str = f"{int(uptime // 3600)}h {int((uptime % 3600) // 60)}m {int(uptime % 60)}s"
    cpu_usage = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    text = (
        f"🖥 <b>Статус сервера</b>\n\n"
        f"🟢 Статус бота: <b>{'Включен' if BOT_ENABLED else 'Отключен'}</b>\n"
        f"⏳ Время работы: <b>{uptime_str}</b>\n"
        f"💻 CPU: <b>{cpu_usage}%</b>\n"
        f"🧠 RAM: <b>{memory.percent}% ({memory.used / (1024**3):.2f} GB / {memory.total / (1024**3):.2f} GB)</b>\n"
        f"💾 Диск: <b>{disk.percent}% ({disk.used / (1024**3):.2f} GB / {disk.total / (1024**3):.2f} GB)</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developer_server_keyboard()
    )

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
    BOT_ENABLED = True
    await edit_message_if_changed(
        callback=callback,
        text="🟢 Бот включен.",
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
    BOT_ENABLED = False
    await edit_message_if_changed(
        callback=callback,
        text="🔴 Бот отключен.",
        reply_markup=await create_developer_server_keyboard()
    )

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
    await edit_message_if_changed(
        callback=callback,
        text="🔄 Перезагрузка бота...",
        reply_markup=None
    )
    try:
        await bot.close()
        os.execl(sys.executable, sys.executable, *sys.argv)
    except Exception as e:
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка перезагрузки: {str(e)}",
            reply_markup=await create_developer_server_keyboard()
        )

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
    await edit_message_if_changed(
        callback=callback,
        text="🛑 Аварийное выключение...",
        reply_markup=None
    )
    try:
        await bot.close()
        os._exit(0)
    except Exception as e:
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка аварийного выключения: {str(e)}",
            reply_markup=await create_developer_server_keyboard()
        )

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
        text="🗄 <b>Управление базой данных</b>\n\nВыберите действие:",
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
        await bot.send_document(
            chat_id=callback.message.chat.id,
            document=types.InputFile(DATABASE_FILE),
            caption="📥 База данных"
        )
    except Exception as e:
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка отправки базы данных: {str(e)}",
            reply_markup=await create_developer_database_keyboard()
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
    await edit_message_if_changed(
        callback=callback,
        text="📤 Отправьте файл базы данных (.db):",
        reply_markup=await create_developer_database_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="upload_database")

@dp.message_handler(content_types=[types.ContentType.DOCUMENT], state=AdminStates.SystemMessage)
async def process_upload_database(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "upload_database":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    document = message.document
    if not document.file_name.endswith(".db"):
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, отправьте файл базы данных с расширением .db"
        )
        await state.finish()
        return
    try:
        file_path = f"temp_{document.file_name}"
        await document.download(destination_file=file_path)
        shutil.copy(file_path, DATABASE_FILE)
        os.remove(file_path)
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="✅ База данных успешно загружена."
        )
    except Exception as e:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Ошибка загрузки базы данных: {str(e)}"
        )
    await state.finish()

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
    try:
        os.remove(DATABASE_FILE)
        await init_database()
        await edit_message_if_changed(
            callback=callback,
            text="✅ База данных сброшена и инициализирована заново.",
            reply_markup=await create_developer_database_keyboard()
        )
    except Exception as e:
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка сброса базы данных: {str(e)}",
            reply_markup=await create_developer_database_keyboard()
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
        text="📢 <b>Управление системными сообщениями</b>\n\nВыберите действие:",
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
    await edit_message_if_changed(
        callback=callback,
        text="📩 Введите текст системного сообщения для рассылки всем пользователям:",
        reply_markup=await create_developer_messages_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="send_system_message")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_send_system_message(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "send_system_message":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректное действие."
        )
        await state.finish()
        return
    message_text = message.text
    message_id = str(uuid4())
    users = await execute_with_retry_async("SELECT user_id FROM users")
    sent_count = 0
    async with RetryClient() as client:
        for (user_id,) in users:
            try:
                async with client.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={
                    "chat_id": user_id,
                    "text": f"📢 <b>Системное сообщение:</b>\n\n{message_text}",
                    "parse_mode": "HTML"
                }) as response:
                    if (await response.json()).get('ok'):
                        sent_count += 1
            except Exception as e:
                logger.error(f"Failed to send system message to {user_id}: {str(e)}")
            await asyncio.sleep(0.03)  # Пауза для соблюдения лимитов Telegram
    await execute_with_retry_async(
        "INSERT INTO system_messages (message_id, message_text, sent_at, sender_id) VALUES (?, ?, ?, ?)",
        (message_id, message_text, int(datetime.now().timestamp()), user_id)
    )
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=f"✅ Системное сообщение отправлено {sent_count} пользователям."
    )
    await state.finish()

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
    messages = await execute_with_retry_async(
        "SELECT message_id, message_text, sent_at, sender_id FROM system_messages ORDER BY sent_at DESC LIMIT 5"
    )
    if not messages:
        await edit_message_if_changed(
            callback=callback,
            text="📜 История системных сообщений пуста.",
            reply_markup=await create_developer_messages_keyboard()
        )
        return
    text = "📜 <b>История системных сообщений</b>\n\n"
    for message_id, message_text, sent_at, sender_id in messages:
        sender_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)) else f"Админ {sender_id}"
        date = datetime.fromtimestamp(sent_at).strftime("%d.%m.%Y %H:%M")
        text += f"🆔 {message_id}\n👤 {sender_name}\n📅 {date}\n📝 {message_text[:100]}{'...' if len(message_text) > 100 else ''}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developer_messages_keyboard()
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
        text="👨‍💻 <b>Управление разработчиками</b>\n\nВыберите действие:",
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
    await edit_message_if_changed(
        callback=callback,
        text="👨‍💻 Введите ID и имя пользователя для добавления в разработчики (формат: ID|Имя):",
        reply_markup=await create_developers_keyboard()
    )
    await state.set_state(AdminStates.AddDeveloper)

@dp.message_handler(state=AdminStates.AddDeveloper)
async def process_add_developer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    try:
        dev_id, dev_name = message.text.split("|", 1)
        dev_id = int(dev_id.strip())
        dev_name = dev_name.strip()
        await execute_with_retry_async(
            "INSERT OR REPLACE INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
            (dev_id, dev_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        DEVELOPER_IDS.add(dev_id)
        await send_message_safe(
            bot=bot,
            chat_id=dev_id,
            text="👨‍💻 Вы были назначены разработчиком!"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Пользователь {dev_name} (ID: {dev_id}) добавлен в разработчики."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Некорректный формат. Используйте: ID|Имя"
        )
    await state.finish()

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
    await edit_message_if_changed(
        callback=callback,
        text="🗑 Введите ID разработчика для удаления:",
        reply_markup=await create_developers_keyboard()
    )
    await state.set_state(AdminStates.RemoveDeveloper)

@dp.message_handler(state=AdminStates.RemoveDeveloper)
async def process_remove_developer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ У вас нет прав разработчика."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    try:
        dev_id = int(message.text)
        if dev_id == user_id:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⛔ Нельзя удалить самого себя из разработчиков."
            )
            await state.finish()
            return
        developer = await execute_with_retry_async(
            "SELECT username FROM developers WHERE user_id = ?",
            (dev_id,)
        )
        if not developer:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Разработчик не найден."
            )
            await state.finish()
            return
        await execute_with_retry_async(
            "DELETE FROM developers WHERE user_id = ?",
            (dev_id,)
        )
        DEVELOPER_IDS.discard(dev_id)
        await send_message_safe(
            bot=bot,
            chat_id=dev_id,
            text="🚫 Вы были удалены из разработчиков."
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Разработчик {dev_id} удален."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID разработчика (число)."
        )
    await state.finish()

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
            text="👨‍💻 Список разработчиков пуст.",
            reply_markup=await create_developers_keyboard()
        )
        return
    text = "👨‍💻 <b>Список разработчиков</b>\n\n"
    for dev_id, username, added_at in developers:
        text += f"🆔 {dev_id}\nИмя: {username}\nДобавлен: {added_at}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developers_keyboard()
    )

async def on_startup(_):
    await init_database()
    logger.info("Bot started")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True, on_startup=on_startup)