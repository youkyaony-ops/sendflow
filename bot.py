import asyncio
import re
import json
import os
import logging
import time
import random
import shutil
import hashlib
import base64
import string
import sys
import signal
import traceback
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
from functools import wraps
from concurrent.futures import ThreadPoolExecutor

from telethon import TelegramClient, errors
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    FloodWaitError,
    RPCError,
    AuthKeyError,
    UserDeactivatedError,
    PhoneNumberInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    ChatAdminRequiredError,
    UserAlreadyParticipantError,
    ChannelPrivateError,
    ChannelInvalidError,
    PeerFloodError,
    UserPrivacyRestrictedError,
    UsernameNotOccupiedError,
    UsernameInvalidError,
    ChatWriteForbiddenError,
    MessageTooLongError,
    MediaInvalidError,
    FileReferenceExpiredError
)
from telethon.tl.custom import Button
from telethon.tl.types import MessageEntityTextUrl, MessageEntityBold, MessageEntityItalic, MessageEntityCode, MessageEntityPre

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    ConversationHandler,
    PreCheckoutQueryHandler
)
from aiohttp import web

# ==================== НАСТРОЙКА ====================
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
BACKUP_FILE = 'user_data_backup.json'
SESSIONS_DIR = 'telegram_sessions'
MEDIA_DIR = 'media_files'
LOGS_DIR = 'logs'

# Настройки для Render
PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

# Ограничения
MAX_BROADCASTS_PER_USER = 10
MAX_GROUPS_PER_BROADCAST = 50
MIN_INTERVAL = 3
MAX_INTERVAL = 300
DEFAULT_INTERVAL = 30

# Создаём необходимые папки
for folder in [SESSIONS_DIR, MEDIA_DIR, LOGS_DIR]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
user_data = {}
active_tasks = {}
sessions = {}
user_states = {}
broadcast_stats = defaultdict(lambda: {'sent': 0, 'errors': 0, 'last_sent': None, 'start_time': None})
executor = ThreadPoolExecutor(max_workers=4)

# ==================== ДЕКОРАТОРЫ ====================
def log_error(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в {func.__name__}: {e}")
            logger.error(traceback.format_exc())
            return None
    return wrapper

def retry_on_fail(max_retries=3, delay=1):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise
                    await asyncio.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator

# ==================== РАБОТА С ДАННЫМИ ====================
@log_error
def save_data():
    """Сохранение всех данных с резервным копированием"""
    try:
        clean_data = {}
        for uid, data in user_data.items():
            clean_data[str(uid)] = {
                'broadcasts': data.get('broadcasts', []),
                'groups': data.get('groups', []),
                'settings': data.get('settings', {'notify': True, 'autosave': True, 'def_interval': 30}),
                'sessions': data.get('sessions', {}),
                'created_at': data.get('created_at', str(datetime.now())),
                'total_sent': data.get('total_sent', 0),
                'total_errors': data.get('total_errors', 0),
                'last_activity': data.get('last_activity', str(datetime.now()))
            }
        
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        
        with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        
        print(f"[SAVE] Данные сохранены для {len(user_data)} пользователей")
        return True
    except Exception as e:
        print(f"Save error: {e}")
        return False

@log_error
def load_data():
    """Загрузка всех данных"""
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
                print(f"[LOAD] Загружены данные для {len(user_data)} пользователей")
        elif os.path.exists(BACKUP_FILE):
            with open(BACKUP_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
                print(f"[LOAD] Загружены данные из бэкапа для {len(user_data)} пользователей")
        else:
            user_data = {}
            print("[LOAD] Создан новый файл данных")
        return True
    except Exception as e:
        print(f"Load error: {e}")
        user_data = {}
        return False

@log_error
def save_user(uid):
    """Сохранение пользователя с проверкой"""
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'settings': {'notify': True, 'autosave': True, 'def_interval': 30},
            'sessions': {},
            'created_at': str(datetime.now()),
            'total_sent': 0,
            'total_errors': 0,
            'last_activity': str(datetime.now())
        }
        save_data()
        print(f"[USER] Новый пользователь {uid} создан")
    return user_data[uid]

# ==================== РАБОТА С СЕССИЯМИ ====================
def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f'session_{user_id}.session')

def get_media_path(user_id, broadcast_id):
    return os.path.join(MEDIA_DIR, f'user_{user_id}_bc_{broadcast_id}.jpg')

@log_error
def has_valid_session(user_id):
    """Проверка наличия валидной сессии"""
    if user_id not in user_data:
        return False
    
    session_info = user_data[user_id].get('sessions', {})
    if not session_info.get('is_authorized'):
        return False
    
    session_file = get_session_path(user_id)
    if not os.path.exists(session_file):
        return False
    
    last_used = session_info.get('last_used')
    if last_used:
        try:
            last_used_date = datetime.fromisoformat(last_used)
            if datetime.now() - last_used_date > timedelta(days=30):
                return False
        except:
            pass
    
    return True

@log_error
async def get_client(user_id):
    """Получение клиента с автоматическим восстановлением сессии"""
    if user_id in sessions:
        try:
            await sessions[user_id].get_me()
            return sessions[user_id]
        except:
            try:
                await sessions[user_id].disconnect()
            except:
                pass
            del sessions[user_id]
    
    session_file = get_session_path(user_id)
    client = TelegramClient(session_file, API_ID, API_HASH)
    
    try:
        await client.connect()
        if await client.is_user_authorized():
            sessions[user_id] = client
            print(f"[CLIENT] Клиент загружен для {user_id}")
            return client
        else:
            await client.disconnect()
            return None
    except Exception as e:
        print(f"[CLIENT] Ошибка загрузки клиента для {user_id}: {e}")
        return None

@log_error
async def save_session_info(user_id, phone):
    """Сохранение информации о сессии"""
    if user_id not in user_data:
        save_user(user_id)
    
    if 'sessions' not in user_data[user_id]:
        user_data[user_id]['sessions'] = {}
    
    user_data[user_id]['sessions'] = {
        'phone': phone,
        'is_authorized': True,
        'last_used': str(datetime.now()),
        'session_file': get_session_path(user_id)
    }
    save_data()
    print(f"[SESSION] Информация о сессии сохранена для {user_id}")

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 МОИ ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data='my_stats')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'edit_text_{bid}'), InlineKeyboardButton("📷 ФОТО", callback_data=f'edit_photo_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'edit_groups_{bid}'), InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'edit_interval_{bid}')],
        [InlineKeyboardButton("🎲 РАНДОМ", callback_data=f'edit_random_{bid}'), InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data=f'toggle_loop_{bid}')],
        [InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data=f'edit_schedule_{bid}'), InlineKeyboardButton("📋 ПРЕВЬЮ", callback_data=f'preview_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data=f'start_247_{bid}'), InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data=f'send_once_{bid}')],
        [InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_broadcast_{bid}'), InlineKeyboardButton("📊 СТАТУС", callback_data=f'bc_status_{bid}')],
        [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data=f'clone_broadcast_{bid}'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_broadcast_{bid}')],
        [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data='list_groups')],
    [InlineKeyboardButton("🔄 ПРОВЕРИТЬ ГРУППЫ", callback_data='check_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data='remove_group')],
    [InlineKeyboardButton("📤 ЭКСПОРТ ГРУПП", callback_data='export_groups')],
    [InlineKeyboardButton("📥 ИМПОРТ ГРУПП", callback_data='import_groups')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data='toggle_notify')],
    [InlineKeyboardButton("💾 АВТОСОХРАНЕНИЕ", callback_data='toggle_autosave')],
    [InlineKeyboardButton("⏱ ИНТЕРВАЛ ПО УМОЛЧ.", callback_data='def_interval')],
    [InlineKeyboardButton("🗑 ОЧИСТИТЬ СЕССИЮ", callback_data='clear_session')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 БЫСТРЫЙ СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔄 РЕЖИМЫ РАБОТЫ", callback_data='help_modes')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("💡 СОВЕТЫ", callback_data='help_tips')],
    [InlineKeyboardButton("❓ FAQ", callback_data='help_faq')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])
BACK_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
@log_error
async def send_safe(chat_id, bot, text, keyboard=None):
    """Безопасная отправка сообщений с повтором при ошибке"""
    for attempt in range(3):
        try:
            if keyboard:
                await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='HTML')
            else:
                await bot.send_message(chat_id, text, parse_mode='HTML')
            return True
        except Exception as e:
            if attempt == 2:
                print(f"Send error: {e}")
                return False
            await asyncio.sleep(1)
    return False

@log_error
async def main_menu(chat_id, bot, text=None):
    """Показать главное меню"""
    msg = text if text else "🥓 <b>SendFlow Pro</b>\n\nВыберите действие:"
    await send_safe(chat_id, bot, msg, MAIN_MENU)

@log_error
async def show_broadcast_menu(uid, bot, bid):
    """Показать меню рассылки"""
    if uid not in user_data:
        save_user(uid)
    
    broadcasts = user_data[uid].get('broadcasts', [])
    if bid >= len(broadcasts):
        await send_safe(uid, bot, "❌ Рассылка не найдена", MAIN_MENU)
        return
    
    bc = broadcasts[bid]
    task_key = f"{uid}_{bid}"
    is_running = task_key in active_tasks and not active_tasks[task_key].done()
    has_photo = os.path.exists(get_media_path(uid, bid))
    
    status = "🟢 АКТИВНА" if is_running else "🔴 ОСТАНОВЛЕНА"
    txt = f"📢 <b>{bc.get('name', f'Рассылка {bid+1}')}</b>\n\n"
    txt += f"Статус: {status}\n"
    txt += f"📝 Текст: {'✅' if bc.get('text') else '❌'}\n"
    if bc.get('text'):
        preview = bc['text'][:50] + '...' if len(bc['text']) > 50 else bc['text']
        txt += f"   → {preview}\n"
    txt += f"📷 Фото: {'✅' if has_photo else '❌'}\n"
    txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
    if bc.get('groups'):
        txt += f"   → {', '.join(bc['groups'][:3])}\n"
    txt += f"⏱ Интервал: {bc.get('interval', 30)} сек\n"
    if bc.get('random_min') and bc.get('random_max'):
        txt += f"🎲 Рандом: {bc['random_min']}-{bc['random_max']} сек\n"
    txt += f"🔄 Зациклено: {'✅' if bc.get('loop', True) else '❌'}\n"
    if bc.get('schedule'):
        txt += f"📅 Расписание: {bc['schedule']}\n"
    txt += f"📨 Отправлено: {bc.get('sent', 0)}\n"
    txt += f"❌ Ошибок: {bc.get('errors', 0)}"
    
    await send_safe(uid, bot, txt, get_broadcast_actions(bid))

@log_error
def validate_group_link(link):
    """Валидация ссылки на группу"""
    link = link.strip()
    patterns = [
        r'^@[a-zA-Z0-9_]{5,32}$',
        r'^https?://t\.me/[a-zA-Z0-9_]{5,32}$',
        r'^t\.me/[a-zA-Z0-9_]{5,32}$'
    ]
    for pattern in patterns:
        if re.match(pattern, link):
            if not link.startswith('@'):
                link = re.sub(r'https?://t\.me/', '@', link)
                link = re.sub(r't\.me/', '@', link)
            return True, link
    return False, link

@log_error
def validate_phone(phone):
    """Валидация номера телефона"""
    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    if re.match(r'^\+[0-9]{10,15}$', phone):
        return True, phone
    return False, "Неверный формат"

@log_error
def validate_interval(interval):
    """Валидация интервала"""
    try:
        interval = int(interval)
        if MIN_INTERVAL <= interval <= MAX_INTERVAL:
            return True, interval
        return False, f"От {MIN_INTERVAL} до {MAX_INTERVAL}"
    except ValueError:
        return False, "Введите число"

@log_error
def validate_time(time_str):
    """Валидация времени"""
    match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return True, f"{hour:02d}:{minute:02d}"
    return False, None

# ==================== КОМАНДЫ ====================
@log_error
async def start_cmd(update: Update, context):
    """Обработчик /start"""
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    
    welcome = f"👋 Привет, {update.effective_user.first_name}!\n\n"
    if has_valid_session(uid):
        welcome += "✅ У вас есть сохранённая сессия Telegram\n🔄 Рассылки будут работать без повторной авторизации"
    else:
        welcome += "⚠️ Для запуска рассылок потребуется авторизация в Telegram (один раз)"
    
    await main_menu(uid, context.bot, welcome)

@log_error
async def skip_cmd(update: Update, context):
    """Обработчик /skip для пропуска 2FA"""
    uid = update.effective_user.id
    if user_states.get(uid, {}).get('step') == 'waiting_2fa':
        update.message.text = '/skip'
        await message_handler(update, context)

# ==================== КНОПКИ ====================
@log_error
async def button_handler(update: Update, context):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    
    try:
        await query.message.delete()
    except:
        pass
    
    # ===== ГЛАВНОЕ МЕНЮ =====
    if data == 'back_to_main':
        await main_menu(uid, context.bot)
    
    elif data == 'my_broadcasts':
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        
        if not broadcasts:
            await send_safe(uid, context.bot, "📢 У вас нет рассылок\n\n➕ Создайте новую через кнопку в меню", MAIN_MENU)
            return
        
        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            task_key = f"{uid}_{i}"
            is_running = task_key in active_tasks and not active_tasks[task_key].done()
            status = "🟢" if is_running else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'select_bc_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        
        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif data == 'new_broadcast':
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        
        if len(broadcasts) >= MAX_BROADCASTS_PER_USER:
            await send_safe(uid, context.bot, f"❌ Максимум {MAX_BROADCASTS_PER_USER} рассылок\nУдалите ненужные", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        new_broadcast = {
            'name': f'Рассылка {new_id+1}',
            'text': None,
            'groups': [],
            'interval': user_data[uid].get('settings', {}).get('def_interval', DEFAULT_INTERVAL),
            'active': False,
            'loop': True,
            'random_min': 0,
            'random_max': 0,
            'sent': 0,
            'errors': 0,
            'schedule': None,
            'created_at': str(datetime.now())
        }
        user_data[uid]['broadcasts'].append(new_broadcast)
        save_data()
        print(f"[NEW] Создана новая рассылка #{new_id+1} для {uid}")
        await show_broadcast_menu(uid, context.bot, new_id)
    
    elif data == 'my_groups':
        if uid not in user_data:
            save_user(uid)
        groups = user_data[uid].get('groups', [])
        
        if not groups:
            await send_safe(uid, context.bot, "📁 У вас нет сохранённых групп\n\n➕ Добавьте первую через кнопку ниже", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n"
            for i, g in enumerate(groups[:20], 1):
                txt += f"{i}. {g}\n"
            if len(groups) > 20:
                txt += f"\n... и ещё {len(groups)-20} групп"
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'my_stats':
        if uid not in user_data:
            save_user(uid)
        data_u = user_data[uid]
        bc = data_u.get('broadcasts', [])
        active = 0
        for i, b in enumerate(bc):
            task_key = f"{uid}_{i}"
            if task_key in active_tasks and not active_tasks[task_key].done():
                active += 1
        total_sent = data_u.get('total_sent', 0)
        total_errors = data_u.get('total_errors', 0)
        
        txt = f"📊 <b>ВАША СТАТИСТИКА</b>\n\n"
        txt += f"📢 Рассылок: {len(bc)} (🟢 {active} активных)\n"
        txt += f"📨 Отправлено: {total_sent}\n"
        txt += f"❌ Ошибок: {total_errors}\n"
        txt += f"📈 Успешность: {((total_sent - total_errors) / max(1, total_sent) * 100):.1f}%\n"
        txt += f"📁 Сохранено групп: {len(data_u.get('groups', []))}\n"
        txt += f"🔐 Сессия: {'✅ Сохранена' if has_valid_session(uid) else '❌ Не сохранена'}\n"
        txt += f"📅 Регистрация: {data_u.get('created_at', 'Неизвестно')[:10]}"
        await send_safe(uid, context.bot, txt, MAIN_MENU)
    
    elif data == 'settings':
        if uid not in user_data:
            save_user(uid)
        s = user_data[uid].get('settings', {})
        txt = f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        txt += f"🔔 Уведомления: {'✅ Вкл' if s.get('notify', True) else '❌ Выкл'}\n"
        txt += f"💾 Автосохранение: {'✅ Вкл' if s.get('autosave', True) else '❌ Выкл'}\n"
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', DEFAULT_INTERVAL)} сек\n"
        txt += f"🔐 Сессия: {'✅ Сохранена' if has_valid_session(uid) else '❌ Не сохранена'}"
        await send_safe(uid, context.bot, txt, SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    # ===== ПОМОЩЬ =====
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой текст и группы\n3️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n4️⃣ Введи номер телефона (один раз)\n5️⃣ Введи код из Telegram\n\n✅ Готово! Рассылка работает 24/7\n\n💡 Подпись к фото становится текстом сообщения!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>ТЕКСТ:</b>\n1. Нажми '📝 ТЕКСТ'\n2. Отправь текстовое сообщение\n\n<b>ФОТО:</b>\n1. Нажми '📷 ФОТО'\n2. Отправь фото (можно с подписью)\n\n<b>ГРУППЫ:</b>\n1. Нажми '👥 ГРУППЫ'\n2. Введи @group1, @group2\n\n<b>ИНТЕРВАЛ:</b>\nВремя между сообщениями (5-300 сек)"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_modes':
        txt = "🔄 <b>РЕЖИМЫ РАБОТЫ</b>\n\n<b>🚀 ЗАПУСТИТЬ 24/7:</b>\nБесконечная отправка по кругу\nГруппа1 → Группа2 → ... → ГруппаN → Группа1\n\n<b>▶️ ОТПРАВИТЬ РАЗОМ:</b>\nОдно сообщение во все группы, после чего остановка\n\n<b>🎲 РАНДОМ:</b>\nСлучайная задержка между сообщениями\n\n<b>📅 РАСПИСАНИЕ:</b>\nАвтоматический запуск в указанное время"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345\n<b>Фото не сохраняется:</b> фото сохраняется на диск, проблем быть не должно!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_tips':
        txt = "💡 <b>СОВЕТЫ</b>\n\n• Если фото не отправляется - проверь папку media_files\n• Сессия сохраняется на 30 дней\n• Для красивых сообщений используй подпись к фото\n• Можно запустить несколько рассылок одновременно\n• Все данные автоматически сохраняются"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_faq':
        txt = "❓ <b>FAQ</b>\n\n<b>Вопрос:</b> Нужно ли каждый раз вводить код?\n<b>Ответ:</b> Нет, сессия сохраняется\n\n<b>Вопрос:</b> Сколько можно создать рассылок?\n<b>Ответ:</b> До 10 рассылок\n\n<b>Вопрос:</b> Фото с подписью работает?\n<b>Ответ:</b> Да, подпись становится текстом сообщения\n\n<b>Вопрос:</b> Бот падает после перезапуска?\n<b>Ответ:</b> Нет, все данные сохраняются"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    # ===== ВЫБОР РАССЫЛКИ =====
    elif data.startswith('select_bc_'):
        bid = int(data.split('_')[2])
        await show_broadcast_menu(uid, context.bot, bid)
    
    # ===== ДЕЙСТВИЯ С РАССЫЛКОЙ =====
    elif data.startswith('edit_text_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_text', 'bid': bid}
        await send_safe(uid, context.bot, "📝 Введите текст рассылки (можно с эмодзи):", CANCEL_BTN)
    
    elif data.startswith('edit_photo_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_photo', 'bid': bid}
        await send_safe(uid, context.bot, "📷 Отправьте ФОТО для рассылки\n\nПодпись к фото станет текстом сообщения", CANCEL_BTN)
    
    elif data.startswith('edit_groups_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        
        saved_groups = user_data[uid].get('groups', [])
        if saved_groups:
            kb = []
            for g in saved_groups[:10]:
                kb.append([InlineKeyboardButton(f"📌 {g}", callback_data=f'select_saved_group_{bid}_{g}')])
            kb.append([InlineKeyboardButton("✏️ ВВЕСТИ ВРУЧНУЮ", callback_data=f'manual_groups_{bid}')])
            kb.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')])
            await context.bot.send_message(uid, "👥 <b>ВЫБЕРИТЕ ГРУППЫ</b>\n\nМожно выбрать из сохранённых или ввести вручную:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2", CANCEL_BTN)
    
    elif data.startswith('manual_groups_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2", CANCEL_BTN)
    
    elif data.startswith('select_saved_group_'):
        parts = data.split('_')
        bid = int(parts[3])
        group = '_'.join(parts[4:])
        groups = user_data[uid]['broadcasts'][bid].get('groups', [])
        if group not in groups:
            groups.append(group)
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Добавлена группа: {group}\n\nВсего групп: {len(groups)}")
        else:
            await send_safe(uid, context.bot, f"⚠️ Группа {group} уже есть")
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('edit_interval_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_interval', 'bid': bid}
        await send_safe(uid, context.bot, f"⏱ Введите интервал ({MIN_INTERVAL}-{MAX_INTERVAL} секунд):", CANCEL_BTN)
    
    elif data.startswith('edit_random_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_random', 'bid': bid}
        await send_safe(uid, context.bot, "🎲 Введите диапазон случайной задержки:\n\nФормат: мин-макс\nПример: 10-30\n0 - отключить", CANCEL_BTN)
    
    elif data.startswith('toggle_loop_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        bc['loop'] = not bc.get('loop', True)
        save_data()
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('edit_schedule_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_schedule', 'bid': bid}
        await send_safe(uid, context.bot, "📅 Введите время для автоматического запуска:\n\nФормат: ЧЧ:ММ\nПример: 14:30\noff - отключить", CANCEL_BTN)
    
    elif data.startswith('preview_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_safe(uid, context.bot, "❌ Нет текста или фото для предпросмотра")
            return
        
        preview_text = "📋 <b>ПРЕДПРОСМОТР</b>\n\n"
        if bc.get('text'):
            preview_text += f"📝 <b>Текст:</b>\n{bc['text'][:300]}\n\n"
        if has_photo:
            preview_text += "📷 <b>Фото:</b> есть\n"
        
        await send_safe(uid, context.bot, preview_text)
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('start_247_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!", BACK_BTN)
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BACK_BTN)
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            await send_safe(uid, context.bot, f"⚠️ Рассылка #{bid+1} уже запущена!", BACK_BTN)
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await send_safe(uid, context.bot, "✅ Использую сохранённую сессию")
            await start_broadcast_with_client(uid, context.bot, bid, client, is_247=True)
            return
        
        user_states[uid] = {'step': 'start_247', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится на 30 дней)", CANCEL_BTN)
    
    elif data.startswith('send_once_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!", BACK_BTN)
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BACK_BTN)
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await send_safe(uid, context.bot, "✅ Использую сохранённую сессию")
            await start_broadcast_with_client(uid, context.bot, bid, client, is_247=False)
            return
        
        user_states[uid] = {'step': 'send_once', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится на 30 дней)", CANCEL_BTN)
    
    elif data.startswith('stop_broadcast_'):
        bid = int(data.split('_')[2])
        task_key = f"{uid}_{bid}"
        
        if task_key in active_tasks and not active_tasks[task_key].done():
            task = active_tasks[task_key]
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                print(f"Ошибка отмены: {e}")
            finally:
                if task_key in active_tasks:
                    del active_tasks[task_key]
                
                if uid in sessions:
                    other_tasks = [k for k in active_tasks.keys() if k.startswith(f"{uid}_")]
                    if not other_tasks:
                        try:
                            await sessions[uid].disconnect()
                        except:
                            pass
                        if uid in sessions:
                            del sessions[uid]
                
                if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                    user_data[uid]['broadcasts'][bid]['active'] = False
                    save_data()
            
            await send_safe(uid, context.bot, f"🛑 Рассылка #{bid+1} ОСТАНОВЛЕНА")
        else:
            await send_safe(uid, context.bot, f"❌ Рассылка #{bid+1} не активна")
        
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('bc_status_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        task_key = f"{uid}_{bid}"
        is_running = task_key in active_tasks and not active_tasks[task_key].done()
        status = "🟢 РАБОТАЕТ" if is_running else "🔴 ОСТАНОВЛЕНА"
        txt = f"📊 <b>СТАТУС РАССЫЛКИ #{bid+1}</b>\n\n"
        txt += f"Имя: {bc.get('name', f'Рассылка {bid+1}')}\n"
        txt += f"Статус: {status}\n"
        txt += f"Отправлено: {bc.get('sent', 0)}\n"
        txt += f"Ошибок: {bc.get('errors', 0)}\n"
        txt += f"Групп: {len(bc.get('groups', []))}"
        await send_safe(uid, context.bot, txt)
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('clone_broadcast_'):
        bid = int(data.split('_')[2])
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= MAX_BROADCASTS_PER_USER:
            await send_safe(uid, context.bot, f"❌ Достигнут лимит рассылок ({MAX_BROADCASTS_PER_USER})", MAIN_MENU)
            return
        
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original.get('name', f'Рассылка {bid+1}')}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', DEFAULT_INTERVAL),
            'active': False,
            'loop': original.get('loop', True),
            'random_min': original.get('random_min', 0),
            'random_max': original.get('random_max', 0),
            'sent': 0,
            'errors': 0,
            'schedule': original.get('schedule'),
            'created_at': str(datetime.now())
        }
        user_data[uid]['broadcasts'].append(new_bc)
        save_data()
        
        # Копируем фото если есть
        old_media = get_media_path(uid, bid)
        new_media = get_media_path(uid, len(broadcasts))
        if os.path.exists(old_media):
            shutil.copy(old_media, new_media)
        
        await send_safe(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)
    
    elif data.startswith('delete_broadcast_'):
        bid = int(data.split('_')[2])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            active_tasks[task_key].cancel()
            if task_key in active_tasks:
                del active_tasks[task_key]
        
        # Удаляем медиа файл
        media_path = get_media_path(uid, bid)
        if os.path.exists(media_path):
            os.remove(media_path)
        
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, f"🗑 Рассылка #{bid+1} удалена", MAIN_MENU)
    
    # ===== ГРУППЫ =====
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_safe(uid, context.bot, "➕ Введите ссылку на группу:\n\nПример: @group_name или https://t.me/group", CANCEL_BTN)
    
    elif data == 'list_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет сохранённых групп", GROUPS_MENU)
        else:
            txt = "📋 <b>ВСЕ ГРУППЫ</b>\n\n" + "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'check_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет групп для проверки", GROUPS_MENU)
            return
        
        await send_safe(uid, context.bot, f"🔄 Проверяю {len(groups)} групп...")
        
        client = await get_client(uid)
        if not client:
            await send_safe(uid, context.bot, "❌ Нет активной сессии Telegram\nСначала запустите любую рассылку для авторизации", GROUPS_MENU)
            return
        
        valid = []
        invalid = []
        
        for group in groups:
            try:
                await client.get_entity(group)
                valid.append(group)
            except:
                invalid.append(group)
        
        txt = f"✅ Доступно: {len(valid)}\n❌ Недоступно: {len(invalid)}"
        if invalid:
            txt += f"\n\nНедоступные группы:\n" + "\n".join(invalid[:10])
        await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'remove_group':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет групп для удаления", GROUPS_MENU)
            return
        
        kb = []
        for i, g in enumerate(groups[:20]):
            kb.append([InlineKeyboardButton(f"❌ {g}", callback_data=f'del_group_{i}')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='my_groups')])
        
        await context.bot.send_message(uid, "🗑 <b>ВЫБЕРИТЕ ГРУППУ ДЛЯ УДАЛЕНИЯ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif data.startswith('del_group_'):
        idx = int(data.split('_')[2])
        groups = user_data[uid].get('groups', [])
        if idx < len(groups):
            removed = groups.pop(idx)
            user_data[uid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Удалена группа: {removed}", GROUPS_MENU)
    
    elif data == 'export_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет групп для экспорта", GROUPS_MENU)
            return
        
        export_file = f"groups_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(export_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(groups))
        
        with open(export_file, 'rb') as f:
            await context.bot.send_document(uid, InputFile(f), caption="📁 Экспорт групп")
        
        os.remove(export_file)
    
    elif data == 'import_groups':
        user_states[uid] = {'step': 'import_groups'}
        await send_safe(uid, context.bot, "📥 Отправьте текстовый файл со списком групп (по одной на строку)", CANCEL_BTN)
    
    # ===== НАСТРОЙКИ =====
    elif data == 'toggle_notify':
        s = user_data[uid].get('settings', {})
        s['notify'] = not s.get('notify', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"🔔 Уведомления: {'ВКЛЮЧЕНЫ' if s['notify'] else 'ВЫКЛЮЧЕНЫ'}", SETTINGS_MENU)
    
    elif data == 'toggle_autosave':
        s = user_data[uid].get('settings', {})
        s['autosave'] = not s.get('autosave', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"💾 Автосохранение: {'ВКЛЮЧЕНО' if s['autosave'] else 'ВЫКЛЮЧЕНО'}", SETTINGS_MENU)
    
    elif data == 'def_interval':
        user_states[uid] = {'step': 'def_interval'}
        await send_safe(uid, context.bot, f"⏱ Введите интервал по умолчанию ({MIN_INTERVAL}-{MAX_INTERVAL} сек):", CANCEL_BTN)
    
    elif data == 'clear_session':
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        
        session_file = get_session_path(uid)
        if os.path.exists(session_file):
            os.remove(session_file)
        
        if uid in user_data and 'sessions' in user_data[uid]:
            user_data[uid]['sessions'] = {}
            save_data()
        
        await send_safe(uid, context.bot, "🗑 Сессия очищена\nПри следующем запуске потребуется авторизация", SETTINGS_MENU)
    
    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Действие отменено")

# ==================== ЗАПУСК РАССЫЛКИ С КЛИЕНТОМ ====================
@log_error
async def start_broadcast_with_client(uid, bot, bid, client, is_247=True):
    """Запуск рассылки с существующим клиентом"""
    if uid not in user_data:
        save_user(uid)
    
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    text = bc.get('text', '')
    interval = bc.get('interval', DEFAULT_INTERVAL)
    random_min = bc.get('random_min', 0)
    random_max = bc.get('random_max', 0)
    media_path = get_media_path(uid, bid)
    has_photo = os.path.exists(media_path)
    
    # Проверка групп
    valid_groups = []
    for group in groups:
        try:
            await client.get_entity(group)
            valid_groups.append(group)
        except Exception as e:
            await send_safe(uid, bot, f"⚠️ {group} - недоступна: {str(e)[:50]}")
    
    if not valid_groups:
        await send_safe(uid, bot, "❌ Нет доступных групп!\nПроверьте что бот добавлен в группы", MAIN_MENU)
        return
    
    bc['groups'] = valid_groups
    bc['active'] = True
    save_data()
    
    media_info = " 📷" if has_photo else ""
    
    if is_247:
        await send_safe(uid, bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}", MAIN_MENU)
        
        task_key = f"{uid}_{bid}"
        if task_key not in active_tasks or active_tasks[task_key].done():
            task = asyncio.create_task(run_broadcast_247(uid, bid, client, valid_groups, text, interval, random_min, random_max, media_path, has_photo))
            active_tasks[task_key] = task
            print(f"[START] Запущена рассылка #{bid+1} для пользователя {uid}")
        else:
            await send_safe(uid, bot, f"⚠️ Рассылка #{bid+1} уже запущена!", MAIN_MENU)
    else:
        await send_safe(uid, bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
        success = 0
        for group in valid_groups:
            try:
                if has_photo and os.path.exists(media_path):
                    await client.send_file(group, media_path, caption=text)
                else:
                    await client.send_message(group, text)
                success += 1
                await asyncio.sleep(2)
            except Exception as e:
                await send_safe(uid, bot, f"❌ {group}: {str(e)[:50]}")
        await send_safe(uid, bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)

# ==================== БЕСКОНЕЧНАЯ РАССЫЛКА 24/7 ====================
@log_error
async def run_broadcast_247(uid, bid, client, groups, text, interval, random_min, random_max, media_path, has_photo):
    """Бесконечная рассылка 24/7"""
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    print(f"[START] Бесконечная рассылка #{bid+1} для {uid} запущена")
    
    try:
        while True:
            # Проверка на остановку через флаг
            if not user_data[uid]['broadcasts'][bid].get('active', True):
                print(f"[STOP] Рассылка #{bid+1} для {uid} остановлена по флагу")
                break
            
            for group in groups:
                # Проверка на отмену задачи
                task = asyncio.current_task()
                if task and task.cancelled():
                    print(f"[CANCEL] Рассылка #{bid+1} для {uid} отменена")
                    break
                
                if not user_data[uid]['broadcasts'][bid].get('active', True):
                    break
                
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=text)
                    else:
                        await client.send_message(group, text)
                    
                    sent += 1
                    
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['sent'] = sent
                        user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                        save_data()
                    
                    print(f"[SEND] {uid} -> рассылка #{bid+1} -> {group} (всего: {sent})")
                except FloodWaitError as e:
                    print(f"[FLOOD] {uid} ждёт {e.seconds} сек")
                    await asyncio.sleep(e.seconds)
                except asyncio.CancelledError:
                    print(f"[CANCEL] Рассылка #{bid+1} для {uid} отменена во время отправки")
                    break
                except Exception as e:
                    print(f"[ERROR] {uid} рассылка #{bid+1}: {e}")
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['errors'] = user_data[uid]['broadcasts'][bid].get('errors', 0) + 1
                        user_data[uid]['total_errors'] = user_data[uid].get('total_errors', 0) + 1
                        save_data()
                
                delay = interval
                if random_min and random_max:
                    delay = random.randint(random_min, random_max)
                await asyncio.sleep(delay)
            
            if not user_data[uid]['broadcasts'][bid].get('active', True):
                break
                
    except asyncio.CancelledError:
        print(f"[CANCEL] Бесконечная рассылка #{bid+1} для {uid} отменена. Отправлено: {sent}")
    finally:
        if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
        print(f"[STOP] Бесконечная рассылка #{bid+1} для {uid} полностью остановлена. Отправлено: {sent}")

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
@log_error
async def message_handler(update: Update, context):
    """Обработка текстовых сообщений"""
    uid = update.effective_user.id
    save_user(uid)
    
    step_data = user_states.get(uid, {})
    step = step_data.get('step')
    
    if not step:
        await main_menu(uid, context.bot)
        return
    
    # НАСТРОЙКА ИНТЕРВАЛА ПО УМОЛЧАНИЮ
    if step == 'def_interval':
        if not update.message.text:
            return
        text = update.message.text.strip()
        valid, result = validate_interval(text)
        if valid:
            user_data[uid]['settings']['def_interval'] = result
            save_data()
            await send_safe(uid, context.bot, f"✅ Интервал по умолчанию: {result} сек", SETTINGS_MENU)
        else:
            await send_safe(uid, context.bot, f"❌ {result}", CANCEL_BTN)
            return
        del user_states[uid]
    
    # ИМПОРТ ГРУПП
    elif step == 'import_groups':
        if update.message.document:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            new_groups = content.decode('utf-8').strip().split('\n')
            groups = user_data[uid].get('groups', [])
            added = 0
            for g in new_groups:
                g = g.strip()
                if g and g not in groups:
                    valid, group = validate_group_link(g)
                    if valid:
                        groups.append(group)
                        added += 1
            user_data[uid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Импортировано {added} групп\nВсего групп: {len(groups)}", GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, "❌ Отправьте текстовый файл", CANCEL_BTN)
        del user_states[uid]
    
    # ДОБАВЛЕНИЕ ГРУППЫ
    elif step == 'add_group':
        if not update.message.text:
            return
        text = update.message.text.strip()
        valid, group = validate_group_link(text)
        if valid:
            groups = user_data[uid].get('groups', [])
            if group not in groups:
                groups.append(group)
                user_data[uid]['groups'] = groups
                save_data()
                await send_safe(uid, context.bot, f"✅ Группа {group} добавлена!", GROUPS_MENU)
            else:
                await send_safe(uid, context.bot, f"⚠️ Группа {group} уже есть", GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, "❌ Неверный формат группы\nПример: @group_name", CANCEL_BTN)
            return
        del user_states[uid]
    
    # РЕДАКТИРОВАНИЕ ТЕКСТА
    elif step == 'edit_text':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        
        if len(text) > 4096:
            await send_safe(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        
        if bid >= len(user_data[uid].get('broadcasts', [])):
            await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
            del user_states[uid]
            return
        
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_safe(uid, context.bot, "✅ Текст сохранён!")
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ФОТО (СОХРАНЕНИЕ НА ДИСК)
    elif step == 'edit_photo':
        bid = step_data['bid']
        
        if update.message.photo:
            photo = update.message.photo[-1]
            media_path = get_media_path(uid, bid)
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(media_path)
            
            # Сохраняем подпись как текст
            if update.message.caption:
                user_data[uid]['broadcasts'][bid]['text'] = update.message.caption.strip()
                save_data()
            
            await send_safe(uid, context.bot, "✅ Фото сохранено!\n\nПодпись к фото сохранена как текст рассылки")
            print(f"[PHOTO] Сохранено фото для рассылки #{bid+1} (путь: {media_path})")
        else:
            await send_safe(uid, context.bot, "❌ Отправьте ФОТО", CANCEL_BTN)
            return
        
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ГРУПП
    elif step == 'edit_groups':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in raw:
            valid, group = validate_group_link(g)
            if valid:
                groups.append(group)
        
        if groups:
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Сохранено {len(groups)} групп!")
        else:
            await send_safe(uid, context.bot, "❌ Не найдено корректных групп", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ИНТЕРВАЛА
    elif step == 'edit_interval':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        valid, result = validate_interval(text)
        if valid:
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['interval'] = result
            save_data()
            await send_safe(uid, context.bot, f"✅ Интервал: {result} сек")
        else:
            await send_safe(uid, context.bot, f"❌ {result}", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ РАНДОМА
    elif step == 'edit_random':
        bid = step_data['bid']
        text = update.message.text.strip()
        
        if text == '0':
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['random_min'] = 0
            user_data[uid]['broadcasts'][bid]['random_max'] = 0
            save_data()
            await send_safe(uid, context.bot, "✅ Рандом отключён")
            del user_states[uid]
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        match = re.match(r'(\d+)-(\d+)', text)
        if match:
            min_val = int(match.group(1))
            max_val = int(match.group(2))
            if 0 < min_val < max_val <= MAX_INTERVAL:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['random_min'] = min_val
                user_data[uid]['broadcasts'][bid]['random_max'] = max_val
                save_data()
                await send_safe(uid, context.bot, f"✅ Рандом: {min_val}-{max_val} сек")
            else:
                await send_safe(uid, context.bot, f"❌ Диапазон: мин < макс, макс ≤ {MAX_INTERVAL}", CANCEL_BTN)
                return
        else:
            await send_safe(uid, context.bot, "❌ Формат: 10-30", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ РАСПИСАНИЯ
    elif step == 'edit_schedule':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        
        if text.lower() == 'off':
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['schedule'] = None
            save_data()
            await send_safe(uid, context.bot, "✅ Расписание отключено")
            del user_states[uid]
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        valid, schedule = validate_time(text)
        if valid:
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['schedule'] = schedule
            save_data()
            await send_safe(uid, context.bot, f"✅ Расписание: {schedule}")
        else:
            await send_safe(uid, context.bot, "❌ Формат: 14:30", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # АВТОРИЗАЦИЯ И ЗАПУСК
    elif step in ['start_247', 'send_once']:
        if not update.message.text:
            return
        bid = step_data['bid']
        is_247 = (step == 'start_247')
        text = update.message.text.strip()
        
        valid, phone = validate_phone(text)
        if not valid:
            await send_safe(uid, context.bot, f"❌ {phone}", CANCEL_BTN)
            return
        
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        
        user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'is_247': is_247, 'phone': phone}
        
        session_file = get_session_path(uid)
        client = TelegramClient(session_file, API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(phone)
            await send_safe(uid, context.bot, "📲 Введите код из Telegram:\n\nФормат: code12345\n\n✅ Сессия сохранится на 30 дней", CANCEL_BTN)
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'waiting_code':
        if not update.message.text:
            return
        text = update.message.text.strip()
        match = re.search(r'(\d{5,6})', text)
        code = match.group(1) if match else None
        if not code:
            await send_safe(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        
        user_states[uid]['code'] = code
        user_states[uid]['step'] = 'waiting_2fa'
        await send_safe(uid, context.bot, "🔐 Введите пароль 2FA (если есть)\n\nЕсли нет - отправьте /skip", CANCEL_BTN)
    
    elif step == 'waiting_2fa':
        if not update.message.text:
            return
        text = update.message.text.strip()
        password = None if text == '/skip' else text
        client = sessions.get(uid)
        
        if not client:
            await send_safe(uid, context.bot, "❌ Ошибка сессии, начните /start", MAIN_MENU)
            del user_states[uid]
            return
        
        bid = user_states[uid]['bid']
        is_247 = user_states[uid]['is_247']
        phone = user_states[uid]['phone']
        code = user_states[uid]['code']
        
        if bid >= len(user_data[uid].get('broadcasts', [])):
            await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
            del user_states[uid]
            return
        
        bc = user_data[uid]['broadcasts'][bid]
        groups = bc.get('groups', [])
        msg = bc.get('text', '')
        interval = bc.get('interval', DEFAULT_INTERVAL)
        random_min = bc.get('random_min', 0)
        random_max = bc.get('random_max', 0)
        media_path = get_media_path(uid, bid)
        has_photo = os.path.exists(media_path)
        
        try:
            await client.sign_in(phone, code=code)
        except SessionPasswordNeededError:
            if not password:
                await send_safe(uid, context.bot, "🔐 Введите пароль 2FA:", CANCEL_BTN)
                return
            try:
                await client.sign_in(password=password)
            except:
                await send_safe(uid, context.bot, "❌ Неверный пароль", CANCEL_BTN)
                return
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
            return
        
        # Сохраняем информацию о сессии
        await save_session_info(uid, phone)
        
        # Проверка групп
        valid_groups = []
        for group in groups:
            try:
                await client.get_entity(group)
                valid_groups.append(group)
            except:
                await send_safe(uid, context.bot, f"⚠️ {group} - недоступна")
        
        if not valid_groups:
            await send_safe(uid, context.bot, "❌ Нет доступных групп!\nПроверьте что бот добавлен в группы", MAIN_MENU)
            del user_states[uid]
            return
        
        bc['groups'] = valid_groups
        bc['active'] = True
        save_data()
        
        media_info = " 📷" if has_photo else ""
        
        if is_247:
            await send_safe(uid, context.bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n✅ Сессия сохранена на 30 дней!", MAIN_MENU)
            
            task_key = f"{uid}_{bid}"
            if task_key not in active_tasks or active_tasks[task_key].done():
                task = asyncio.create_task(run_broadcast_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max, media_path, has_photo))
                active_tasks[task_key] = task
        else:
            await send_safe(uid, context.bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
            success = 0
            for group in valid_groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=msg)
                    else:
                        await client.send_message(group, msg)
                    success += 1
                    await asyncio.sleep(2)
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    await send_safe(uid, context.bot, f"❌ {group}: {str(e)[:50]}")
            await send_safe(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)
        
        del user_states[uid]

# ==================== HTTP СЕРВЕР ДЛЯ RENDER ====================
async def health_check(request):
    """Health check endpoint для Render и cron-job.org"""
    return web.Response(text="OK", status=200, headers={'Content-Type': 'text/plain'})

async def handle_webhook(request):
    """Обработка webhook от Telegram"""
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"Webhook error: {e}")
        return web.Response(text="ERROR", status=500)

async def start_http_server():
    """Запускает HTTP сервер для Render (держит бота живым)"""
    app = web.Application()
    app.router.add_get('/health', health_check)
    app.router.add_get('/ping', health_check)
    app.router.add_get('/', health_check)
    app.router.add_post(f'/webhook/{BOT_TOKEN}', handle_webhook)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    print(f"[HTTP] Сервер запущен на порту {PORT}")
    print(f"[HTTP] Health check: {RENDER_URL}/health")
    print(f"[HTTP] Webhook: {RENDER_URL}/webhook/{BOT_TOKEN}")
    
    # Бесконечное ожидание
    await asyncio.Event().wait()

# ==================== ЗАПУСК БОТА ====================
async def run_bot():
    global bot_app
    load_data()
    
    # Создаём приложение бота
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("skip", skip_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, message_handler))
    bot_app.add_handler(MessageHandler(filters.Document.ALL, message_handler))
    
    # Установка команд бота
    await bot_app.bot.set_my_commands([
        BotCommand("start", "Главное меню"),
        BotCommand("skip", "Пропустить 2FA"),
    ])
    
    # Инициализируем бота
    await bot_app.initialize()
    await bot_app.start()
    
    # Устанавливаем webhook (для Render)
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot_app.bot.set_webhook(webhook_url)
    print(f"[WEBHOOK] Установлен: {webhook_url}")
    
    print("=" * 70)
    print("🥓 SENDFLOW PRO V4.0 - БОТ ЗАПУЩЕН")
    print("=" * 70)
    print(f"📁 Данные: {DATA_FILE}")
    print(f"📁 Бэкап: {BACKUP_FILE}")
    print(f"📁 Сессии: {SESSIONS_DIR}")
    print(f"📁 Медиа: {MEDIA_DIR}")
    print("=" * 70)
    print("✅ ФУНКЦИОНАЛ:")
    print("   📝 ТЕКСТ + ФОТО (сохраняются на диск)")
    print("   🔐 СЕССИИ СОХРАНЯЮТСЯ (30 дней)")
    print("   🔄 РАССЫЛКА 24/7 ПО КРУГУ")
    print("   📊 ПОЛНАЯ СТАТИСТИКА")
    print("   👥 УПРАВЛЕНИЕ ГРУППАМИ")
    print("=" * 70)
    print("🌐 WEBHOOK РЕЖИМ - БОТ НЕ ЗАСЫПАЕТ")
    print("=" * 70)
    
    # Запускаем HTTP сервер (держит бота живым)
    await start_http_server()

def main():
    """Точка входа"""
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        traceback.print_exc()

if __name__ == '__main__':
    main()
