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

# Bot setup
bot = Bot(token="8178374718:AAHvyoBH5Ty2VKwNyfdWeOez9XLSflNQtaM")
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
    max_op: int = 3,
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
    
    # Добавляем дополнительные параметры, если они есть
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

async def check_subscription(user_id: int, check_type: int = 1) -> bool:
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id FROM advertise_channels WHERE check_type = ?', (check_type,))
    channels = cursor.fetchall()
    conn.close()
    
    for channel in channels:
        channel_id = channel[0]
        try:
            member = await bot.get_chat_member(channel_id, user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                return False
        except:
            return False
    return True

# Добавим глобальную переменную для хранения времени последней успешной проверки
LAST_SUBSCRIPTION_CHECK = {}

@dp.callback_query(F.data == "subgram_check")
async def subgram_check_callback(callback: CallbackQuery):
    try:
        user_id = callback.from_user.id
        current_time = time.time()
        
        # Проверяем, была ли успешная проверка подписки в последний час
        if user_id in LAST_SUBSCRIPTION_CHECK and (current_time - LAST_SUBSCRIPTION_CHECK[user_id]) < 3600:
            await callback.message.delete()
            return
            
        # Меняем текст кнопки на "Проверяю статус подписки..."
        await callback.message.edit_text("Проверяю статус подписки...")
        
        # Проверяем подписки
        subgram_response = await check_subgram_subscription(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            first_name=callback.from_user.first_name,
            language_code=callback.from_user.language_code,
            premium=callback.from_user.is_premium
        )
        
        if subgram_response.get('status') == 'ok':
            # Сохраняем время успешной проверки
            LAST_SUBSCRIPTION_CHECK[user_id] = current_time
            
            # Удаляем сообщение с каналами
            await callback.message.delete()
            
            # Отправляем временное сообщение об успешной проверке
            msg = await callback.message.answer("✅ Вы успешно прошли проверку подписки!")
            
            # Удаляем сообщение через 3 секунды
            await asyncio.sleep(3)
            await msg.delete()
            
        else:
            # Проверяем, есть ли неподписанные каналы
            unsubscribed = False
            if 'additional' in subgram_response and 'sponsors' in subgram_response['additional']:
                for sponsor in subgram_response['additional']['sponsors']:
                    if sponsor['status'] != 'subscribed':
                        unsubscribed = True
                        break
            
            if not unsubscribed and 'links' not in subgram_response:
                # Если все подписки есть, но API почему-то не вернул статус 'ok'
                LAST_SUBSCRIPTION_CHECK[user_id] = current_time
                await callback.message.delete()
                msg = await callback.message.answer("✅ Вы успешно прошли проверку подписки!")
                await asyncio.sleep(3)
                await msg.delete()
            else:
                # Обновляем сообщение с новыми каналами
                keyboard = InlineKeyboardBuilder()
                keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data="subgram_check"))
                
                channels_text = "📢 Подпишитесь на каналы:\n\n"
                if 'links' in subgram_response:
                    for link in subgram_response['links']:
                        channels_text += f"• {link}\n"
                elif 'additional' in subgram_response and 'sponsors' in subgram_response['additional']:
                    for sponsor in subgram_response['additional']['sponsors']:
                        if sponsor['status'] != 'subscribed':
                            channels_text += f"• {sponsor['link']} - {sponsor['resource_name'] or 'Канал'}\n"
                
                channels_text += "\nСначала нажмите на кнопку 'Я выполнил', затем нажмите на кнопку ниже. "
                
                await callback.message.edit_text(channels_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
                await callback.answer("Пожалуйста, подпишитесь на все каналы")
    except Exception as e:
        log_event('ERROR', f"Error in subgram_check_callback: {str(e)}")
        await callback.answer("❌ Произошла ошибка при проверке подписки")

@dp.callback_query(F.data.startswith("subgram_check_"))
async def subgram_check_with_message_callback(callback: CallbackQuery):
    message_id = callback.data.split('_')[2]
    user_id = callback.from_user.id
    current_time = time.time()
    
    try:
        # Проверяем, была ли успешная проверка подписки в последний час
        if user_id in LAST_SUBSCRIPTION_CHECK and (current_time - LAST_SUBSCRIPTION_CHECK[user_id]) < 3600:
            await callback.message.delete()
            
            # Получаем оригинальное сообщение и обрабатываем его
            try:
                original_message = await bot.get_message(callback.message.chat.id, message_id)
                await original_message.answer(original_message.text)
            except:
                pass
            return
            
        # Меняем текст кнопки на "Проверяю статус подписки..."
        await callback.message.edit_text("Проверяю статус подписки...")
        
        # Проверяем подписки
        subgram_response = await check_subgram_subscription(
            user_id=user_id,
            chat_id=callback.message.chat.id,
            first_name=callback.from_user.first_name,
            language_code=callback.from_user.language_code,
            premium=callback.from_user.is_premium
        )
        
        if subgram_response.get('status') == 'ok':
            # Сохраняем время успешной проверки
            LAST_SUBSCRIPTION_CHECK[user_id] = current_time
            
            # Удаляем сообщение с каналами
            await callback.message.delete()
            
            # Отправляем временное сообщение об успешной проверке
            msg = await callback.message.answer("✅ Вы успешно прошли проверку подписки!")
            
            # Удаляем сообщение через 3 секунды
            await asyncio.sleep(3)
            await msg.delete()
            
            # Продолжаем выполнение команды, которую хотел пользователь
            try:
                original_message = await bot.get_message(callback.message.chat.id, message_id)
                await original_message.answer(original_message.text)
            except:
                pass
        else:
            # Обновляем сообщение с новыми каналами
            keyboard = InlineKeyboardBuilder()
            keyboard.add(InlineKeyboardButton(text="✅ Я подписался", callback_data=f"subgram_check_{message_id}"))
            
            channels_text = "📢 Подпишитесь на каналы:\n\n"
            if 'links' in subgram_response:
                for link in subgram_response['links']:
                    channels_text += f"• {link}\n"
            elif 'additional' in subgram_response and 'sponsors' in subgram_response['additional']:
                for sponsor in subgram_response['additional']['sponsors']:
                    if sponsor['status'] != 'subscribed':
                        channels_text += f"• {sponsor['link']} - {sponsor['resource_name'] or 'Канал'}\n"
            
            channels_text += "\nПосле подписки нажмите кнопку ниже"
            
            await callback.message.edit_text(channels_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
            await callback.answer("Пожалуйста, подпишитесь на все каналы")
    except Exception as e:
        log_event('ERROR', f"Error in subgram_check_with_message_callback: {str(e)}")
        await callback.answer("❌ Произошла ошибка при проверке подписки")

@dp.message(CommandStart())
async def cmd_start(message: Message):
    start_args = message.text.split()
    
    # Проверяем, забанен ли пользователь
    if is_banned(message.from_user.id):
        await message.answer("❌ Вы заблокированы в этом боте.")
        return
    
    # Обновляем статистику пользователя
    user = message.from_user
    update_user_visit(user.id, user.username, user.first_name, user.last_name)
    
    # Если есть аргумент (ссылка), сначала проверяем подписку на дополнительные каналы
    if len(start_args) > 1:
        link_id = start_args[1]
        
        user_id = message.from_user.id
        current_time = time.time()
        
        # Проверяем, была ли успешная проверка подписки в последний час
        if user_id in LAST_SUBSCRIPTION_CHECK and (current_time - LAST_SUBSCRIPTION_CHECK[user_id]) < 3600:
            # Пропускаем проверку подписки
            pass
        elif not await check_subscription(message.from_user.id, 2):
            conn = sqlite3.connect('/root/bot_mirrozz_database.db')
            cursor = conn.cursor()
            cursor.execute('SELECT channel_id, username, title FROM advertise_channels WHERE check_type = 2')
            channels = cursor.fetchall()
            conn.close()
            
            subscribe_text = "📢 Для просмотра содержимого ссылки подпишитесь на каналы:\n\n"
            for channel in channels:
                subscribe_text += f"• {hlink(channel[2], f'https://t.me/{channel[1]}')}\n"
            subscribe_text += "\nПосле подписки нажмите /start " + link_id
            
            await message.answer(subscribe_text, parse_mode=ParseMode.HTML)
            return
        
        # Проверяем подписки через SubGram, если не было успешной проверки в последний час
        if user_id not in LAST_SUBSCRIPTION_CHECK or (current_time - LAST_SUBSCRIPTION_CHECK[user_id]) >= 3600:
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
                
                # Сохраняем message_id для последующего удаления
                msg = await message.answer(channels_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
                
                # Второе сообщение с дополнительными каналами
                await message.answer("📢 Подпишитесь на дополнительные каналы")
                return
    
    # Если подписки проверены или нет аргумента, продолжаем обычную работу
    welcome_text = f"""
👋 Привет, {hbold(user.first_name)}!

Я Mirrozz Scripts — бот, который выдает актуальные скрипты и инжекторы для Roblox по ссылке! 🚀

{hbold('Почему я лучший?')}
• {hbold('Актуальные скрипты')} — база обновляется регулярно!
• {hbold('Мгновенный доступ')} — получай скрипты в пару кликов!
• {hbold('Надежное хранение')} — твои скрипты всегда под рукой!
• {hbold('Стабильная работа')} — бот на мощном сервере, без сбоев!
"""
    if is_admin(user.id):
        welcome_text += f"\n{hbold('👑 Вы администратор бота!')}\nДоступ к админ-панели: /admin"
    if is_developer(user.id):
        welcome_text += f"\n{hbold('💻 Вы разработчик бота!')}\nДоступ к панели разработчика: /admin"
    
    welcome_text += "\n\nНапиши /help, чтобы узнать все команды!"
    
    await message.answer(welcome_text, parse_mode=ParseMode.HTML)
    log_event('INFO', f"User {user.id} started the bot")
    
    # Если есть аргумент, обрабатываем ссылку
    if len(start_args) > 1:
        link_id = start_args[1]
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM links WHERE link_id = ?', (link_id,))
        link = cursor.fetchone()
        
        if link:
            cursor.execute('UPDATE links SET visits = visits + 1 WHERE link_id = ?', (link_id,))
            cursor.execute('UPDATE users SET link_visits = link_visits + 1 WHERE user_id = ?', (message.from_user.id,))
            conn.commit()
            
            content_type = link[1]
            content_text = link[2]
            content_file_id = link[3]
            
            if content_type == 'text':
                await message.answer(content_text)
            elif content_type == 'photo':
                await message.answer_photo(content_file_id, caption=content_text)
            elif content_type == 'document':
                await message.answer_document(content_file_id, caption=content_text)
            
            conn.close()
            log_event('INFO', f"User {message.from_user.id} accessed link {link_id}")

# Help command handler
@dp.message(Command('help'))
async def cmd_help(message: Message):
    help_text = f"""
{hbold('📚 Команды Mirrozz Scripts')}

{hbold('/start')} — Начать работу с ботом
{hbold('/help')} — Показать это сообщение
{hbold('/user_stats')} — Показать вашу статистику
{hbold('/report [сообщение]')} — Отправить жалобу администраторам
"""
    if is_admin(message.from_user.id):
        help_text += f"\n{hbold('👑 Админ-команды')}\n{hbold('/admin')} — Открыть админ-панель"
    
    await message.answer(help_text, parse_mode=ParseMode.HTML)
    log_event('INFO', f"User {message.from_user.id} accessed help")

# User stats command handler
@dp.message(Command('user_stats'))
async def cmd_user_stats(message: Message):
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
                f"⚠️ Новый репорт #{report_id}!\n\nОт: {user.full_name} (@{user.username or 'нет'})\nID: {user.id}\nДата: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\nСообщение: {report_text}",
                reply_markup=keyboard.as_markup()
            )
        except:
            pass
    
    await message.answer("✅ Ваша жалоба отправлена администраторам. Ответ придёт в течение 30 минут.")
    log_event('INFO', f"User {user.id} submitted report #{report_id}")

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
    
    if is_developer(message.from_user.id):
        keyboard.add(InlineKeyboardButton(text="💻 Панель разработчика", callback_data="admin_developer"))
    
    keyboard.adjust(2)
    
    await message.answer(f"{hbold('👑 Админ-панель')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    log_event('INFO', f"Admin {message.from_user.id} accessed admin panel")

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
    
    await callback.message.edit_text(stats_text, parse_mode=ParseMode.HTML)
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
async def list_links_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT link_id, created_by, creation_date, visits FROM links ORDER BY creation_date DESC LIMIT 10')
    links = cursor.fetchall()
    conn.close()
    
    if not links:
        await callback.message.answer("❌ Нет созданных ссылок.")
        await callback.answer()
        return
    
    links_text = f"{hbold('📋 Последние 10 ссылок')}\n\n"
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
    
    await callback.message.answer(links_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed links list")

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

# List admins callback
@dp.callback_query(F.data == "list_admins")
async def list_admins_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT admin_id, username, first_name, last_name, added_by, add_date FROM admins')
    admins = cursor.fetchall()
    conn.close()
    
    if not admins:
        await callback.message.answer("❌ Нет администраторов.")
        await callback.answer()
        return
    
    admins_text = f"{hbold('👑 Список администраторов')}\n\n"
    for admin in admins:
        admin_id, username, first_name, last_name, added_by, add_date = admin
        admins_text += f"👤 {first_name} {last_name if last_name else ''}\n"
        admins_text += f"📛 @{username if username else 'нет'}\n"
        admins_text += f"🆔 {admin_id}\n"
        admins_text += f"📅 Добавлен: {add_date}\n\n"
    
    await callback.message.answer(admins_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed admins list")

# Admin reports callback
@dp.callback_query(F.data == "admin_reports")
async def admin_reports_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="📋 Все репорты", callback_data="all_reports"))
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить репорт", callback_data="delete_report"))
    keyboard.add(InlineKeyboardButton(text="📜 Список репортов", callback_data="report_list"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_back"))
    
    keyboard.adjust(1)
    
    await callback.message.edit_text(f"{hbold('⚠️ Управление репортами')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# All reports callback
@dp.callback_query(F.data == "all_reports")
async def all_reports_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT report_id, user_id, message, report_date FROM reports WHERE status = "open" ORDER BY report_date DESC LIMIT 10')
    reports = cursor.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("❌ Нет открытых репортов.")
        await callback.answer()
        return
    
    reports_text = f"{hbold('⚠️ Последние 10 открытых репортов')}\n\n"
    for report in reports:
        report_id, user_id, message, report_date = report
        
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
        reports_text += f"📝 Сообщение: {message[:50]}...\n\n"
        
        keyboard = InlineKeyboardBuilder()
        keyboard.add(InlineKeyboardButton(text="✉️ Ответить", callback_data=f"answer_report_{report_id}"))
        keyboard.add(InlineKeyboardButton(text="🚫 Забанить", callback_data=f"ban_{user_id}"))
        keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"delete_report_{report_id}"))
        keyboard.adjust(1)
        
        await callback.message.answer(reports_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        reports_text = ""
    
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed reports")

# Answer report callback
@dp.callback_query(F.data.startswith("answer_report_"))
async def answer_report_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    report_id = int(callback.data.split('_')[2])
    await state.update_data(report_id=report_id)
    await callback.message.answer("✉️ Введите ответ на репорт:")
    await state.set_state(Form.answer_report)
    await callback.answer()

# Answer report handler
@dp.message(Form.answer_report)
async def answer_report_handler(message: Message, state: FSMContext):
    data = await state.get_data()
    report_id = data['report_id']
    answer_text = message.text
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM reports WHERE report_id = ?', (report_id,))
    user_id = cursor.fetchone()[0]
    
    cursor.execute(
        'UPDATE reports SET status = "closed", answer = ?, answered_by = ?, answer_date = ? WHERE report_id = ?',
        (answer_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), report_id)
    )
    conn.commit()
    conn.close()
    
    try:
        user = await bot.get_chat(user_id)
        await bot.send_message(
            user_id,
            f"📩 Ответ на ваш репорт #{report_id}:\n\n{answer_text}\n\nАдминистратор: {message.from_user.full_name}"
        )
    except:
        pass
    
    await message.answer(f"✅ Ответ на репорт #{report_id} отправлен.")
    await state.clear()
    log_event('INFO', f"Admin {message.from_user.id} answered report #{report_id}")

# Delete report callback
@dp.callback_query(F.data == "delete_report")
async def delete_report_callback(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT report_id, user_id, message, report_date FROM reports ORDER BY report_date DESC')
    reports = cursor.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("❌ Нет репортов для удаления.")
        await callback.answer()
        return
    
    await state.update_data(reports=reports, report_index=0)
    await show_report_for_deletion(callback.message, state, 0)
    await callback.answer()

async def show_report_for_deletion(message: Message, state: FSMContext, index: int):
    data = await state.get_data()
    reports = data['reports']
    if index < 0 or index >= len(reports):
        await message.answer("❌ Репорты закончились.")
        return
    
    report = reports[index]
    report_id, user_id, message_text, report_date = report
    
    try:
        user = await bot.get_chat(user_id)
        user_name = user.full_name
        username = f"@{user.username}" if user.username else "нет"
    except:
        user_name = "Неизвестно"
        username = "нет"
    
    text = f"""
{hbold('⚠️ Репорт для удаления')}

🆔 ID репорта: {report_id}
👤 От: {user_name} ({username})
📅 Дата: {report_date}
📝 Сообщение: {message_text[:50]}...
"""
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="🗑 Удалить", callback_data=f"confirm_delete_report_{report_id}"))
    if index > 0:
        keyboard.add(InlineKeyboardButton(text="⬅️ Предыдущий", callback_data=f"prev_report_{index}"))
    if index < len(reports) - 1:
        keyboard.add(InlineKeyboardButton(text="➡️ Следующий", callback_data=f"next_report_{index}"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_reports"))
    
    keyboard.adjust(1)
    
    await message.edit_text(text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)

@dp.callback_query(F.data.startswith("prev_report_"))
async def prev_report_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) - 1
    await show_report_for_deletion(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("next_report_"))
async def next_report_callback(callback: CallbackQuery, state: FSMContext):
    index = int(callback.data.split('_')[2]) + 1
    await show_report_for_deletion(callback.message, state, index)
    await callback.answer()

@dp.callback_query(F.data.startswith("confirm_delete_report_"))
async def confirm_delete_report_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    report_id = int(callback.data.split('_')[3])
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('DELETE FROM reports WHERE report_id = ?', (report_id,))
    conn.commit()
    conn.close()
    
    await callback.message.answer(f"✅ Репорт #{report_id} удалён.")
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} deleted report #{report_id}")

# Report list callback
@dp.callback_query(F.data == "report_list")
async def report_list_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT report_id, user_id, message, report_date, status FROM reports ORDER BY report_date DESC LIMIT 10')
    reports = cursor.fetchall()
    conn.close()
    
    if not reports:
        await callback.message.answer("❌ Нет репортов.")
        await callback.answer()
        return
    
    reports_text = f"{hbold('📜 Список репортов')}\n\n"
    for report in reports:
        report_id, user_id, message, report_date, status = report
        
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
        reports_text += f"📝 Сообщение: {message[:50]}...\n"
        reports_text += f"📊 Статус: {status}\n\n"
    
    await callback.message.answer(reports_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed report list")

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
async def banned_users_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id, username, first_name, last_name, ban_reason, banned_by, ban_date FROM users WHERE is_banned = 1')
    banned_users = cursor.fetchall()
    conn.close()
    
    if not banned_users:
        await callback.message.answer("❌ Нет заблокированных пользователей.")
        await callback.answer()
        return
    
    banned_text = f"{hbold('🚫 Заблокированные пользователи')}\n\n"
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
        
        await callback.message.answer(banned_text, reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
        banned_text = ""
    
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed banned users")

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
async def list_advertise_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT channel_id, username, title, added_by, add_date, check_type, subscribers_count FROM advertise_channels')
    channels = cursor.fetchall()
    conn.close()
    
    if not channels:
        await callback.message.answer("❌ Нет добавленных каналов/чатов.")
        await callback.answer()
        return
    
    channels_text = f"{hbold('📢 Список рекламных каналов/чатов')}\n\n"
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
    
    await callback.message.answer(channels_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Admin {callback.from_user.id} viewed advertise channels list")

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

# Developer database callback
@dp.callback_query(F.data == "developer_database")
async def developer_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    keyboard = InlineKeyboardBuilder()
    keyboard.add(InlineKeyboardButton(text="⬇️ Скачать базу", callback_data="download_database"))
    keyboard.add(InlineKeyboardButton(text="🔄 Сбросить базу", callback_data="reset_database"))
    keyboard.add(InlineKeyboardButton(text="📅 Последнее обновление", callback_data="last_database_update"))
    keyboard.add(InlineKeyboardButton(text="📤 Загрузить базу", callback_data="load_database"))
    keyboard.add(InlineKeyboardButton(text="🔙 Назад", callback_data="admin_developer"))
    
    keyboard.adjust(2)
    
    await callback.message.edit_text(f"{hbold('💾 Управление базой данных')}", reply_markup=keyboard.as_markup(), parse_mode=ParseMode.HTML)
    await callback.answer()

# Download database callback
@dp.callback_query(F.data == "download_database")
async def download_database_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    db_file = FSInputFile('bot_mirrozz_database.db', filename='bot_mirrozz_database.db')
    await callback.message.answer_document(db_file, caption="📦 Резервная копия базы данных")
    await callback.answer()
    log_event('INFO', f"Developer {callback.from_user.id} downloaded database")

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

# Developer messages callback
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

# Send system message callback
@dp.callback_query(F.data == "send_system_message")
async def send_system_message_callback(callback: CallbackQuery, state: FSMContext):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    await callback.message.answer("📩 Введите текст системного сообщения для всех пользователей:")
    await state.set_state(Form.system_message)
    await callback.answer()

# Send system message handler
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
    
    recipients_count = 0
    for user in users:
        user_id = user[0]
        try:
            await bot.send_message(user_id, f"📢 Системное сообщение:\n\n{message_text}")
            recipients_count += 1
        except:
            continue
    
    cursor.execute(
        'INSERT INTO system_messages (message_text, sent_by, send_date, recipients_count) VALUES (?, ?, ?, ?)',
        (message_text, message.from_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), recipients_count)
    )
    conn.commit()
    conn.close()
    
    await message.answer(f"✅ Системное сообщение отправлено {recipients_count} пользователям.")
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
        
        await callback.message.edit_text(status_text, parse_mode=ParseMode.HTML)
        log_event('INFO', f"Developer {callback.from_user.id} viewed detailed error stats")
        
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка при получении статистики: {str(e)}")
        log_event('ERROR', f"Failed to get error stats: {str(e)}")
    
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
    
    try:
        # Очистка логов в базе данных
        conn = sqlite3.connect('/root/bot_mirrozz_database.db')
        cursor = conn.cursor()
        cursor.execute('DELETE FROM logs')
        conn.commit()
        conn.close()
        
        # Очистка файла логов
        with open('bot_mirrozz.log', 'w') as f:
            f.write('')
        
        await callback.message.answer("✅ Логи успешно очищены.")
        await callback.answer()
        
    except Exception as e:
        await callback.message.answer(f"❌ Ошибка при очистке логов: {str(e)}")
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
async def list_developers_callback(callback: CallbackQuery):
    if not is_developer(callback.from_user.id):
        await callback.answer("❌ У вас нет доступа.")
        return
    
    conn = sqlite3.connect('/root/bot_mirrozz_database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT developer_id, username, first_name, last_name, added_by, add_date FROM developers')
    developers = cursor.fetchall()
    conn.close()
    
    if not developers:
        await callback.message.answer("❌ Нет разработчиков.")
        await callback.answer()
        return
    
    developers_text = f"{hbold('👨‍💻 Список разработчиков')}\n\n"
    for developer in developers:
        developer_id, username, first_name, last_name, added_by, add_date = developer
        try:
            adder = await bot.get_chat(added_by)
            adder_name = adder.full_name
        except:
            adder_name = "Неизвестно"
        
        developers_text += f"👤 {first_name} {last_name if last_name else ''}\n"
        developers_text += f"📛 @{username if username else 'нет'}\n"
        developers_text += f"🆔 {developer_id}\n"
        developers_text += f"👤 Добавил: {adder_name}\n"
        developers_text += f"📅 Дата: {add_date}\n\n"
    
    await callback.message.answer(developers_text, parse_mode=ParseMode.HTML)
    await callback.answer()
    log_event('INFO', f"Developer {callback.from_user.id} viewed developers list")

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
async def error_handler(update: types.Update, exception: Exception):
    log_event('ERROR', f"Update {update.update_id if update else 'None'} caused error: {str(exception)}")
    if update and update.message:
        await update.message.answer("❌ Произошла ошибка. Пожалуйста, попробуйте позже.")
    elif update and update.callback_query:
        await update.callback_query.answer("❌ Произошла ошибка.")
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