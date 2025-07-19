import asyncio
import logging
import os
import sys
import shutil
from datetime import datetime, timedelta
from uuid import uuid4
import psutil
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import ClientSession
from aiohttp.client_exceptions import ClientError

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("bot.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Конфигурация
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "8178374718:AAHvyoBH5Ty2VKwNyfdWeOez9XLSflNQtaM")
DATABASE_FILE = "bot_mirrozz_database"
DEVELOPER_IDS = {7057452528, 7236484299}  # Замените на реальные ID разработчиков
BOT_ENABLED = True
start_time = datetime.now().timestamp()

# Инициализация бота
bot = Bot(token=TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

# Класс для состояний
class AdminStates(StatesGroup):
    SystemMessage = State()
    AddDeveloper = State()
    RemoveDeveloper = State()

# HTTP-клиент с повторными попытками
class RetryClient:
    def __init__(self, retries=3, delay=1):
        self.retries = retries
        self.delay = delay

    async def __aenter__(self):
        self.session = ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.session.close()

    async def get(self, url, **kwargs):
        for attempt in range(self.retries):
            try:
                async with self.session.get(url, **kwargs) as response:
                    return response
            except ClientError as e:
                if attempt == self.retries - 1:
                    raise
                await asyncio.sleep(self.delay)

    async def post(self, url, **kwargs):
        for attempt in range(self.retries):
            try:
                async with self.session.post(url, **kwargs) as response:
                    return response
            except ClientError as e:
                if attempt == self.retries - 1:
                    raise
                await asyncio.sleep(self.delay)

# Инициализация базы данных
async def init_database():
    async with aiosqlite.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                first_name TEXT,
                username TEXT,
                join_date INTEGER
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS bans (
                user_id INTEGER PRIMARY KEY,
                admin_id INTEGER,
                reason TEXT,
                banned_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS reports (
                report_id TEXT PRIMARY KEY,
                reporter_id INTEGER,
                message_text TEXT,
                created_at INTEGER,
                is_checked INTEGER
            );
            CREATE TABLE IF NOT EXISTS links (
                link_id TEXT PRIMARY KEY,
                content_type TEXT,
                content_data TEXT,
                caption TEXT,
                creator_id INTEGER,
                created_at INTEGER,
                visits INTEGER
            );
            CREATE TABLE IF NOT EXISTS channels (
                channel_id TEXT PRIMARY KEY,
                title TEXT,
                check_scope TEXT
            );
            CREATE TABLE IF NOT EXISTS stats (
                stat_id INTEGER PRIMARY KEY AUTOINCREMENT,
                total_users INTEGER,
                total_links INTEGER,
                total_visits INTEGER,
                updated_at INTEGER
            );
            CREATE TABLE IF NOT EXISTS developers (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                added_at TEXT
            );
        """)
        conn.commit()

# Вспомогательные функции
async def execute_with_retry_async(query, params=()):
    for attempt in range(3):
        try:
            async with aiosqlite.connect(DATABASE_FILE) as conn:
                cursor = await conn.cursor()
                await cursor.execute(query, params)
                if query.strip().upper().startswith("SELECT"):
                    return await cursor.fetchall()
                await conn.commit()
                return []
        except Exception as e:
            logger.error(f"Database error (attempt {attempt + 1}): {str(e)}")
            if attempt == 2:
                raise
            await asyncio.sleep(1)

async def escape_md(text):
    if not text:
        return text
    chars = ["_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"]
    for char in chars:
        text = text.replace(char, f"\\{char}")
    return text

async def send_message_safe(bot, chat_id, text, reply_markup=None, **kwargs):
    try:
        async with RetryClient() as client:
            async with client.post(
                f"https://api.telegram.org/bot{TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": "HTML",
                    "reply_markup": reply_markup.to_json() if reply_markup else None,
                    **kwargs
                }
            ) as response:
                if not (await response.json()).get("ok"):
                    logger.error(f"Failed to send message to {chat_id}: {await response.json()}")
    except Exception as e:
        logger.error(f"Failed to send message to {chat_id}: {str(e)}")

async def edit_message_if_changed(callback, text, reply_markup=None):
    try:
        if callback.message.text != text or (reply_markup and callback.message.reply_markup != reply_markup):
            await callback.message.edit_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    except Exception as e:
        logger.error(f"Failed to edit message: {str(e)}")

async def update_stats(total_users=None, total_links=None, total_visits=None):
    async with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO stats (total_users, total_links, total_visits, updated_at) VALUES (?, ?, ?, ?)",
            (
                total_users or (await execute_with_retry_async("SELECT COUNT(*) FROM users"))[0][0],
                total_links or (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0],
                total_visits or (await execute_with_retry_async("SELECT SUM(visits) FROM links"))[0][0] or 0,
                int(datetime.now().timestamp())
            )
        )
        conn.commit()

# Клавиатуры
async def create_main_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📢 Каналы", callback_data="section_channels"))
    keyboard.add(InlineKeyboardButton("🔗 Ссылки", callback_data="section_links"))
    keyboard.add(InlineKeyboardButton("📩 Репорты", callback_data="section_reports"))
    if await is_admin(callback.from_user.id):
        keyboard.add(InlineKeyboardButton("👑 Админ-панель", callback_data="admin_panel"))
    return keyboard

async def create_admin_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("👥 Пользователи", callback_data="section_users"))
    keyboard.add(InlineKeyboardButton("🔗 Ссылки", callback_data="section_links"))
    keyboard.add(InlineKeyboardButton("📢 Реклама", callback_data="section_ads"))
    keyboard.add(InlineKeyboardButton("👑 Администраторы", callback_data="section_admins"))
    if callback.from_user.id in DEVELOPER_IDS:
        keyboard.add(InlineKeyboardButton("🛠 Панель разработчика", callback_data="admin_developer"))
    return keyboard

async def create_check_subscription_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Я подписался", callback_data="check_subscription"))
    return keyboard

async def create_reports_keyboard(page=0):
    keyboard = InlineKeyboardMarkup()
    reports = await execute_with_retry_async(
        "SELECT report_id FROM reports ORDER BY created_at DESC LIMIT 5 OFFSET ?",
        (page * 5,)
    )
    for (report_id,) in reports:
        keyboard.add(InlineKeyboardButton(f"Репорт {report_id[:8]}", callback_data=f"report_{report_id}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"reports_page_{page - 1}"))
    if len(reports) == 5:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"reports_page_{page + 1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_report_actions_keyboard(report_id, reporter_id):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🗑 Удалить", callback_data=f"delete_report_{report_id}"))
    keyboard.add(InlineKeyboardButton("👤 Пользователь", callback_data=f"user_info_{reporter_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_reports"))
    return keyboard

async def create_users_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔎 Поиск пользователя", callback_data="search_user"))
    keyboard.add(InlineKeyboardButton("🚫 Заблокированные", callback_data="banned_users_0"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_user_actions_keyboard(user_id):
    keyboard = InlineKeyboardMarkup()
    is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
    keyboard.add(InlineKeyboardButton(
        "🚫 Заблокировать" if not is_banned else "✅ Разблокировать",
        callback_data=f"{'ban_user' if not is_banned else 'unban_user'}_{user_id}"
    ))
    keyboard.add(InlineKeyboardButton("📩 Отправить сообщение", callback_data=f"send_message_{user_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_users"))
    return keyboard

async def create_banned_users_keyboard(page=0):
    keyboard = InlineKeyboardMarkup()
    banned_users = await execute_with_retry_async(
        "SELECT user_id FROM bans ORDER BY banned_at DESC LIMIT 5 OFFSET ?",
        (page * 5,)
    )
    for (user_id,) in banned_users:
        keyboard.add(InlineKeyboardButton(f"Пользователь {user_id}", callback_data=f"banned_user_info_{user_id}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"banned_users_{page - 1}"))
    if len(banned_users) == 5:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"banned_users_{page + 1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_users"))
    return keyboard

async def create_links_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Создать ссылку", callback_data="create_link"))
    keyboard.add(InlineKeyboardButton("🗑 Удалить ссылку", callback_data="delete_link"))
    keyboard.add(InlineKeyboardButton("📋 Список ссылок", callback_data="list_links_0"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_links_list_keyboard(page=0):
    keyboard = InlineKeyboardMarkup()
    links = await execute_with_retry_async(
        "SELECT link_id FROM links ORDER BY created_at DESC LIMIT 5 OFFSET ?",
        (page * 5,)
    )
    for (link_id,) in links:
        keyboard.add(InlineKeyboardButton(f"Ссылка {link_id[:8]}", callback_data=f"link_info_{link_id}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"list_links_{page - 1}"))
    if len(links) == 5:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"list_links_{page + 1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_links"))
    return keyboard

async def create_link_actions_keyboard(link_id):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🗑 Удалить", callback_data=f"confirm_delete_{link_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="list_links_0"))
    return keyboard

async def create_confirm_delete_keyboard(link_id):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Подтвердить", callback_data=f"delete_link_{link_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="list_links_0"))
    return keyboard

async def create_ads_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Добавить канал", callback_data="add_channel"))
    keyboard.add(InlineKeyboardButton("🗑 Удалить канал", callback_data="remove_channel"))
    keyboard.add(InlineKeyboardButton("📋 Список каналов", callback_data="channel_list"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_channels_keyboard(action):
    keyboard = InlineKeyboardMarkup()
    channels = await execute_with_retry_async("SELECT channel_id, title FROM channels")
    for channel_id, title in channels:
        keyboard.add(InlineKeyboardButton(title, callback_data=f"{action}_{channel_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_ads"))
    return keyboard

async def create_confirm_channel_delete_keyboard(channel_id):
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Подтвердить", callback_data=f"delete_channel_{channel_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="section_ads"))
    return keyboard

async def create_channel_scope_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("Только ссылки", callback_data="scope_links_only"))
    keyboard.add(InlineKeyboardButton("Все функции", callback_data="scope_all_functions"))
    keyboard.add(InlineKeyboardButton("🔙 Отмена", callback_data="section_ads"))
    return keyboard

async def create_admins_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Добавить администратора", callback_data="add_admin"))
    keyboard.add(InlineKeyboardButton("🗑 Удалить администратора", callback_data="remove_admin"))
    keyboard.add(InlineKeyboardButton("📋 Список администраторов", callback_data="list_admins_0"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_admins_list_keyboard(page=0):
    keyboard = InlineKeyboardMarkup()
    admins = await execute_with_retry_async(
        "SELECT user_id FROM admins ORDER BY user_id LIMIT 5 OFFSET ?",
        (page * 5,)
    )
    for (user_id,) in admins:
        keyboard.add(InlineKeyboardButton(f"Админ {user_id}", callback_data=f"admin_info_{user_id}"))
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"list_admins_{page - 1}"))
    if len(admins) == 5:
        nav_buttons.append(InlineKeyboardButton("Вперед ➡️", callback_data=f"list_admins_{page + 1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="section_admins"))
    return keyboard

async def create_admin_actions_keyboard(admin_id):
    keyboard = InlineKeyboardMarkup()
    if admin_id not in DEVELOPER_IDS:
        keyboard.add(InlineKeyboardButton("🗑 Удалить", callback_data=f"remove_admin_{admin_id}"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="list_admins_0"))
    return keyboard

async def create_developer_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🖥 Сервер", callback_data="developer_server"))
    keyboard.add(InlineKeyboardButton("🗄 База данных", callback_data="developer_database"))
    keyboard.add(InlineKeyboardButton("👨‍💻 Разработчики", callback_data="developer_management"))
    keyboard.add(InlineKeyboardButton("📋 Логи", callback_data="developer_logs"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def create_developer_server_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📊 Статус", callback_data="server_status"))
    keyboard.add(InlineKeyboardButton("🟢 Включить", callback_data="enable_bot"))
    keyboard.add(InlineKeyboardButton("🔴 Отключить", callback_data="disable_bot"))
    keyboard.add(InlineKeyboardButton("🔄 Перезагрузить", callback_data="restart_bot"))
    keyboard.add(InlineKeyboardButton("🛑 Аварийное выключение", callback_data="emergency_shutdown"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_developer"))
    return keyboard

async def create_developer_database_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📥 Скачать базу", callback_data="download_database"))
    keyboard.add(InlineKeyboardButton("📤 Загрузить базу", callback_data="upload_database"))
    keyboard.add(InlineKeyboardButton("🔄 Сбросить базу", callback_data="reset_database"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_developer"))
    return keyboard

async def create_developers_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Добавить разработчика", callback_data="add_developer"))
    keyboard.add(InlineKeyboardButton("🗑 Удалить разработчика", callback_data="remove_developer"))
    keyboard.add(InlineKeyboardButton("📋 Список разработчиков", callback_data="list_developers"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_developer"))
    return keyboard

async def create_developer_logs_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📋 Просмотреть логи", callback_data="view_logs"))
    keyboard.add(InlineKeyboardButton("📥 Скачать логи", callback_data="download_logs"))
    keyboard.add(InlineKeyboardButton("🗑 Очистить логи", callback_data="clear_logs"))
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="admin_developer"))
    return keyboard

async def create_back_keyboard():
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return keyboard

async def scope_text(scope):
    return "Только ссылки" if scope == "links_only" else "Все функции"

# Обработчики
@dp.message_handler(commands=["start"])
async def start_command(message: types.Message, state: FSMContext):
    if not BOT_ENABLED:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🛑 Бот временно отключен."
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
    await execute_with_retry_async(
        "INSERT OR IGNORE INTO users (user_id, first_name, username, join_date) VALUES (?, ?, ?, ?)",
        (user_id, message.from_user.first_name, message.from_user.username, int(datetime.now().timestamp()))
    )
    await update_user_activity(user_id)
    link_id = message.get_args()
    if link_id:
        link = await execute_with_retry_async(
            "SELECT content_type, content_data, caption FROM links WHERE link_id = ?",
            (link_id,)
        )
        if link:
            await state.update_data(link_id=link_id)
            channels = await execute_with_retry_async("SELECT channel_id FROM channels WHERE check_scope = 'all_functions' OR check_scope = 'links_only'")
            for (channel_id,) in channels:
                if not await is_subscribed(user_id, channel_id):
                    await send_message_safe(
                        bot=bot,
                        chat_id=message.chat.id,
                        text=f"📢 Пожалуйста, подпишитесь на канал {channel_id} и нажмите 'Я подписался'.",
                        reply_markup=await create_check_subscription_keyboard()
                    )
                    return
                await asyncio.sleep(0.05)  # Пауза для соблюдения лимитов Telegram
            content_type, content_data, caption = link[0]
            await execute_with_retry_async(
                "UPDATE links SET visits = visits + 1 WHERE link_id = ?",
                (link_id,)
            )
            total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
            await update_stats(total_links=total_links)
            if content_type == "text":
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text=f"{content_data}\n\n{caption or ''}"
                )
            elif content_type in ["photo", "document"]:
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text=caption or "📎 Файл:",
                    **{content_type: content_data}
                )
            await state.finish()
            return
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text="👋 Добро пожаловать! Выберите действие:",
        reply_markup=await create_main_keyboard()
    )

@dp.message_handler(commands=["admin"])
async def admin_command(message: types.Message):
    if not BOT_ENABLED:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🛑 Бот временно отключен."
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
        reply_markup=await create_admin_keyboard()
    )

@dp.message_handler(content_types=[types.ContentType.TEXT])
async def handle_text(message: types.Message):
    if not BOT_ENABLED:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="🛑 Бот временно отключен."
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
    report_id = str(uuid4())
    await execute_with_retry_async(
        "INSERT INTO reports (report_id, reporter_id, message_text, created_at, is_checked) VALUES (?, ?, ?, ?, 0)",
        (report_id, user_id, message.text, int(datetime.now().timestamp()))
    )
    await send_message_safe(
        bot=bot,
        chat_id=message.chat.id,
        text=f"📩 Ваш репорт (ID: {report_id[:8]}) принят. Мы рассмотрим его в ближайшее время."
    )

async def is_subscribed(user_id: int, channel_id: str) -> bool:
    try:
        async with RetryClient() as client:
            response = await client.get(
                f"https://api.telegram.org/bot{TOKEN}/getChatMember",
                params={"chat_id": channel_id, "user_id": user_id}
            )
            data = await response.json()
            result = data.get("ok") and data.get("result", {}).get("status") in ["member", "administrator", "creator"]
            logger.info(f"Subscription check for user {user_id} on channel {channel_id}: {result}")
            await asyncio.sleep(0.05)  # Пауза для соблюдения лимитов Telegram
            return result
    except Exception as e:
        logger.error(f"Failed to check subscription for user {user_id} on channel {channel_id}: {str(e)}")
        return False

async def is_banned(user_id: int) -> bool:
    result = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (user_id,))
    return bool(result)

async def is_admin(user_id: int) -> bool:
    if user_id in DEVELOPER_IDS:
        return True
    result = await execute_with_retry_async("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
    return bool(result)

async def update_user_activity(user_id: int):
    await execute_with_retry_async(
        "UPDATE users SET last_activity = ? WHERE user_id = ?",
        (int(datetime.now().timestamp()), user_id)
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
        text="📩 <b>Репорты</b>\n\nВыберите репорт для просмотра:",
        reply_markup=await create_reports_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data.startswith("reports_page_"))
async def reports_page(callback: types.CallbackQuery):
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
        text="📩 <b>Репорты</b>\n\nВыберите репорт для просмотра:",
        reply_markup=await create_reports_keyboard(page=page)
    )

@dp.callback_query_handler(lambda c: c.data.startswith("report_"))
async def view_report(callback: types.CallbackQuery):
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
        "SELECT reporter_id, message_text, created_at, is_checked FROM reports WHERE report Shed report_id = ?",
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

@dp.callback_query_handler(lambda c: c.data == "check_subscription")
async def check_subscription(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    data = await state.get_data()
    link_id = data.get("link_id")
    if not link_id:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка: ссылка не найдена.",
            reply_markup=None
        )
        return
    channels = await execute_with_retry_async("SELECT channel_id FROM channels WHERE check_scope = 'all_functions' OR check_scope = 'links_only'")
    for (channel_id,) in channels:
        if not await is_subscribed(user_id, channel_id):
            await edit_message_if_changed(
                callback=callback,
                text=f"📢 Пожалуйста, подпишитесь на канал {channel_id} и нажмите 'Я подписался'.",
                reply_markup=await create_check_subscription_keyboard()
            )
            return
        await asyncio.sleep(0.05)  # Пауза для соблюдения лимитов Telegram
    link = await execute_with_retry_async(
        "SELECT content_type, content_data, caption, visits FROM links WHERE link_id = ?",
        (link_id,)
    )
    if not link:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ссылка не найдена.",
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
    if content_type == "text":
        await edit_message_if_changed(
            callback=callback,
            text=f"{content_data}\n\n{caption or ''}",
            reply_markup=None
        )
    elif content_type in ["photo", "document"]:
        await callback.message.delete()
        await send_message_safe(
            bot=bot,
            chat_id=callback.message.chat.id,
            text=caption or "📎 Файл:",
            **{content_type: content_data}
        )
    await state.finish()

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
    try:
        search_id = int(message.text)
        user = await execute_with_retry_async(
            "SELECT first_name, username, join_date FROM users WHERE user_id = ?",
            (search_id,)
        )
        if not user:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не найден."
            )
            await state.finish()
            return
        first_name, username, join_date = user[0]
        join_date_str = datetime.fromtimestamp(join_date).strftime("%d.%m.%Y %H:%M")
        is_banned = await execute_with_retry_async("SELECT 1 FROM bans WHERE user_id = ?", (search_id,))
        text = (
            f"👤 <b>Информация о пользователе</b>\n\n"
            f"🆔 ID: <b>{search_id}</b>\n"
            f"Имя: <b>{first_name}</b>\n"
            f"Username: <b>@{await escape_md(username) if username else 'Нет'}</b>\n"
            f"📅 Дата регистрации: <b>{join_date_str}</b>\n"
            f"🚫 Статус: <b>{'Заблокирован' if is_banned else 'Активен'}</b>"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=text,
            reply_markup=await create_user_actions_keyboard(search_id)
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID пользователя (число)."
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
        f"Username: <b>@{await escape_md(username) if username else 'Нет'}</b>\n"
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
    await edit_message_if_changed(
        callback=callback,
        text=f"📩 Введите сообщение для пользователя {target_user_id}:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(target_user_id=target_user_id)

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
    target_user_id = data.get("target_user_id")
    action = data.get("action")
    if action == "ban" and target_user_id:
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
    elif action == "create_link":
        try:
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
                await state.finish()
                return
            link_id = str(uuid4())
            await execute_with_retry_async(
                "INSERT INTO links (link_id, content_type, content_data, caption, creator_id, created_at, visits) VALUES (?, ?, ?, ?, ?, ?, 0)",
                (link_id, content_type, content_data, caption, user_id, int(datetime.now().timestamp()))
            )
            total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
            await update_stats(total_links=total_links)
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text=f"✅ Ссылка создана!\nID: {link_id}\nСсылка: https://t.me/{(await bot.get_me()).username}?start={link_id}"
            )
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Некорректный формат. Используйте: Тип_контента|Данные|Подпись"
            )
    elif action == "delete_link":
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
    elif action == "add_channel":
        try:
            channel_id, title = message.text.split("|", 1)
            channel_id = channel_id.strip()
            title = title.strip()
            await state.update_data(channel_id=channel_id, title=title)
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="📢 Выберите область проверки подписки для канала:",
                reply_markup=await create_channel_scope_keyboard()
            )
        except ValueError:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Некорректный формат. Используйте: ID|Название"
            )
    elif action == "add_admin":
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
            if await is_admin(new_admin_id):
                await send_message_safe(
                    bot=bot,
                    chat_id=message.chat.id,
                    text="⚠️ Пользователь уже является администратором."
                )
                await state.finish()
                return
            await execute_with_retry_async(
                "INSERT INTO admins (user_id) VALUES (?)",
                (new_admin_id,)
            )
            await send_message_safe(
                bot=bot,
                chat_id=new_admin_id,
                text="👑 Вы были назначены администратором."
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
    elif target_user_id:
        message_text = message.text
        await send_message_safe(
            bot=bot,
            chat_id=target_user_id,
            text=f"📩 <b>Сообщение от администрации:</b>\n\n{message_text}"
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Сообщение отправлено пользователю {target_user_id}."
        )
    else:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Ошибка: ID пользователя не указан."
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
        text=f"🚫 Введите причину бана для пользователя {target_user_id} (или 'Без причины' для бана без указания причины):",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(target_user_id=target_user_id, action="ban")

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
        f"Username: <b>@{await escape_md(username) if username else 'Нет'}</b>\n"
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
    await edit_message_if_changed(
        callback=callback,
        text="🔗 Введите данные для создания ссылки в формате:\n\nТип_контента|Данные|Подпись\n\nПример:\ntext|Привет, это текст|Подпись к тексту",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="create_link")

@dp.callback_query_handler(lambda c: c.data == "delete_link")
async def delete_link(callback: types.CallbackQuery, state: FSMContext):
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
        text="📢 Введите ID канала и его название в формате: ID|Название",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_channel")

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
        text="👑 Введите ID пользователя для добавления в администраторы:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.SystemMessage)
    await state.update_data(action="add_admin")

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
    if admin_id in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Нельзя удалить разработчика из администраторов.",
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
    admins = await execute_with_retry_async(
        "SELECT user_id FROM admins ORDER BY user_id LIMIT 5 OFFSET ?",
        (page * 5,)
    )
    text = "👑 <b>Список администраторов</b>\n\n"
    for (admin_id,) in admins:
        user = await execute_with_retry_async(
            "SELECT first_name, username FROM users WHERE user_id = ?",
            (admin_id,)
        )
        first_name, username = user[0] if user else ("Неизвестно", None)
        text += f"🆔 {admin_id}\nИмя: {first_name}\nUsername: @{username if username else 'Нет'}\n\n"
    if not admins:
        text = "👑 Список администраторов пуст."
    await edit_message_if_changed(
        callback=callback,
        text=text,
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
            text="⚠️ Администратор не найден.",
            reply_markup=await create_admins_list_keyboard()
        )
        return
    first_name, username = user[0]
    text = (
        f"👑 <b>Информация об администраторе</b>\n\n"
        f"🆔 ID: <b>{admin_id}</b>\n"
        f"Имя: <b>{first_name}</b>\n"
        f"Username: <b>@{await escape_md(username) if username else 'Нет'}</b>"
    )
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_admin_actions_keyboard(admin_id)
    )

@dp.callback_query_handler(lambda c: c.data == "admin_developer")
async def admin_developer(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="🛠 <b>Панель разработчика</b>\n\nВыберите действие:",
        reply_markup=await create_developer_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "developer_server")
async def developer_server(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
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
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    uptime = datetime.now().timestamp() - start_time
    uptime_str = str(timedelta(seconds=int(uptime)))
    cpu_usage = psutil.cpu_percent()
    memory = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    total_users = (await execute_with_retry_async("SELECT COUNT(*) FROM users"))[0][0]
    total_links = (await execute_with_retry_async("SELECT COUNT(*) FROM links"))[0][0]
    total_visits = (await execute_with_retry_async("SELECT SUM(visits) FROM links"))[0][0] or 0
    text = (
        f"🖥 <b>Статус сервера</b>\n\n"
        f"🕒 Время работы: <b>{uptime_str}</b>\n"
        f"💻 CPU: <b>{cpu_usage}%</b>\n"
        f"🧠 Память: <b>{memory.percent}% ({memory.used / 1024**3:.2f}/{memory.total / 1024**3:.2f} GB)</b>\n"
        f"💾 Диск: <b>{disk.percent}% ({disk.used / 1024**3:.2f}/{disk.total / 1024**3:.2f} GB)</b>\n"
        f"👥 Пользователей: <b>{total_users}</b>\n"
        f"🔗 Ссылок: <b>{total_links}</b>\n"
        f"🔗 Переходов: <b>{total_visits}</b>\n"
        f"🟢 Статус бота: <b>{'Включен' if BOT_ENABLED else 'Отключен'}</b>"
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
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    BOT_ENABLED = True
    await edit_message_if_changed(
        callback=callback,
        text="✅ Бот включен.",
        reply_markup=await create_developer_server_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "disable_bot")
async def disable_bot(callback: types.CallbackQuery):
    global BOT_ENABLED
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    BOT_ENABLED = False
    await edit_message_if_changed(
        callback=callback,
        text="🛑 Бот отключен.",
        reply_markup=await create_developer_server_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "restart_bot")
async def restart_bot(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="🔄 Бот перезагружается...",
        reply_markup=None
    )
    await bot.session.close()
    os.execl(sys.executable, sys.executable, *sys.argv)

@dp.callback_query_handler(lambda c: c.data == "emergency_shutdown")
async def emergency_shutdown(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="🛑 Аварийное выключение...",
        reply_markup=None
    )
    await bot.session.close()
    sys.exit(0)

@dp.callback_query_handler(lambda c: c.data == "developer_database")
async def developer_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
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
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    if os.path.exists(DATABASE_FILE):
        try:
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=types.InputFile(DATABASE_FILE, filename="bot.db"),
                caption="📥 База данных"
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
                text="⚠️ Ошибка при отправке базы данных.",
                reply_markup=await create_developer_database_keyboard()
            )
    else:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ База данных не найдена.",
            reply_markup=await create_developer_database_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "upload_database")
async def upload_database(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="📤 Отправьте файл базы данных (.db):",
        reply_markup=await create_back_keyboard()
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
            text="⛔ Доступ только для разработчиков."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    data = await state.get_data()
    if data.get("action") != "upload_database":
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Неверное действие."
        )
        await state.finish()
        return
    if message.document.file_name.endswith(".db"):
        try:
            file_info = await bot.get_file(message.document.file_id)
            file_path = file_info.file_path
            downloaded_file = await bot.download_file(file_path)
            backup_path = f"backup_{DATABASE_FILE}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(DATABASE_FILE):
                shutil.copy(DATABASE_FILE, backup_path)
            with open(DATABASE_FILE, "wb") as f:
                f.write(downloaded_file.read())
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="✅ База данных успешно загружена."
            )
        except Exception as e:
            logger.error(f"Failed to upload database: {str(e)}")
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Ошибка при загрузке базы данных."
            )
    else:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, отправьте файл с расширением .db."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "reset_database")
async def reset_database(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    try:
        backup_path = f"backup_{DATABASE_FILE}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if os.path.exists(DATABASE_FILE):
            shutil.copy(DATABASE_FILE, backup_path)
        os.remove(DATABASE_FILE)
        await init_database()
        await update_stats()
        await edit_message_if_changed(
            callback=callback,
            text="✅ База данных сброшена. Создана резервная копия.",
            reply_markup=await create_developer_database_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to reset database: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка при сбросе базы данных.",
            reply_markup=await create_developer_database_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "developer_management")
async def developer_management(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
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
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="👨‍💻 Введите ID пользователя для добавления в разработчики:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.AddDeveloper)

@dp.message_handler(state=AdminStates.AddDeveloper)
async def process_add_developer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ Доступ только для разработчиков."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    try:
        new_developer_id = int(message.text)
        user = await execute_with_retry_async(
            "SELECT first_name, username FROM users WHERE user_id = ?",
            (new_developer_id,)
        )
        if not user:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не найден."
            )
            await state.finish()
            return
        first_name, username = user[0]
        if new_developer_id in DEVELOPER_IDS:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь уже является разработчиком."
            )
            await state.finish()
            return
        await execute_with_retry_async(
            "INSERT INTO developers (user_id, username, added_at) VALUES (?, ?, ?)",
            (new_developer_id, username, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        DEVELOPER_IDS.add(new_developer_id)
        await send_message_safe(
            bot=bot,
            chat_id=new_developer_id,
            text="🛠 Вы были назначены разработчиком бота."
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Пользователь {first_name} (ID: {new_developer_id}) добавлен в разработчики."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID пользователя (число)."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "remove_developer")
async def remove_developer(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="👨‍💻 Введите ID пользователя для удаления из разработчиков:",
        reply_markup=await create_back_keyboard()
    )
    await state.set_state(AdminStates.RemoveDeveloper)

@dp.message_handler(state=AdminStates.RemoveDeveloper)
async def process_remove_developer(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id not in DEVELOPER_IDS:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⛔ Доступ только для разработчиков."
        )
        await state.finish()
        return
    await update_user_activity(user_id)
    try:
        remove_developer_id = int(message.text)
        if remove_developer_id not in DEVELOPER_IDS:
            await send_message_safe(
                bot=bot,
                chat_id=message.chat.id,
                text="⚠️ Пользователь не является разработчиком."
            )
            await state.finish()
            return
        await execute_with_retry_async(
            "DELETE FROM developers WHERE user_id = ?",
            (remove_developer_id,)
        )
        DEVELOPER_IDS.discard(remove_developer_id)
        await send_message_safe(
            bot=bot,
            chat_id=remove_developer_id,
            text="🚫 Вы были удалены из разработчиков бота."
        )
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text=f"✅ Пользователь (ID: {remove_developer_id}) удален из разработчиков."
        )
    except ValueError:
        await send_message_safe(
            bot=bot,
            chat_id=message.chat.id,
            text="⚠️ Пожалуйста, введите корректный ID пользователя (число)."
        )
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "list_developers")
async def list_developers(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    developers = await execute_with_retry_async("SELECT user_id, username, added_at FROM developers")
    text = "👨‍💻 <b>Список разработчиков</b>\n\n"
    for dev_id, username, added_at in developers:
        user = await execute_with_retry_async(
            "SELECT first_name FROM users WHERE user_id = ?",
            (dev_id,)
        )
        first_name = user[0][0] if user else "Неизвестно"
        text += f"🆔 {dev_id}\nИмя: {first_name}\nUsername: @{username if username else 'Нет'}\nДобавлен: {added_at}\n\n"
    if not developers:
        text = "👨‍💻 Список разработчиков пуст."
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_developers_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "developer_logs")
async def developer_logs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    await edit_message_if_changed(
        callback=callback,
        text="📋 <b>Управление логами</b>\n\nВыберите действие:",
        reply_markup=await create_developer_logs_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "view_logs")
async def view_logs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    if os.path.exists("bot.log"):
        try:
            with open("bot.log", "r", encoding="utf-8") as f:
                logs = f.readlines()[-10:]  # Последние 10 строк
                log_text = "".join(logs) if logs else "Логи пусты."
            await edit_message_if_changed(
                callback=callback,
                text=f"📋 <b>Последние логи</b>\n\n{log_text}",
                reply_markup=await create_developer_logs_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to read logs: {str(e)}")
            await edit_message_if_changed(
                callback=callback,
                text="⚠️ Ошибка при чтении логов.",
                reply_markup=await create_developer_logs_keyboard()
            )
    else:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Файл логов не найден.",
            reply_markup=await create_developer_logs_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "download_logs")
async def download_logs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    if os.path.exists("bot.log"):
        try:
            await bot.send_document(
                chat_id=callback.message.chat.id,
                document=types.InputFile("bot.log", filename="bot.log"),
                caption="📥 Файл логов"
            )
            await edit_message_if_changed(
                callback=callback,
                text="✅ Логи отправлены.",
                reply_markup=await create_developer_logs_keyboard()
            )
        except Exception as e:
            logger.error(f"Failed to send logs: {str(e)}")
            await edit_message_if_changed(
                callback=callback,
                text="⚠️ Ошибка при отправке логов.",
                reply_markup=await create_developer_logs_keyboard()
            )
    else:
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Файл логов не найден.",
            reply_markup=await create_developer_logs_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "clear_logs")
async def clear_logs(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if user_id not in DEVELOPER_IDS:
        await edit_message_if_changed(
            callback=callback,
            text="⛔ Доступ только для разработчиков.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    try:
        with open("bot.log", "w", encoding="utf-8") as f:
            f.write("")
        await edit_message_if_changed(
            callback=callback,
            text="✅ Логи очищены.",
            reply_markup=await create_developer_logs_keyboard()
        )
    except Exception as e:
        logger.error(f"Failed to clear logs: {str(e)}")
        await edit_message_if_changed(
            callback=callback,
            text="⚠️ Ошибка при очистке логов.",
            reply_markup=await create_developer_logs_keyboard()
        )

@dp.callback_query_handler(lambda c: c.data == "section_channels")
async def section_channels(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    channels = await execute_with_retry_async("SELECT channel_id, title FROM channels WHERE check_scope = 'all_functions'")
    if not channels:
        await edit_message_if_changed(
            callback=callback,
            text="📢 Нет доступных каналов.",
            reply_markup=await create_main_keyboard()
        )
        return
    text = "📢 <b>Список каналов</b>\n\n"
    for channel_id, title in channels:
        text += f"{title}: {channel_id}\n"
    await edit_message_if_changed(
        callback=callback,
        text=text,
        reply_markup=await create_main_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "admin_panel")
async def admin_panel(callback: types.CallbackQuery):
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
        reply_markup=await create_admin_keyboard()
    )

@dp.callback_query_handler(lambda c: c.data == "back")
async def back_button(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    if await is_banned(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="🚫 Вы заблокированы в боте.",
            reply_markup=None
        )
        return
    await update_user_activity(user_id)
    if await is_admin(user_id):
        await edit_message_if_changed(
            callback=callback,
            text="👑 <b>Панель администратора</b>\n\nВыберите раздел:",
            reply_markup=await create_admin_keyboard()
        )
    else:
        await edit_message_if_changed(
            callback=callback,
            text="👋 Выберите действие:",
            reply_markup=await create_main_keyboard()
        )

async def main():
    try:
        await init_database()
        await update_stats()
        logger.info("Bot started successfully")
        await dp.start_polling()
    except Exception as e:
        logger.error(f"Fatal error: {str(e)}")
        await (await bot.get_session()).close()  # Правильное закрытие сессии
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())