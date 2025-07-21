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
import re

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
ADMINS = {7057452528, 7236484299, 6634823286, 8153569100}  # Replace with actual admin ID(s)
DEVELOPER_IDS = {7057452528}
DATABASE_FILE = "bot_mirrozz_database.db"
BOT_ENABLED = True
start_time = time.time()

# Инициализация бота и диспетчера
bot = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Кэши
subgram_cache = TTLCache(maxsize=1000, ttl=0)  # Увеличено с 3 до 30 секунд
callback_cache = TTLCache(maxsize=1000, ttl=10)
subscription_cache = TTLCache(maxsize=1000, ttl=0)  # Кэш для результатов проверки подписки

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
                    await asyncio.sleep(delay * (2 ** attempt))  # Exponential backoff
                elif "database disk image is malformed" in str(e):
                    logger.error("Database corrupted, attempting repair")
                    await repair_database()
                    if attempt == retries - 1:
                        raise
                else:
                    logger.error(f"Database error: {str(e)}")
                    raise
            finally:
                await cursor.close()
    logger.error("Database is locked after maximum retries")
    raise sqlite3.OperationalError("Database is locked after maximum retries")

async def repair_database():
    backup_file = f"{DATABASE_FILE}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    try:
        logger.info("Creating database backup before repair")
        shutil.copy(DATABASE_FILE, backup_file)
        with open("dump.sql", "w") as f:
            for line in sqlite3.connect(DATABASE_FILE).iterdump():
                f.write(f"{line}\n")
        os.rename(DATABASE_FILE, f"{DATABASE_FILE}.corrupted")
        async with aiosqlite.connect(DATABASE_FILE) as conn:
            with open("dump.sql", "r") as f:
                await conn.executescript(f.read())
            await conn.commit()
        os.remove("dump.sql")
        logger.info("Database repaired successfully")
    except Exception as e:
        logger.error(f"Failed to repair database: {str(e)}")
        shutil.copy(backup_file, DATABASE_FILE)  # Restore backup
        raise

async def is_admin(user_id: int) -> bool:
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

async def edit_message_if_changed(callback: types.CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    serialized_markup = [[button.to_dict() for button in row] for row in reply_markup.inline_keyboard] if reply_markup else []
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
    except MessageNotModified:
        logger.debug(f"Message edit skipped for callback {callback.data}: Content not modified")
        await send_message_safe(bot, callback.message.chat.id, text, reply_markup)
    except InvalidQueryID:
        logger.debug(f"Message edit skipped for callback {callback.data}: Invalid query")
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
        # Create a backup before initialization
        if os.path.exists(DATABASE_FILE):
            backup_file = f"{DATABASE_FILE}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy(DATABASE_FILE, backup_file)
            logger.info(f"Database backed up to {backup_file}")

        # Use file locking if available (Unix systems)
        if fcntl:
            with open(lock_file, 'a') as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    await _init_database()
                finally:
                    os.remove(lock_file)
        else:
            # For non-Unix systems, proceed without locking but log a warning
            logger.warning("fcntl not available, proceeding without file locking")
            await _init_database()

    except Exception as e:
        logger.error(f"Database initialization failed: {str(e)}")
        if os.path.exists(lock_file):
            os.remove(lock_file)
        raise

async def _init_database():
    async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
        # Enable WAL mode for better concurrency
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.commit()

        # Check database integrity
        cursor = await conn.execute("PRAGMA integrity_check")
        integrity_result = await cursor.fetchone()
        if integrity_result[0] != 'ok':
            logger.error(f"Database integrity check failed: {integrity_result[0]}")
            await repair_database()
            # Reconnect after repair
            async with aiosqlite.connect(DATABASE_FILE, timeout=10) as conn:
                await conn.execute("PRAGMA journal_mode=WAL")
                await conn.commit()

        # Check if database is initialized
        cursor = await conn.execute("PRAGMA table_info(stats)")
        if await cursor.fetchall():
            logger.debug("Database already initialized, checking for missing columns")
            # Check and add missing columns
            cursor = await conn.execute("PRAGMA table_info(channels)")
            columns = [info[1] for info in await cursor.fetchall()]
            if 'check_scope' not in columns:
                logger.info("Adding check_scope column to channels table")
                await conn.execute("ALTER TABLE channels ADD COLUMN check_scope TEXT DEFAULT 'links_only'")
                await conn.commit()
            cursor = await conn.execute("PRAGMA table_info(subscriptions)")
            columns = [info[1] for info in await cursor.fetchall()]
            if 'subgram_links' not in columns:
                logger.info("Adding subgram_links column to subscriptions table")
                await conn.execute("ALTER TABLE subscriptions ADD COLUMN subgram_links TEXT DEFAULT '[]'")
                await conn.commit()
            if 'subgram_timestamp' not in columns:
                logger.info("Adding subgram_timestamp column to subscriptions table")
                await conn.execute("ALTER TABLE subscriptions ADD COLUMN subgram_timestamp INTEGER")
                await conn.commit()
            if 'gender' not in columns:
                logger.info("Adding gender column to subscriptions table")
                await conn.execute("ALTER TABLE subscriptions ADD COLUMN gender TEXT")
                await conn.commit()
            logger.info("Database schema updated")
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

        # Adding indexes for optimization
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

        # Initialize default data
        await execute_with_retry_async("INSERT OR IGNORE INTO stats (id, total_users, link_visits, total_links) VALUES (1, 0, 0, 0)")
        for admin_id in ADMINS:
            await execute_with_retry_async("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (admin_id,))
        for dev_id in DEVELOPER_IDS:
            await execute_with_retry_async(
                "INSERT OR IGNORE INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
                (dev_id, "Unknown", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )

        logger.info("Database initialization completed")

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
            reply_markup=keyboard.add(InlineKeyboardButton(text="✅ Я выполнил", callback_data=callback_data))
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
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Подпишитесь на каналы в первом сообщении, затем нажмите 'Я выполнил' и нажмите на кнопку 'Я подписался' в этом сообщении:"
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
        keyboard.add(InlineKeyboardButton(text="✅ Я выполнил", callback_data="check_local_subscription_help"))
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
                f"Подпишитесь на каналы в первом сообщении, затем нажмите 'Я выполнил' и нажмите на кнопку 'Я подписался' в этом сообщении:"
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
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🔗 Просмотров ссылок: <b>{link_visits}</b>\n"
        f"📜 Создано ссылок: <b>{total_links}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_admin_main_keyboard(user_id)
    )

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
        text="📩 <b>Репорты</b>\n\nВыберите непроверенные репорты:",
        reply_markup=await create_reports_keyboard()
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
            reply_markup=await create_reports_keyboard()
        )
        return
    reporter_id, message_text, created_at, is_checked = report[0]
    user_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (reporter_id,)) else f"Пользователь {reporter_id}"
    date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    text = (
        f"📩 <b>Репорт</b>\n\n"
        f"🆔 ID: <b>{report_id}</b>\n"
        f"👤 Пользователь: <b>{user_name}</b> (ID: {reporter_id})\n"
        f"📅 Дата: <b>{date}</b>\n"
        f"📝 Сообщение: <b>{message_text}</b>\n"
        f"✅ Проверен: <b>{'Да' if is_checked else 'Нет'}</b>"
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
        text="✅ Репорт удален.",
        reply_markup=await create_reports_keyboard()
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
    await edit_message_if_changed(
        callback=callback,
        text="📩 Введите сообщение для отправки пользователю:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="send_message", target_user_id=target_user_id)

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
    action = data.get("action")
    if action == "send_message":
        target_user_id = data.get("target_user_id")
        message_text = message.text.strip()
        try:
            await send_message_safe(
                bot=bot,
                chat_id=target_user_id,
                text=f"📩 <b>Сообщение от администратора:</b>\n\n{message_text}"
            )
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Сообщение отправлено пользователю {target_user_id}.",
                reply_markup=await create_back_keyboard()
            )
        except Exception as e:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка отправки сообщения: {str(e)}",
                reply_markup=await create_back_keyboard()
            )
        await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("ban_user_"))
async def ban_user(callback: types.CallbackQuery, state: FSMContext):
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
        text="🚫 Введите причину бана:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="ban_user", target_user_id=target_user_id)

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
    action = data.get("action")
    if action == "ban_user":
        target_user_id = data.get("target_user_id")
        reason = message.text.strip()
        await execute_with_retry_async(
            "INSERT OR REPLACE INTO bans (user_id, admin_id, reason, banned_at) VALUES (?, ?, ?, ?)",
            (target_user_id, user_id, reason, int(datetime.now().timestamp()))
        )
        await send_message_safe(
            bot=bot,
            chat_id=target_user_id,
            text=f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Пользователь {target_user_id} заблокирован.",
            reply_markup=await create_back_keyboard()
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
        text="✅ Вы были разблокированы в боте."
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Пользователь {target_user_id} разблокирован.",
        reply_markup=await create_back_keyboard()
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
        "SELECT first_name, username, join_date, last_activity FROM users WHERE user_id = ?",
        (target_user_id,)
    )
    if not user:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Пользователь не найден.",
            reply_markup=await create_back_keyboard()
        )
        return
    first_name, username, join_date, last_activity = user[0]
    join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
    last_activity_str = datetime.fromtimestamp(last_activity).strftime("%d.%m.%Y %H:%M") if last_activity else "Нет активности"
    is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (target_user_id,))
    text = (
        f"👤 <b>Информация о пользователе</b>\n\n"
        f"🆔 ID: <b>{target_user_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
        f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
        f"🕒 Последняя активность: <b>{last_activity_str}</b>\n"
        f"🚫 Статус: <b>{'Заблокирован' if is_banned else 'Активен'}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(target_user_id)
    )

@dp.callback_query_handler(lambda c: c.data == "search_user")
async def search_user(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        try:
            await edit_message_if_changed(
                callback=callback,
                text="🚫 Вы заблокированы в боте.",
                reply_markup=None
            )
        except Exception as e:
            logger.error(f"Error editing message for callback {callback.data}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=callback.message.chat.id,
                text="🚫 Вы заблокированы в боте.",
                reply_markup=None
            )
        return
    if not await is_admin(user_id):
        try:
            await edit_message_if_changed(
                callback=callback,
                text="⛔ У вас нет прав администратора.",
                reply_markup=None
            )
        except Exception as e:
            logger.error(f"Error editing message for callback {callback.data}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=callback.message.chat.id,
                text="⛔ У вас нет прав администратора.",
                reply_markup=None
            )
        return
    await update_user_activity(user_id)
    try:
        await edit_message_if_changed(
            callback=callback,
            text="🔎 Введите ID или @username пользователя для поиска:",
            reply_markup=await create_back_keyboard()
        )
    except Exception as e:
        logger.error(f"Error editing message for callback {callback.data}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=callback.message.chat.id,
            text="🔎 Введите ID или @username пользователя для поиска:",
            reply_markup=await create_back_keyboard()
        )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="search_user")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_search_user(message: types.Message, state: FSMContext):
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
    if data.get("action") != "search_user":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверное действие. Попробуйте снова.",
            reply_markup=await create_users_keyboard()
        )
        await state.finish()
        return
    input_text = message.text.strip()
    try:
        # Parse input: either numeric ID or @username
        if input_text.startswith("@"):
            username = input_text[1:]
            user_data = await execute_with_retry_async(
                "SELECT user_id, first_name, username, join_date, last_activity FROM users WHERE username = ?",
                (username,)
            )
        else:
            target_user_id = int(input_text)
            user_data = await execute_with_retry_async(
                "SELECT user_id, first_name, username, join_date, last_activity FROM users WHERE user_id = ?",
                (target_user_id,)
            )
        if not user_data:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не найден.",
                reply_markup=await create_users_keyboard()
            )
            await state.finish()
            return
        target_user_id, first_name, username, join_date, last_activity = user_data[0]
        join_date_str = datetime.fromtimestamp(join_date).strftime("%Y-%m-%d %H:%M:%S") if join_date else "Неизвестно"
        last_activity_str = datetime.fromtimestamp(last_activity).strftime("%Y-%m-%d %H:%M:%S") if last_activity else "Неизвестно"
        is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (target_user_id,))
        text = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"🆔 ID: <b>{target_user_id}</b>\n"
            f"Имя: <b>{first_name}</b>\n"
            f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
            f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
            f"🕒 Последняя активность: <b>{last_activity_str}</b>\n"
            f"🚫 Статус: <b>{'Заблокирован' if is_banned else 'Активен'}</b>"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=text,
            reply_markup=await create_user_actions_keyboard(target_user_id)
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверный формат ID. Введите числовой ID или @username.",
            reply_markup=await create_users_keyboard()
        )
    except Exception as e:
        logger.error(f"Ошибка при поиске пользователя {input_text}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Ошибка: {str(e)}",
            reply_markup=await create_users_keyboard()
        )
    await state.finish()

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
        text="👥 <b>Пользователи</b>\n\nВыберите действие:",
        reply_markup=await create_users_keyboard()
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
        text="🔗 <b>Ссылки</b>\n\nВыберите действие:",
        reply_markup=await create_links_keyboard()
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
        text="📢 <b>Реклама</b>\n\nВыберите действие:",
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
        text="👑 <b>Админы</b>\n\nВыберите действие:",
        reply_markup=await create_admins_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "add_admin")
async def add_admin(callback: types.CallbackQuery, state: FSMContext):
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
        text="👑 Введите ID нового администратора (только число):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_admin")

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_admin_action(message: types.Message, state: FSMContext):
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
    action = data.get("action")
    logger.debug(f"Processing admin action: {action} for user {user_id}")

    if action == "add_admin":
        input_text = message.text.strip()
        try:
            new_admin_id = int(input_text)
            if new_admin_id in ADMINS:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь уже является главным администратором.",
                    reply_markup=await create_admins_keyboard()
                )
                await state.finish()
                return
            if await is_admin(new_admin_id):
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь уже администратор.",
                    reply_markup=await create_admins_keyboard()
                )
                await state.finish()
                return
            user_exists = await execute_with_retry_async(
                "SELECT 1 FROM users WHERE user_id = ?",
                (new_admin_id,)
            )
            if not user_exists:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Пользователь с таким ID не найден в базе пользователей.",
                    reply_markup=await create_admins_keyboard()
                )
                await state.finish()
                return
            await execute_with_retry_async(
                "INSERT INTO admins (user_id) VALUES (?)",
                (new_admin_id,)
            )
            try:
                await send_message_safe(
                    bot=bot,
                    chat_id=new_admin_id,
                    text="👑 Вы были назначены администратором бота!"
                )
            except Exception as e:
                logger.warning(f"Failed to notify new admin {new_admin_id}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Пользователь {new_admin_id} добавлен как администратор.",
                reply_markup=await create_admins_keyboard()
            )
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат ID. Введите только числовой ID.",
                reply_markup=await create_admins_keyboard()
            )
        except Exception as e:
            logger.error(f"Error adding admin {input_text}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при добавлении администратора: {str(e)}",
                reply_markup=await create_admins_keyboard()
            )
        await state.finish()

    elif action == "add_channel":
        input_text = message.text.strip()
        if not (input_text.startswith("@") or input_text.startswith("-100")):
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат. Введите @username или ID канала (начинается с -100).",
                reply_markup=await create_ads_keyboard()
            )
            await state.finish()
            return
        try:
            async with RetryClient() as client:
                async with client.get(f"https://api.telegram.org/bot{TOKEN}/getChat?chat_id={input_text}") as response:
                    chat = await response.json()
                    if not chat.get("ok"):
                        await send_message_safe(
                            bot=bot,
                            chat_id=message.chat.id,
                            text="⚠️ Канал не найден или бот не имеет к нему доступа.",
                            reply_markup=await create_ads_keyboard()
                        )
                        await state.finish()
                        return
                    chat_id = str(chat["result"]["id"])
                    title = chat["result"].get("title", "Без названия")
                    async with client.get(f"https://api.telegram.org/bot{TOKEN}/getChatMember?chat_id={chat_id}&user_id={bot.id}") as member_response:
                        member = await member_response.json()
                        if not member.get("ok") or member["result"]["status"] not in ["administrator", "creator"]:
                            await send_message_safe(
                                bot=bot,
                                chat_id=message.chat.id,
                                text="⚠️ Бот должен быть администратором в этом канале.",
                                reply_markup=await create_ads_keyboard()
                            )
                            await state.finish()
                            return
            existing_channel = await execute_with_retry_async(
                "SELECT 1 FROM channels WHERE channel_id = ?",
                (chat_id,)
            )
            if existing_channel:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот канал уже добавлен.",
                    reply_markup=await create_ads_keyboard()
                )
                await state.finish()
                return
            await execute_with_retry_async(
                "INSERT INTO channels (channel_id, title, check_scope) VALUES (?, ?, ?)",
                (chat_id, title, "links_only")
            )
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Канал {title} добавлен для проверки подписки.",
                reply_markup=await create_ads_keyboard()
            )
            await state.update_data(action="set_channel_scope", channel_id=chat_id)
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="📢 Выберите область проверки подписки для канала:",
                reply_markup=await create_channel_scope_keyboard()
            )
        except Exception as e:
            logger.error(f"Error adding channel {input_text}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при добавлении канала: {str(e)}",
                reply_markup=await create_ads_keyboard()
            )
            await state.finish()

    elif action == "create_link":
        content_type = data.get("content_type")
        content_data = message.text.strip()
        caption = data.get("caption", "")
        link_id = str(uuid4())
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        link_url = f"https://t.me/{(await bot.get_me()).username}?start={link_id}"
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Ссылка создана!\nURL: {link_url}",
            reply_markup=await create_links_keyboard()
        )
        await state.finish()

    elif action == "send_system_message":
        message_text = message.text.strip()
        message_id = str(uuid4())
        users = await execute_with_retry_async("SELECT user_id FROM users")
        sent_count = 0
        for (target_user_id,) in users:
            try:
                await send_message_safe(
                    bot=bot,
                    chat_id=target_user_id,
                    text=f"📢 <b>Системное сообщение:</b>\n\n{message_text}"
                )
                sent_count += 1
                await asyncio.sleep(0.03)  # Соблюдаем лимиты Telegram
            except Exception as e:
                logger.warning(f"Failed to send system message to {target_user_id}: {str(e)}")
        await execute_with_retry_async(
            "INSERT INTO system_messages (message_id, message_text, sent_at, sender_id) VALUES (?, ?, ?, ?)",
            (message_id, message_text, int(datetime.now().timestamp()), user_id)
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Системное сообщение отправлено {sent_count} пользователям.",
            reply_markup=await create_developer_messages_keyboard()
        )
        await state.finish()

    elif action == "add_developer":
        input_text = message.text.strip()
        try:
            new_dev_id = int(input_text)
            if new_dev_id in DEVELOPER_IDS:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь уже является разработчиком.",
                    reply_markup=await create_developers_keyboard()
                )
                await state.finish()
                return
            user = await execute_with_retry_async(
                "SELECT first_name, username FROM users WHERE user_id = ?",
                (new_dev_id,)
            )
            if not user:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Пользователь с таким ID не найден.",
                    reply_markup=await create_developers_keyboard()
                )
                await state.finish()
                return
            first_name, username = user[0]
            await execute_with_retry_async(
                "INSERT INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
                (new_dev_id, username or first_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            DEVELOPER_IDS.add(new_dev_id)
            await send_message_safe(
                bot=bot,
                chat_id=new_dev_id,
                text="🛠 Вы были назначены разработчиком бота!"
            )
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Пользователь {new_dev_id} добавлен как разработчик.",
                reply_markup=await create_developers_keyboard()
            )
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат ID. Введите только числовой ID.",
                reply_markup=await create_developers_keyboard()
            )
        except Exception as e:
            logger.error(f"Error adding developer {input_text}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при добавлении разработчика: {str(e)}",
                reply_markup=await create_developers_keyboard()
            )
        await state.finish()

    elif action == "remove_developer":
        input_text = message.text.strip()
        try:
            dev_id = int(input_text)
            if dev_id not in DEVELOPER_IDS:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Этот пользователь не является разработчиком.",
                    reply_markup=await create_developers_keyboard()
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
                text="🛠 Вы были удалены из списка разработчиков."
            )
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Разработчик {dev_id} удален.",
                reply_markup=await create_developers_keyboard()
            )
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Неверный формат ID. Введите только числовой ID.",
                reply_markup=await create_developers_keyboard()
            )
        except Exception as e:
            logger.error(f"Error removing developer {input_text}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при удалении разработчика: {str(e)}",
                reply_markup=await create_developers_keyboard()
            )
        await state.finish()

    else:
        logger.error(f"Unknown action in SystemMessage state: {action}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неизвестное действие. Пожалуйста, начните заново.",
            reply_markup=await create_admin_main_keyboard(user_id)
        )
        await state.finish()

@dp.callback_query_handler(lambda c: c.data == "add_channel")
async def add_channel(callback: types.CallbackQuery, state: FSMContext):
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
        text="📢 Введите @username или ID канала (начинается с -100):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_channel")

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
        reply_markup=await create_channels_keyboard(action="confirm_delete_channel")
    )

@dp.callback_query_handler(lambda c: c.data.startswith("confirm_delete_channel_"))
async def confirm_delete_channel(callback: types.CallbackQuery):
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
        text=f"⚠️ Вы уверены, что хотите удалить канал {title}?",
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
            text="📢 Нет добавленных каналов.",
            reply_markup=await create_ads_keyboard()
        )
        return
    text = "📢 <b>Список каналов</b>\n\n"
    for channel_id, title, check_scope in channels:
        text += f"🆔 {channel_id}\n📛 {title}\n🔍 Область проверки: {await scope_text(check_scope)}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_ads_keyboard()
    )

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
    if not channel_id:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка: канал не выбран.",
            reply_markup=await create_ads_keyboard()
        )
        await state.finish()
        return
    scope = "links_only" if callback.data == "scope_links_only" else "all_functions"
    await execute_with_retry_async(
        "UPDATE channels SET check_scope = ? WHERE channel_id = ?",
        (scope, channel_id)
    )
    channel = await execute_with_retry_async(
        "SELECT title FROM channels WHERE channel_id = ?",
        (channel_id,)
    )
    title = channel[0][0] if channel else "Неизвестный канал"
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Область проверки для канала {title} установлена: {await scope_text(scope)}",
        reply_markup=await create_ads_keyboard()
    )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "create_link")
async def create_link(callback: types.CallbackQuery, state: FSMContext):
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
    keyboard = InlineKeyboardMarkup(row_width=1)
    keyboard.add(
        InlineKeyboardButton(text="📝 Текст", callback_data="link_type_text"),
        InlineKeyboardButton(text="🖼 Фото", callback_data="link_type_photo"),
        InlineKeyboardButton(text="📎 Документ", callback_data="link_type_document"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="section_links")
    )
    await edit_message_if_changed(
        callback=callback,
        text="🔗 Выберите тип контента для ссылки:",
        reply_markup=keyboard
    )

@dp.callback_query_handler(lambda c: c.data.startswith("link_type_"))
async def select_link_type(callback: types.CallbackQuery, state: FSMContext):
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
    content_type = callback.data.split("_")[-1]
    await state.update_data(content_type=content_type, action="create_link")
    if content_type == "text":
        await edit_message_if_changed(
            callback=callback,
            text="📝 Введите текст для ссылки:",
            reply_markup=await create_back_keyboard()
        )
        await state.set_state(AdminStates.SystemMessage)
    elif content_type in ["photo", "document"]:
        await edit_message_if_changed(
            callback=callback,
            text=f"📎 Пришлите {'фото' if content_type == 'photo' else 'документ'}:",
            reply_markup=await create_back_keyboard()
        )
        await state.set_state(AdminStates.SystemMessage)

@dp.message_handler(content_types=["photo", "document"], state=AdminStates.SystemMessage)
async def process_link_content(message: types.Message, state: FSMContext):
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
    action = data.get("action")
    content_type = data.get("content_type")
    if action != "create_link" or content_type not in ["photo", "document"]:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверное действие или тип контента.",
            reply_markup=await create_links_keyboard()
        )
        await state.finish()
        return
    content_data = message.photo[-1].file_id if content_type == "photo" else message.document.file_id
    await state.update_data(content_data=content_data)
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text="📝 Введите подпись для контента (или отправьте /skip для пропуска):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="set_caption")

@dp.message_handler(commands=["skip"], state=AdminStates.SystemMessage)
async def skip_caption(message: types.Message, state: FSMContext):
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
    action = data.get("action")
    if action == "set_caption":
        content_type = data.get("content_type")
        content_data = data.get("content_data")
        link_id = str(uuid4())
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, "", user_id, int(datetime.now().timestamp()))
        )
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        link_url = f"https://t.me/{(await bot.get_me()).username}?start={link_id}"
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Ссылка создана!\nURL: {link_url}",
            reply_markup=await create_links_keyboard()
        )
        await state.finish()

@dp.message_handler(state=AdminStates.SystemMessage)
async def process_caption(message: types.Message, state: FSMContext):
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
    action = data.get("action")
    if action == "set_caption":
        content_type = data.get("content_type")
        content_data = data.get("content_data")
        caption = message.text.strip()
        link_id = str(uuid4())
        await execute_with_retry_async(
            "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
        )
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
        await update_stats(total_links=total_links)
        link_url = f"https://t.me/{(await bot.get_me()).username}?start={link_id}"
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Ссылка создана!\nURL: {link_url}",
            reply_markup=await create_links_keyboard()
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
        text="🔗 Выберите ссылку для удаления:",
        reply_markup=await create_links_list_keyboard()
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
            reply_markup=await create_links_keyboard()
        )
        return
    content_type, content_data, caption, creator_id, created_at, visits = link[0]
    creator_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (creator_id,)) else f"Админ {creator_id}"
    date = datetime.fromtimestamp(created_at).strftime("%d.%m.%Y %H:%M")
    text = (
        f"🔗 <b>Информация о ссылке</b>\n\n"
        f"🆔 ID: <b>{link_id}</b>\n"
        f"📝 Тип: <b>{content_type}</b>\n"
        f"📛 Создатель: <b>{creator_name}</b> (ID: {creator_id})\n"
        f"📅 Дата создания: <b>{date}</b>\n"
        f"👀 Просмотров: <b>{visits}</b>\n"
        f"📜 Подпись: <b>{caption or 'Без подписи'}</b>"
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
        text="⚠️ Вы уверены, что хотите удалить эту ссылку?",
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
        text="✅ Ссылка удалена.",
        reply_markup=await create_links_keyboard()
    )

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
        text="🔗 <b>Список ссылок</b>",
        reply_markup=await create_links_list_keyboard(page=page)
    )

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
        text="👑 <b>Список админов</b>",
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
    user = await execute_with_retry_async(
        "SELECT first_name, username FROM users WHERE user_id = ?",
        (admin_id,)
    )
    if not user:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Админ не найден.",
            reply_markup=await create_admins_list_keyboard()
        )
        return
    first_name, username = user[0]
    text = (
        f"👑 <b>Информация об админе</b>\n\n"
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
    admin_id = int(callback.data.split("_")[-1])
    if admin_id in ADMINS:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Нельзя удалить главного администратора.",
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
        text="👑 Вы были удалены из списка администраторов."
    )
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Админ {admin_id} удален.",
        reply_markup=await create_admins_list_keyboard()
    )

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
        text="🚫 <b>Забаненные пользователи</b>",
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
    ban_info = await execute_with_retry_async(
        "SELECT admin_id, reason, banned_at FROM bans WHERE user_id = ?",
        (banned_user_id,)
    )
    user_info = await execute_with_retry_async(
        "SELECT first_name, username FROM users WHERE user_id = ?",
        (banned_user_id,)
    )
    if not ban_info or not user_info:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Информация о бане не найдена.",
            reply_markup=await create_banned_users_keyboard()
        )
        return
    admin_id, reason, banned_at = ban_info[0]
    first_name, username = user_info[0]
    date = datetime.fromtimestamp(banned_at).strftime("%d.%m.%Y %H:%M")
    admin_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (admin_id,)) else f"Админ {admin_id}"
    text = (
        f"🚫 <b>Информация о бане</b>\n\n"
        f"🆔 Пользователь: <b>{banned_user_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{escape_md(username) if username else 'Нет'}</b>\n"
        f"📅 Дата бана: <b>{date}</b>\n"
        f"👑 Админ: <b>{admin_name}</b>\n"
        f"📝 Причина: <b>{reason}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_user_actions_keyboard(banned_user_id)
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
        text="📢 <b>Системные сообщения</b>\n\nВыберите действие:",
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
async def add_developer(callback: types.CallbackQuery, state: FSMContext):
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
        text="👨‍💻 Введите ID нового разработчика (только число):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_developer")

@dp.callback_query_handler(lambda c: c.data == "remove_developer")
async def remove_developer(callback: types.CallbackQuery, state: FSMContext):
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
        text="🗑 Введите ID разработчика для удаления (только число):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="remove_developer")

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
            text="👨‍💻 Нет добавленных разработчиков.",
            reply_markup=await create_developers_keyboard()
        )
        return
    text = "👨‍💻 <b>Список разработчиков</b>\n\n"
    for dev_id, username, added_at in developers:
        text += f"🆔 {dev_id}\n👤 @{username or 'Без имени'}\n📅 Добавлен: {added_at}\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developers_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "send_system_message")
async def send_system_message(callback: types.CallbackQuery, state: FSMContext):
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
        text="📢 Введите текст системного сообщения для всех пользователей:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="send_system_message")

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
            text="📜 Нет системных сообщений.",
            reply_markup=await create_developer_messages_keyboard()
        )
        return
    text = "📜 <b>История системных сообщений</b>\n\n"
    for message_id, message_text, sent_at, sender_id in messages:
        sender_name = (await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)))[0][0] if await execute_with_retry_async("SELECT first_name FROM users WHERE user_id = ?", (sender_id,)) else f"Админ {sender_id}"
        date = datetime.fromtimestamp(sent_at).strftime("%d.%m.%Y %H:%M")
        text += f"🆔 {message_id}\n👤 {sender_name}\n📅 {date}\n📝 {message_text[:100]}...\n\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developer_messages_keyboard()
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
            chat_id=user_id,
            document=types.InputFile(DATABASE_FILE),
            caption="🗄 Текущая база данных"
        )
        await edit_message_if_changed(
            callback=callback,
            text="✅ База данных отправлена.",
            reply_markup=await create_developer_database_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to send database: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка при отправке базы данных: {str(e)}",
            reply_markup=await create_developer_database_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "upload_database")
async def upload_database(callback: types.CallbackQuery, state: FSMContext):
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
        text="📤 Пришлите файл базы данных (.db):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="upload_database")

@dp.message_handler(content_types=["document"], state=AdminStates.SystemMessage)
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
    action = data.get("action")
    if action != "upload_database":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверное действие."
        )
        await state.finish()
        return
    try:
        file_info = await bot.get_file(message.document.file_id)
        file_path = file_info.file_path
        if not file_path.endswith(".db"):
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Файл должен быть в формате .db",
                reply_markup=await create_developer_database_keyboard()
            )
            await state.finish()
            return
        backup_file = f"{DATABASE_FILE}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(DATABASE_FILE, backup_file)
        await bot.download_file(file_path, DATABASE_FILE)
        await init_database()  # Проверка целостности новой базы
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="✅ База данных успешно загружена и проверена.",
            reply_markup=await create_developer_database_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to upload database: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Ошибка при загрузке базы данных: {str(e)}",
            reply_markup=await create_developer_database_keyboard()
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
        backup_file = f"{DATABASE_FILE}.bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.copy(DATABASE_FILE, backup_file)
        os.remove(DATABASE_FILE)
        await init_database()
        await edit_message_if_changed(
            callback=callback,
            text="✅ База данных сброшена и инициализирована заново.",
            reply_markup=await create_developer_database_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to reset database: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка при сбросе базы данных: {str(e)}",
            reply_markup=await create_developer_database_keyboard()
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
    uptime = int(time.time() - start_time)
    uptime_str = f"{uptime // 3600} ч {(uptime % 3600) // 60} мин {uptime % 60} сек"
    cpu_usage = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    memory_usage = f"{memory.used / 1024**3:.2f}/{memory.total / 1024**3:.2f} GB ({memory.percent}%)"
    text = (
        f"📊 <b>Статус сервера</b>\n\n"
        f"🟢 Бот: <b>{'Включен' if BOT_ENABLED else 'Отключен'}</b>\n"
        f"⏳ Время работы: <b>{uptime_str}</b>\n"
        f"💻 CPU: <b>{cpu_usage}%</b>\n"
        f"🧠 Память: <b>{memory_usage}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
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
        text="🔴 Бот отключен. Только панель разработчика доступна.",
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
        python = sys.executable
        os.execl(python, python, *sys.argv)
    except Exception as e:
        logger.error(f"Failed to restart bot: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка при перезагрузке: {str(e)}",
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
        text="🛑 Аварийное завершение работы...",
        reply_markup=None
    )
    try:
        await bot.close()
        sys.exit(0)
    except Exception as e:
        logger.error(f"Failed to shutdown bot: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text=f"⚠️ Ошибка при аварийном завершении: {str(e)}",
            reply_markup=await create_developer_server_keyboard()
        )

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
    action = callback.data.split("_")[-1]
    check_all_functions = action in ["start", "help"]
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions)
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я выполнил", callback_data=f"check_local_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к {'скрипту' if action not in ['start', 'help'] else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_subgram_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Подпишитесь на каналы выше, затем нажмите кнопку ниже:"
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
            callback=callback,
            text=welcome_text,
            reply_markup=None
        )
    elif action == "help":
        text = (
            f"📚 <b>Команды Mirrozz Scripts</b>\n\n"
            f"👋 /start — Запустить бота и получить приветствие\n"
            f"📊 /user_stats — Посмотреть статистику твоего профиля\n"
            f"📩 /report [сообщение] — Отправить жалобу админам\n"
        )
        if await is_admin(user_id):
            text += f"👑 /admin — Открыть панель администратора"
        await edit_message_if_changed(
            callback=callback,
            text=text,
            reply_markup=None
        )
    else:
        await handle_link_visit(callback.message, action)

@dp.callback_query_handler(lambda c: c.data.startswith("check_subgram_subscription_"))
async def check_subgram_subscription(callback: types.CallbackQuery):
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
    check_all_functions = action in ["start", "help"]
    unsubscribed_local = await check_subscriptions(user_id, check_all_functions)
    if unsubscribed_local:
        channel_list = "\n".join([f"👉 {name}" for name in unsubscribed_local])
        keyboard = InlineKeyboardMarkup()
        keyboard.add(InlineKeyboardButton(text="✅ Я выполнил", callback_data=f"check_local_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на наши каналы</b>\n\n"
                f"Для доступа к {'скрипту' if action not in ['start', 'help'] else 'функциям бота'} подпишитесь на каналы:\n\n{channel_list}\n\n"
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"check_subgram_subscription_{action}"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Подпишитесь на каналы выше, затем нажмите кнопку ниже:"
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
            callback=callback,
            text=welcome_text,
            reply_markup=None
        )
    elif action == "help":
        text = (
            f"📚 <b>Команды Mirrozz Scripts</b>\n\n"
            f"👋 /start — Запустить бота и получить приветствие\n"
            f"📊 /user_stats — Посмотреть статистику твоего профиля\n"
            f"📩 /report [сообщение] — Отправить жалобу админам\n"
        )
        if await is_admin(user_id):
            text += f"👑 /admin — Открыть панель администратора"
        await edit_message_if_changed(
            callback=callback,
            text=text,
            reply_markup=None
        )
    else:
        await handle_link_visit(callback.message, action)

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
    gender = "male" if callback.data.endswith("male") else "female"
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
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="check_subgram_subscription_start"))
        await edit_message_if_changed(
            callback=callback,
            text=(
                f"📢 <b>Подпишитесь на дополнительные каналы</b>\n\n"
                f"Подпишитесь на каналы выше, затем нажмите кнопку ниже:"
            ),
            reply_markup=keyboard
        )
        return
    elif status == 'subscribed':
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
            callback=callback,
            text=welcome_text,
            reply_markup=None
        )
    else:
        logger.error(f"Unexpected subgram status for user {user_id}: {status}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Произошла ошибка при проверке подписки. Попробуйте позже.",
            reply_markup=await create_back_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data.startswith("ban_user_"))
async def ban_user(callback: types.CallbackQuery, state: FSMContext):
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
        text="🚫 Введите причину бана:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="ban_user", target_user_id=target_user_id)

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
    try:
        await send_message_safe(
            bot=bot,
            chat_id=target_user_id,
            text="✅ Вы были разблокированы в боте!"
        )
    except Exception as e:
        logger.warning(f"Failed to notify user {target_user_id} about unban: {str(e)}")
    await edit_message_if_changed(
        callback=callback,
        text=f"✅ Пользователь {target_user_id} разблокирован.",
        reply_markup=await create_users_keyboard()
    )

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
    action = data.get("action")
    if action == "ban_user":
        target_user_id = data.get("target_user_id")
        reason = message.text.strip()
        if not reason:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Причина бана не может быть пустой.",
                reply_markup=await create_users_keyboard()
            )
            await state.finish()
            return
        try:
            await execute_with_retry_async(
                "INSERT INTO bans (user_id, admin_id, reason, banned_at) VALUES (?, ?, ?, ?)",
                (target_user_id, user_id, reason, int(datetime.now().timestamp()))
            )
            try:
                await send_message_safe(
                    bot=bot,
                    chat_id=target_user_id,
                    text=f"🚫 Вы были заблокированы в боте.\nПричина: {reason}"
                )
            except Exception as e:
                logger.warning(f"Failed to notify user {target_user_id} about ban: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Пользователь {target_user_id} заблокирован.\nПричина: {reason}",
                reply_markup=await create_users_keyboard()
            )
        except Exception as e:
            logger.error(f"Error banning user {target_user_id}: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"⚠️ Ошибка при блокировке пользователя: {str(e)}",
                reply_markup=await create_users_keyboard()
            )
        await state.finish()

@dp.callback_query_handler(lambda c: c.data == "search_user")
async def search_user(callback: types.CallbackQuery, state: FSMContext):
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
        text="🔍 Введите ID пользователя или @username:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="search_user")

@dp.callback_query_handler(lambda c: c.data == "back")
async def back_button(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    action = data.get("action")
    if action in ["search_user", "ban_user"]:
        await edit_message_if_changed(
            callback=callback,
            text="👥 <b>Пользователи</b>\n\nВыберите действие:",
            reply_markup=await create_users_keyboard()
        )
    elif action in ["add_channel", "set_channel_scope"]:
        await edit_message_if_changed(
            callback=callback,
            text="📢 <b>Реклама</b>\n\nВыберите действие:",
            reply_markup=await create_ads_keyboard()
        )
    elif action in ["create_link", "set_caption"]:
        await edit_message_if_changed(
            callback=callback,
            text="🔗 <b>Ссылки</b>\n\nВыберите действие:",
            reply_markup=await create_links_keyboard()
        )
    elif action == "add_admin":
        await edit_message_if_changed(
            callback=callback,
            text="👑 <b>Админы</b>\n\nВыберите действие:",
            reply_markup=await create_admins_keyboard()
        )
    elif action in ["add_developer", "remove_developer", "upload_database"]:
        await edit_message_if_changed(
            callback=callback,
            text="👨‍💻 <b>Управление разработчиками</b>\n\nВыберите действие:",
            reply_markup=await create_developers_keyboard()
        )
    elif action == "send_system_message":
        await edit_message_if_changed(
            callback=callback,
            text="📢 <b>Системные сообщения</b>\n\nВыберите действие:",
            reply_markup=await create_developer_messages_keyboard()
        )
    else:
        if await is_admin(user_id):
            await edit_message_if_changed(
                callback=callback,
                text="👑 <b>Админ-панель</b>\n\nВыберите раздел:",
                reply_markup=await create_admin_main_keyboard(user_id)
            )
        else:
            await edit_message_if_changed(
                callback=callback,
                text="⚠️ У вас нет доступа к админ-панели.",
                reply_markup=None
            )
    await state.finish()

async def handle_link_visit(message: types.Message, link_id: str):
    user_id = message.chat.id
    link = await execute_with_retry_async(
        "SELECT content_type, content_data, caption, visits FROM links WHERE link_id = ?",
        (link_id,)
    )
    if not link:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ссылка не найдена или удалена.",
            reply_markup=None
        )
        return
    content_type, content_data, caption, visits = link[0]
    await execute_with_retry_async(
        "UPDATE links SET visits = visits + 1 WHERE link_id = ?",
        (link_id,)
    )
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    await update_stats(total_links=total_links)
    try:
        if content_type == "text":
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=content_data,
                reply_markup=None
            )
        elif content_type == "photo":
            await bot.send_photo(
                chat_id=message.chat.id,
                photo=content_data,
                caption=caption or None
            )
        elif content_type == "document":
            await bot.send_document(
                chat_id=message.chat.id,
                document=content_data,
                caption=caption or None
            )
    except Exception as e:
        logger.error(f"Error sending link content {link_id} to user {user_id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"⚠️ Ошибка при отправке контента: {str(e)}",
            reply_markup=None
        )

async def create_users_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="search_user"),
        InlineKeyboardButton(text="🚫 Забаненные", callback_data="banned_users_1"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_user_actions_keyboard(user_id: int):
    keyboard = InlineKeyboardMarkup(row_width=2)
    is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
    if is_banned:
        keyboard.add(InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_user_{user_id}"))
    else:
        keyboard.add(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_user_{user_id}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_admins_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="➕ Добавить админа", callback_data="add_admin"),
        InlineKeyboardButton(text="📋 Список админов", callback_data="list_admins_1"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_admins_list_keyboard(page: int = 1):
    admins = await execute_with_retry_async("SELECT user_id FROM admins")
    total_pages = (len(admins) + 9) // 10
    page = max(1, min(page, total_pages))
    start = (page - 1) * 10
    end = start + 10
    keyboard = InlineKeyboardMarkup(row_width=2)
    for admin_id, in admins[start:end]:
        user = await execute_with_retry_async(
            "SELECT first_name, username FROM users WHERE user_id = ?",
            (admin_id,)
        )
        name = user[0][0] if user else f"ID: {admin_id}"
        keyboard.add(InlineKeyboardButton(text=name, callback_data=f"admin_info_{admin_id}"))
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"list_admins_{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"list_admins_{page+1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_admin_actions_keyboard(admin_id: int):
    keyboard = InlineKeyboardMarkup(row_width=2)
    if admin_id not in ADMINS:
        keyboard.add(InlineKeyboardButton(text="🗑 Удалить админа", callback_data=f"remove_admin_{admin_id}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_ads_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_channel"),
        InlineKeyboardButton(text="🗑 Удалить канал", callback_data="remove_channel"),
        InlineKeyboardButton(text="📋 Список каналов", callback_data="channel_list"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_channels_keyboard(action: str):
    channels = await execute_with_retry_async("SELECT channel_id, title FROM channels")
    keyboard = InlineKeyboardMarkup(row_width=1)
    for channel_id, title in channels:
        keyboard.add(InlineKeyboardButton(text=title, callback_data=f"{action}_{channel_id}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_confirm_channel_delete_keyboard(channel_id: str):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"delete_channel_{channel_id}"),
        InlineKeyboardButton(text="🔙 Отмена", callback_data="back")
    )
    return keyboard

async def create_links_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="➕ Создать ссылку", callback_data="create_link"),
        InlineKeyboardButton(text="🗑 Удалить ссылку", callback_data="delete_link"),
        InlineKeyboardButton(text="📋 Список ссылок", callback_data="list_links_1"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_links_list_keyboard(page: int = 1):
    links = await execute_with_retry_async("SELECT link_id, content_type FROM links")
    total_pages = (len(links) + 9) // 10
    page = max(1, min(page, total_pages))
    start = (page - 1) * 10
    end = start + 10
    keyboard = InlineKeyboardMarkup(row_width=2)
    for link_id, content_type in links[start:end]:
        keyboard.add(InlineKeyboardButton(text=f"{content_type} {link_id[:8]}...", callback_data=f"link_info_{link_id}"))
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"list_links_{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"list_links_{page+1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_link_actions_keyboard(link_id: str):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_{link_id}"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_confirm_delete_keyboard(link_id: str):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"delete_link_{link_id}"),
        InlineKeyboardButton(text="🔙 Отмена", callback_data="back")
    )
    return keyboard

async def create_banned_users_keyboard(page: int = 1):
    bans = await execute_with_retry_async("SELECT user_id FROM bans")
    total_pages = (len(bans) + 9) // 10
    page = max(1, min(page, total_pages))
    start = (page - 1) * 10
    end = start + 10
    keyboard = InlineKeyboardMarkup(row_width=2)
    for user_id, in bans[start:end]:
        user = await execute_with_retry_async(
            "SELECT first_name, username FROM users WHERE user_id = ?",
            (user_id,)
        )
        name = user[0][0] if user else f"ID: {user_id}"
        keyboard.add(InlineKeyboardButton(text=name, callback_data=f"banned_user_info_{user_id}"))
    nav_buttons = []
    if page > 1:
        nav_buttons.append(InlineKeyboardButton(text="⬅️", callback_data=f"banned_users_{page-1}"))
    if page < total_pages:
        nav_buttons.append(InlineKeyboardButton(text="➡️", callback_data=f"banned_users_{page+1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def create_admin_main_keyboard(user_id: int):
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="👥 Пользователи", callback_data="section_users"),
        InlineKeyboardButton(text="🔗 Ссылки", callback_data="section_links"),
        InlineKeyboardButton(text="📢 Реклама", callback_data="section_ads"),
        InlineKeyboardButton(text="👑 Админы", callback_data="section_admins")
    )
    if user_id in DEVELOPER_IDS:
        keyboard.add(InlineKeyboardButton(text="🛠 Разработчик", callback_data="admin_developer"))
    return keyboard

async def create_developer_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🖥 Сервер", callback_data="developer_server"),
        InlineKeyboardButton(text="🗄 База данных", callback_data="developer_database"),
        InlineKeyboardButton(text="📢 Сообщения", callback_data="developer_messages"),
        InlineKeyboardButton(text="👨‍💻 Разработчики", callback_data="developer_management"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_developer_server_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📊 Статус", callback_data="server_status"),
        InlineKeyboardButton(text="🔴 Отключить", callback_data="disable_bot") if BOT_ENABLED else InlineKeyboardButton(text="🟢 Включить", callback_data="enable_bot"),
        InlineKeyboardButton(text="🔄 Перезагрузить", callback_data="restart_bot"),
        InlineKeyboardButton(text="🛑 Аварийное выключение", callback_data="emergency_shutdown"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_developer_database_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📤 Скачать базу", callback_data="download_database"),
        InlineKeyboardButton(text="📥 Загрузить базу", callback_data="upload_database"),
        InlineKeyboardButton(text="🗑 Сбросить базу", callback_data="reset_database"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_developers_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="➕ Добавить разработчика", callback_data="add_developer"),
        InlineKeyboardButton(text="🗑 Удалить разработчика", callback_data="remove_developer"),
        InlineKeyboardButton(text="📋 Список разработчиков", callback_data="list_developers"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_developer_messages_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="📢 Отправить сообщение", callback_data="send_system_message"),
        InlineKeyboardButton(text="📜 История сообщений", callback_data="message_history"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_channel_scope_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="🔗 Только ссылки", callback_data="scope_links_only"),
        InlineKeyboardButton(text="🌐 Все функции", callback_data="scope_all_functions"),
        InlineKeyboardButton(text="🔙 Назад", callback_data="back")
    )
    return keyboard

async def create_gender_selection_keyboard():
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(text="👨 Мужской", callback_data="subgram_gender_male"),
        InlineKeyboardButton(text="👩 Женский", callback_data="subgram_gender_female")
    )
    return keyboard

async def create_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="back"))
    return keyboard

async def scope_text(scope: str) -> str:
    return "Только ссылки" if scope == "links_only" else "Все функции"

async def check_subscriptions(user_id: int, check_all_functions: bool) -> list:
    channels = await execute_with_retry_async("SELECT channel_id, title, check_scope FROM channels")
    unsubscribed = []
    for channel_id, title, check_scope in channels:
        if check_all_functions or check_scope == "links_only":
            try:
                async with RetryClient() as client:
                    async with client.get(f"https://api.telegram.org/bot{TOKEN}/getChatMember?chat_id={channel_id}&user_id={user_id}") as response:
                        result = await response.json()
                        if not result.get("ok") or result["result"]["status"] in ["left", "kicked"]:
                            unsubscribed.append(title)
            except Exception as e:
                logger.warning(f"Failed to check subscription for user {user_id} in channel {channel_id}: {str(e)}")
                unsubscribed.append(title)
    return unsubscribed

async def update_stats(total_users: int = None, total_links: int = None):
    if total_users is None:
        total_users = (await execute_with_retry_async("SELECT COUNT(*) FROM users"))[0][0]
    if total_links is None:
        total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    await execute_with_retry_async(
        "INSERT OR REPLACE INTO stats (id, total_users, total_links, updated_at) VALUES (?, ?, ?, ?)",
        (1, total_users, total_links, int(datetime.now().timestamp()))
    )

async def update_user_activity(user_id: int):
    await execute_with_retry_async(
        "UPDATE users SET last_activity = ? WHERE user_id = ?",
        (int(datetime.now().timestamp()), user_id)
    )

async def is_admin(user_id: int) -> bool:
    if user_id in ADMINS:
        return True
    return bool(await execute_with_retry_async("SELECT 1 FROM admins WHERE user_id = ?", (user_id,)))

async def is_banned(user_id: int) -> bool:
    return bool(await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (user_id,)))

async def edit_message_if_changed(callback: types.CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        if callback.message.text != text or callback.message.reply_markup != reply_markup:
            await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to edit message for user {callback.from_user.id}: {str(e)}")
        await send_message_safe(
            bot=bot,
            chat_id=callback.message.chat.id,
            text=text,
            reply_markup=reply_markup
        )

async def send_message_safe(bot: Bot, chat_id: int, text: str, reply_markup: InlineKeyboardMarkup = None):
    try:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode="HTML",
            reply_markup=reply_markup,
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"Failed to send message to {chat_id}: {str(e)}")

async def check_subgram_subscription(user_id: int, chat_id: int, first_name: str, language_code: str, is_premium: bool = False, gender: str = None) -> tuple:
    try:
        async with RetryClient() as client:
            data = {
                "user_id": user_id,
                "chat_id": chat_id,
                "first_name": first_name,
                "language_code": language_code,
                "is_premium": is_premium
            }
            if gender:
                data["gender"] = gender
            async with client.post(f"https://api.subgram.app/check_subscription?bot_id={bot.id}", json=data) as response:
                result = await response.json()
                return result.get("status", "error"), result.get("data", [])
    except Exception as e:
        logger.error(f"Failed to check subgram subscription for user {user_id}: {str(e)}")
        return "error", []

async def update_user_subscription(user_id: int, total_fixed_link: int, gender: str = None):
    try:
        async with RetryClient() as client:
            data = {
                "user_id": user_id,
                "total_fixed_link": total_fixed_link
            }
            if gender:
                data["gender"] = gender
            async with client.post(f"https://api.subgram.app/update_subscription?bot_id={bot.id}", json=data) as response:
                result = await response.json()
                return result.get("status", "error")
    except Exception as e:
        logger.error(f"Failed to update subgram subscription for user {user_id}: {str(e)}")
        return "error"

async def init_database():
    async with aiosqlite.connect(DATABASE_FILE) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                join_date INTEGER,
                last_activity INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                added_at INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                admin_id INTEGER,
                reason TEXT,
                banned_at INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                title TEXT,
                check_scope TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS links (
                link_id TEXT PRIMARY KEY,
                content_type TEXT,
                content_data TEXT,
                caption TEXT,
                creator_id INTEGER,
                created_at INTEGER,
                visits INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                id INTEGER PRIMARY KEY,
                total_users INTEGER,
                total_links INTEGER,
                updated_at INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS developers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS system_messages (
                message_id TEXT PRIMARY KEY,
                message_text TEXT,
                sent_at INTEGER,
                sender_id INTEGER
            )
        """)
        await db.commit()

async def execute_with_retry_async(query: str, params: tuple = ()):
    for attempt in range(3):
        try:
            async with aiosqlite.connect(DATABASE_FILE) as db:
                cursor = await db.execute(query, params)
                if query.strip().upper().startswith("SELECT"):
                    result = await cursor.fetchall()
                    await cursor.close()
                    return result
                await db.commit()
                return None
        except Exception as e:
            logger.warning(f"Database query failed (attempt {attempt + 1}): {str(e)}")
            if attempt == 2:
                logger.error(f"Final database query failure: {str(e)}")
                raise
            await asyncio.sleep(0.5)

async def main():
    await init_database()
    developers = await execute_with_retry_async("SELECT user_id FROM developers")
    for dev_id, in developers:
        DEVELOPER_IDS.add(dev_id)
    await dp.start_polling()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger = logging.getLogger(__name__)
    asyncio.run(main())