import os
import asyncio
import sqlite3
import random
import string
import time
import psutil
import shutil
import logging
import aiohttp
from typing import Optional
from datetime import datetime, timedelta
from typing import Optional, Union, List, Dict, Any
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandStart
from aiogram.types import ErrorEvent
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, 
    InlineKeyboardButton, FSInputFile, InputMediaPhoto,
    InputMediaDocument, InputMedia
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.enums import ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.markdown import hbold, hlink, hcode
from enum import Enum

# Logging setup
logging.basicConfig(
    filename='bot_mirrozz.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        join_date TEXT,
        visit_count INTEGER DEFAULT 0,
        link_visits INTEGER DEFAULT 0,
        is_banned INTEGER DEFAULT 0,
        ban_reason TEXT,
        banned_by INTEGER,
        ban_date TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS links (
        link_id TEXT PRIMARY KEY,
        content_type TEXT,
        content_text TEXT,
        content_file_id TEXT,
        created_by INTEGER,
        creation_date TEXT,
        visits INTEGER DEFAULT 0
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS admins (
        admin_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        added_by INTEGER,
        add_date TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reports (
        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        report_date TEXT,
        status TEXT DEFAULT 'open',
        answer TEXT,
        answered_by INTEGER,
        answer_date TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS advertise_channels (
        channel_id INTEGER PRIMARY KEY,
        username TEXT,
        title TEXT,
        added_by INTEGER,
        add_date TEXT,
        check_type INTEGER DEFAULT 1,
        subscribers_count INTEGER DEFAULT 0
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS system_messages (
        message_id INTEGER PRIMARY KEY AUTOINCREMENT,
        message_text TEXT,
        sent_by INTEGER,
        send_date TEXT,
        recipients_count INTEGER
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS developers (
        developer_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        added_by INTEGER,
        add_date TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS logs (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT,
        level TEXT,
        message TEXT,
        log_date TEXT
    )
    ''')
    
    conn.commit()
    conn.close()

init_db()

def init_catalog_db():
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scripts (
        script_id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        description TEXT NOT NULL,
        script_code TEXT NOT NULL,
        menu TEXT NOT NULL,
        has_key INTEGER DEFAULT 0,
        created_by INTEGER NOT NULL,
        creation_date TEXT NOT NULL,
        views INTEGER DEFAULT 0,
        image_id TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS script_reactions (
        reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
        script_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        reaction_type INTEGER NOT NULL,  -- 1 like, -1 dislike
        reaction_date TEXT NOT NULL,
        FOREIGN KEY(script_id) REFERENCES scripts(script_id)
    )
    ''')
    
    cursor.execute('''
    CREATE INDEX IF NOT EXISTS idx_script_reactions ON script_reactions(script_id, user_id)
    ''')
    
    conn.commit()
    conn.close()

init_catalog_db()

# Добавляем enum для состояний
class CatalogStates(StatesGroup):
    search_query = State()
    add_script_name = State()
    add_script_description = State()
    add_script_menu = State()
    add_script_code = State()
    add_script_key = State()
    add_script_image = State()
    edit_script_name = State()
    edit_script_description = State()
    edit_script_menu = State()
    edit_script_code = State()
    edit_script_key = State()
    edit_script_image = State()

# Добавляем enum для типов отображения каталога
class CatalogViewType(Enum):
    SEARCH = "search"
    POPULAR = "popular"
    RECENT = "recent"
    ADMIN_VIEW = "admin_view"

# Bot setup
bot = Bot(token="8178374718:AAE3pz2LVKgvaQ75SNK7AuMaK_1ZNNICs9U")
dp = Dispatcher()
BOT_START_TIME = time.time()
SUBGRAM_API_KEY = "8a1994b006b02e4e126dae69f8ce9832f87d005a77480d0a40854c4b592947ad"
SUBGRAM_API_URL = "https://api.subgram.ru/request-op/"

# States
class Form(StatesGroup):
    create_link_content = State()
    create_link_file = State()
    create_custom_link_id = State()
    report_message = State()
    admin_add = State()
    admin_remove = State()
    user_search = State()
    send_user_message = State()
    ban_user = State()
    add_advertise = State()
    add_advertise_type = State()
    remove_advertise = State()
    system_message = State()
    add_developer = State()
    remove_developer = State()
    answer_report = State()
    delete_link = State()
    delete_report = State()
    load_database = State()

# Admin and Developer IDs
DEVELOPERS = [7057452528]  # Snowt_TG
ADMINS = [7057452528, 7236484299, 6634823286, 8153569100]  # Snowt_TG, soIaire_0f_astora, Ena, Qiwik

async def check_subgram_subscription(
    user_id: int,
    chat_id: int,
    first_name: Optional[str] = None,
    language_code: Optional[str] = None,
    premium: Optional[bool] = None,
    gender: Optional[str] = None,
    max_op: int = 8,
    action: str = "subscribe",
    exclude_channel_ids: Optional[List[str]] = None
) -> dict:
    """
    Проверяет подписки пользователя через SubGram API
    """
    headers = {
        "Auth": SUBGRAM_API_KEY
    }
    
    data = {
        "UserId": str(user_id),
        "ChatId": str(chat_id),
        "MaxOP": max_op,
        "action": action
    }
    
    if first_name:
        data["first_name"] = first_name
    if language_code:
        data["language_code"] = language_code
    if premium is not None:
        data["Premium"] = premium
    if gender:
        data["Gender"] = gender
    if exclude_channel_ids:
        data["exclude_channel_ids"] = exclude_channel_ids
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(SUBGRAM_API_URL, headers=headers, json=data) as response:
                result = await response.json()
                
                # Дополнительная проверка статусов подписок
                if result.get('status') == 'ok':
                    return result
                
                if 'additional' in result and 'sponsors' in result['additional']:
                    all_subscribed = True
                    for sponsor in result['additional']['sponsors']:
                        if sponsor['status'] != 'subscribed':
                            all_subscribed = False
                            break
                    
                    if all_subscribed:
                        result['status'] = 'ok'
                        result['message'] = 'Все подписки оформлены'
                
                return result
    except Exception as e:
        logger.error(f"SubGram API error: {str(e)}")
        return {"status": "error", "code": 500, "message": "Ошибка соединения с SubGram"}

# Helper functions
def get_script_info(script_id: int) -> dict:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.*, 
               COUNT(CASE WHEN sr.reaction_type = 1 THEN 1 END) as likes,
               COUNT(CASE WHEN sr.reaction_type = -1 THEN 1 END) as dislikes
        FROM scripts s
        LEFT JOIN script_reactions sr ON s.script_id = sr.script_id
        WHERE s.script_id = ?
        GROUP BY s.script_id
    ''', (script_id,))
    script = cursor.fetchone()
    conn.close()
    
    if script:
        return {
            'script_id': script[0],
            'name': script[1],
            'description': script[2],
            'script_code': script[3],
            'menu': script[4],
            'has_key': script[5],
            'created_by': script[6],
            'creation_date': script[7],
            'views': script[8],
            'image_id': script[9],
            'likes': script[10] or 0,
            'dislikes': script[11] or 0
        }
    return None

def increment_script_views(script_id: int):
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE scripts SET views = views + 1 WHERE script_id = ?', (script_id,))
    conn.commit()
    conn.close()

def add_user_reaction(script_id: int, user_id: int, reaction_type: int):
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    
    # Удаляем предыдущую реакцию пользователя если есть
    cursor.execute('DELETE FROM script_reactions WHERE script_id = ? AND user_id = ?', (script_id, user_id))
    
    # Добавляем новую реакцию
    cursor.execute('''
        INSERT INTO script_reactions (script_id, user_id, reaction_type, reaction_date)
        VALUES (?, ?, ?, ?)
    ''', (script_id, user_id, reaction_type, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    
    conn.commit()
    conn.close()

def get_scripts_by_search(query: str, limit: int = 10, offset: int = 0) -> list:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT script_id FROM scripts 
        WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? OR LOWER(menu) LIKE ?
        ORDER BY script_id DESC LIMIT ? OFFSET ?
    ''', (f"%{query.lower()}%", f"%{query.lower()}%", f"%{query.lower()}%", limit, offset))
    scripts = cursor.fetchall()
    conn.close()
    return [script[0] for script in scripts]

def get_popular_scripts(limit: int = 10, offset: int = 0) -> list:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT s.script_id, 
               s.views + 
               (COUNT(CASE WHEN sr.reaction_type = 1 THEN 1 END) * 2) - 
               (COUNT(CASE WHEN sr.reaction_type = -1 THEN 1 END) * 1) as score
        FROM scripts s
        LEFT JOIN script_reactions sr ON s.script_id = sr.script_id
        GROUP BY s.script_id
        ORDER BY score DESC, s.views DESC
        LIMIT ? OFFSET ?
    ''', (limit, offset))
    scripts = cursor.fetchall()
    conn.close()
    return [script[0] for script in scripts]

def get_recent_scripts(limit: int = 10, offset: int = 0) -> list:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT script_id FROM scripts ORDER BY creation_date DESC LIMIT ? OFFSET ?', (limit, offset))
    scripts = cursor.fetchall()
    conn.close()
    return [script[0] for script in scripts]

def get_total_scripts_count() -> int:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM scripts')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def get_total_views_count() -> int:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(views) FROM scripts')
    count = cursor.fetchone()[0] or 0
    conn.close()
    return count

def get_total_likes_count() -> int:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM script_reactions WHERE reaction_type = 1')
    count = cursor.fetchone()[0] or 0
    conn.close()
    return count

def get_total_dislikes_count() -> int:
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM script_reactions WHERE reaction_type = -1')
    count = cursor.fetchone()[0] or 0
    conn.close()
    return count

def can_send_report(user_id: int) -> tuple[bool, str]:
    """Проверяет, может ли пользователь отправить репорт.
    Возвращает (может_отправить, сообщение_об_ошибке)"""
    if is_admin(user_id) or is_developer(user_id):
        return True, ""
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT report_date FROM reports 
        WHERE user_id = ? 
        ORDER BY report_date DESC 
        LIMIT 1
    ''', (user_id,))
    last_report = cursor.fetchone()
    conn.close()
    
    if not last_report:
        return True, ""
    
    last_report_time = datetime.strptime(last_report[0], "%Y-%m-%d %H:%M:%S")
    time_since_last = datetime.now() - last_report_time
    
    if time_since_last.total_seconds() < 1800:  # 30 минут = 1800 секунд
        remaining = 1800 - time_since_last.total_seconds()
        minutes = int(remaining // 60)
        seconds = int(remaining % 60)
        return False, f"❌ Вы можете отправлять репорты только раз в 30 минут.\nПопробуйте через {minutes} мин. {seconds} сек."
    
    return True, ""

def generate_random_string(length=8):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def log_event(level: str, message: str):
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO logs (level, message, log_date) VALUES (?, ?, ?)',
        (level, message, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    logger.log(getattr(logging, level.upper()), message)

def get_user_info(user_id: int) -> dict:
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return {
            'user_id': user[0],
            'username': user[1],
            'first_name': user[2],
            'last_name': user[3],
            'join_date': user[4],
            'visit_count': user[5],
            'link_visits': user[6],
            'is_banned': user[7],
            'ban_reason': user[8],
            'banned_by': user[9],
            'ban_date': user[10]
        }
    return None

def update_user_visit(user_id: int, username: str, first_name: str, last_name: str):
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
    user = cursor.fetchone()
    
    if user:
        cursor.execute('UPDATE users SET visit_count = visit_count + 1 WHERE user_id = ?', (user_id,))
    else:
        cursor.execute(
            'INSERT INTO users (user_id, username, first_name, last_name, join_date, visit_count) VALUES (?, ?, ?, ?, ?, 1)',
            (user_id, username, first_name, last_name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
    
    conn.commit()
    conn.close()

def is_admin(user_id: int) -> bool:
    if user_id in ADMINS:
        return True
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM admins WHERE admin_id = ?', (user_id,))
    admin = cursor.fetchone()
    conn.close()
    
    return admin is not None

def is_developer(user_id: int) -> bool:
    return user_id in DEVELOPERS

def is_banned(user_id: int) -> bool:
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT is_banned FROM users WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()
    
    return result and result[0] == 1

# Добавим глобальную переменную для хранения времени последней успешной проверки
LAST_SUBSCRIPTION_CHECK = {}

@dp.callback_query(F.data.startswith("subgram_check"))
async def subgram_check_callback(callback: CallbackQuery, state: FSMContext):
    try:
        user_id = callback.from_user.id
        current_time = time.time()
        
        # Удаляем кнопку "Я подписался"
        await callback.message.edit_reply_markup(reply_markup=None)
        
        # Показываем сообщение о проверке
        checking_msg = await callback.message.answer("🔄 Проверяю ваши подписки...")
        
        # Получаем контекст команды из состояния или сообщения
        data = await state.get_data()
        command_context = data.get('command_context')
        
        # Если контекст не в состоянии, пробуем определить из сообщения
        if not command_context:
            if hasattr(callback.message, 'text'):
                if callback.message.text.startswith('/start'):
                    command_context = 'start'
                elif callback.message.text.startswith('/help'):
                    command_context = 'help'
                elif callback.message.text.startswith('/catalog'):
                    command_context = 'catalog'
                elif callback.message.text.startswith('/user_stats'):
                    command_context = 'user_stats'
        
        # Выполняем проверку через SubGram API
        subgram_response = await check_subgram_subscription(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            first_name=callback.from_user.first_name,
            language_code=callback.from_user.language_code,
            premium=callback.from_user.is_premium
        )
        
        if subgram_response.get('status') == 'ok':
            # Успешная проверка
            LAST_SUBSCRIPTION_CHECK[user_id] = current_time
            await checking_msg.delete()
            
            # Выполняем действие в зависимости от контекста команды
            if command_context == 'start':
                parts = callback.message.text.split()
                if len(parts) > 1:
                    link_id = parts[1]
                    await process_link(link_id, callback.message, user_id)
                else:
                    await show_welcome(callback.message)
            
            elif command_context == 'help':
                await cmd_help(callback.message)
            
            elif command_context == 'catalog':
                await show_catalog_main_menu(callback.message)
            
            elif command_context == 'user_stats':
                await cmd_user_stats(callback.message)
            
            elif 'link_id' in data:
                await process_link(data['link_id'], callback.message, user_id)
                await state.clear()
            
            else:
                await show_welcome(callback.message)
            
            # Удаляем оригинальное сообщение с каналами
            await asyncio.sleep(1)
            await callback.message.delete()
            
        else:
            # Не все подписки оформлены
            await checking_msg.delete()
            
            # Сохраняем контекст команды в состоянии
            await state.update_data(command_context=command_context)
            
            # Формируем новый список каналов
            channels_text = "❌ Вы не подписаны на все каналы:\n\n"
            if 'links' in subgram_response:
                for link in subgram_response['links']:
                    channels_text += f"• {link}\n"
            elif 'additional' in subgram_response and 'sponsors' in subgram_response['additional']:
                for sponsor in subgram_response['additional']['sponsors']:
                    if sponsor['status'] != 'subscribed':
                        channels_text += f"• {sponsor['link']}\n"
            
            channels_text += "📢 Для использования бота подпишитесь на каналы, затем нажмите 'Я выполнил' и нажмите кнопку ниже ↓\n"
            
            # Создаем новую кнопку
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="✅ Я подписался", 
                callback_data="subgram_check"
            ))
            
            # Обновляем сообщение
            await callback.message.edit_text(
                channels_text,
                reply_markup=keyboard.as_markup()
            )
            
            await callback.answer("Пожалуйста, подпишитесь на все каналы")
            
    except Exception as e:
        log_event('ERROR', f"Error in subgram_check_callback: {str(e)}")
        try:
            await checking_msg.delete()
        except:
            pass
            
        await callback.message.answer("❌ Произошла ошибка при проверке подписки. Попробуйте позже.")
        await callback.answer()

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    try:
        user = message.from_user
        
        if is_banned(user.id):
            await message.answer("❌ Вы заблокированы в этом боте.")
            return
        
        update_user_visit(user.id, user.username, user.first_name, user.last_name)
        
        if is_admin(user.id) or is_developer(user.id):
            await handle_start_content(message, state, message.text.split())
            return
            
        subgram_response = await check_subgram_subscription(
            user_id=user.id,
            chat_id=message.chat.id,
            first_name=user.first_name
        )
        
        if subgram_response.get('status') != 'ok':
            start_args = message.text.split()
            if len(start_args) > 1:
                await state.update_data(link_id=start_args[1])
            
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(
                text="✅ Я подписался", 
                callback_data="subgram_check"
            ))
            
            channels_text = "📢 Для использования бота подпишитесь на каналы, нажмите на кнопку 'Я выполнил' и затем нажмите на кнопку ниже ↓\n\n"
            if 'links' in subgram_response:
                for link in subgram_response['links']:
                    channels_text += f"• {link}\n"
            
            await message.answer(
                channels_text,
                reply_markup=keyboard.as_markup()
            )
        else:
            await handle_start_content(message, state, message.text.split())
            
    except Exception as e:
        log_event('ERROR', f"Error in cmd_start: {str(e)}")
        await message.answer("⚠️ Произошла ошибка. Пожалуйста, попробуйте позже.")

async def handle_start_content(message: Message, state: FSMContext, start_args: list):
    """Обработка контента после успешной проверки подписки"""
    user = message.from_user
    
    # Если есть ссылка - обрабатываем её
    if len(start_args) > 1:
        link_id = start_args[1]
        await process_link(link_id, message, user.id)
    else:
        # Показываем обычное приветствие
        await show_welcome(message)

async def process_link(link_id: str, message: Message, user_id: int):
    """Обработка ссылки и выдача контента"""
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM links WHERE link_id = ?', (link_id,))
    link = cursor.fetchone()
    
    if link:
        content_type = link[1]
        content_text = link[2]
        content_file_id = link[3]
        
        # Обновляем статистику
        cursor.execute('UPDATE links SET visits = visits + 1 WHERE link_id = ?', (link_id,))
        cursor.execute('UPDATE users SET link_visits = link_visits + 1 WHERE user_id = ?', (user_id,))
        conn.commit()
        
        # Отправляем контент
        if content_type == 'text':
            await message.answer(content_text)
        elif content_type == 'photo':
            await message.answer_photo(content_file_id, caption=content_text)
        elif content_type == 'document':
            await message.answer_document(content_file_id, caption=content_text)
    else:
        await message.answer("❌ Ссылка не найдена или была удалена")
    
    conn.close()

async def show_welcome(message: Message):
    user = message.from_user
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users')
    user_count = cursor.fetchone()[0]
    conn.close()
    
    welcome_text = f"""
# Привет, <b>{user.first_name}</b> 👋

Я Mirrozz Scripts — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀

<b>Почему я лучший?</b>
- <b>Актуальные скрипты</b> — база обновляется регулярно!
- <b>Мгновенный доступ</b> — получай скрипты в пару кликов!
- <b>Надежное хранение</b> — твои скрипты всегда под рукой!
- <b>Стабильная работа</b> — бот на мощном сервере, без сбоев!
"""
    if is_admin(user.id):
        welcome_text += f"\n\n<b>👑 Вы администратор бота!</b>\nДоступ к админ-панели: /admin"
    if is_developer(user.id):
        welcome_text += f"\n\n<b>💻 Вы разработчик бота!</b>\nДоступ к панели разработчика: /admin"
    
    welcome_text += "\n\nНапиши /help, чтобы узнать все команды!"
    
    await message.answer(welcome_text, parse_mode=ParseMode.HTML)

# Help command handler
@dp.message(Command('help'))
async def cmd_help(message: Message, state: FSMContext):
    await state.update_data(command_context='help')
    help_text = f"""
{hbold('📚 Команды Mirrozz Scripts')}

{hbold('/start')} — Начать работу с ботом
{hbold('/user_stats')} — Показать вашу статистику
{hbold('/report [сообщение]')} — Отправить жалобу администраторам
{hbold('/catalog')} — Открыть каталог скриптов
"""
    if is_admin(message.from_user.id):
        help_text += f"\n{hbold('👑 Админ-команды')}\n{hbold('/admin')} — Открыть админ-панель"
    
    await message.answer(help_text, parse_mode=ParseMode.HTML)
    log_event('INFO', f"User {message.from_user.id} accessed help")

# User stats command handler
@dp.message(Command('user_stats'))
async def cmd_user_stats(message: Message, state: FSMContext):
    await state.update_data(command_context='user_stats')
    if is_banned(message.from_user.id):
        await message.answer("❌ Вы заблокированы в этом боте.")
        return
    
    # Проверяем подписки через SubGram
    subgram_response = await check_subgram_subscription(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        first_name=message.from_user.first_name,
        language_code=message.from_user.language_code,
        premium=message.from_user.is_premium
    )
    
    if subgram_response.get('status') != 'ok':
        # Показываем сообщение с каналами для подписки
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="✅ Я выполнил", callback_data=f"subgram_check_{message.message_id}"))
        
        channels_text = "📢 Подпишитесь на каналы:\n\n"
        if 'links' in subgram_response:
            for link in subgram_response['links']:
                channels_text += f"• {link}\n"
        elif 'additional' in subgram_response and 'sponsors' in subgram_response['additional']:
            for sponsor in subgram_response['additional']['sponsors']:
                channels_text += f"• {sponsor['link']} - {sponsor['resource_name'] or 'Канал'}\n"
        
        channels_text += "\nПосле подписки нажмите кнопку ниже"
        
        await message.answer(channels_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        return
    
    # Если подписки проверены, показываем статистику
    user_info = get_user_info(message.from_user.id)
    
    if not user_info:
        await message.answer("❌ Информация о вас не найдена.")
        return
    
    stats_text = f"""
{hbold('📊 Ваша статистика')}

👤 {hbold('Имя')}: {user_info['first_name']} {user_info['last_name'] if user_info['last_name'] else ''}
📛 {hbold('Юзернейм')}: @{user_info['username'] if user_info['username'] else 'нет'}
🆔 {hbold('ID')}: {user_info['user_id']}
📅 {hbold('Дата регистрации')}: {user_info['join_date']}
🔄 {hbold('Всего посещений')}: {user_info['visit_count']}
🔗 {hbold('Переходов по ссылкам')}: {user_info['link_visits']}
"""
    await message.answer(stats_text, parse_mode=ParseMode.HTML)
    log_event('INFO', f"User {message.from_user.id} accessed user stats")

# Report command handler
@dp.message(Command('report'))
async def cmd_report(message: Message, state: FSMContext):
    if is_banned(message.from_user.id):
        await message.answer("❌ Вы заблокированы в этом боте.")
        return
    
    # Проверяем, может ли пользователь отправить репорт
    can_send, error_msg = can_send_report(message.from_user.id)
    if not can_send:
        await message.answer(error_msg)
        return
    
    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.answer("❌ Пожалуйста, укажите сообщение для жалобы.\nПример: /report Это спам")
        return
    
    report_text = args[1]
    user = message.from_user
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO reports (user_id, message, report_date) VALUES (?, ?, ?)',
        (user.id, report_text, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    report_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✉️ Ответить", callback_data=f"answer_report_{report_id}"))
    keyboard.add(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user.id}"))
    
    for admin_id in ADMINS:
        try:
            await bot.send_message(
                admin_id,
                f"⚠️ Новый репорт #{report_id}!\n\n"
                f"От: {user.full_name} (@{user.username or 'нет'})\n"
                f"ID: {user.id}\n"
                f"Дата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Сообщение: {report_text}",
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            log_event('ERROR', f"Не удалось отправить репорт админу {admin_id}: {str(e)}")
    
    await message.answer("✅ Ваша жалоба отправлена администраторам. Ответ придёт в течение 30 минут.")
    log_event('INFO', f"User {user.id} submitted report #{report_id}")

# Команда /catalog
@dp.message(Command('catalog'))
async def cmd_catalog(message: Message, state: FSMContext):
    await state.update_data(command_context='catalog')
    # Проверка подписки через SubGram
    subgram_response = await check_subgram_subscription(
        user_id=message.from_user.id,
        chat_id=message.chat.id,
        first_name=message.from_user.first_name
    )
    
    if subgram_response.get('status') != 'ok':
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="subgram_check"))
        
        channels_text = "📢 Для использования каталога подпишитесь на каналы:\n\n"
        if 'links' in subgram_response:
            for link in subgram_response['links']:
                channels_text += f"• {link}\n"
        
        await message.answer(channels_text, reply_markup=keyboard.as_markup())
        return
    
    await show_catalog_main_menu(message)

async def show_catalog_main_menu(message: Message):
    total_scripts = get_total_scripts_count()
    total_views = get_total_views_count()
    total_likes = get_total_likes_count()
    
    text = f"""
📚 <b>Каталог скриптов</b>

Всего скриптов: {total_scripts}
Общее кол-во просмотров: {total_views}
Общее кол-во лайков: {total_likes}
"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔍 Поиск скрипта", callback_data="catalog_search"))
    keyboard.add(InlineKeyboardButton(text="🔥 Популярные скрипты", callback_data="catalog_popular"))
    keyboard.add(InlineKeyboardButton(text="🆕 Последние скрипты", callback_data="catalog_recent"))
    
    if is_admin(message.from_user.id):
        keyboard.add(InlineKeyboardButton(text="👑 Управление каталогом", callback_data="catalog_admin"))
    
    keyboard.adjust(1)
    
    await message.answer(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

# Обработчики кнопок каталога
@dp.callback_query(F.data == "catalog_search")
async def catalog_search_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("🔍 Введите название режима или скрипта для поиска:")
    await state.set_state(CatalogStates.search_query)
    await callback.answer()

@dp.callback_query(F.data == "catalog_popular")
async def catalog_popular_callback(callback: CallbackQuery, state: FSMContext):
    await show_scripts_list(callback.message, CatalogViewType.POPULAR)
    await callback.answer()

@dp.callback_query(F.data == "catalog_recent")
async def catalog_recent_callback(callback: CallbackQuery, state: FSMContext):
    await show_scripts_list(callback.message, CatalogViewType.RECENT)
    await callback.answer()

# Admin command handler
@dp.message(Command('admin'))
async def cmd_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ У вас нет доступа к этой команде.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    keyboard.add(InlineKeyboardButton(text="🔗 Ссылки", callback_data="admin_links"))
    keyboard.add(InlineKeyboardButton(text="👑 Админы", callback_data="admin_admins"))
    keyboard.add(InlineKeyboardButton(text="⚠️ Репорты", callback_data="admin_reports"))
    keyboard.add(InlineKeyboardButton(text="👤 Пользователи", callback_data="admin_users"))
    keyboard.add(InlineKeyboardButton(text="📢 Реклама", callback_data="admin_advertise"))
    keyboard.add(InlineKeyboardButton(text="📚 Каталог скриптов", callback_data="catalog_admin"))  # Новая кнопка
    
    if is_developer(message.from_user.id):
        keyboard.add(InlineKeyboardButton(text="💻 Панель разработчика", callback_data="admin_developer"))
    
    keyboard.adjust(2)  # Можно настроить количество кнопок в ряду
    
    await message.answer(f"{hbold('👑 Админ-панель')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    log_event('INFO', f"Admin {message.from_user.id} accessed admin panel")

@dp.callback_query(F.data == "catalog_admin")
async def catalog_admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Добавить скрипт", callback_data="catalog_add_script"))
    keyboard.add(InlineKeyboardButton(text="📋 Список скриптов", callback_data="catalog_list_scripts"))
    keyboard.add(InlineKeyboardButton(text="📊 Статистика", callback_data="catalog_stats"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="catalog_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text("👑 Управление каталогом скриптов", reply_markup=keyboard.as_markup())
    await callback.answer()

# Обработчик поискового запроса
@dp.message(CatalogStates.search_query)
async def process_search_query(message: Message, state: FSMContext):
    query = message.text.strip()
    if not query:
        await message.answer("❌ Запрос не может быть пустым.")
        return
    
    await state.update_data(search_query=query, offset=0)
    await show_scripts_list(message, CatalogViewType.SEARCH, query)

async def show_scripts_list(message: Message, view_type: CatalogViewType, query: str = None, offset: int = 0):
    if view_type == CatalogViewType.SEARCH:
        script_ids = get_scripts_by_search(query, limit=1, offset=offset)
    elif view_type == CatalogViewType.POPULAR:
        script_ids = get_popular_scripts(limit=1, offset=offset)
    elif view_type == CatalogViewType.RECENT:
        script_ids = get_recent_scripts(limit=1, offset=offset)
    else:
        return
    
    if not script_ids:
        await message.answer("❌ Скрипты не найдены.")
        return
    
    script_id = script_ids[0]
    await show_script(message, script_id, view_type, query, offset)

async def show_script(message: Message, script_id: int, view_type: CatalogViewType, query: str = None, offset: int = 0):
    script = get_script_info(script_id)
    if not script:
        await message.answer("❌ Скрипт не найден.")
        return
    
    increment_script_views(script_id)
    
    try:
        creator = await bot.get_chat(script['created_by'])
        creator_name = creator.full_name
    except:
        creator_name = "Неизвестно"
    
    text = f"""
📜 <b>{script['name']}</b>

📅 Дата создания: {script['creation_date']}
👤 Создал: {creator_name}
👀 Просмотров: {script['views']}
👍 Лайков: {script['likes']}
👎 Дизлайков: {script['dislikes']}
🔑 Ключ система: {'✅' if script['has_key'] else '❌'}

📝 Описание:
{script['description']}

🎮 Меню:
{script['menu']}
"""
    keyboard = InlineKeyboardBuilder()
    
    # Кнопки навигации
    if view_type == CatalogViewType.SEARCH:
        prev_callback = f"search_prev_{offset}_{query}"
        next_callback = f"search_next_{offset}_{query}"
    elif view_type == CatalogViewType.POPULAR:
        prev_callback = f"popular_prev_{offset}"
        next_callback = f"popular_next_{offset}"
    elif view_type == CatalogViewType.RECENT:
        prev_callback = f"recent_prev_{offset}"
        next_callback = f"recent_next_{offset}"
    else:
        prev_callback = next_callback = ""
    
    keyboard.add(InlineKeyboardButton(text="🔙 Каталог", callback_data="catalog_back"))
    
    if offset > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=prev_callback))
    
    keyboard.add(InlineKeyboardButton(text="📥 Получить", callback_data=f"get_script_{script_id}"))
    
    if view_type != CatalogViewType.ADMIN_VIEW:
        keyboard.add(InlineKeyboardButton(text="👍", callback_data=f"like_script_{script_id}"))
        keyboard.add(InlineKeyboardButton(text="👎", callback_data=f"dislike_script_{script_id}"))
    
    if (view_type == CatalogViewType.SEARCH and len(get_scripts_by_search(query, limit=1, offset=offset+1)) > 0) or \
       (view_type == CatalogViewType.POPULAR and len(get_popular_scripts(limit=1, offset=offset+1)) > 0) or \
       (view_type == CatalogViewType.RECENT and len(get_recent_scripts(limit=1, offset=offset+1)) > 0):
        keyboard.add(InlineKeyboardButton(text="➡️ Вперед", callback_data=next_callback))
    
    keyboard.adjust(2, repeat=True)
    
    if script['image_id']:
        await message.answer_photo(script['image_id'], caption=text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    else:
        await message.answer(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

# Обработчики навигации
@dp.callback_query(F.data.startswith("search_prev_"))
async def search_prev_callback(callback: CallbackQuery):
    parts = callback.data.split('_')
    offset = int(parts[2]) - 1
    query = '_'.join(parts[3:])
    
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.SEARCH, query, offset)
    await callback.answer()

@dp.callback_query(F.data.startswith("search_next_"))
async def search_next_callback(callback: CallbackQuery):
    parts = callback.data.split('_')
    offset = int(parts[2]) + 1
    query = '_'.join(parts[3:])
    
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.SEARCH, query, offset)
    await callback.answer()

@dp.callback_query(F.data.startswith("popular_prev_"))
async def popular_prev_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) - 1
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.POPULAR, offset=offset)
    await callback.answer()

@dp.callback_query(F.data.startswith("popular_next_"))
async def popular_next_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) + 1
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.POPULAR, offset=offset)
    await callback.answer()

@dp.callback_query(F.data.startswith("recent_prev_"))
async def recent_prev_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) - 1
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.RECENT, offset=offset)
    await callback.answer()

@dp.callback_query(F.data.startswith("recent_next_"))
async def recent_next_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) + 1
    await callback.message.delete()
    await show_scripts_list(callback.message, CatalogViewType.RECENT, offset=offset)
    await callback.answer()

# Обработчики лайков/дизлайков
@dp.callback_query(F.data.startswith("like_script_"))
async def like_script_callback(callback: CallbackQuery):
    script_id = int(callback.data.split('_')[2])
    add_user_reaction(script_id, callback.from_user.id, 1)
    await callback.answer("👍 Вы поставили лайк!")

@dp.callback_query(F.data.startswith("dislike_script_"))
async def dislike_script_callback(callback: CallbackQuery):
    script_id = int(callback.data.split('_')[2])
    add_user_reaction(script_id, callback.from_user.id, -1)
    await callback.answer("👎 Вы поставили дизлайк!")

# Обработчик получения скрипта
@dp.callback_query(F.data.startswith("get_script_"))
async def get_script_callback(callback: CallbackQuery):
    script_id = int(callback.data.split('_')[2])
    script = get_script_info(script_id)
    
    if not script:
        await callback.answer("❌ Скрипт не найден.")
        return
    
    await callback.message.answer(f"📜 Скрипт для {script['name']}:\n\n{script['script_code']}")
    await callback.answer()

# Обработчик возврата в каталог
@dp.callback_query(F.data == "catalog_back")
async def catalog_back_callback(callback: CallbackQuery):
    await callback.message.delete()
    await show_catalog_main_menu(callback.message)
    await callback.answer()

# Админские функции каталога
@dp.callback_query(F.data == "catalog_add_script")
async def catalog_add_script_callback(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("📝 Введите название скрипта:")
    await state.set_state(CatalogStates.add_script_name)
    await callback.answer()

@dp.message(CatalogStates.add_script_name)
async def process_add_script_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("📝 Введите описание скрипта:")
    await state.set_state(CatalogStates.add_script_description)

@dp.message(CatalogStates.add_script_description)
async def process_add_script_description(message: Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("🎮 Введите меню скрипта (какие функции доступны):")
    await state.set_state(CatalogStates.add_script_menu)

@dp.message(CatalogStates.add_script_menu)
async def process_add_script_menu(message: Message, state: FSMContext):
    await state.update_data(menu=message.text)
    await message.answer("📜 Введите сам скрипт:")
    await state.set_state(CatalogStates.add_script_code)

@dp.message(CatalogStates.add_script_code)
async def process_add_script_code(message: Message, state: FSMContext):
    await state.update_data(script_code=message.text)
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✅ Да", callback_data="script_has_key_1"))
    keyboard.add(InlineKeyboardButton(text="❌ Нет", callback_data="script_has_key_0"))
    
    await message.answer("🔑 Скрипт требует ключ для работы?", reply_markup=keyboard.as_markup())

@dp.callback_query(F.data.startswith("script_has_key_"))
async def process_script_has_key(callback: CallbackQuery, state: FSMContext):
    has_key = int(callback.data.split('_')[3])
    await state.update_data(has_key=has_key)
    await callback.message.answer("🖼 Отправьте изображение для скрипта (или нажмите /skip чтобы пропустить):")
    await state.set_state(CatalogStates.add_script_image)
    await callback.answer()

@dp.message(Command('skip'), CatalogStates.add_script_image)
async def skip_script_image(message: Message, state: FSMContext):
    await process_script_image(message, state, None)

@dp.message(CatalogStates.add_script_image)
async def process_script_image_handler(message: Message, state: FSMContext):
    if message.photo:
        image_id = message.photo[-1].file_id
    elif message.text == '/skip':
        image_id = None
    else:
        await message.answer("Пожалуйста, отправьте изображение или нажмите /skip")
        return
    
    data = await state.get_data()
    
    # Создаем предпросмотр поста
    preview_text = f"""
📜 <b>Предпросмотр скрипта</b>

🎮 <b>Название:</b> {data['name']}
📝 <b>Описание:</b> {data['description']}
🔑 <b>Ключ система:</b> {'✅ Да' if data.get('has_key', 0) else '❌ Нет'}
🖼 <b>Изображение:</b> {'Есть' if image_id else 'Нет'}
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✅ Готово", callback_data="confirm_script_post"))
    keyboard.add(InlineKeyboardButton(text="✏️ Редактировать", callback_data="edit_script_post"))
    keyboard.add(InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_script_post"))
    
    if image_id:
        await message.answer_photo(image_id, caption=preview_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    else:
        await message.answer(preview_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    
    await state.update_data(image_id=image_id, preview_shown=True)

async def process_script_image(message: Message, state: FSMContext, image_id: str = None):
    data = await state.get_data()
    
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO scripts (name, description, script_code, menu, has_key, created_by, creation_date, image_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['name'],
        data['description'],
        data['script_code'],
        data['menu'],
        data['has_key'],
        message.from_user.id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        image_id
    ))
    script_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Скрипт успешно добавлен в каталог! ID: {script_id}")
    await state.clear()

@dp.callback_query(F.data == "confirm_script_post")
async def confirm_script_post(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    
    conn = sqlite3.connect('/root/mirrozz_catalog_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO scripts (name, description, script_code, menu, has_key, created_by, creation_date, image_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        data['name'],
        data['description'],
        data['script_code'],
        data['menu'],
        data.get('has_key', 0),
        callback.from_user.id,
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        data.get('image_id')
    ))
    script_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(f"✅ Скрипт успешно добавлен в каталог! ID: {script_id}")
    await state.clear()
    await show_catalog_main_menu(callback.message)

@dp.callback_query(F.data == "catalog_list_scripts")
async def catalog_list_scripts_callback(callback: CallbackQuery):
    await show_admin_scripts_list(callback.message)

async def show_admin_scripts_list(message: Message, offset: int = 0):
    script_ids = get_recent_scripts(limit=5, offset=offset)
    
    if not script_ids:
        await message.answer("❌ В каталоге нет скриптов.")
        return
    
    keyboard = InlineKeyboardBuilder()
    
    for script_id in script_ids:
        script = get_script_info(script_id)
        keyboard.add(InlineKeyboardButton(
            text=f"{script['name']} (ID: {script_id})",
            callback_data=f"admin_view_script_{script_id}"
        ))
    
    keyboard.adjust(1)
    
    # Кнопки навигации
    nav_keyboard = InlineKeyboardBuilder()
    
    if offset > 0:
        nav_keyboard.add(InlineKeyboardButton(text="⏪ В начало", callback_data="admin_scripts_first"))
        nav_keyboard.add(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"admin_scripts_prev_{offset}"))
    
    nav_keyboard.add(InlineKeyboardButton(text=f"{offset//5 + 1}", callback_data="no_action"))
    
    if len(get_recent_scripts(limit=5, offset=offset+5)) > 0:
        nav_keyboard.add(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"admin_scripts_next_{offset}"))
        nav_keyboard.add(InlineKeyboardButton(text="В конец ⏩", callback_data=f"admin_scripts_last"))
    
    nav_keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="catalog_admin"))
    nav_keyboard.adjust(4, 1)
    
    await message.answer("📋 Список скриптов (последние добавленные):", reply_markup=keyboard.as_markup())
    await message.answer("Навигация:", reply_markup=nav_keyboard.as_markup())

@dp.callback_query(F.data.startswith("admin_view_script_"))
async def admin_view_script_callback(callback: CallbackQuery):
    script_id = int(callback.data.split('_')[3])
    await show_script(callback.message, script_id, CatalogViewType.ADMIN_VIEW)
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_scripts_prev_"))
async def admin_scripts_prev_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) - 5
    await callback.message.delete()
    await show_admin_scripts_list(callback.message, max(0, offset))
    await callback.answer()

@dp.callback_query(F.data.startswith("admin_scripts_next_"))
async def admin_scripts_next_callback(callback: CallbackQuery):
    offset = int(callback.data.split('_')[2]) + 5
    await callback.message.delete()
    await show_admin_scripts_list(callback.message, offset)
    await callback.answer()

@dp.callback_query(F.data == "admin_scripts_first")
async def admin_scripts_first_callback(callback: CallbackQuery):
    await callback.message.delete()
    await show_admin_scripts_list(callback.message, 0)
    await callback.answer()

@dp.callback_query(F.data == "admin_scripts_last")
async def admin_scripts_last_callback(callback: CallbackQuery):
    total = get_total_scripts_count()
    offset = max(0, total - 5)
    await callback.message.delete()
    await show_admin_scripts_list(callback.message, offset)
    await callback.answer()

@dp.callback_query(F.data == "catalog_stats")
async def catalog_stats_callback(callback: CallbackQuery):
    total_scripts = get_total_scripts_count()
    total_views = get_total_views_count()
    total_likes = get_total_likes_count()
    total_dislikes = get_total_dislikes_count()
    
    # Самый популярный скрипт
    popular_scripts = get_popular_scripts(limit=1)
    if popular_scripts:
        popular_script = get_script_info(popular_scripts[0])
        popular_text = f"{popular_script['name']} (ID: {popular_script['script_id']}, 👀: {popular_script['views']}, 👍: {popular_script['likes']})"
    else:
        popular_text = "Нет данных"
    
    # Последний скрипт
    recent_scripts = get_recent_scripts(limit=1)
    if recent_scripts:
        recent_script = get_script_info(recent_scripts[0])
        recent_text = f"{recent_script['name']} (ID: {recent_script['script_id']}, добавлен {recent_script['creation_date']}"
    else:
        recent_text = "Нет данных"
    
    text = f"""
📊 <b>Статистика каталога</b>

📜 Всего скриптов: {total_scripts}
👀 Всего просмотров: {total_views}
👍 Всего лайков: {total_likes}
👎 Всего дизлайков: {total_dislikes}

🔥 <b>Самый популярный скрипт</b>:
{popular_text}

🆕 <b>Последний добавленный скрипт</b>:
{recent_text}
"""
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="catalog_admin"))
    
    await callback.message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Admin stats callback
@dp.callback_query(F.data == "admin_stats")
async def admin_stats_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM users')
    total_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    banned_users = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM admins')
    total_admins = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM links')
    total_links = cursor.fetchone()[0]
    
    cursor.execute('SELECT SUM(visits) FROM links')
    total_link_visits = cursor.fetchone()[0] or 0
    
    cursor.execute('SELECT COUNT(*) FROM advertise_channels')
    total_advertise = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM reports WHERE status = "open"')
    open_reports = cursor.fetchone()[0]
    
    conn.close()
    
    stats_text = f"""
{hbold('📊 Статистика бота')}

{hbold('👥 Пользователи')}
• Всего: {total_users}
• Заблокированы: {banned_users}
• Активные: {total_users - banned_users}

{hbold('👑 Администраторы')}: {total_admins}
{hbold('🔗 Ссылки')}: {total_links} (переходов: {total_link_visits})
{hbold('📢 Каналы для подписки')}: {total_advertise}
{hbold('⚠️ Открытые репорты')}: {open_reports}
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    await callback.message.edit_text(stats_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed stats")

# Admin links callback
@dp.callback_query(F.data == "admin_links")
async def admin_links_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Создать ссылку", callback_data="create_link"))
    keyboard.add(InlineKeyboardButton(text="➕ Создать кастомную ссылку", callback_data="create_custom_link"))
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить ссылку", callback_data="delete_link"))
    keyboard.add(InlineKeyboardButton(text="📋 Список ссылок", callback_data="list_links"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('🔗 Управление ссылками')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Create link callback
@dp.callback_query(F.data == "create_link")
async def create_link_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📝 Введите содержимое для ссылки (текст):")
    await state.set_state(Form.create_link_content)
    await callback.answer()

# Create custom link callback
@dp.callback_query(F.data == "create_custom_link")
async def create_custom_link_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📝 Введите содержимое для ссылки (текст):")
    await state.set_state(Form.create_link_content)
    await state.update_data(is_custom=True)
    await callback.answer()

# Create link content handler
@dp.message(Form.create_link_content)
async def create_link_content_handler(message: Message, state: FSMContext):
    await state.update_data(content_text=message.text)
    data = await state.get_data()
    
    if data.get('is_custom', False):
        await message.answer("🔤 Введите ID ссылки (8 символов):")
        await state.set_state(Form.create_custom_link_id)
    else:
        await message.answer("📎 Отправьте файл (фото или документ), или нажмите /skip чтобы пропустить.")
        await state.set_state(Form.create_link_file)

# Create custom link ID handler
@dp.message(Form.create_custom_link_id)
async def create_custom_link_id_handler(message: Message, state: FSMContext):
    link_id = message.text.strip()
    
    if len(link_id) != 8 or not link_id.isalnum():
        await message.answer("❌ ID ссылки должен состоять из 8 букв или цифр.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM links WHERE link_id = ?', (link_id,))
    if cursor.fetchone():
        await message.answer("❌ Ссылка с таким ID уже существует.")
        conn.close()
        return
    
    data = await state.get_data()
    content_text = data.get('content_text', '')
    
    cursor.execute(
        'INSERT INTO links (link_id, content_type, content_text, created_by, creation_date) VALUES (?, ?, ?, ?, ?)',
        (link_id, 'text', content_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    bot_username = (await bot.get_me()).username
    link_url = f"https://t.me/{bot_username}?start={link_id}"
    
    await message.answer(f"✅ Кастомная ссылка создана!\n\n🔗 URL: {hlink('Перейти', link_url)}\n📝 Содержимое: {content_text}", parse_mode=ParseMode.HTML)
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} created custom link {link_id}")

# Skip file for link
@dp.message(Command('skip'), Form.create_link_file)
async def skip_link_file(message: Message, state: FSMContext):
    data = await state.get_data()
    content_text = data.get('content_text', '')
    
    link_id = generate_random_string(8)
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO links (link_id, content_type, content_text, created_by, creation_date) VALUES (?, ?, ?, ?, ?)',
        (link_id, 'text', content_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    bot_username = (await bot.get_me()).username
    link_url = f"https://t.me/{bot_username}?start={link_id}"
    
    await message.answer(f"✅ Ссылка создана!\n\n🔗 URL: {hlink('Перейти', link_url)}\n📝 Содержимое: {content_text}", parse_mode=ParseMode.HTML)
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} created link {link_id}")

# Create link file handler
@dp.message(Form.create_link_file)
async def create_link_file_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    content_text = data.get('content_text', '')
    
    content_type = None
    content_file_id = None
    
    if message.photo:
        content_type = 'photo'
        content_file_id = message.photo[-1].file_id
    elif message.document:
        if message.document.file_size > 2 * 1024 * 1024:
            await message.answer("❌ Файл слишком большой. Максимальный размер - 2 МБ.")
            return
        content_type = 'document'
        content_file_id = message.document.file_id
    else:
        await message.answer("❌ Пожалуйста, отправьте фото или документ.")
        return
    
    link_id = generate_random_string(8)
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO links (link_id, content_type, content_text, content_file_id, created_by, creation_date) VALUES (?, ?, ?, ?, ?, ?)',
        (link_id, content_type, content_text, content_file_id, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    bot_username = (await bot.get_me()).username
    link_url = f"https://t.me/{bot_username}?start={link_id}"
    
    await message.answer(f"✅ Ссылка создана!\n\n🔗 URL: {hlink('Перейти', link_url)}\n📝 Содержимое: {content_text}", parse_mode=ParseMode.HTML)
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} created link {link_id} with file")

# Delete link callback
@dp.callback_query(F.data == "delete_link")
async def delete_link_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT link_id, content_text, created_by, creation_date, visits FROM links ORDER BY creation_date DESC')
    links = cursor.fetchall()
    conn.close()
    
    if not links:
        await callback.message.answer("❌ Нет созданных ссылок.")
        await callback.answer()
        return
    
    await state.update_data(links=links, link_index=0)
    await show_link_for_deletion(callback.message, state, 0)
    await callback.answer()

async def show_link_for_deletion(message: Message, state: FSMContext, index: int):
    data = await state.get_data()
    links = data['links']
    if index < 0 or index >= len(links):
        await message.answer("❌ Ссылки закончились.")
        return
    
    link = links[index]
    link_id, content_text, created_by, creation_date, visits = link
    
    try:
        creator = await bot.get_chat(created_by)
        creator_name = creator.full_name
    except:
        creator_name = "Неизвестно"
    
    bot_username = (await bot.get_me()).username
    link_url = f"https://t.me/{bot_username}?start={link_id}"
    
    text = f"""
{hbold('🔗 Ссылка для удаления')}

🔗 URL: {hlink('Перейти', link_url)}
👤 Создал: {creator_name}
📅 Дата: {creation_date}
👀 Переходов: {visits}
📝 Содержимое: {content_text[:50]}...
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_link_{link_id}"))
    if index > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"prev_link_{index}"))
    if index < len(links) - 1:
        keyboard.add(InlineKeyboardButton(text="➡️ Следующая", callback_data=f"next_link_{index}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_links"))
    
    keyboard.adjust(1)
    
    await message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("prev_link_"))
async def prev_link_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) - 1
    await show_link_for_deletion(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("next_link_"))
async def next_link_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) + 1
    await show_link_for_deletion(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_link_"))
async def confirm_delete_link_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    link_id = callback.data.split('_')[3]
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM links WHERE link_id = ?', (link_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Ссылка {link_id} удалена.")
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} deleted link {link_id}")

# List links callback
@dp.callback_query(F.data == "list_links")
async def list_links_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM links')
    total_links = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_links + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT link_id, created_by, creation_date, visits 
        FROM links 
        ORDER BY creation_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    links = cursor.fetchall()
    conn.close()
    
    if not links:
        await callback.message.answer("❌ Нет созданных ссылок.")
        await callback.answer()
        return
    
    await state.update_data(links_page=0, total_pages=total_pages)
    await show_links_page(callback.message, state, links, 0, total_pages)
    await callback.answer()

async def show_links_page(message: Message, state: FSMContext, links: list, page: int, total_pages: int):
    links_text = f"{hbold('📋 Список ссылок')}\n\n"
    for link in links:
        link_id, created_by, creation_date, visits = link
        bot_username = (await bot.get_me()).username
        link_url = f"https://t.me/{bot_username}?start={link_id}"
        
        try:
            creator = await bot.get_chat(created_by)
            creator_name = creator.full_name
        except:
            creator_name = "Неизвестно"
        
        links_text += f"🔗 {hlink('Перейти', link_url)}\n👤 Создал: {creator_name}\n📅 Дата: {creation_date}\n👀 Переходов: {visits}\n\n"
    
    keyboard = create_navigation_keyboard(page, total_pages, "admin_links", "links_")
    
    await message.edit_text(links_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

# Обработчики навигации для списка ссылок
@dp.callback_query(F.data.startswith("links_prev_"))
async def links_prev_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) - 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT link_id, created_by, creation_date, visits 
        FROM links 
        ORDER BY creation_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    links = cursor.fetchall()
    conn.close()
    
    await state.update_data(links_page=page)
    await show_links_page(callback.message, state, links, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data.startswith("links_next_"))
async def links_next_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) + 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT link_id, created_by, creation_date, visits 
        FROM links 
        ORDER BY creation_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    links = cursor.fetchall()
    conn.close()
    
    await state.update_data(links_page=page)
    await show_links_page(callback.message, state, links, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "links_first")
async def links_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT link_id, created_by, creation_date, visits 
        FROM links 
        ORDER BY creation_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    links = cursor.fetchall()
    conn.close()
    
    await state.update_data(links_page=0)
    await show_links_page(callback.message, state, links, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "links_last")
async def links_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT link_id, created_by, creation_date, visits 
        FROM links 
        ORDER BY creation_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    links = cursor.fetchall()
    conn.close()
    
    await state.update_data(links_page=page)
    await show_links_page(callback.message, state, links, page, total_pages)
    await callback.answer()

# Admin back callback
@dp.callback_query(F.data == "admin_back")
async def admin_back_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"))
    keyboard.add(InlineKeyboardButton(text="🔗 Ссылки", callback_data="admin_links"))
    keyboard.add(InlineKeyboardButton(text="👑 Админы", callback_data="admin_admins"))
    keyboard.add(InlineKeyboardButton(text="⚠️ Репорты", callback_data="admin_reports"))
    keyboard.add(InlineKeyboardButton(text="👤 Пользователи", callback_data="admin_users"))
    keyboard.add(InlineKeyboardButton(text="📢 Реклама", callback_data="admin_advertise"))
    
    if is_developer(callback.from_user.id):
        keyboard.add(InlineKeyboardButton(text="💻 Панель разработчика", callback_data="admin_developer"))
    
    keyboard.adjust(2)
    
    await callback.message.edit_text(f"{hbold('👑 Админ-панель')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Admin admins callback
@dp.callback_query(F.data == "admin_admins")
async def admin_admins_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Добавить админа", callback_data="add_admin"))
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить админа", callback_data="remove_admin"))
    keyboard.add(InlineKeyboardButton(text="📋 Список админов", callback_data="list_admins"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('👑 Управление администраторами')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Add admin callback
@dp.callback_query(F.data == "add_admin")
async def add_admin_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("👤 Введите username или ID пользователя, которого хотите сделать администратором:")
    await state.set_state(Form.admin_add)
    await callback.answer()

# Add admin handler
@dp.message(Form.admin_add)
async def add_admin_handler(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    try:
        user_id = int(user_input)
        try:
            user = await bot.get_chat(user_id)
        except:
            await message.answer("❌ Пользователь с таким ID не найден.")
            await state.clear()
            return
    except ValueError:
        if not user_input.startswith('@'):
            user_input = '@' + user_input
        
        try:
            user = await bot.get_chat(user_input)
            user_id = user.id
        except:
            await message.answer("❌ Пользователь с таким username не найден.")
            await state.clear()
            return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM admins WHERE admin_id = ?', (user_id,))
    existing_admin = cursor.fetchone()
    
    if existing_admin:
        await message.answer("❌ Этот пользователь уже является администратором.")
        conn.close()
        await state.clear()
        return
    
    cursor.execute(
        'INSERT INTO admins (admin_id, username, first_name, last_name, added_by, add_date) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, user.username, user.first_name, user.last_name, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Пользователь {user.full_name} (@{user.username or 'нет'}) успешно добавлен в администраторы!")
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} added admin {user_id}")

# Remove admin callback
@dp.callback_query(F.data == "remove_admin")
async def remove_admin_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id, username, first_name, last_name FROM admins')
    admins = cursor.fetchall()
    conn.close()
    
    if not admins:
        await callback.message.answer("❌ Нет администраторов для удаления.")
        await callback.answer()
        return
    
    await state.update_data(admins=admins, admin_index=0)
    await show_admin_for_removal(callback.message, state, 0)
    await callback.answer()

async def show_admin_for_removal(message: Message, state: FSMContext, index: int):
    data = await state.get_data()
    admins = data['admins']
    if index < 0 or index >= len(admins):
        await message.answer("❌ Администраторы закончились.")
        return
    
    admin = admins[index]
    admin_id, username, first_name, last_name = admin
    
    text = f"""
{hbold('👑 Администратор для удаления')}

👤 {first_name} {last_name if last_name else ''}
📛 @{username if username else 'нет'}
🆔 {admin_id}
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_remove_admin_{admin_id}"))
    if index > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Предыдущий", callback_data=f"prev_admin_{index}"))
    if index < len(admins) - 1:
        keyboard.add(InlineKeyboardButton(text="➡️ Следующий", callback_data=f"next_admin_{index}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_admins"))
    
    keyboard.adjust(1)
    
    await message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("prev_admin_"))
async def prev_admin_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) - 1
    await show_admin_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("next_admin_"))
async def next_admin_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) + 1
    await show_admin_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_remove_admin_"))
async def confirm_remove_admin_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    admin_id = int(callback.data.split('_')[3])
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM admins WHERE admin_id = ?', (admin_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Администратор с ID {admin_id} удалён.")
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} removed admin {admin_id}")

def create_navigation_keyboard(page: int, total_pages: int, back_callback: str, prefix: str = ""):
    keyboard = InlineKeyboardBuilder()
    
    # Добавляем кнопки навигации
    if page > 0:
        keyboard.add(InlineKeyboardButton(text="⏪ В начало", callback_data=f"{prefix}first"))
        keyboard.add(InlineKeyboardButton(text="◀️ Назад", callback_data=f"{prefix}prev_{page}"))
    
    # Кнопка с текущей страницей
    keyboard.add(InlineKeyboardButton(text=f"{page+1}/{total_pages}", callback_data="no_action"))
    
    if page < total_pages - 1:
        keyboard.add(InlineKeyboardButton(text="Вперед ▶️", callback_data=f"{prefix}next_{page}"))
        keyboard.add(InlineKeyboardButton(text="В конец ⏩", callback_data=f"{prefix}last"))
    
    # Кнопка возврата
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data=back_callback))
    
    keyboard.adjust(4, 1)  # 4 кнопки в первом ряду, 1 во втором
    return keyboard

# List admins callback
@dp.callback_query(F.data == "list_admins")
async def list_admins_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM admins')
    total_admins = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_admins + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT admin_id, username, first_name, last_name, added_by, add_date 
        FROM admins 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    admins = cursor.fetchall()
    conn.close()
    
    if not admins:
        await callback.message.answer("❌ Нет администраторов.")
        await callback.answer()
        return
    
    await state.update_data(admins_page=0, total_pages=total_pages)
    await show_admins_page(callback.message, state, admins, 0, total_pages)
    await callback.answer()

async def show_admins_page(message: Message, state: FSMContext, admins: list, page: int, total_pages: int):
    admins_text = f"{hbold('👑 Список администраторов')}\n\n"
    for admin in admins:
        admin_id, username, first_name, last_name, added_by, add_date = admin
        try:
            adder = await bot.get_chat(added_by)
            adder_name = adder.full_name
        except:
            adder_name = "Неизвестно"
        
        admins_text += f"👤 {first_name} {last_name if last_name else ''}\n"
        admins_text += f"📛 @{username if username else 'нет'}\n"
        admins_text += f"🆔 {admin_id}\n"
        admins_text += f"👤 Добавил: {adder_name}\n"
        admins_text += f"📅 Дата: {add_date}\n\n"
    
    keyboard = create_navigation_keyboard(page, total_pages, "admin_admins", "admins_")
    
    await message.edit_text(admins_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

# Обработчики навигации для списка администраторов
@dp.callback_query(F.data.startswith("admins_prev_"))
async def admins_prev_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) - 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT admin_id, username, first_name, last_name, added_by, add_date 
        FROM admins 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    admins = cursor.fetchall()
    conn.close()
    
    await state.update_data(admins_page=page)
    await show_admins_page(callback.message, state, admins, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data.startswith("admins_next_"))
async def admins_next_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) + 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT admin_id, username, first_name, last_name, added_by, add_date 
        FROM admins 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    admins = cursor.fetchall()
    conn.close()
    
    await state.update_data(admins_page=page)
    await show_admins_page(callback.message, state, admins, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "admins_first")
async def admins_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT admin_id, username, first_name, last_name, added_by, add_date 
        FROM admins 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    admins = cursor.fetchall()
    conn.close()
    
    await state.update_data(admins_page=0)
    await show_admins_page(callback.message, state, admins, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "admins_last")
async def admins_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT admin_id, username, first_name, last_name, added_by, add_date 
        FROM admins 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    admins = cursor.fetchall()
    conn.close()
    
    await state.update_data(admins_page=page)
    await show_admins_page(callback.message, state, admins, page, total_pages)
    await callback.answer()

# Пустая callback для кнопки с номером страницы
@dp.callback_query(F.data == "no_action")
async def no_action_callback(callback: CallbackQuery):
    await callback.answer()

# Обработчики навигации для списка репортов
@dp.callback_query(F.data.startswith("reports_prev_"))
async def reports_prev_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split('_')
    page = int(parts[2]) - 1
    report_type = parts[3]
    
    await process_reports_page(callback, state, page, report_type)

@dp.callback_query(F.data.startswith("reports_next_"))
async def reports_next_callback(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split('_')
    page = int(parts[2]) + 1
    report_type = parts[3]
    
    await process_reports_page(callback, state, page, report_type)

async def process_reports_page(callback: CallbackQuery, state: FSMContext, page: int, report_type: str):
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    cursor.execute(
        'SELECT COUNT(*) FROM reports WHERE status = ?',
        (report_type,)
    )
    total_reports = cursor.fetchone()[0]
    items_per_page = 5
    total_pages = (total_reports + items_per_page - 1) // items_per_page
    
    cursor.execute(
        f'''
        SELECT report_id, user_id, message, report_date, status
        FROM reports 
        WHERE status = ?
        ORDER BY report_date DESC 
        LIMIT ? OFFSET ?
        ''',
        (report_type, items_per_page, page * items_per_page)
    )
    reports = cursor.fetchall()
    conn.close()
    
    await state.update_data(
        reports_page=page,
        total_pages=total_pages,
        reports_type=report_type
    )
    
    # Удаляем предыдущие сообщения
    await callback.message.delete()
    
    await show_reports_page(callback.message, state, reports, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "reports_first")
async def reports_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT report_id, user_id, message, report_date, status 
        FROM reports 
        ORDER BY report_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    reports = cursor.fetchall()
    conn.close()
    
    await state.update_data(reports_page=0)
    await show_reports_page(callback.message, state, reports, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "reports_last")
async def reports_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT report_id, user_id, message, report_date, status 
        FROM reports 
        ORDER BY report_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    reports = cursor.fetchall()
    conn.close()
    
    await state.update_data(reports_page=page)
    await show_reports_page(callback.message, state, reports, page, total_pages)
    await callback.answer()

# В admin_reports_callback измените клавиатуру:
@dp.callback_query(F.data == "admin_reports")
async def admin_reports_callback(callback: CallbackQuery):
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📜 Открытые репорты", callback_data="open_reports"))
    keyboard.add(InlineKeyboardButton(text="📂 Закрытые репорты", callback_data="closed_reports"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    await callback.message.edit_text("⚠️ Управление репортами", reply_markup=keyboard.as_markup())

# Унифицированный обработчик для открытых/закрытых репортов
@dp.callback_query(F.data.in_(["open_reports", "closed_reports"]))
async def show_reports(callback: CallbackQuery, state: FSMContext):
    report_type = "open" if callback.data == "open_reports" else "closed"
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    # Получаем общее количество репортов данного типа
    cursor.execute('SELECT COUNT(*) FROM reports WHERE status = ?', (report_type,))
    total = cursor.fetchone()[0]
    
    # Получаем первую страницу репортов (5 на страницу)
    cursor.execute('''
        SELECT report_id, user_id, message, report_date, status 
        FROM reports 
        WHERE status = ?
        ORDER BY report_date DESC 
        LIMIT 5 OFFSET 0
    ''', (report_type,))
    reports = cursor.fetchall()
    conn.close()
    
    await state.update_data(
        reports_page=0,
        total_pages=(total + 4) // 5,  # Округляем вверх
        reports_type=report_type
    )
    
    await callback.message.delete()  # Удаляем предыдущее сообщение
    await show_reports_page(callback.message, state, reports, 0, (total + 4) // 5)
    await callback.answer()

# Улучшенная функция показа страницы с репортами
async def show_reports_page(message: Message, state: FSMContext, reports: list, page: int, total_pages: int):
    data = await state.get_data()
    report_type = data["reports_type"]
    
    if not reports:
        await message.answer(f"❌ Нет {report_type} репортов.")
        return
    
    # Отправляем каждый репорт отдельным сообщением
    for report in reports:
        report_id, user_id, message_text, report_date, status = report
        
        try:
            user = await bot.get_chat(user_id)
            user_info = f"{user.full_name} (@{user.username})" if user.username else user.full_name
        except:
            user_info = "Неизвестный пользователь"
        
        text = f"""
📋 Репорт #{report_id} ({status})
👤 От: {user_info}
📅 Дата: {report_date}
📝 Сообщение: {message_text[:200]}...
"""
        keyboard = InlineKeyboardBuilder()
        
        if status == "open":
            keyboard.add(InlineKeyboardButton(
                text="✉️ Ответить", 
                callback_data=f"answer_report_{report_id}"
            ))
            keyboard.add(InlineKeyboardButton(
                text="🚫 Забанить", 
                callback_data=f"ban_{user_id}"
            ))
        
        keyboard.add(InlineKeyboardButton(
            text="🗑 Удалить", 
            callback_data=f"delete_report_{report_id}"
        ))
        
        await message.answer(text, reply_markup=keyboard.as_markup())

    # Кнопки навигации (только если есть несколько страниц)
    if total_pages > 1:
        nav_keyboard = InlineKeyboardBuilder()
        
        if page > 0:
            nav_keyboard.add(InlineKeyboardButton(
                text="⬅️ Назад", 
                callback_data=f"reports_prev_{page}_{report_type}"
            ))
        
        nav_keyboard.add(InlineKeyboardButton(
            text=f"{page+1}/{total_pages}", 
            callback_data="no_action"
        ))
        
        if page < total_pages - 1:
            nav_keyboard.add(InlineKeyboardButton(
                text="Вперед ➡️", 
                callback_data=f"reports_next_{page}_{report_type}"
            ))
        
        await message.answer(
            f"Страница {page+1} из {total_pages}", 
            reply_markup=nav_keyboard.as_markup()
        )

# Добавьте этот обработчик для ответа на репорты
@dp.callback_query(F.data.startswith("answer_report_"))
async def answer_report_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    report_id = int(callback.data.split('_')[2])
    await state.update_data(current_report_id=report_id)
    
    await callback.message.answer("✉️ Введите ответ на этот репорт:")
    await state.set_state(Form.answer_report)
    await callback.answer()

@dp.message(Form.answer_report)
async def answer_report_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    report_id = data['current_report_id']
    answer_text = message.text
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    
    # Получаем информацию о репорте
    cursor.execute(
        'SELECT user_id FROM reports WHERE report_id = ?',
        (report_id,)
    )
    user_id = cursor.fetchone()[0]
    
    # Обновляем репорт
    cursor.execute(
        '''
        UPDATE reports 
        SET answer = ?, answered_by = ?, answer_date = ?, status = 'closed'
        WHERE report_id = ?
        ''',
        (answer_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), report_id)
    )
    conn.commit()
    conn.close()
    
    # Отправляем ответ пользователю
    try:
        await bot.send_message(
            user_id,
            f"📩 Ответ на ваш репорт #{report_id}:\n\n{answer_text}\n\n"
            f"Администратор: {message.from_user.full_name}"
        )
    except Exception as e:
        log_event('ERROR', f"Не удалось отправить ответ пользователю {user_id}: {str(e)}")
    
    await message.answer(
        f"✅ Ответ на репорт #{report_id} отправлен.",
        reply_markup=InlineKeyboardBuilder()
            .add(InlineKeyboardButton(text="📜 К списку репортов", callback_data="open_reports"))
            .as_markup()
    )
    await state.clear()

@dp.callback_query(F.data == "closed_reports")
async def closed_reports_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reports WHERE status = "closed"')
    total_reports = cursor.fetchone()[0]
    items_per_page = 5
    total_pages = (total_reports + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT report_id, user_id, message, report_date 
        FROM reports 
        WHERE status = "closed"
        ORDER BY report_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    reports = cursor.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("❌ Нет закрытых репортов.")
        await callback.answer()
        return
    
    await state.update_data(
        reports_page=0,
        total_pages=total_pages,
        reports_type="closed"
    )
    await show_reports_page(callback.message, state, reports, 0, total_pages)
    await callback.answer()

# Delete report callback - с пагинацией
@dp.callback_query(F.data.startswith("delete_report_"))
async def delete_report_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    report_id = int(callback.data.split('_')[2])
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(
        text="✅ Да, удалить", 
        callback_data=f"confirm_delete_report_{report_id}"
    ))
    keyboard.add(InlineKeyboardButton(
        text="❌ Нет, отмена", 
        callback_data="cancel_delete"
    ))
    
    await callback.message.edit_text(
        f"⚠️ Вы уверены, что хотите удалить репорт #{report_id}?",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_report_"))
async def confirm_delete_report_callback(callback: CallbackQuery):
    report_id = int(callback.data.split('_')[3])
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM reports WHERE report_id = ?', (report_id,))
    conn.commit()
    conn.close()
    
    await callback.message.edit_text(f"✅ Репорт #{report_id} удалён.")
    await callback.answer()

@dp.callback_query(F.data == "cancel_delete")
async def cancel_delete_callback(callback: CallbackQuery):
    await callback.message.delete()
    await callback.answer("❌ Удаление отменено.")

# Report list callback - измененная версия с пагинацией
@dp.callback_query(F.data == "report_list")
async def report_list_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM reports')
    total_reports = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_reports + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT report_id, user_id, message, report_date, status 
        FROM reports 
        ORDER BY report_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    reports = cursor.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("❌ Нет репортов.")
        await callback.answer()
        return
    
    await state.update_data(reports_page=0, total_pages=total_pages)
    await show_reports_page(callback.message, state, reports, 0, total_pages)
    await callback.answer()

async def show_reports_page(message: Message, state: FSMContext, reports: list, page: int, total_pages: int):
    reports_text = f"{hbold('📜 Список репортов')} (Страница {page + 1}/{total_pages})\n\n"
    
    for report in reports:
        report_id, user_id, message_text, report_date, status = report
        
        try:
            user = await bot.get_chat(user_id)
            user_name = user.full_name
            username = f"@{user.username}" if user.username else "нет"
        except:
            user_name = "Неизвестно"
            username = "нет"
        
        reports_text += f"🆔 ID репорта: {report_id}\n"
        reports_text += f"👤 От: {user_name} ({username})\n"
        reports_text += f"📅 Дата: {report_date}\n"
        reports_text += f"📊 Статус: {status}\n"
        reports_text += f"📝 Сообщение: {message_text[:50]}...\n\n"
    
    keyboard = create_navigation_keyboard(page, total_pages, "admin_reports", "reports_")
    
    # Добавляем кнопки действий для первого репорта на странице
    if reports:
        first_report = reports[0]
        report_id = first_report[0]
        user_id = first_report[1]
        
        action_keyboard = InlineKeyboardBuilder()
        action_keyboard.add(InlineKeyboardButton(text="✉️ Ответить", callback_data=f"answer_report_{report_id}"))
        action_keyboard.add(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user_id}"))
        action_keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_report_{report_id}"))
        action_keyboard.adjust(1)
        
        await message.answer(reports_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        await message.answer("Действия с первым репортом на странице:", reply_markup=action_keyboard.as_markup())
    else:
        await message.answer(reports_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

# Admin users callback
@dp.callback_query(F.data == "admin_users")
async def admin_users_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔍 Найти пользователя", callback_data="search_user"))
    keyboard.add(InlineKeyboardButton(text="🚫 Забаненные", callback_data="banned_users"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('👤 Управление пользователями')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Search user callback
@dp.callback_query(F.data == "search_user")
async def search_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("👤 Введите username или ID пользователя:")
    await state.set_state(Form.user_search)
    await callback.answer()

# Search user handler
@dp.message(Form.user_search)
async def search_user_handler(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    try:
        user_id = int(user_input)
        try:
            user = await bot.get_chat(user_id)
        except:
            await message.answer("❌ Пользователь с таким ID не найден.")
            await state.clear()
            return
    except ValueError:
        if not user_input.startswith('@'):
            user_input = '@' + user_input
        
        try:
            user = await bot.get_chat(user_input)
            user_id = user.id
        except:
            await message.answer("❌ Пользователь с таким username не найден.")
            await state.clear()
            return
    
    user_info = get_user_info(user_id)
    
    if not user_info:
        await message.answer("❌ Этот пользователь не зарегистрирован в боте.")
        await state.clear()
        return
    
    user_text = f"""
{hbold('📊 Информация о пользователе')}

👤 {hbold('Имя')}: {user_info['first_name']} {user_info['last_name'] if user_info['last_name'] else ''}
📛 {hbold('Юзернейм')}: @{user_info['username'] if user_info['username'] else 'нет'}
🆔 {hbold('ID')}: {user_info['user_id']}
📅 {hbold('Дата регистрации')}: {user_info['join_date']}
🔄 {hbold('Всего посещений')}: {user_info['visit_count']}
🔗 {hbold('Переходов по ссылкам')}: {user_info['link_visits']}
🚫 {hbold('Статус')}: {'Заблокирован' if user_info['is_banned'] else 'Активен'}
"""
    if user_info['is_banned']:
        user_text += f"\n📝 {hbold('Причина бана')}: {user_info['ban_reason']}"
        user_text += f"\n👮 {hbold('Забанил')}: {user_info['banned_by']}"
        user_text += f"\n📅 {hbold('Дата бана')}: {user_info['ban_date']}"
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✉️ Отправить сообщение", callback_data=f"send_msg_{user_id}"))
    
    if user_info['is_banned']:
        keyboard.add(InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{user_id}"))
    else:
        keyboard.add(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user_id}"))
    
    keyboard.add(InlineKeyboardButton(text="📊 Информация", callback_data=f"user_info_{user_id}"))
    keyboard.adjust(1)
    
    await message.answer(user_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} searched user {user_id}")

# Send message callback
@dp.callback_query(F.data.startswith("send_msg_"))
async def send_msg_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    user_id = int(callback.data.split('_')[2])
    await state.update_data(user_id=user_id)
    await callback.message.answer("✉️ Введите сообщение для пользователя:")
    await state.set_state(Form.send_user_message)
    await callback.answer()

# Send message handler
@dp.message(Form.send_user_message)
async def send_user_message_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data['user_id']
    message_text = message.text
    
    try:
        await bot.send_message(user_id, f"📩 Сообщение от администрации:\n\n{message_text}")
        await message.answer(f"✅ Сообщение отправлено пользователю с ID {user_id}.")
    except:
        await message.answer(f"❌ Не удалось отправить сообщение пользователю с ID {user_id}.")
    
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} sent message to user {user_id}")

# Ban user callback
@dp.callback_query(F.data.startswith("ban_"))
async def ban_user_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    user_id = int(callback.data.split('_')[1])
    await state.update_data(user_id=user_id)
    await callback.message.answer("📝 Укажите причину бана:")
    await state.set_state(Form.ban_user)
    await callback.answer()

# Ban user handler
@dp.message(Form.ban_user)
async def ban_user_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    user_id = data['user_id']
    ban_reason = message.text
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE users SET is_banned = 1, ban_reason = ?, banned_by = ?, ban_date = ? WHERE user_id = ?',
        (ban_reason, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
    )
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(
            user_id,
            f"🚫 Вы были заблокированы в боте.\n\n📝 Причина: {ban_reason}\n\nЕсли вы считаете, что это ошибка, свяжитесь с администратором."
        )
    except:
        pass
    
    await message.answer(f"✅ Пользователь с ID {user_id} успешно заблокирован.")
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} banned user {user_id}")

# Unban user callback
@dp.callback_query(F.data.startswith("unban_"))
async def unban_user_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    user_id = int(callback.data.split('_')[1])
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE users SET is_banned = 0, ban_reason = NULL, banned_by = NULL, ban_date = NULL WHERE user_id = ?',
        (user_id,)
    )
    conn.commit()
    conn.close()
    
    try:
        await bot.send_message(user_id, "✅ Вы были разблокированы в боте.")
    except:
        pass
    
    await callback.message.answer(f"✅ Пользователь с ID {user_id} успешно разблокирован.")
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} unbanned user {user_id}")

# Banned users callback
@dp.callback_query(F.data == "banned_users")
async def banned_users_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_banned = 1')
    total_banned = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_banned + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date 
        FROM users 
        WHERE is_banned = 1
        ORDER BY ban_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    banned_users = cursor.fetchall()
    conn.close()
    
    if not banned_users:
        await callback.message.answer("❌ Нет заблокированных пользователей.")
        await callback.answer()
        return
    
    await state.update_data(banned_page=0, total_pages=total_pages)
    await show_banned_page(callback.message, state, banned_users, 0, total_pages)
    await callback.answer()

async def show_banned_page(message: Message, state: FSMContext, banned_users: list, page: int, total_pages: int):
    banned_text = f"{hbold('🚫 Заблокированные пользователи')} (Страница {page + 1}/{total_pages})\n\n"
    
    for user in banned_users:
        user_id, username, first_name, last_name, ban_reason, banned_by, ban_date = user
        banned_text += f"👤 {first_name} {last_name if last_name else ''}\n"
        banned_text += f"📛 @{username if username else 'нет'}\n"
        banned_text += f"🆔 {user_id}\n"
        banned_text += f"📝 Причина: {ban_reason}\n"
        banned_text += f"👮 Забанил: {banned_by}\n"
        banned_text += f"📅 Дата: {ban_date}\n\n"
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="✅ Разбанить", callback_data=f"unban_{user_id}"))
        keyboard.add(InlineKeyboardButton(text="📊 Информация", callback_data=f"user_info_{user_id}"))
        keyboard.adjust(1)
        
        await message.answer(banned_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        banned_text = ""
    
    # Добавляем навигацию только если есть несколько страниц
    if total_pages > 1:
        nav_keyboard = create_navigation_keyboard(page, total_pages, "admin_users", "banned_")
        await message.answer("Навигация по списку:", reply_markup=nav_keyboard.as_markup())

@dp.callback_query(F.data.startswith("banned_prev_"))
async def banned_prev_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) - 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date 
        FROM users 
        WHERE is_banned = 1
        ORDER BY ban_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    banned_users = cursor.fetchall()
    conn.close()
    
    await state.update_data(banned_page=page)
    await show_banned_page(callback.message, state, banned_users, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data.startswith("banned_next_"))
async def banned_next_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) + 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date 
        FROM users 
        WHERE is_banned = 1
        ORDER BY ban_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    banned_users = cursor.fetchall()
    conn.close()
    
    await state.update_data(banned_page=page)
    await show_banned_page(callback.message, state, banned_users, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "banned_first")
async def banned_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date 
        FROM users 
        WHERE is_banned = 1
        ORDER BY ban_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    banned_users = cursor.fetchall()
    conn.close()
    
    await state.update_data(banned_page=0)
    await show_banned_page(callback.message, state, banned_users, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "banned_last")
async def banned_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date 
        FROM users 
        WHERE is_banned = 1
        ORDER BY ban_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    banned_users = cursor.fetchall()
    conn.close()
    
    await state.update_data(banned_page=page)
    await show_banned_page(callback.message, state, banned_users, page, total_pages)
    await callback.answer()

# User info callback
@dp.callback_query(F.data.startswith("user_info_"))
async def user_info_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    user_id = int(callback.data.split('_')[2])
    user_info = get_user_info(user_id)
    
    if not user_info:
        await callback.message.answer("❌ Пользователь не найден.")
        await callback.answer()
        return
    
    user_text = f"""
{hbold('📊 Информация о пользователе')}

👤 {hbold('Имя')}: {user_info['first_name']} {user_info['last_name'] if user_info['last_name'] else ''}
📛 {hbold('Юзернейм')}: @{user_info['username'] if user_info['username'] else 'нет'}
🆔 {hbold('ID')}: {user_info['user_id']}
📅 {hbold('Дата регистрации')}: {user_info['join_date']}
🔄 {hbold('Всего посещений')}: {user_info['visit_count']}
🔗 {hbold('Переходов по ссылкам')}: {user_info['link_visits']}
🚫 {hbold('Статус')}: {'Заблокирован' if user_info['is_banned'] else 'Активен'}
"""
    if user_info['is_banned']:
        user_text += f"\n📝 {hbold('Причина бана')}: {user_info['ban_reason']}"
        user_text += f"\n👮 {hbold('Забанил')}: {user_info['banned_by']}"
        user_text += f"\n📅 {hbold('Дата бана')}: {user_info['ban_date']}"
    
    await callback.message.answer(user_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed user info {user_id}")

# Admin advertise callback
@dp.callback_query(F.data == "admin_advertise")
async def admin_advertise_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Добавить канал", callback_data="add_advertise"))
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить канал", callback_data="remove_advertise"))
    keyboard.add(InlineKeyboardButton(text="📋 Список каналов", callback_data="list_advertise"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('📢 Управление рекламными каналами')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Add advertise callback
@dp.callback_query(F.data == "add_advertise")
async def add_advertise_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📢 Введите @username или ID канала/чата:")
    await state.set_state(Form.add_advertise)
    await callback.answer()

# Add advertise handler
@dp.message(Form.add_advertise)
async def add_advertise_handler(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    try:
        channel_id = int(user_input)
        try:
            chat = await bot.get_chat(channel_id)
        except:
            await message.answer("❌ Канал/чат с таким ID не найден.")
            await state.clear()
            return
    except ValueError:
        if not user_input.startswith('@'):
            user_input = '@' + user_input
        
        try:
            chat = await bot.get_chat(user_input)
            channel_id = chat.id
        except:
            await message.answer("❌ Канал/чат с таким username не найден.")
            await state.clear()
            return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM advertise_channels WHERE channel_id = ?', (channel_id,))
    existing_channel = cursor.fetchone()
    
    if existing_channel:
        await message.answer("❌ Этот канал/чат уже добавлен.")
        conn.close()
        await state.clear()
        return
    
    await state.update_data(channel_id=channel_id, username=chat.username, title=chat.title)
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="На все функции", callback_data="advertise_type_1"))
    keyboard.add(InlineKeyboardButton(text="Только на ссылки", callback_data="advertise_type_2"))
    
    await message.answer("📢 Выберите тип проверки подписки:", reply_markup=keyboard.as_markup())
    await state.set_state(Form.add_advertise_type)

# Add advertise type handler
@dp.callback_query(F.data.startswith("advertise_type_"))
async def add_advertise_type_callback(callback: CallbackQuery, state: FSMContext):
    check_type = int(callback.data.split('_')[2])
    data = await state.get_data()
    channel_id = data['channel_id']
    username = data['username']
    title = data['title']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO advertise_channels (channel_id, username, title, added_by, add_date, check_type) VALUES (?, ?, ?, ?, ?, ?)',
        (channel_id, username, title, callback.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), check_type)
    )
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Канал/чат {title} (@{username or 'нет'}) успешно добавлен! Тип проверки: {'На все функции' if check_type == 1 else 'Только на ссылки'}")
    await state.clear()
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} added advertise channel {channel_id}")

# Remove advertise callback
@dp.callback_query(F.data == "remove_advertise")
async def remove_advertise_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id, username, title FROM advertise_channels')
    channels = cursor.fetchall()
    conn.close()
    
    if not channels:
        await callback.message.answer("❌ Нет каналов для удаления.")
        await callback.answer()
        return
    
    await state.update_data(channels=channels, channel_index=0)
    await show_channel_for_removal(callback.message, state, 0)
    await callback.answer()

async def show_channel_for_removal(message: Message, state: FSMContext, index: int):
    data = await state.get_data()
    channels = data['channels']
    if index < 0 or index >= len(channels):
        await message.answer("❌ Каналы закончились.")
        return
    
    channel = channels[index]
    channel_id, username, title = channel
    
    text = f"""
{hbold('📢 Канал для удаления')}

📢 {title}
📛 @{username if username else 'нет'}
�ID {channel_id}
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_remove_channel_{channel_id}"))
    if index > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Предыдущий", callback_data=f"prev_channel_{index}"))
    if index < len(channels) - 1:
        keyboard.add(InlineKeyboardButton(text="➡️ Следующий", callback_data=f"next_channel_{index}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_advertise"))
    
    keyboard.adjust(1)
    
    await message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("prev_channel_"))
async def prev_channel_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) - 1
    await show_channel_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("next_channel_"))
async def next_channel_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) + 1
    await show_channel_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_remove_channel_"))
async def confirm_remove_channel_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    channel_id = int(callback.data.split('_')[3])
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM advertise_channels WHERE channel_id = ?', (channel_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Канал с ID {channel_id} удалён из проверки подписки.")
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} removed advertise channel {channel_id}")

# List advertise callback
@dp.callback_query(F.data == "list_advertise")
async def list_advertise_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM advertise_channels')
    total_channels = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_channels + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count 
        FROM advertise_channels 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    channels = cursor.fetchall()
    conn.close()
    
    if not channels:
        await callback.message.answer("❌ Нет добавленных каналов/чатов.")
        await callback.answer()
        return
    
    await state.update_data(channels_page=0, total_pages=total_pages)
    await show_channels_page(callback.message, state, channels, 0, total_pages)
    await callback.answer()

async def show_channels_page(message: Message, state: FSMContext, channels: list, page: int, total_pages: int):
    channels_text = f"{hbold('📢 Список рекламных каналов')} (Страница {page + 1}/{total_pages})\n\n"
    
    for channel in channels:
        channel_id, username, title, added_by, add_date, check_type, subscribers_count = channel
        
        try:
            adder = await bot.get_chat(added_by)
            adder_name = adder.full_name
        except:
            adder_name = "Неизвестно"
        
        channels_text += f"📢 {title}\n"
        channels_text += f"📛 @{username if username else 'нет'}\n"
        channels_text += f"🆔 {channel_id}\n"
        channels_text += f"👤 Добавил: {adder_name}\n"
        channels_text += f"📅 Дата: {add_date}\n"
        channels_text += f"🔍 Проверка: {'На все функции' if check_type == 1 else 'Только на ссылки'}\n"
        channels_text += f"👥 Подписчиков: {subscribers_count}\n\n"
    
    keyboard = create_navigation_keyboard(page, total_pages, "admin_advertise", "channels_")
    
    await message.edit_text(channels_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("channels_prev_"))
async def channels_prev_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) - 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count 
        FROM advertise_channels 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    channels = cursor.fetchall()
    conn.close()
    
    await state.update_data(channels_page=page)
    await show_channels_page(callback.message, state, channels, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data.startswith("channels_next_"))
async def channels_next_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) + 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count 
        FROM advertise_channels 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    channels = cursor.fetchall()
    conn.close()
    
    await state.update_data(channels_page=page)
    await show_channels_page(callback.message, state, channels, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "channels_first")
async def channels_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count 
        FROM advertise_channels 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    channels = cursor.fetchall()
    conn.close()
    
    await state.update_data(channels_page=0)
    await show_channels_page(callback.message, state, channels, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "channels_last")
async def channels_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count 
        FROM advertise_channels 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    channels = cursor.fetchall()
    conn.close()
    
    await state.update_data(channels_page=page)
    await show_channels_page(callback.message, state, channels, page, total_pages)
    await callback.answer()

# Admin developer callback
@dp.callback_query(F.data == "admin_developer")
async def admin_developer_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="💾 База данных", callback_data="developer_database"))
    keyboard.add(InlineKeyboardButton(text="📨 Сообщения", callback_data="developer_messages"))
    keyboard.add(InlineKeyboardButton(text="🚫 Ошибки", callback_data="developer_errors"))
    keyboard.add(InlineKeyboardButton(text="👨‍💻 Разработчики", callback_data="developer_developers"))
    keyboard.add(InlineKeyboardButton(text="🖥 Сервер", callback_data="developer_server"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(2)
    
    await callback.message.edit_text(f"{hbold('💻 Панель разработчика')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Developer {callback.from_user.id} accessed developer panel")

# Developer database callback - с кнопкой назад
# Улучшенная панель разработчика
@dp.callback_query(F.data == "developer_database")
async def developer_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="⬇️ Скачать базу", callback_data="download_database"))
    keyboard.add(InlineKeyboardButton(text="ℹ️ Информация", callback_data="database_info"))
    keyboard.add(InlineKeyboardButton(text="🔄 Сбросить базу", callback_data="reset_database"))
    keyboard.add(InlineKeyboardButton(text="📤 Загрузить базу", callback_data="load_database"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
    
    keyboard.adjust(2)
    
    await callback.message.edit_text(f"{hbold('💾 Управление базой данных')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Новая функция - информация о базе данных
@dp.callback_query(F.data == "database_info")
async def database_info_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        
        # Получаем информацию о таблицах
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cursor.fetchall()
        
        info_text = f"{hbold('ℹ️ Информация о базе данных')}\n\n"
        info_text += f"📊 Всего таблиц: {len(tables)}\n\n"
        
        # Получаем размер базы данных
        db_size = os.path.getsize('/root/bot_mirrozz_database.db') / (1024 * 1024)  # в MB
        
        info_text += f"📦 Размер базы: {db_size:.2f} MB\n\n"
        
        # Получаем количество записей в основных таблицах
        for table in tables:
            table_name = table[0]
            cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
            count = cursor.fetchone()[0]
            info_text += f"• {table_name}: {count} записей\n"
        
        conn.close()
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_database"))
        
        await callback.message.edit_text(info_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении информации: {str(e)}")
    
    await callback.answer()

# Developer messages callback - с кнопкой назад
@dp.callback_query(F.data == "developer_messages")
async def developer_messages_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📩 Отправить системное сообщение", callback_data="send_system_message"))
    keyboard.add(InlineKeyboardButton(text="📜 История сообщений", callback_data="message_history"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('📨 Управление системными сообщениями')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Developer errors callback - с кнопкой назад
@dp.callback_query(F.data == "developer_errors")
async def developer_errors_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📊 Подробная статистика", callback_data="error_status"))
    keyboard.add(InlineKeyboardButton(text="📜 Последние ошибки", callback_data="list_errors"))
    keyboard.add(InlineKeyboardButton(text="📥 Скачать логи", callback_data="download_logs"))
    keyboard.add(InlineKeyboardButton(text="🗑 Очистить логи", callback_data="clear_logs"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(
        f"{hbold('🚫 Управление ошибками и логами')}\n\nПросмотр статистики ошибок и управление логами бота",
        reply_markup=keyboard.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

# Developer server callback - с кнопкой назад
@dp.callback_query(F.data == "developer_server")
async def developer_server_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Calculate uptime
        uptime = time.time() - BOT_START_TIME
        uptime_str = str(timedelta(seconds=int(uptime)))
        
        # Get server stats with error handling
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
        except Exception as e:
            cpu_percent = f"Ошибка: {str(e)}"
        
        try:
            memory = psutil.virtual_memory()
            memory_str = f"{memory.percent}% ({memory.used / (1024**3):.2f} / {memory.total / (1024**3):.2f} GB)"
        except Exception as e:
            memory_str = f"Ошибка: {str(e)}"
        
        try:
            disk = psutil.disk_usage('/')
            disk_str = f"{disk.percent}% ({disk.used / (1024**3):.2f} / {disk.total / (1024**3):.2f} GB)"
        except Exception as e:
            disk_str = f"Ошибка: {str(e)}"
        
        server_text = f"""
{hbold('🖥 Информация о сервере')}

⏳ Время работы: {uptime_str}
🧮 CPU: {cpu_percent}
💾 Память: {memory_str}
💿 Диск: {disk_str}
"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔄 Перезапустить бота", callback_data="restart_bot"))
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
        
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            server_text,
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('INFO', f"Developer {callback.from_user.id} viewed server stats")
        
    except Exception as e:
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
        await callback.message.edit_text(
            f"❌ Ошибка при получении информации о сервере: {str(e)}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('ERROR', f"Developer {callback.from_user.id} failed to view server stats: {str(e)}")
    
    await callback.answer()

# Улучшенное скачивание базы данных
@dp.callback_query(F.data == "download_database")
async def download_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Создаем временную копию базы для безопасности
        temp_db = f"bot_mirrozz_database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copyfile('/root/bot_mirrozz_database.db', temp_db)
        
        db_file = FSInputFile(temp_db, filename='bot_mirrozz_database.db')
        await callback.message.answer_document(db_file, caption="📦 Резервная копия базы данных")
        
        # Удаляем временный файл после отправки
        os.remove(temp_db)
        
        await callback.answer()
        log_event('INFO', f"Developer {callback.from_user.id} downloaded database")
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при создании резервной копии: {str(e)}")
        await callback.answer()

# Reset database callback
@dp.callback_query(F.data == "reset_database")
async def reset_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✅ Да", callback_data="confirm_reset_database"))
    keyboard.add(InlineKeyboardButton(text="❌ Нет", callback_data="developer_database"))
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите сбросить базу данных? Все данные будут удалены!",
        reply_markup=keyboard.as_markup(),
        parse_mode=ParseMode.HTML
    )
    await callback.answer()

# Confirm reset database callback
@dp.callback_query(F.data == "confirm_reset_database")
async def confirm_reset_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Backup current database before reset
        backup_file = f"bot_mirrozz_database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        os.system(f"cp bot_mirrozz_database.db {backup_file}")
        
        # Reset database
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        
        # Drop all tables
        cursor.execute('DROP TABLE IF EXISTS users')
        cursor.execute('DROP TABLE IF EXISTS links')
        cursor.execute('DROP TABLE IF EXISTS admins')
        cursor.execute('DROP TABLE IF EXISTS reports')
        cursor.execute('DROP TABLE IF EXISTS advertise_channels')
        cursor.execute('DROP TABLE IF EXISTS system_messages')
        cursor.execute('DROP TABLE IF EXISTS developers')
        cursor.execute('DROP TABLE IF EXISTS logs')
        
        conn.commit()
        conn.close()
        
        # Reinitialize database
        init_db()
        
        await callback.message.answer(f"✅ База данных успешно сброшена. Резервная копия сохранена: {backup_file}")
        log_event('WARNING', f"Developer {callback.from_user.id} reset the database")
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при сбросе базы данных: {str(e)}")
        log_event('ERROR', f"Developer {callback.from_user.id} failed to reset database: {str(e)}")
    
    await callback.answer()

# Last database update callback
@dp.callback_query(F.data == "last_database_update")
async def last_database_update_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        
        # Get latest update from logs
        cursor.execute('SELECT log_date FROM logs ORDER BY log_date DESC LIMIT 1')
        last_update = cursor.fetchone()
        
        conn.close()
        
        update_text = f"{hbold('📅 Последнее обновление базы данных')}\n\n"
        update_text += f"Дата: {last_update[0] if last_update else 'Нет данных'}\n"
        
        await callback.message.edit_text(update_text, parse_mode=ParseMode.HTML)
        log_event('INFO', f"Developer {callback.from_user.id} checked last database update")
        
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении информации: {str(e)}")
        log_event('ERROR', f"Developer {callback.from_user.id} failed to check last database update: {str(e)}")
    
    await callback.answer()

# Load database callback
@dp.callback_query(F.data == "load_database")
async def load_database_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📤 Пожалуйста, отправьте файл базы данных (.db):")
    await state.set_state(Form.load_database)
    await callback.answer()

# Load database handler
@dp.message(Form.load_database)
async def load_database_handler(message: Message, state: FSMContext):
    if not is_developer(message.from_user.id):
        await message.answer("❌ У вас нет доступа.")
        return
    
    if not message.document or not message.document.file_name.endswith('.db'):
        await message.answer("❌ Пожалуйста, отправьте файл базы данных с расширением .db")
        await state.clear()
        return
    
    try:
        # Download the database file
        file = await bot.get_file(message.document.file_id)
        file_path = f"temp_database_{message.from_user.id}.db"
        await bot.download_file(file.file_path, file_path)
        
        # Backup current database
        backup_file = f"bot_mirrozz_database_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        os.system(f"cp bot_mirrozz_database.db {backup_file}")
        
        # Replace current database with uploaded one
        os.system(f"mv {file_path} bot_mirrozz_database.db")
        
        # Verify database integrity
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        cursor.execute('PRAGMA integrity_check')
        integrity = cursor.fetchone()[0]
        conn.close()
        
        if integrity != 'ok':
            # Restore backup if integrity check fails
            os.system(f"mv {backup_file} bot_mirrozz_database.db")
            await message.answer("❌ Загруженная база данных повреждена. Восстановлена предыдущая версия.")
            log_event('ERROR', f"Developer {message.from_user.id} failed to load database: integrity check failed")
        else:
            await message.answer(f"✅ База данных успешно загружена. Резервная копия сохранена: {backup_file}")
            log_event('INFO', f"Developer {message.from_user.id} loaded new database")
        
        os.remove(file_path) if os.path.exists(file_path) else None
        
    except Exception as e:
        await message.answer(f"❌ Ошибка при загрузке базы данных: {str(e)}")
        log_event('ERROR', f"Developer {message.from_user.id} failed to load database: {str(e)}")
    
    await state.clear()

# Send system message callback
@dp.callback_query(F.data == "send_system_message")
async def send_system_message_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📩 Введите текст системного сообщения для всех пользователей:")
    await state.set_state(Form.system_message)
    await callback.answer()

@dp.message(Form.system_message)
async def send_system_message_handler(message: Message, state: FSMContext):
    if not is_developer(message.from_user.id):
        await message.answer("❌ У вас нет доступа.")
        return
    
    message_text = message.text
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE is_banned = 0')
    users = cursor.fetchall()
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_messages"))
    
    recipients_count = 0
    errors_count = 0
    for user in users:
        user_id = user[0]
        try:
            await bot.send_message(user_id, f"📢 Системное сообщение:\n\n{message_text}")
            recipients_count += 1
        except Exception as e:
            errors_count += 1
            log_event('ERROR', f"Failed to send system message to {user_id}: {str(e)}")
            continue
    
    cursor.execute(
        'INSERT INTO system_messages (message_text, sent_by, send_date, recipients_count) VALUES (?, ?, ?, ?)',
        (message_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), recipients_count)
    )
    conn.commit()
    conn.close()
    
    result_text = f"✅ Системное сообщение отправлено {recipients_count} пользователям."
    if errors_count > 0:
        result_text += f"\n❌ Не удалось отправить {errors_count} пользователям."
    
    await message.answer(result_text, reply_markup=keyboard.as_markup())
    log_event('INFO', f"Developer {message.from_user.id} sent system message to {recipients_count} users")
    await state.clear()

# Message history callback
@dp.callback_query(F.data == "message_history")
async def message_history_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT message_id, message_text, sent_by, send_date, recipients_count FROM system_messages ORDER BY send_date DESC LIMIT 10')
    messages = cursor.fetchall()
    conn.close()
    
    if not messages:
        await callback.message.answer("❌ Нет системных сообщений.")
        await callback.answer()
        return
    
    history_text = f"{hbold('📜 История системных сообщений')}\n\n"
    for msg in messages:
        message_id, message_text, sent_by, send_date, recipients_count = msg
        
        try:
            sender = await bot.get_chat(sent_by)
            sender_name = sender.full_name
        except:
            sender_name = "Неизвестно"
        
        history_text += f"🆔 ID сообщения: {message_id}\n"
        history_text += f"👤 Отправитель: {sender_name}\n"
        history_text += f"📅 Дата: {send_date}\n"
        history_text += f"👥 Получатели: {recipients_count}\n"
        history_text += f"📝 Текст: {message_text[:50]}...\n\n"
    
    await callback.message.answer(history_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Developer {callback.from_user.id} viewed message history")

# Список ошибок
@dp.callback_query(F.data == "list_errors")
async def list_errors_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT log_id, level, message, log_date FROM logs ORDER BY log_date DESC LIMIT 10')
        logs = cursor.fetchall()
        conn.close()
        
        if not logs:
            await callback.message.answer("❌ Нет логов.")
            await callback.answer()
            return
        
        logs_text = f"{hbold('🚫 Последние 10 ошибок')}\n\n"
        for log in logs:
            log_id, level, message, log_date = log
            logs_text += f"🆔 ID: {log_id}\n"
            logs_text += f"📊 Уровень: {level}\n"
            logs_text += f"📅 Дата: {log_date}\n"
            logs_text += f"📝 Сообщение: {message[:100]}...\n\n"
        
        await callback.message.answer(logs_text, parse_mode=ParseMode.HTML)
        await callback.answer()
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при получении логов: {str(e)}")
        await callback.answer()

@dp.callback_query(F.data == "error_status")
async def error_status_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Получаем статистику ошибок из базы данных
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        
        # Общее количество ошибок
        cursor.execute('SELECT COUNT(*) FROM logs')
        total_errors = cursor.fetchone()[0]
        
        # Количество ошибок по уровням
        cursor.execute('SELECT level, COUNT(*) FROM logs GROUP BY level')
        levels_stats = cursor.fetchall()
        
        # Последние 5 критических ошибок
        cursor.execute('SELECT message, log_date FROM logs WHERE level = "error" ORDER BY log_date DESC LIMIT 5')
        recent_errors = cursor.fetchall()
        
        conn.close()
        
        # Получаем информацию о файле логов
        log_file_size = 0
        if os.path.exists('bot_mirrozz.log'):
            log_file_size = os.path.getsize('bot_mirrozz.log') / 1024  # Размер в KB
        
        # Формируем текст сообщения
        status_text = f"""
{hbold('📊 Подробная статистика ошибок')}

{hbold('🔢 Общее количество ошибок')}: {total_errors}
{hbold('📁 Размер файла логов')}: {log_file_size:.2f} KB

{hbold('📈 Распределение по уровням')}:
"""
        
        # Добавляем статистику по уровням
        for level, count in levels_stats:
            status_text += f"• {level.upper()}: {count} ошибок\n"
        
        # Добавляем последние критические ошибки
        if recent_errors:
            status_text += f"\n{hbold('⚠️ Последние 5 критических ошибок')}:\n"
            for error, date in recent_errors:
                status_text += f"• {date}: {error[:50]}...\n"

        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_errors"))
        
        await callback.message.edit_text(status_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        log_event('INFO', f"Developer {callback.from_user.id} viewed detailed error stats")
        
    except Exception as e:
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_errors"))
        await callback.message.edit_text(
            f"❌ Ошибка при получении статистики: {str(e)}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
    
    await callback.answer()

# Скачать логи
@dp.callback_query(F.data == "download_logs")
async def download_logs_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        log_file = FSInputFile('bot_mirrozz.log')
        await callback.message.answer_document(log_file, caption="📁 Логи бота")
        await callback.answer()
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при скачивании логов: {str(e)}")
        await callback.answer()

# Очистить логи
@dp.callback_query(F.data == "clear_logs")
async def clear_logs_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="✅ Да", callback_data="confirm_clear_logs"))
    keyboard.add(InlineKeyboardButton(text="❌ Нет", callback_data="developer_errors"))
    
    await callback.message.edit_text(
        "⚠️ Вы уверены, что хотите очистить все логи?",
        reply_markup=keyboard.as_markup()
    )
    await callback.answer()

@dp.callback_query(F.data == "confirm_clear_logs")
async def confirm_clear_logs_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_errors"))
    
    try:
        # ... существующий код очистки ...
        
        await callback.message.edit_text(
            f"✅ Логи успешно очищены.",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        
    except Exception as e:
        await callback.message.edit_text(
            f"❌ Ошибка при очистке логов: {str(e)}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
    
    await callback.answer()

# Confirm clear logs callback
@dp.callback_query(F.data == "confirm_clear_logs")
async def confirm_clear_logs_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Backup logs before clearing
        backup_file = f"bot_mirrozz_logs_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        if os.path.exists("bot_mirrozz.log"):
            shutil.copy("bot_mirrozz.log", backup_file)
        
        # Clear database logs
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM logs')
        conn.commit()
        conn.close()
        
        # Clear log file
        if os.path.exists("bot_mirrozz.log"):
            with open("bot_mirrozz.log", "w") as f:
                f.write("")
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_errors"))
        await callback.message.edit_text(
            f"✅ Логи успешно очищены. Резервная копия сохранена: {backup_file if os.path.exists(backup_file) else 'нет резервной копии'}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('WARNING', f"Developer {callback.from_user.id} cleared logs")
        
    except Exception as e:
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_errors"))
        await callback.message.edit_text(
            f"❌ Ошибка при очистке логов: {str(e)}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('ERROR', f"Developer {callback.from_user.id} failed to clear logs: {str(e)}")
    
    await callback.answer()

# Developer developers callback
@dp.callback_query(F.data == "developer_developers")
async def developer_developers_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="➕ Добавить разработчика", callback_data="add_developer"))
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить разработчика", callback_data="remove_developer"))
    keyboard.add(InlineKeyboardButton(text="📋 Список разработчиков", callback_data="list_developers"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('👨‍💻 Управление разработчиками')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Add developer callback
@dp.callback_query(F.data == "add_developer")
async def add_developer_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("👨‍💻 Введите username или ID пользователя, которого хотите сделать разработчиком:")
    await state.set_state(Form.add_developer)
    await callback.answer()

# Add developer handler
@dp.message(Form.add_developer)
async def add_developer_handler(message: Message, state: FSMContext):
    user_input = message.text.strip()
    
    try:
        user_id = int(user_input)
        try:
            user = await bot.get_chat(user_id)
        except:
            await message.answer("❌ Пользователь с таким ID не найден.")
            await state.clear()
            return
    except ValueError:
        if not user_input.startswith('@'):
            user_input = '@' + user_input
        
        try:
            user = await bot.get_chat(user_input)
            user_id = user.id
        except:
            await message.answer("❌ Пользователь с таким username не найден.")
            await state.clear()
            return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM developers WHERE developer_id = ?', (user_id,))
    existing_developer = cursor.fetchone()
    
    if existing_developer:
        await message.answer("❌ Этот пользователь уже является разработчиком.")
        conn.close()
        await state.clear()
        return
    
    cursor.execute(
        'INSERT INTO developers (developer_id, username, first_name, last_name, added_by, add_date) VALUES (?, ?, ?, ?, ?, ?)',
        (user_id, user.username, user.first_name, user.last_name, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()
    conn.close()
    
    DEVELOPERS.append(user_id)  # Update in-memory DEVELOPERS list
    
    await message.answer(f"✅ Пользователь {user.full_name} (@{user.username or 'нет'}) успешно добавлен в разработчики!")
    await state.clear()
    log_event('INFO', f"Developer {message.from_user.id} added developer {user_id}")

# Remove developer callback
@dp.callback_query(F.data == "remove_developer")
async def remove_developer_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT developer_id, username, first_name, last_name FROM developers')
    developers = cursor.fetchall()
    conn.close()
    
    if not developers:
        await callback.message.answer("❌ Нет разработчиков для удаления.")
        await callback.answer()
        return
    
    await state.update_data(developers=developers, developer_index=0)
    await show_developer_for_removal(callback.message, state, 0)
    await callback.answer()

async def show_developer_for_removal(message: Message, state: FSMContext, index: int):
    data = await state.get_data()
    developers = data['developers']
    if index < 0 or index >= len(developers):
        await message.answer("❌ Разработчики закончились.")
        return
    
    developer = developers[index]
    developer_id, username, first_name, last_name = developer
    
    text = f"""
{hbold('👨‍💻 Разработчик для удаления')}

👤 {first_name} {last_name if last_name else ''}
📛 @{username if username else 'нет'}
🆔 {developer_id}
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_remove_developer_{developer_id}"))
    if index > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Предыдущий", callback_data=f"prev_developer_{index}"))
    if index < len(developers) - 1:
        keyboard.add(InlineKeyboardButton(text="➡️ Следующий", callback_data=f"next_developer_{index}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="developer_developers"))
    
    keyboard.adjust(1)
    
    await message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("prev_developer_"))
async def prev_developer_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) - 1
    await show_developer_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("next_developer_"))
async def next_developer_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) + 1
    await show_developer_for_removal(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_remove_developer_"))
async def confirm_remove_developer_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    developer_id = int(callback.data.split('_')[3])
    
    if developer_id == callback.from_user.id:
        await callback.message.answer("❌ Вы не можете удалить самого себя из разработчиков.")
        await callback.answer()
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM developers WHERE developer_id = ?', (developer_id,))
    conn.commit()
    conn.close()
    
    if developer_id in DEVELOPERS:
        DEVELOPERS.remove(developer_id)  # Update in-memory DEVELOPERS list
    
    await callback.message.answer(f"✅ Разработчик с ID {developer_id} удалён.")
    await callback.answer()
    log_event('INFO', f"Developer {callback.from_user.id} removed developer {developer_id}")

# List developers callback
@dp.callback_query(F.data == "list_developers")
async def list_developers_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM developers')
    total_devs = cursor.fetchone()[0]
    items_per_page = 10
    total_pages = (total_devs + items_per_page - 1) // items_per_page
    
    cursor.execute('''
        SELECT developer_id, username, first_name, last_name, added_by, add_date 
        FROM developers 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (items_per_page, 0))
    developers = cursor.fetchall()
    conn.close()
    
    if not developers:
        await callback.message.answer("❌ Нет разработчиков.")
        await callback.answer()
        return
    
    await state.update_data(devs_page=0, total_pages=total_pages)
    await show_developers_page(callback.message, state, developers, 0, total_pages)
    await callback.answer()

async def show_developers_page(message: Message, state: FSMContext, developers: list, page: int, total_pages: int):
    devs_text = f"{hbold('👨‍💻 Список разработчиков')} (Страница {page + 1}/{total_pages})\n\n"
    
    for dev in developers:
        developer_id, username, first_name, last_name, added_by, add_date = dev
        try:
            adder = await bot.get_chat(added_by)
            adder_name = adder.full_name
        except:
            adder_name = "Неизвестно"
        
        devs_text += f"👤 {first_name} {last_name if last_name else ''}\n"
        devs_text += f"📛 @{username if username else 'нет'}\n"
        devs_text += f"🆔 {developer_id}\n"
        devs_text += f"👤 Добавил: {adder_name}\n"
        devs_text += f"📅 Дата: {add_date}\n\n"
    
    keyboard = create_navigation_keyboard(page, total_pages, "developer_developers", "devs_")
    
    await message.edit_text(devs_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("devs_prev_"))
async def devs_prev_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) - 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT developer_id, username, first_name, last_name, added_by, add_date 
        FROM developers 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    developers = cursor.fetchall()
    conn.close()
    
    await state.update_data(devs_page=page)
    await show_developers_page(callback.message, state, developers, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data.startswith("devs_next_"))
async def devs_next_callback(callback: CallbackQuery, state: FSMContext):
    page = int(callback.data.split('_')[2]) + 1
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT developer_id, username, first_name, last_name, added_by, add_date 
        FROM developers 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    developers = cursor.fetchall()
    conn.close()
    
    await state.update_data(devs_page=page)
    await show_developers_page(callback.message, state, developers, page, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "devs_first")
async def devs_first_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT developer_id, username, first_name, last_name, added_by, add_date 
        FROM developers 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, 0))
    developers = cursor.fetchall()
    conn.close()
    
    await state.update_data(devs_page=0)
    await show_developers_page(callback.message, state, developers, 0, total_pages)
    await callback.answer()

@dp.callback_query(F.data == "devs_last")
async def devs_last_callback(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total_pages = data['total_pages']
    page = total_pages - 1
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT developer_id, username, first_name, last_name, added_by, add_date 
        FROM developers 
        ORDER BY add_date DESC 
        LIMIT ? OFFSET ?
    ''', (10, page * 10))
    developers = cursor.fetchall()
    conn.close()
    
    await state.update_data(devs_page=page)
    await show_developers_page(callback.message, state, developers, page, total_pages)
    await callback.answer()

# Developer server callback
@dp.callback_query(F.data == "developer_server")
async def developer_server_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    try:
        # Calculate uptime
        uptime = time.time() - BOT_START_TIME
        uptime_str = str(timedelta(seconds=int(uptime)))
        
        # Get server stats with error handling
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
        except Exception as e:
            cpu_percent = f"Ошибка: {str(e)}"
        
        try:
            memory = psutil.virtual_memory()
            memory_str = f"{memory.percent}% ({memory.used / (1024**3):.2f} / {memory.total / (1024**3):.2f} GB)"
        except Exception as e:
            memory_str = f"Ошибка: {str(e)}"
        
        try:
            disk = psutil.disk_usage('/')
            disk_str = f"{disk.percent}% ({disk.used / (1024**3):.2f} / {disk.total / (1024**3):.2f} GB)"
        except Exception as e:
            disk_str = f"Ошибка: {str(e)}"
        
        server_text = f"""
{hbold('🖥 Информация о сервере')}

⏳ Время работы: {uptime_str}
🧮 CPU: {cpu_percent}
💾 Память: {memory_str}
💿 Диск: {disk_str}
"""
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔄 Перезапустить бота", callback_data="restart_bot"))
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
        
        keyboard.adjust(1)
        
        await callback.message.edit_text(
            server_text,
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('INFO', f"Developer {callback.from_user.id} viewed server stats")
        
    except Exception as e:
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
        await callback.message.edit_text(
            f"❌ Ошибка при получении информации о сервере: {str(e)}",
            reply_markup=keyboard.as_markup(),
            parse_mode=ParseMode.HTML
        )
        log_event('ERROR', f"Developer {callback.from_user.id} failed to view server stats: {str(e)}")
    
    await callback.answer()

# Restart bot callback
@dp.callback_query(F.data == "restart_bot")
async def restart_bot_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("🔄 Бот перезапускается...")
    log_event('WARNING', f"Developer {callback.from_user.id} initiated bot restart")
    
    # Notify all developers
    for dev_id in DEVELOPERS:
        try:
            await bot.send_message(dev_id, f"⚠️ Бот перезапущен разработчиком {callback.from_user.full_name} (@{callback.from_user.username or 'нет'})")
        except:
            pass
    
    # Simulate restart (actual implementation depends on hosting environment)
    os._exit(0)

@dp.errors()
async def error_handler(event: ErrorEvent):
    logger.error(f"Ошибка в обработке обновления {event.update.update_id}: {str(event.exception)}")
    return True

# Main function
async def main():
    try:
        await dp.start_polling(bot)
    except Exception as e:
        log_event('ERROR', f"Bot crashed: {str(e)}")
        await asyncio.sleep(5)
        os._exit(1)

if __name__ == '__main__':
    asyncio.run(main())