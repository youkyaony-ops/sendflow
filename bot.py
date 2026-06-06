import asyncio
import re
import json
import os
import logging
import time
import random
import sys
import sqlite3
import hashlib
import base64
import string
import threading
import queue
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any, Union
from dataclasses import dataclass, field, asdict
from enum import Enum

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
    UserPrivacyRestrictedError
)
from telethon.tl.functions.messages import SendMessageRequest, SendMediaRequest
from telethon.tl.types import (
    MessageEntityTextUrl,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    InputMediaPhotoExternal,
    InputMediaDocumentExternal,
    MessageMediaPhoto,
    MessageMediaDocument
)

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
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

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
LOG_DIR = "logs"
if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

LOG_FILE = f'{LOG_DIR}/bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'
ERROR_LOG = f'{LOG_DIR}/errors_{datetime.now().strftime("%Y%m%d")}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
DATABASE_FILE = 'sendflow.db'
MAX_BROADCASTS = 10
MAX_GROUPS_PER_BROADCAST = 50
DEFAULT_INTERVAL = 30
MIN_INTERVAL = 3
MAX_INTERVAL = 3600
ADMIN_ID = 6301912178

# ==================== БАЗА ДАННЫХ ====================
def init_database():
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            phone TEXT,
            created_at TIMESTAMP,
            last_active TIMESTAMP,
            total_broadcasts INTEGER DEFAULT 0,
            total_messages_sent INTEGER DEFAULT 0,
            total_errors INTEGER DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0,
            language TEXT DEFAULT 'ru',
            notifications BOOLEAN DEFAULT 1,
            auto_save BOOLEAN DEFAULT 1,
            default_interval INTEGER DEFAULT 30
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            text TEXT,
            groups TEXT,
            interval_seconds INTEGER,
            is_active BOOLEAN DEFAULT 0,
            is_loop BOOLEAN DEFAULT 1,
            random_min INTEGER DEFAULT 0,
            random_max INTEGER DEFAULT 0,
            schedule_time TEXT,
            messages_sent INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            created_at TIMESTAMP,
            last_started TIMESTAMP,
            last_stopped TIMESTAMP,
            total_rounds INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_name TEXT,
            group_link TEXT,
            group_id INTEGER,
            is_active BOOLEAN DEFAULT 1,
            added_at TIMESTAMP,
            last_used TIMESTAMP,
            total_sent INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            broadcast_id INTEGER,
            message_sent_at TIMESTAMP,
            group_name TEXT,
            group_link TEXT,
            success BOOLEAN,
            error_message TEXT,
            response_time INTEGER,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone TEXT,
            created_at TIMESTAMP,
            expires_at TIMESTAMP,
            last_used TIMESTAMP,
            ip_address TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS message_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            content TEXT,
            created_at TIMESTAMP,
            last_used TIMESTAMP,
            use_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scheduled_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            broadcast_id INTEGER,
            scheduled_time TIMESTAMP,
            repeat_type TEXT,
            repeat_interval INTEGER,
            is_active BOOLEAN DEFAULT 1,
            last_run TIMESTAMP,
            next_run TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id),
            FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS analytics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            total_users INTEGER,
            active_broadcasts INTEGER,
            total_messages_sent INTEGER,
            unique_groups INTEGER
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

init_database()

# ==================== ДАТАКЛАССЫ ====================
class BroadcastStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"
    SCHEDULED = "scheduled"

class MessageType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
    MEDIA_GROUP = "media_group"

@dataclass
class BroadcastConfig:
    id: int = 0
    name: str = ""
    text: str = ""
    groups: List[str] = field(default_factory=list)
    interval: int = DEFAULT_INTERVAL
    status: BroadcastStatus = BroadcastStatus.STOPPED
    messages_sent: int = 0
    errors: int = 0
    start_time: Optional[datetime] = None
    loop_enabled: bool = True
    random_delay_min: int = 0
    random_delay_max: int = 0
    schedule_time: Optional[str] = None
    auto_stop_after: Optional[int] = None
    current_round: int = 0
    total_rounds: int = 0
    message_type: MessageType = MessageType.TEXT
    media_file_id: Optional[str] = None
    caption: Optional[str] = None
    buttons: List[Dict] = field(default_factory=list)
    use_spintax: bool = False
    spintax_variants: List[str] = field(default_factory=list)
    use_emoji_randomizer: bool = False
    emojis: List[str] = field(default_factory=list)
    use_link_shortener: bool = False
    shortener_api: Optional[str] = None

@dataclass
class UserSettings:
    user_id: int
    language: str = "ru"
    notifications: bool = True
    auto_save: bool = True
    dark_mode: bool = False
    auto_start: bool = False
    max_concurrent: int = 3
    default_interval: int = DEFAULT_INTERVAL
    backup_enabled: bool = True
    log_level: str = "INFO"
    timezone: str = "UTC"
    date_format: str = "%d.%m.%Y %H:%M"
    theme: str = "default"
    sound_enabled: bool = True
    inline_mode: bool = True
    keyboard_size: str = "normal"

@dataclass
class GroupInfo:
    link: str
    name: str = ""
    id: int = 0
    members: int = 0
    is_active: bool = True
    last_checked: Optional[datetime] = None

@dataclass
class ScheduledTask:
    id: int
    broadcast_id: int
    schedule_time: datetime
    repeat_type: str
    repeat_interval: int
    is_active: bool

@dataclass
class BroadcastStats:
    total_sent: int = 0
    total_errors: int = 0
    success_rate: float = 0.0
    avg_response_time: float = 0.0
    messages_per_minute: float = 0.0
    active_time: int = 0
    groups_count: int = 0
    rounds_completed: int = 0

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
def generate_session_key() -> str:
    return ''.join(random.choices(string.ascii_letters + string.digits, k=32))

def encrypt_data(data: str) -> str:
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', BOT_TOKEN.encode(), salt, 100000)
    encrypted = base64.b64encode(salt + key)
    return encrypted.decode()

def decrypt_data(encrypted: str) -> str:
    try:
        data = base64.b64decode(encrypted.encode())
        return data[32:].decode()
    except:
        return ""

def validate_group_link(link: str) -> Tuple[bool, str]:
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

def validate_phone(phone: str) -> Tuple[bool, str]:
    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    if re.match(r'^\+[0-9]{10,15}$', phone):
        return True, phone
    return False, "Неверный формат"

def validate_interval(interval: int) -> Tuple[bool, str]:
    if MIN_INTERVAL <= interval <= MAX_INTERVAL:
        return True, "OK"
    return False, f"От {MIN_INTERVAL} до {MAX_INTERVAL}"

def validate_time(time_str: str) -> Tuple[bool, Optional[str]]:
    match = re.match(r'^(\d{1,2}):(\d{2})$', time_str)
    if match:
        hour, minute = int(match.group(1)), int(match.group(2))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return True, f"{hour:02d}:{minute:02d}"
    return False, None

def format_number(num: int) -> str:
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num/1_000:.1f}K"
    return str(num)

def format_time(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    if seconds < 3600:
        return f"{seconds // 60} мин {seconds % 60} сек"
    return f"{seconds // 3600} ч {(seconds % 3600) // 60} мин"

def generate_spintax(text: str) -> str:
    pattern = r'\{([^{}]*)\}'
    while re.search(pattern, text):
        text = re.sub(pattern, lambda m: random.choice(m.group(1).split('|')), text)
    return text

def randomize_emojis(text: str, emojis: List[str]) -> str:
    if not emojis:
        return text
    emoji_placeholders = re.findall(r'\[emoji\]', text)
    for _ in emoji_placeholders:
        text = text.replace('[emoji]', random.choice(emojis), 1)
    return text

def create_backup(user_id: int, data: Dict) -> str:
    backup_dir = "backups"
    if not os.path.exists(backup_dir):
        os.makedirs(backup_dir)
    backup_file = f"{backup_dir}/user_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(backup_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return backup_file

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
user_data: Dict[int, Dict] = {}
active_tasks: Dict[str, asyncio.Task] = {}
sessions: Dict[int, TelegramClient] = {}
user_states: Dict[int, Dict] = {}
broadcast_stats: Dict[int, Dict[int, BroadcastStats]] = defaultdict(lambda: defaultdict(BroadcastStats))
scheduled_tasks: Dict[int, List[ScheduledTask]] = defaultdict(list)
message_queue: Dict[int, asyncio.Queue] = {}
rate_limits: Dict[int, List[float]] = defaultdict(list)
daily_stats: Dict[int, Dict[str, Any]] = defaultdict(lambda: {'sent': 0, 'errors': 0, 'start_time': None})

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 МОИ ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data='my_stats')],
    [InlineKeyboardButton("📝 ШАБЛОНЫ", callback_data='templates')],
    [InlineKeyboardButton("⏰ ЗАДАНИЯ", callback_data='scheduled_tasks')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')],
    [InlineKeyboardButton("ℹ️ О БОТЕ", callback_data='about')]
])

BROADCAST_ACTIONS = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 ТЕКСТ", callback_data='edit_text'), InlineKeyboardButton("👥 ГРУППЫ", callback_data='edit_groups')],
    [InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data='edit_interval'), InlineKeyboardButton("🎲 РАНДОМ", callback_data='edit_random')],
    [InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data='toggle_loop'), InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data='edit_schedule')],
    [InlineKeyboardButton("📎 МЕДИА", callback_data='edit_media'), InlineKeyboardButton("🔘 КНОПКИ", callback_data='edit_buttons')],
    [InlineKeyboardButton("🎭 СПИНТАКС", callback_data='toggle_spintax'), InlineKeyboardButton("😀 ЭМОДЗИ", callback_data='edit_emojis')],
    [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data='start_247'), InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data='send_once')],
    [InlineKeyboardButton("⏸️ ПАУЗА", callback_data='pause_broadcast'), InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data='stop_broadcast')],
    [InlineKeyboardButton("📊 СТАТУС", callback_data='bc_status'), InlineKeyboardButton("📈 ДЕТАЛЬНО", callback_data='bc_details')],
    [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data='clone_broadcast'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data='delete_broadcast')],
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

TEMPLATES_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ СОЗДАТЬ ШАБЛОН", callback_data='create_template')],
    [InlineKeyboardButton("📋 МОИ ШАБЛОНЫ", callback_data='list_templates')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SCHEDULED_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ЗАДАНИЕ", callback_data='add_scheduled')],
    [InlineKeyboardButton("📋 ВСЕ ЗАДАНИЯ", callback_data='list_scheduled')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🌐 ЯЗЫК", callback_data='set_lang'), InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data='toggle_notify')],
    [InlineKeyboardButton("🎨 ТЕМА", callback_data='set_theme'), InlineKeyboardButton("💾 АВТОСОХРАНЕНИЕ", callback_data='toggle_autosave')],
    [InlineKeyboardButton("⏱ ИНТЕРВАЛ ПО УМОЛЧ.", callback_data='def_interval'), InlineKeyboardButton("🔊 ЗВУК", callback_data='toggle_sound')],
    [InlineKeyboardButton("📅 ФОРМАТ ДАТЫ", callback_data='set_date_format'), InlineKeyboardButton("🌍 ЧАСОВОЙ ПОЯС", callback_data='set_timezone')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 БЫСТРЫЙ СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔄 РЕЖИМЫ РАБОТЫ", callback_data='help_modes')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("💡 СОВЕТЫ", callback_data='help_tips')],
    [InlineKeyboardButton("❓ ЧАСТЫЕ ВОПРОСЫ", callback_data='help_faq')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

STATS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 ПО РАССЫЛКАМ", callback_data='stats_by_broadcast')],
    [InlineKeyboardButton("📅 ПО ДНЯМ", callback_data='stats_by_day')],
    [InlineKeyboardButton("🏆 ТОП ГРУПП", callback_data='stats_top_groups')],
    [InlineKeyboardButton("📈 ЭФФЕКТИВНОСТЬ", callback_data='stats_efficiency')],
    [InlineKeyboardButton("📉 ОШИБКИ", callback_data='stats_errors')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])

CONFIRM_BTN = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ ДА", callback_data='confirm_yes')],
    [InlineKeyboardButton("❌ НЕТ", callback_data='confirm_no')]
])

BACK_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]])

# ==================== СОХРАНЕНИЕ ДАННЫХ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
                    'templates': data.get('templates', []),
                    'settings': data.get('settings', {}),
                    'created_at': data.get('created_at', str(datetime.now())),
                    'total_sent': data.get('total_sent', 0),
                    'total_errors': data.get('total_errors', 0)
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Данные сохранены для {len(user_data)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

def load_data():
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
            logger.info(f"Данные загружены для {len(user_data)} пользователей")
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        user_data = {}

def save_user(uid: int):
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'templates': [],
            'settings': {
                'notify': True,
                'autosave': True,
                'def_interval': 30,
                'language': 'ru',
                'theme': 'default',
                'sound': True,
                'date_format': '%d.%m.%Y %H:%M',
                'timezone': 'UTC'
            },
            'created_at': str(datetime.now()),
            'total_sent': 0,
            'total_errors': 0
        }
        save_data()

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОТПРАВКИ ====================
async def send_safe(chat_id: int, bot, text: str, keyboard=None):
    try:
        if keyboard:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await bot.send_message(chat_id, text, parse_mode='HTML')
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")
        return False

async def main_menu(chat_id: int, bot, text: str = None):
    msg = text if text else "🥓 <b>SendFlow Pro</b>\n\nВыберите действие:"
    await send_safe(chat_id, bot, msg, MAIN_MENU)

async def edit_message_safe(chat_id: int, bot, message_id: int, text: str, keyboard=None):
    try:
        if keyboard:
            await bot.edit_message_text(text, chat_id, message_id, reply_markup=keyboard, parse_mode='HTML')
        else:
            await bot.edit_message_text(text, chat_id, message_id, parse_mode='HTML')
    except:
        pass

# ==================== КОМАНДЫ ====================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    load_data()
    save_user(uid)

    welcome_text = f"""
🌟 <b>ДОБРО ПОЖАЛОВАТЬ В SENDFLOW PRO!</b> 🌟

Привет, {update.effective_user.first_name}! 👋

<b>🤖 Что я умею:</b>
• 📢 Массовая рассылка в Telegram 24/7
• 🔄 Бесконечная отправка по кругу
• 🎯 Отправка в несколько групп сразу
• 📎 Поддержка фото, видео, документов
• 🔘 Инлайн-кнопки в сообщениях
• 🎭 Спинтакс (генерация вариантов текста)
• 😀 Случайные эмодзи
• 📊 Подробная статистика
• 💾 Автосохранение и бэкапы
• ⏰ Задания по расписанию
• 📝 Шаблоны сообщений

<b>📌 Команды:</b>
/start - Главное меню
/help - Помощь
/skip - Пропустить 2FA
/stats - Статистика
/groups - Мои группы
/backup - Бэкап
/export - Экспорт
/import - Импорт
/clear - Очистить данные
/feedback - Отзыв
/report - Репорт ошибки

<b>🚀 Начните с создания первой рассылки!</b>
    """
    await send_safe(uid, context.bot, welcome_text, MAIN_MENU)

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📖 <b>ПОЛНАЯ СПРАВКА SENDFLOW PRO</b> 📖

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🚀 БЫСТРЫЙ СТАРТ (3 шага):</b>
1️⃣ Нажми "➕ НОВАЯ РАССЫЛКА"
2️⃣ Настрой текст, группы, интервал
3️⃣ Нажми "🚀 ЗАПУСТИТЬ 24/7"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📢 ТИПЫ РАССЫЛОК:</b>
• <b>24/7 режим</b> - Бесконечная отправка по кругу
• <b>Разовый режим</b> - Одно сообщение во все группы
• <b>По расписанию</b> - Запуск в указанное время
• <b>С зацикливанием</b> - Повторять N раз

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>⚙️ РАСШИРЕННЫЕ НАСТРОЙКИ:</b>
• <b>🎲 Рандом</b> - Случайная задержка между сообщениями
• <b>🔘 Кнопки</b> - Добавление inline-кнопок
• <b>🎭 Спинтакс</b> - Генерация уникальных вариантов текста
• <b>😀 Эмодзи</b> - Случайные эмодзи в тексте
• <b>📎 Медиа</b> - Фото, видео, документы
• <b>📅 Расписание</b> - Запуск в определенное время

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🔧 РЕШЕНИЕ ПРОБЛЕМ:</b>
• <b>Ошибка 2FA</b> - Введи пароль или используй /skip
• <b>Группа недоступна</b> - Бот должен быть участником
• <b>Флуд-контроль</b> - Увеличь интервал до 30+ сек
• <b>Не отправляется</b> - Проверь права в группе

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>💡 ПОЛЕЗНЫЕ СОВЕТЫ:</b>
• Сохраняйте группы в разделе "ГРУППЫ" для быстрого доступа
• Используйте опцию "КЛОНИРОВАТЬ" для похожих рассылок
• Регулярно делайте бэкапы через "💾 БЭКАП"
• Следите за статистикой для оптимизации
• Используйте шаблоны для частых сообщений

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📞 ПОДДЕРЖКА:</b>
• /feedback - Отправить отзыв
• /report - Сообщить об ошибке
• /donate - Поддержать проект

<b>Версия:</b> 4.0.0 Pro | <b>Обновлено:</b> 06.06.2026
    """
    await send_safe(update.effective_user.id, context.bot, help_text, HELP_MENU)

async def skip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if user_states.get(uid, {}).get('step') == 'waiting_2fa':
        update.message.text = '/skip'
        await message_handler(update, context)
    else:
        await send_safe(uid, context.bot, "❌ Нет активного запроса 2FA", MAIN_MENU)

async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = user_data.get(uid, {})
    bc = data.get('broadcasts', [])

    txt = f"📊 <b>ВАША СТАТИСТИКА</b>\n\n"
    txt += f"📢 Рассылок: {len(bc)}\n"
    txt += f"🟢 Активных: {sum(1 for b in bc if b.get('active'))}\n"
    txt += f"📨 Отправлено: {data.get('total_sent', 0)}\n"
    txt += f"❌ Ошибок: {data.get('total_errors', 0)}\n"
    txt += f"✅ Успешно: {data.get('total_sent', 0) - data.get('total_errors', 0)}\n"
    txt += f"📁 Групп: {len(data.get('groups', []))}\n"
    txt += f"📝 Шаблонов: {len(data.get('templates', []))}\n"
    txt += f"📅 Создан: {data.get('created_at', 'Неизвестно')[:10]}"

    await send_safe(uid, context.bot, txt, STATS_MENU)

async def groups_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    groups = user_data.get(uid, {}).get('groups', [])

    if not groups:
        txt = "📁 <b>У вас нет сохранённых групп</b>\n\nДобавьте первую группу через кнопку '➕ ДОБАВИТЬ ГРУППУ'"
    else:
        txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n"
        for i, g in enumerate(groups, 1):
            txt += f"{i}. {g}\n"
            if i >= 20 and len(groups) > 20:
                txt += f"\n... и ещё {len(groups) - 20} групп"
                break

    await send_safe(uid, context.bot, txt, GROUPS_MENU)

async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = user_data.get(uid, {})

    backup_file = create_backup(uid, data)
    await send_safe(uid, context.bot, f"✅ <b>Бэкап создан!</b>\n\nФайл: {backup_file}\nДата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}")

    try:
        with open(backup_file, 'rb') as f:
            await update.message.reply_document(f, caption="📦 Ваш бэкап данных")
    except:
        pass

async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    data = user_data.get(uid, {})

    export_data = {
        'broadcasts': data.get('broadcasts', []),
        'groups': data.get('groups', []),
        'templates': data.get('templates', []),
        'settings': data.get('settings', {}),
        'export_date': str(datetime.now())
    }

    export_file = f"export_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(export_file, 'w', encoding='utf-8') as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    with open(export_file, 'rb') as f:
        await update.message.reply_document(f, caption="📎 Экспорт настроек")

    os.remove(export_file)

async def import_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = {'step': 'waiting_import'}
    await send_safe(uid, context.bot, "📥 Отправьте JSON файл с экспортированными настройками:", CANCEL_BTN)

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = {'step': 'confirm_clear'}
    await send_safe(uid, context.bot, "⚠️ <b>ВНИМАНИЕ!</b>\n\nВы уверены, что хотите очистить ВСЕ данные?\nЭто действие НЕЛЬЗЯ отменить!", CONFIRM_BTN)

async def feedback_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = {'step': 'waiting_feedback'}
    await send_safe(uid, context.bot, "📝 Напишите ваш отзыв или предложение:", CANCEL_BTN)

async def report_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_states[uid] = {'step': 'waiting_report'}
    await send_safe(uid, context.bot, "🐛 Опишите проблему или ошибку подробно:", CANCEL_BTN)

async def donate_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    donate_text = """
💝 <b>ПОДДЕРЖАТЬ ПРОЕКТ</b> 💝

<b>Спасибо, что пользуетесь SendFlow Pro!</b>

<b>💎 Способы поддержки:</b>
• USDT (TRC20): `TXXXXXXXXXXXXXXXXX`
• BTC: `1XXXXXXXXXXXXXXXXX`
• ETH: `0xXXXXXXXXXXXXXXXXX`

<b>🌟 За поддержку:</b>
• Приоритетная поддержка
• Ранний доступ к функциям
• Ваше имя в списке спонсоров

<i>Спасибо за вашу поддержку! ❤️</i>
    """
    await send_safe(update.effective_user.id, context.bot, donate_text, BACK_BTN)

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_states:
        del user_states[uid]
    await send_safe(uid, context.bot, "❌ Действие отменено", MAIN_MENU)

# ==================== КНОПКИ ====================
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_safe(uid, context.bot, "📢 У вас нет рассылок\n\nСоздайте новую через кнопку '➕ НОВАЯ РАССЫЛКА'", MAIN_MENU)
            return

        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            status = "🟢" if bc.get('active') else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'bc_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])

        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == 'new_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= MAX_BROADCASTS:
            await send_safe(uid, context.bot, f"❌ Максимум {MAX_BROADCASTS} рассылок\nУдалите ненужные", MAIN_MENU)
            return

        new_id = len(broadcasts)
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}',
            'text': None,
            'groups': [],
            'interval': user_data[uid].get('settings', {}).get('def_interval', 30),
            'active': False,
            'loop': True,
            'random_min': 0,
            'random_max': 0,
            'schedule': None,
            'sent': 0,
            'errors': 0,
            'message_type': 'text',
            'media_file_id': None,
            'caption': None,
            'buttons': [],
            'use_spintax': False,
            'use_emoji_randomizer': False,
            'emojis': []
        })
        save_data()
        await send_safe(uid, context.bot, f"✅ Создана рассылка #{new_id+1}\n\nНастройте параметры:", BROADCAST_ACTIONS)

    elif data == 'my_groups':
        await groups_cmd(update, context)

    elif data == 'my_stats':
        await stats_cmd(update, context)

    elif data == 'templates':
        templates = user_data[uid].get('templates', [])
        if not templates:
            await send_safe(uid, context.bot, "📝 У вас нет шаблонов\n\nСоздайте первый через кнопку '➕ СОЗДАТЬ ШАБЛОН'", TEMPLATES_MENU)
            return

        kb = []
        for i, t in enumerate(templates):
            kb.append([InlineKeyboardButton(f"📝 {t.get('name', f'Шаблон {i+1}')}", callback_data=f'template_{i}')])
        kb.append([InlineKeyboardButton("➕ СОЗДАТЬ", callback_data='create_template')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])

        await context.bot.send_message(uid, "📝 <b>ВАШИ ШАБЛОНЫ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == 'scheduled_tasks':
        tasks = scheduled_tasks.get(uid, [])
        if not tasks:
            await send_safe(uid, context.bot, "⏰ У вас нет запланированных заданий\n\nДобавьте задание через кнопку '➕ ДОБАВИТЬ ЗАДАНИЕ'", SCHEDULED_MENU)
            return

        kb = []
        for t in tasks:
            status = "🟢" if t.is_active else "🔴"
            kb.append([InlineKeyboardButton(f"{status} Задание #{t.id}", callback_data=f'task_{t.id}')])
        kb.append([InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data='add_scheduled')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])

        await context.bot.send_message(uid, "⏰ <b>ЗАПЛАНИРОВАННЫЕ ЗАДАНИЯ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == 'settings':
        s = user_data[uid].get('settings', {})
        txt = f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        txt += f"🌐 Язык: {s.get('language', 'ru').upper()}\n"
        txt += f"🔔 Уведомления: {'✅' if s.get('notify', True) else '❌'}\n"
        txt += f"🎨 Тема: {s.get('theme', 'default')}\n"
        txt += f"💾 Автосохранение: {'✅' if s.get('autosave', True) else '❌'}\n"
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', 30)} сек\n"
        txt += f"🔊 Звук: {'✅' if s.get('sound', True) else '❌'}\n"
        txt += f"📅 Формат даты: {s.get('date_format', '%d.%m.%Y %H:%M')}\n"
        txt += f"🌍 Часовой пояс: {s.get('timezone', 'UTC')}"
        await send_safe(uid, context.bot, txt, SETTINGS_MENU)

    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)

    elif data == 'about':
        about_text = """
🤖 <b>SENDFLOW PRO</b> 🤖

<b>Версия:</b> 4.0.0
<b>Разработчик:</b> @Gothbreach
<b>Дата релиза:</b> 06.06.2026

<b>⚡ ВОЗМОЖНОСТИ:</b>
• Массовая рассылка 24/7
• Медиафайлы (фото/видео)
• Инлайн-кнопки
• Спинтакс
• Случайные эмодзи
• Расписание
• Шаблоны
• Бэкапы

<b>📊 СТАТИСТИКА БОТА:</b>
• Пользователей: {len(user_data)}
• Активных рассылок: {sum(1 for u in user_data.values() for b in u.get('broadcasts', []) if b.get('active'))}
• Отправлено сообщений: {sum(u.get('total_sent', 0) for u in user_data.values())}

<i>Спасибо, что выбираете SendFlow Pro! 🌟</i>
        """
        await send_safe(uid, context.bot, about_text.format(len(user_data)), BACK_BTN)

    # ===== ВЫБОР РАССЫЛКИ =====
    elif data.startswith('bc_'):
        bid = int(data.split('_')[1])
        user_data[uid]['current_bc'] = bid
        save_data()

        bc = user_data[uid]['broadcasts'][bid]
        status = "🟢 АКТИВНА" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"

        txt = f"📢 <b>{bc['name']}</b>\n\n"
        txt += f"Статус: {status}\n"
        txt += f"📝 Текст: {'✅' if bc.get('text') else '❌'}\n"
        txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
        txt += f"⏱ Интервал: {bc.get('interval', 30)} сек\n"
        if bc.get('random_min') and bc.get('random_max'):
            txt += f"🎲 Рандом: {bc['random_min']}-{bc['random_max']} сек\n"
        txt += f"🔄 Зациклено: {'✅' if bc.get('loop', True) else '❌'}\n"
        if bc.get('schedule'):
            txt += f"📅 Расписание: {bc['schedule']}\n"
        txt += f"📨 Отправлено: {bc.get('sent', 0)}\n"
        txt += f"❌ Ошибок: {bc.get('errors', 0)}\n"
        txt += f"📎 Тип: {bc.get('message_type', 'text')}\n"
        txt += f"🔘 Кнопок: {len(bc.get('buttons', []))}\n"
        txt += f"🎭 Спинтакс: {'✅' if bc.get('use_spintax') else '❌'}\n"
        txt += f"😀 Эмодзи: {'✅' if bc.get('use_emoji_randomizer') else '❌'}"

        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)

    # ===== ДЕЙСТВИЯ С РАССЫЛКОЙ =====
    elif data == 'edit_text':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_text', 'bid': bid}
        await send_safe(uid, context.bot, "📝 Введите текст рассылки:", CANCEL_BTN)

    elif data == 'edit_groups':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}

        saved_groups = user_data[uid].get('groups', [])
        if saved_groups:
            kb = []
            for g in saved_groups[:10]:
                kb.append([InlineKeyboardButton(f"📌 {g}", callback_data=f'select_group_{g}')])
            kb.append([InlineKeyboardButton("✏️ ВВЕСТИ ВРУЧНУЮ", callback_data='manual_groups')])
            kb.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')])
            await context.bot.send_message(uid, "👥 <b>ВЫБЕРИТЕ ГРУППЫ</b>\n\nМожно выбрать из сохранённых или ввести вручную:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2, https://t.me/group3", CANCEL_BTN)

    elif data == 'manual_groups':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2, https://t.me/group3", CANCEL_BTN)

    elif data.startswith('select_group_'):
        group = data.replace('select_group_', '')
        bid = user_data[uid].get('current_bc', 0)
        groups = user_data[uid]['broadcasts'][bid].get('groups', [])
        if group not in groups:
            groups.append(group)
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Добавлена группа: {group}\n\nВсего групп: {len(groups)}")
        else:
            await send_safe(uid, context.bot, f"⚠️ Группа {group} уже есть в списке")

        bc = user_data[uid]['broadcasts'][bid]
        txt = f"📢 <b>{bc['name']}</b>\n\nГрупп: {len(groups)}"
        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)

    elif data == 'edit_interval':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_interval', 'bid': bid}
        await send_safe(uid, context.bot, f"⏱ Введите интервал ({MIN_INTERVAL}-{MAX_INTERVAL} секунд):", CANCEL_BTN)

    elif data == 'edit_random':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_random', 'bid': bid}
        await send_safe(uid, context.bot, "🎲 Введите диапазон случайной задержки:\n\nФормат: мин-макс\nПример: 10-30\n\nДля отключения введите 0", CANCEL_BTN)

    elif data == 'toggle_loop':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        bc['loop'] = not bc.get('loop', True)
        save_data()
        await send_safe(uid, context.bot, f"🔄 Зацикливание: {'ВКЛЮЧЕНО' if bc['loop'] else 'ВЫКЛЮЧЕНО'}")
        await button_handler(update, context)

    elif data == 'edit_schedule':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_schedule', 'bid': bid}
        await send_safe(uid, context.bot, "📅 Введите время расписания (ЧЧ:ММ):\n\nПример: 14:30\n\nДля отключения введите 'off'", CANCEL_BTN)

    elif data == 'edit_media':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_media', 'bid': bid}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📷 ФОТО", callback_data='media_photo'), InlineKeyboardButton("🎥 ВИДЕО", callback_data='media_video')],
            [InlineKeyboardButton("📄 ДОКУМЕНТ", callback_data='media_document'), InlineKeyboardButton("❌ УДАЛИТЬ", callback_data='media_remove')],
            [InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]
        ])
        await context.bot.send_message(uid, "📎 <b>ВЫБЕРИТЕ ТИП МЕДИА</b>", reply_markup=kb, parse_mode='HTML')

    elif data == 'media_photo':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'waiting_photo', 'bid': bid}
        await send_safe(uid, context.bot, "📷 Отправьте фото:", CANCEL_BTN)

    elif data == 'media_video':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'waiting_video', 'bid': bid}
        await send_safe(uid, context.bot, "🎥 Отправьте видео:", CANCEL_BTN)

    elif data == 'media_document':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'waiting_document', 'bid': bid}
        await send_safe(uid, context.bot, "📄 Отправьте документ:", CANCEL_BTN)

    elif data == 'media_remove':
        bid = user_data[uid].get('current_bc', 0)
        user_data[uid]['broadcasts'][bid]['message_type'] = 'text'
        user_data[uid]['broadcasts'][bid]['media_file_id'] = None
        save_data()
        await send_safe(uid, context.bot, "✅ Медиа удалено")
        await button_handler(update, context)

    elif data == 'edit_buttons':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_buttons', 'bid': bid}
        await send_safe(uid, context.bot, "🔘 Введите кнопки в формате:\n\nТекст|URL\n\nПример:\nGoogle|https://google.com\nYandex|https://yandex.ru\n\nДля отключения введите 'off'", CANCEL_BTN)

    elif data == 'toggle_spintax':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        bc['use_spintax'] = not bc.get('use_spintax', False)
        save_data()
        await send_safe(uid, context.bot, f"🎭 Спинтакс: {'ВКЛЮЧЕН' if bc['use_spintax'] else 'ВЫКЛЮЧЕН'}")
        await button_handler(update, context)

    elif data == 'edit_emojis':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_emojis', 'bid': bid}
        await send_safe(uid, context.bot, "😀 Введите список эмодзи через запятую:\n\nПример: 😊, 🎉, 🔥, ❤️, 👍\n\nЭмодзи будут случайно вставляться в текст вместо [emoji]", CANCEL_BTN)

    elif data == 'start_247':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]

        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!", BROADCAST_ACTIONS)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BROADCAST_ACTIONS)
            return
        if f"{uid}_{bid}" in active_tasks:
            await send_safe(uid, context.bot, "⚠️ Рассылка уже запущена!", BROADCAST_ACTIONS)
            return

        user_states[uid] = {'step': 'start_247', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона Telegram:\n\nПример: +79123456789", CANCEL_BTN)

    elif data == 'send_once':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]

        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!", BROADCAST_ACTIONS)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BROADCAST_ACTIONS)
            return

        user_states[uid] = {'step': 'send_once', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона Telegram:\n\nПример: +79123456789", CANCEL_BTN)

    elif data == 'pause_broadcast':
        bid = user_data[uid].get('current_bc', 0)
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, "⏸️ Рассылка приостановлена")
        else:
            await send_safe(uid, context.bot, "❌ Нет активной рассылки")
        await button_handler(update, context)

    elif data == 'stop_broadcast':
        bid = user_data[uid].get('current_bc', 0)
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, "🛑 Рассылка остановлена")
        else:
            await send_safe(uid, context.bot, "❌ Нет активной рассылки")
        await button_handler(update, context)

    elif data == 'bc_status':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        status = "🟢 РАБОТАЕТ" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"
        txt = f"📊 <b>СТАТУС РАССЫЛКИ</b>\n\n"
        txt += f"Имя: {bc['name']}\n"
        txt += f"Статус: {status}\n"
        txt += f"Отправлено: {bc.get('sent', 0)}\n"
        txt += f"Ошибок: {bc.get('errors', 0)}\n"
        txt += f"Групп: {len(bc.get('groups', []))}\n"
        txt += f"Текущий круг: {bc.get('current_round', 0)}"
        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)

    elif data == 'bc_details':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        stats = broadcast_stats[uid][bid]

        txt = f"📈 <b>ДЕТАЛЬНАЯ СТАТИСТИКА</b>\n\n"
        txt += f"📢 {bc['name']}\n\n"
        txt += f"📨 Отправлено: {stats.total_sent}\n"
        txt += f"❌ Ошибок: {stats.total_errors}\n"
        txt += f"✅ Успешно: {stats.total_sent - stats.total_errors}\n"
        txt += f"📊 Успешность: {stats.success_rate:.1f}%\n"
        txt += f"⚡ Сообщений/мин: {stats.messages_per_minute:.1f}\n"
        txt += f"🔄 Кругов завершено: {stats.rounds_completed}\n"
        txt += f"⏱ Времени в работе: {format_time(stats.active_time)}\n"
        txt += f"👥 Групп в ротации: {stats.groups_count}"
        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)

    elif data == 'clone_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= MAX_BROADCASTS:
            await send_safe(uid, context.bot, f"❌ Достигнут лимит рассылок ({MAX_BROADCASTS})", MAIN_MENU)
            return

        bid = user_data[uid].get('current_bc', 0)
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original['name']}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', 30),
            'active': False,
            'loop': original.get('loop', True),
            'random_min': original.get('random_min', 0),
            'random_max': original.get('random_max', 0),
            'schedule': original.get('schedule'),
            'sent': 0, 'errors': 0,
            'message_type': original.get('message_type', 'text'),
            'media_file_id': original.get('media_file_id'),
            'caption': original.get('caption'),
            'buttons': original.get('buttons', []).copy(),
            'use_spintax': original.get('use_spintax', False),
            'use_emoji_randomizer': original.get('use_emoji_randomizer', False),
            'emojis': original.get('emojis', []).copy()
        }
        user_data[uid]['broadcasts'].append(new_bc)
        save_data()
        await send_safe(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)

    elif data == 'delete_broadcast':
        bid = user_data[uid].get('current_bc', 0)
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, "🗑 Рассылка удалена", MAIN_MENU)

    # ===== ГРУППЫ =====
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_safe(uid, context.bot, "➕ Введите ссылку на группу:\n\nПример: @group_name или https://t.me/group", CANCEL_BTN)

    elif data == 'list_groups':
        await groups_cmd(update, context)

    elif data == 'check_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет групп для проверки", GROUPS_MENU)
            return

        await send_safe(uid, context.bot, f"🔄 Проверяю {len(groups)} групп...")
        valid = []
        invalid = []

        for group in groups:
            try:
                if uid in sessions:
                    client = sessions[uid]
                else:
                    continue
                await client.get_entity(group)
                valid.append(group)
            except:
                invalid.append(group)

        txt = f"✅ Доступно: {len(valid)}\n❌ Недоступно: {len(invalid)}"
        if invalid:
            txt += f"\n\nНедоступные:\n" + "\n".join(invalid[:10])
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

        export_file = f"groups_export_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(export_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(groups))

        with open(export_file, 'rb') as f:
            await update.callback_query.message.reply_document(f, caption="📁 Экспорт групп")

        os.remove(export_file)

    elif data == 'import_groups':
        user_states[uid] = {'step': 'import_groups'}
        await send_safe(uid, context.bot, "📥 Отправьте текстовый файл со списком групп (по одной на строку):", CANCEL_BTN)

    # ===== ШАБЛОНЫ =====
    elif data == 'create_template':
        user_states[uid] = {'step': 'create_template_name'}
        await send_safe(uid, context.bot, "📝 Введите название шаблона:", CANCEL_BTN)

    elif data == 'list_templates':
        templates = user_data[uid].get('templates', [])
        if not templates:
            await send_safe(uid, context.bot, "📝 Нет шаблонов", TEMPLATES_MENU)
            return

        txt = "📝 <b>ВАШИ ШАБЛОНЫ</b>\n\n"
        for i, t in enumerate(templates, 1):
            content = t.get('content', '')[:50]
            txt += f"{i}. {t.get('name')} - {t.get('use_count', 0)} использований\n"
            txt += f"   {content}...\n\n"
        await send_safe(uid, context.bot, txt, TEMPLATES_MENU)

    elif data.startswith('template_'):
        idx = int(data.split('_')[1])
        templates = user_data[uid].get('templates', [])
        if idx < len(templates):
            template = templates[idx]
            template['use_count'] = template.get('use_count', 0) + 1
            save_data()

            bid = user_data[uid].get('current_bc', 0) if user_data[uid].get('current_bc') is not None else None
            if bid is not None:
                user_data[uid]['broadcasts'][bid]['text'] = template['content']
                save_data()
                await send_safe(uid, context.bot, f"✅ Текст из шаблона '{template['name']}' применён к рассылке")
                await button_handler(update, context)
            else:
                user_states[uid] = {'step': 'use_template', 'template': template['content']}
                await send_safe(uid, context.bot, f"✅ Выбран шаблон: {template['name']}\n\nТекст:\n{template['content'][:200]}...\n\nВ какую рассылку применить?", BROADCAST_ACTIONS)

    # ===== ЗАДАНИЯ =====
    elif data == 'add_scheduled':
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_safe(uid, context.bot, "❌ Нет рассылок. Сначала создайте рассылку.", SCHEDULED_MENU)
            return

        kb = []
        for i, bc in enumerate(broadcasts):
            kb.append([InlineKeyboardButton(f"📢 {bc['name']}", callback_data=f'sched_bc_{i}')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='scheduled_tasks')])

        await context.bot.send_message(uid, "⏰ <b>ВЫБЕРИТЕ РАССЫЛКУ ДЛЯ ЗАДАНИЯ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data.startswith('sched_bc_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'sched_time', 'bid': bid}
        await send_safe(uid, context.bot, "📅 Введите время выполнения (ЧЧ:ММ):\n\nПример: 14:30\n\nМожно указать несколько времён через запятую", CANCEL_BTN)

    elif data == 'list_scheduled':
        tasks = scheduled_tasks.get(uid, [])
        if not tasks:
            await send_safe(uid, context.bot, "⏰ Нет запланированных заданий", SCHEDULED_MENU)
            return

        txt = "⏰ <b>ЗАПЛАНИРОВАННЫЕ ЗАДАНИЯ</b>\n\n"
        for t in tasks:
            status = "✅" if t.is_active else "❌"
            txt += f"{status} Задание #{t.id}\n"
            txt += f"   Рассылка ID: {t.broadcast_id}\n"
            txt += f"   Время: {t.schedule_time.strftime('%d.%m.%Y %H:%M')}\n"
            txt += f"   Повтор: {t.repeat_type if t.repeat_type else 'Нет'}\n\n"
        await send_safe(uid, context.bot, txt, SCHEDULED_MENU)

    # ===== НАСТРОЙКИ =====
    elif data == 'set_lang':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🇷🇺 Русский", callback_data='lang_ru')],
            [InlineKeyboardButton("🇬🇧 English", callback_data='lang_en')],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data='settings')]
        ])
        await context.bot.send_message(uid, "🌐 Выберите язык / Choose language:", reply_markup=kb)

    elif data.startswith('lang_'):
        lang = data.split('_')[1]
        user_data[uid]['settings']['language'] = lang
        save_data()
        await send_safe(uid, context.bot, f"🌐 Язык: {'Русский' if lang == 'ru' else 'English'}", SETTINGS_MENU)

    elif data == 'toggle_notify':
        s = user_data[uid].get('settings', {})
        s['notify'] = not s.get('notify', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"🔔 Уведомления: {'ВКЛЮЧЕНЫ' if s['notify'] else 'ВЫКЛЮЧЕНЫ'}", SETTINGS_MENU)

    elif data == 'set_theme':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎨 СВЕТЛАЯ", callback_data='theme_light'), InlineKeyboardButton("🌙 ТЁМНАЯ", callback_data='theme_dark')],
            [InlineKeyboardButton("💙 СИНЯЯ", callback_data='theme_blue'), InlineKeyboardButton("💚 ЗЕЛЁНАЯ", callback_data='theme_green')],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data='settings')]
        ])
        await context.bot.send_message(uid, "🎨 Выберите тему оформления:", reply_markup=kb)

    elif data.startswith('theme_'):
        theme = data.split('_')[1]
        user_data[uid]['settings']['theme'] = theme
        save_data()
        await send_safe(uid, context.bot, f"🎨 Тема: {theme.upper()}", SETTINGS_MENU)

    elif data == 'toggle_autosave':
        s = user_data[uid].get('settings', {})
        s['autosave'] = not s.get('autosave', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"💾 Автосохранение: {'ВКЛЮЧЕНО' if s['autosave'] else 'ВЫКЛЮЧЕНО'}", SETTINGS_MENU)

    elif data == 'def_interval':
        user_states[uid] = {'step': 'def_interval'}
        await send_safe(uid, context.bot, f"⏱ Введите интервал по умолчанию ({MIN_INTERVAL}-{MAX_INTERVAL} сек):", CANCEL_BTN)

    elif data == 'toggle_sound':
        s = user_data[uid].get('settings', {})
        s['sound'] = not s.get('sound', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"🔊 Звук: {'ВКЛЮЧЕН' if s['sound'] else 'ВЫКЛЮЧЕН'}", SETTINGS_MENU)

    elif data == 'set_date_format':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📅 DD.MM.YYYY HH:MM", callback_data='date_1')],
            [InlineKeyboardButton("📅 YYYY-MM-DD HH:MM", callback_data='date_2')],
            [InlineKeyboardButton("📅 MM/DD/YYYY HH:MM", callback_data='date_3')],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data='settings')]
        ])
        await context.bot.send_message(uid, "📅 Выберите формат даты:", reply_markup=kb)

    elif data.startswith('date_'):
        formats = {
            '1': '%d.%m.%Y %H:%M',
            '2': '%Y-%m-%d %H:%M',
            '3': '%m/%d/%Y %H:%M'
        }
        fmt = formats.get(data.split('_')[1], '%d.%m.%Y %H:%M')
        user_data[uid]['settings']['date_format'] = fmt
        save_data()
        await send_safe(uid, context.bot, f"📅 Формат даты обновлён", SETTINGS_MENU)

    elif data == 'set_timezone':
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌍 UTC+0 (Лондон)", callback_data='tz_0')],
            [InlineKeyboardButton("🌍 UTC+3 (Москва)", callback_data='tz_3')],
            [InlineKeyboardButton("🌍 UTC+6 (Астана)", callback_data='tz_6')],
            [InlineKeyboardButton("🌍 UTC+10 (Владивосток)", callback_data='tz_10')],
            [InlineKeyboardButton("🔙 НАЗАД", callback_data='settings')]
        ])
        await context.bot.send_message(uid, "🌍 Выберите часовой пояс:", reply_markup=kb)

    elif data.startswith('tz_'):
        offset = int(data.split('_')[1])
        tz = f"UTC+{offset}" if offset >= 0 else f"UTC{offset}"
        user_data[uid]['settings']['timezone'] = tz
        save_data()
        await send_safe(uid, context.bot, f"🌍 Часовой пояс: {tz}", SETTINGS_MENU)

    # ===== ПОМОЩЬ =====
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой текст, группы, интервал\n3️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n4️⃣ Авторизуйся в Telegram\n\nГотово! Рассылка работает 24/7"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>Текст:</b> любое сообщение, до 4096 символов\n<b>Группы:</b> через запятую: @group1, @group2\n<b>Интервал:</b> время между сообщениями (5-300 сек)\n<b>Рандом:</b> случайная задержка\n<b>Зациклить:</b> бесконечный повтор\n<b>Медиа:</b> фото, видео, документы\n<b>Кнопки:</b> инлайн-кнопки с ссылками"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_modes':
        txt = "🔄 <b>РЕЖИМЫ РАБОТЫ</b>\n\n<b>24/7:</b> Бесконечная отправка по кругу\n<b>Разовый:</b> Одно сообщение во все группы\n<b>Расписание:</b> Запуск в указанное время\n<b>С зацикливанием:</b> Повторять N раз"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345\n<b>Медиа не отправляется:</b> проверь формат файла"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_tips':
        txt = "💡 <b>СОВЕТЫ</b>\n\n• Используйте спинтакс для уникализации текста\n• Добавляйте случайные эмодзи для живости\n• Сохраняйте частые сообщения в шаблоны\n• Регулярно делайте бэкапы\n• Следите за статистикой для оптимизации"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_faq':
        txt = "❓ <b>ЧАСТЫЕ ВОПРОСЫ</b>\n\n<b>Вопрос:</b> Сколько групп можно добавить?\n<b>Ответ:</b> До 50 групп на рассылку\n\n<b>Вопрос:</b> Можно ли отправлять фото?\n<b>Ответ:</b> Да, в разделе Медиа\n\n<b>Вопрос:</b> Что делать если бот не отвечает?\n<b>Ответ:</b> Напиши /start для перезапуска"
        await send_safe(uid, context.bot, txt, HELP_MENU)

    # ===== СТАТИСТИКА =====
    elif data == 'stats_by_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_safe(uid, context.bot, "📊 Нет рассылок", STATS_MENU)
            return

        txt = "📊 <b>СТАТИСТИКА ПО РАССЫЛКАМ</b>\n\n"
        for i, b in enumerate(broadcasts, 1):
            txt += f"{i}. {b.get('name', f'Рассылка {i}')}\n"
            txt += f"   📨 Отправлено: {b.get('sent', 0)}\n"
            txt += f"   ❌ Ошибок: {b.get('errors', 0)}\n"
            txt += f"   🟢 {'Активна' if b.get('active') else 'Остановлена'}\n\n"
        await send_safe(uid, context.bot, txt, STATS_MENU)

    elif data == 'stats_by_day':
        txt = "📅 <b>СТАТИСТИКА ПО ДНЯМ</b>\n\n"
        today = datetime.now().strftime('%d.%m.%Y')
        week_ago = (datetime.now() - timedelta(days=7)).strftime('%d.%m.%Y')
        txt += f"Сегодня ({today}):\n"
        txt += f"   📨 Отправлено: {daily_stats[uid].get('sent', 0)}\n"
        txt += f"   ❌ Ошибок: {daily_stats[uid].get('errors', 0)}\n\n"
        txt += f"За неделю ({week_ago} - {today}):\n"
        txt += f"   📨 Всего отправлено: {user_data[uid].get('total_sent', 0)}\n"
        txt += f"   ❌ Всего ошибок: {user_data[uid].get('total_errors', 0)}"
        await send_safe(uid, context.bot, txt, STATS_MENU)

    elif data == 'stats_top_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "🏆 Нет групп для статистики", STATS_MENU)
            return

        txt = "🏆 <b>ТОП ГРУПП</b>\n\n"
        for i, g in enumerate(groups[:10], 1):
            txt += f"{i}. {g}\n"
        await send_safe(uid, context.bot, txt, STATS_MENU)

    elif data == 'stats_efficiency':
        data_u = user_data[uid]
        total_sent = data_u.get('total_sent', 0)
        total_errors = data_u.get('total_errors', 0)
        success_rate = (total_sent - total_errors) / max(1, total_sent) * 100

        txt = "📈 <b>ЭФФЕКТИВНОСТЬ</b>\n\n"
        txt += f"✅ Успешность: {success_rate:.1f}%\n"
        txt += f"📨 Отправлено: {format_number(total_sent)}\n"
        txt += f"❌ Ошибок: {format_number(total_errors)}\n"
        txt += f"📊 Сообщений в день: {format_number(total_sent / max(1, (datetime.now() - datetime.strptime(data_u.get('created_at', str(datetime.now())), '%Y-%m-%d %H:%M:%S.%f').days)))}"
        await send_safe(uid, context.bot, txt, STATS_MENU)

    elif data == 'stats_errors':
        txt = "📉 <b>СТАТИСТИКА ОШИБОК</b>\n\n"
        txt += "❌ Общее количество ошибок:\n"
        for uid_tmp, data_tmp in user_data.items():
            if data_tmp.get('total_errors', 0) > 0:
                txt += f"   Пользователь {uid_tmp}: {data_tmp.get('total_errors', 0)}\n"
        await send_safe(uid, context.bot, txt, STATS_MENU)

    # ===== ПОДТВЕРЖДЕНИЯ =====
    elif data == 'confirm_yes':
        if user_states.get(uid, {}).get('step') == 'confirm_clear':
            user_data[uid] = {
                'broadcasts': [],
                'groups': [],
                'templates': [],
                'settings': user_data[uid].get('settings', {}),
                'created_at': str(datetime.now()),
                'total_sent': 0,
                'total_errors': 0
            }
            save_data()
            await send_safe(uid, context.bot, "✅ Все данные очищены!", MAIN_MENU)
            del user_states[uid]

    elif data == 'confirm_no':
        if user_states.get(uid, {}).get('step') == 'confirm_clear':
            await send_safe(uid, context.bot, "❌ Очистка отменена", MAIN_MENU)
            del user_states[uid]

    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Действие отменено")

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = update.message.text.strip() if update.message.text else None

    save_user(uid)

    state_data = user_states.get(uid, {})
    step = state_data.get('step')

    if not step:
        await main_menu(uid, context.bot)
        return

    # ===== СОЗДАНИЕ ШАБЛОНА =====
    if step == 'create_template_name':
        user_states[uid] = {'step': 'create_template_content', 'name': text}
        await send_safe(uid, context.bot, "📝 Введите текст шаблона:", CANCEL_BTN)

    elif step == 'create_template_content':
        name = state_data.get('name')
        templates = user_data[uid].get('templates', [])
        templates.append({
            'name': name,
            'content': text,
            'created_at': str(datetime.now()),
            'use_count': 0
        })
        user_data[uid]['templates'] = templates
        save_data()
        await send_safe(uid, context.bot, f"✅ Шаблон '{name}' создан!", TEMPLATES_MENU)
        del user_states[uid]

    # ===== ИМПОРТ =====
    elif step == 'waiting_import':
        if update.message.document:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            try:
                data = json.loads(content.decode('utf-8'))
                user_data[uid]['broadcasts'] = data.get('broadcasts', user_data[uid].get('broadcasts', []))
                user_data[uid]['groups'] = data.get('groups', user_data[uid].get('groups', []))
                user_data[uid]['templates'] = data.get('templates', user_data[uid].get('templates', []))
                user_data[uid]['settings'] = data.get('settings', user_data[uid].get('settings', {}))
                save_data()
                await send_safe(uid, context.bot, "✅ Импорт успешно завершён!", MAIN_MENU)
            except Exception as e:
                await send_safe(uid, context.bot, f"❌ Ошибка импорта: {str(e)[:100]}", MAIN_MENU)
        else:
            await send_safe(uid, context.bot, "❌ Отправьте JSON файл", CANCEL_BTN)
        del user_states[uid]

    # ===== ИМПОРТ ГРУПП =====
    elif step == 'import_groups':
        if update.message.document:
            file = await update.message.document.get_file()
            content = await file.download_as_bytearray()
            new_groups = content.decode('utf-8').strip().split('\n')
            groups = user_data[uid].get('groups', [])
            for g in new_groups:
                g = g.strip()
                if g and g not in groups:
                    groups.append(g)
            user_data[uid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Импортировано {len(new_groups)} групп\nВсего групп: {len(groups)}", GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, "❌ Отправьте текстовый файл", CANCEL_BTN)
        del user_states[uid]

    # ===== ОТЗЫВ =====
    elif step == 'waiting_feedback':
        try:
            await context.bot.send_message(ADMIN_ID, f"📝 <b>НОВЫЙ ОТЗЫВ</b>\n\nОт: {update.effective_user.first_name} (ID: {uid})\n\n{text}", parse_mode='HTML')
            await send_safe(uid, context.bot, "✅ Спасибо за отзыв!", MAIN_MENU)
        except:
            await send_safe(uid, context.bot, "❌ Ошибка отправки", MAIN_MENU)
        del user_states[uid]

    # ===== РЕПОРТ =====
    elif step == 'waiting_report':
        try:
            await context.bot.send_message(ADMIN_ID, f"🐛 <b>НОВЫЙ РЕПОРТ</b>\n\nОт: {update.effective_user.first_name} (ID: {uid})\n\n{text}", parse_mode='HTML')
            await send_safe(uid, context.bot, "✅ Сообщение об ошибке отправлено!", MAIN_MENU)
        except:
            await send_safe(uid, context.bot, "❌ Ошибка отправки", MAIN_MENU)
        del user_states[uid]

    # ===== НАСТРОЙКА ИНТЕРВАЛА ПО УМОЛЧАНИЮ =====
    elif step == 'def_interval':
        try:
            interval = int(text)
            if MIN_INTERVAL <= interval <= MAX_INTERVAL:
                user_data[uid]['settings']['def_interval'] = interval
                save_data()
                await send_safe(uid, context.bot, f"✅ Интервал по умолчанию: {interval} сек", SETTINGS_MENU)
            else:
                await send_safe(uid, context.bot, f"❌ От {MIN_INTERVAL} до {MAX_INTERVAL}", CANCEL_BTN)
                return
        except:
            await send_safe(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]

    # ===== ДОБАВЛЕНИЕ ГРУППЫ =====
    elif step == 'add_group':
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
            await send_safe(uid, context.bot, "❌ Неверный формат группы", CANCEL_BTN)
            return
        del user_states[uid]

    # ===== РЕДАКТИРОВАНИЕ ТЕКСТА =====
    elif step == 'edit_text':
        bid = state_data.get('bid')
        if len(text) > 4096:
            await send_safe(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_safe(uid, context.bot, "✅ Текст сохранён!")
        del user_states[uid]
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ ГРУПП =====
    elif step == 'edit_groups':
        bid = state_data.get('bid')
        raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in raw:
            valid, group = validate_group_link(g)
            if valid:
                groups.append(group)
        if groups:
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Сохранено {len(groups)} групп!")
        else:
            await send_safe(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ ИНТЕРВАЛА =====
    elif step == 'edit_interval':
        bid = state_data.get('bid')
        try:
            interval = int(text)
            if MIN_INTERVAL <= interval <= MAX_INTERVAL:
                user_data[uid]['broadcasts'][bid]['interval'] = interval
                save_data()
                await send_safe(uid, context.bot, f"✅ Интервал: {interval} сек")
            else:
                await send_safe(uid, context.bot, f"❌ От {MIN_INTERVAL} до {MAX_INTERVAL}", CANCEL_BTN)
                return
        except:
            await send_safe(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ РАНДОМА =====
    elif step == 'edit_random':
        bid = state_data.get('bid')
        if text == '0':
            user_data[uid]['broadcasts'][bid]['random_min'] = 0
            user_data[uid]['broadcasts'][bid]['random_max'] = 0
            save_data()
            await send_safe(uid, context.bot, "✅ Рандом отключён")
            del user_states[uid]
            await button_handler(update, context)
            return

        match = re.match(r'(\d+)-(\d+)', text)
        if match:
            min_val = int(match.group(1))
            max_val = int(match.group(2))
            if 0 <= min_val < max_val <= MAX_INTERVAL:
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
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ РАСПИСАНИЯ =====
    elif step == 'edit_schedule':
        bid = state_data.get('bid')
        if text.lower() == 'off':
            user_data[uid]['broadcasts'][bid]['schedule'] = None
            save_data()
            await send_safe(uid, context.bot, "✅ Расписание отключено")
            del user_states[uid]
            await button_handler(update, context)
            return

        valid, schedule = validate_time(text)
        if valid:
            user_data[uid]['broadcasts'][bid]['schedule'] = schedule
            save_data()
            await send_safe(uid, context.bot, f"✅ Расписание: {schedule}")
        else:
            await send_safe(uid, context.bot, "❌ Формат: 14:30", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ КНОПОК =====
    elif step == 'edit_buttons':
        bid = state_data.get('bid')
        if text.lower() == 'off':
            user_data[uid]['broadcasts'][bid]['buttons'] = []
            save_data()
            await send_safe(uid, context.bot, "✅ Кнопки удалены")
            del user_states[uid]
            await button_handler(update, context)
            return

        buttons = []
        for line in text.split('\n'):
            if '|' in line:
                label, url = line.split('|', 1)
                buttons.append({'text': label.strip(), 'url': url.strip()})
        if buttons:
            user_data[uid]['broadcasts'][bid]['buttons'] = buttons
            save_data()
            await send_safe(uid, context.bot, f"✅ Добавлено {len(buttons)} кнопок")
        else:
            await send_safe(uid, context.bot, "❌ Неверный формат кнопок", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    # ===== РЕДАКТИРОВАНИЕ ЭМОДЗИ =====
    elif step == 'edit_emojis':
        bid = state_data.get('bid')
        emojis = [e.strip() for e in text.split(',') if e.strip()]
        if emojis:
            user_data[uid]['broadcasts'][bid]['use_emoji_randomizer'] = True
            user_data[uid]['broadcasts'][bid]['emojis'] = emojis
            save_data()
            await send_safe(uid, context.bot, f"✅ Добавлено {len(emojis)} эмодзи")
        else:
            user_data[uid]['broadcasts'][bid]['use_emoji_randomizer'] = False
            user_data[uid]['broadcasts'][bid]['emojis'] = []
            save_data()
            await send_safe(uid, context.bot, "✅ Эмодзи отключены")
        del user_states[uid]
        await button_handler(update, context)

    # ===== МЕДИА =====
    elif step == 'waiting_photo':
        bid = state_data.get('bid')
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            user_data[uid]['broadcasts'][bid]['message_type'] = 'photo'
            user_data[uid]['broadcasts'][bid]['media_file_id'] = file_id
            user_data[uid]['broadcasts'][bid]['caption'] = update.message.caption
            save_data()
            await send_safe(uid, context.bot, "✅ Фото добавлено!")
        else:
            await send_safe(uid, context.bot, "❌ Отправьте фото", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    elif step == 'waiting_video':
        bid = state_data.get('bid')
        if update.message.video:
            file_id = update.message.video.file_id
            user_data[uid]['broadcasts'][bid]['message_type'] = 'video'
            user_data[uid]['broadcasts'][bid]['media_file_id'] = file_id
            user_data[uid]['broadcasts'][bid]['caption'] = update.message.caption
            save_data()
            await send_safe(uid, context.bot, "✅ Видео добавлено!")
        else:
            await send_safe(uid, context.bot, "❌ Отправьте видео", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    elif step == 'waiting_document':
        bid = state_data.get('bid')
        if update.message.document:
            file_id = update.message.document.file_id
            user_data[uid]['broadcasts'][bid]['message_type'] = 'document'
            user_data[uid]['broadcasts'][bid]['media_file_id'] = file_id
            user_data[uid]['broadcasts'][bid]['caption'] = update.message.caption
            save_data()
            await send_safe(uid, context.bot, "✅ Документ добавлен!")
        else:
            await send_safe(uid, context.bot, "❌ Отправьте документ", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)

    # ===== РАСПИСАНИЕ ЗАДАНИЯ =====
    elif step == 'sched_time':
        bid = state_data.get('bid')
        times = [t.strip() for t in text.split(',')]

        for t in times:
            valid, schedule = validate_time(t)
            if valid:
                task_id = len(scheduled_tasks[uid]) + 1
                task = ScheduledTask(
                    id=task_id,
                    broadcast_id=bid,
                    schedule_time=datetime.now().replace(hour=int(schedule[:2]), minute=int(schedule[3:]), second=0),
                    repeat_type='daily',
                    repeat_interval=1,
                    is_active=True
                )
                scheduled_tasks[uid].append(task)
                await send_safe(uid, context.bot, f"✅ Добавлено задание на {schedule}")
            else:
                await send_safe(uid, context.bot, f"❌ Неверное время: {t}", CANCEL_BTN)
                return

        del user_states[uid]
        await send_safe(uid, context.bot, "✅ Задания добавлены!", SCHEDULED_MENU)

# ==================== АВТОРИЗАЦИЯ И ЗАПУСК ====================
async def auth_and_start(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state_data: dict):
    uid = update.effective_user.id
    bid = state_data.get('bid')
    is_247 = state_data.get('step') == 'start_247'

    valid, phone = validate_phone(text)
    if not valid:
        await send_safe(uid, context.bot, f"❌ {phone}", CANCEL_BTN)
        return

    if uid in sessions:
        try:
            await sessions[uid].disconnect()
        except:
            pass

    user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'is_247': is_247, 'phone': phone}

    client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
    sessions[uid] = client

    try:
        await client.connect()
        await client.send_code_request(phone)
        await send_safe(uid, context.bot, "📲 Введите код из Telegram:\n\nФормат: code12345", CANCEL_BTN)
    except Exception as e:
        await send_safe(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
        del user_states[uid]

async def handle_code(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state_data: dict):
    uid = update.effective_user.id
    bid = state_data.get('bid')
    is_247 = state_data.get('is_247')
    phone = state_data.get('phone')

    match = re.search(r'(\d{5,6})', text)
    code = match.group(1) if match else None
    if not code:
        await send_safe(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
        return

    user_states[uid] = {'step': 'waiting_2fa', 'bid': bid, 'is_247': is_247, 'phone': phone, 'code': code}
    await send_safe(uid, context.bot, "🔐 Введите пароль 2FA (если есть)\n\nЕсли нет - отправьте /skip", CANCEL_BTN)

async def handle_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str, state_data: dict):
    uid = update.effective_user.id
    password = None if text == '/skip' else text
    client = sessions.get(uid)

    if not client:
        await send_safe(uid, context.bot, "❌ Ошибка сессии, начните /start", MAIN_MENU)
        del user_states[uid]
        return

    bid = state_data.get('bid')
    is_247 = state_data.get('is_247')
    phone = state_data.get('phone')
    code = state_data.get('code')

    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    msg = bc.get('text', '')
    interval = bc.get('interval', 30)
    random_min = bc.get('random_min', 0)
    random_max = bc.get('random_max', 0)
    message_type = bc.get('message_type', 'text')
    media_file_id = bc.get('media_file_id')
    caption = bc.get('caption', msg)
    buttons = bc.get('buttons', [])
    use_spintax = bc.get('use_spintax', False)
    use_emoji_randomizer = bc.get('use_emoji_randomizer', False)
    emojis = bc.get('emojis', [])

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

    # Проверка групп
    valid_groups = []
    for group in groups:
        try:
            await client.get_entity(group)
            valid_groups.append(group)
        except:
            await send_safe(uid, context.bot, f"⚠️ {group} - недоступна")

    if not valid_groups:
        await send_safe(uid, context.bot, "❌ Нет доступных групп!", MAIN_MENU)
        del user_states[uid]
        return

    user_data[uid]['broadcasts'][bid]['groups'] = valid_groups
    save_data()

    if is_247:
        await send_safe(uid, context.bot, f"🚀 ЗАПУСК 24/7\n\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{' (рандом ' + str(random_min) + '-' + str(random_max) + ')' if random_min else ''}\n📎 Тип: {message_type}\n{'🔘 Кнопок: ' + str(len(buttons)) if buttons else ''}", MAIN_MENU)
        task = asyncio.create        task = asyncio.create_task(run_broadcast_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max, message_type, media_file_id, caption, buttons, use_spintax, use_emoji_randomizer, emojis))
        active_tasks[f"{uid}_{bid}"] = task
        user_data[uid]['broadcasts'][bid]['active'] = True
        user_data[uid]['broadcasts'][bid]['start_time'] = str(datetime.now())
    else:
        await send_safe(uid, context.bot, f"📤 ОТПРАВКА РАЗОМ\n\n👥 Групп: {len(valid_groups)}", MAIN_MENU)
        success = 0
        for group in valid_groups:
            try:
                await send_message_with_media(client, group, msg, message_type, media_file_id, caption, buttons, use_spintax, use_emoji_randomizer, emojis)
                success += 1
                user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                daily_stats[uid]['sent'] = daily_stats[uid].get('sent', 0) + 1
                await asyncio.sleep(2)
            except Exception as e:
                user_data[uid]['total_errors'] = user_data[uid].get('total_errors', 0) + 1
                daily_stats[uid]['errors'] = daily_stats[uid].get('errors', 0) + 1
        save_data()
        await send_safe(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)
        await client.disconnect()
        if uid in sessions:
            del sessions[uid]

    save_data()
    del user_states[uid]

async def send_message_with_media(client, group, text, msg_type, media_file_id, caption, buttons, use_spintax, use_emoji_randomizer, emojis):
    final_text = text
    if use_spintax:
        final_text = generate_spintax(final_text)
    if use_emoji_randomizer and emojis:
        final_text = randomize_emojis(final_text, emojis)

    reply_markup = None
    if buttons:
        from telethon.tl.custom import Button
        reply_markup = [Button.url(b['text'], b['url']) for b in buttons]

    if msg_type == 'photo' and media_file_id:
        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
    elif msg_type == 'video' and media_file_id:
        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
    elif msg_type == 'document' and media_file_id:
        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
    else:
        await client.send_message(group, final_text, buttons=reply_markup)

async def run_broadcast_247(uid, bid, client, groups, text, interval, random_min, random_max, msg_type, media_file_id, caption, buttons, use_spintax, use_emoji_randomizer, emojis):
    sent = 0
    round_num = 0
    start_time = time.time()

    try:
        while True:
            round_num += 1
            user_data[uid]['broadcasts'][bid]['current_round'] = round_num

            for group in groups:
                try:
                    final_text = text
                    if use_spintax:
                        final_text = generate_spintax(final_text)
                    if use_emoji_randomizer and emojis:
                        final_text = randomize_emojis(final_text, emojis)

                    reply_markup = None
                    if buttons:
                        from telethon.tl.custom import Button
                        reply_markup = [Button.url(b['text'], b['url']) for b in buttons]

                    if msg_type == 'photo' and media_file_id:
                        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
                    elif msg_type == 'video' and media_file_id:
                        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
                    elif msg_type == 'document' and media_file_id:
                        await client.send_file(group, media_file_id, caption=caption or final_text, buttons=reply_markup)
                    else:
                        await client.send_message(group, final_text, buttons=reply_markup)

                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                    daily_stats[uid]['sent'] = daily_stats[uid].get('sent', 0) + 1

                    stats = broadcast_stats[uid][bid]
                    stats.total_sent = sent
                    stats.success_rate = (sent / max(1, sent + user_data[uid]['broadcasts'][bid].get('errors', 0))) * 100
                    stats.active_time = int(time.time() - start_time)
                    stats.messages_per_minute = sent / max(1, stats.active_time / 60)
                    stats.groups_count = len(groups)
                    stats.rounds_completed = round_num - 1

                    save_data()

                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    user_data[uid]['broadcasts'][bid]['errors'] = user_data[uid]['broadcasts'][bid].get('errors', 0) + 1
                    user_data[uid]['total_errors'] = user_data[uid].get('total_errors', 0) + 1
                    daily_stats[uid]['errors'] = daily_stats[uid].get('errors', 0) + 1
                    save_data()

                delay = interval
                if random_min and random_max:
                    delay = random.randint(random_min, random_max)
                await asyncio.sleep(delay)

    except asyncio.CancelledError:
        user_data[uid]['broadcasts'][bid]['active'] = False
        save_data()
        try:
            await client.disconnect()
        except:
            pass
        if uid in sessions:
            del sessions[uid]

# ==================== ПРОВЕРКА РАСПИСАНИЯ ====================
async def check_scheduled_tasks():
    while True:
        try:
            for uid, tasks in scheduled_tasks.items():
                now = datetime.now()
                for task in tasks:
                    if task.is_active and task.schedule_time <= now:
                        bid = task.broadcast_id
                        if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                            bc = user_data[uid]['broadcasts'][bid]
                            if not bc.get('active'):
                                # Запускаем рассылку по расписанию
                                if uid in sessions:
                                    client = sessions.get(uid)
                                    if client:
                                        groups = bc.get('groups', [])
                                        msg = bc.get('text', '')
                                        interval = bc.get('interval', 30)
                                        random_min = bc.get('random_min', 0)
                                        random_max = bc.get('random_max', 0)
                                        msg_type = bc.get('message_type', 'text')
                                        media_file_id = bc.get('media_file_id')
                                        caption = bc.get('caption', msg)
                                        buttons = bc.get('buttons', [])
                                        use_spintax = bc.get('use_spintax', False)
                                        use_emoji_randomizer = bc.get('use_emoji_randomizer', False)
                                        emojis = bc.get('emojis', [])

                                        if groups:
                                            task_key = f"{uid}_{bid}"
                                            if task_key not in active_tasks:
                                                task_obj = asyncio.create_task(run_broadcast_247(uid, bid, client, groups, msg, interval, random_min, random_max, msg_type, media_file_id, caption, buttons, use_spintax, use_emoji_randomizer, emojis))
                                                active_tasks[task_key] = task_obj
                                                user_data[uid]['broadcasts'][bid]['active'] = True
                                                save_data()

                                # Обновляем время следующего запуска
                                if task.repeat_type == 'daily':
                                    task.schedule_time += timedelta(days=task.repeat_interval)
                                elif task.repeat_type == 'hourly':
                                    task.schedule_time += timedelta(hours=task.repeat_interval)
                                task.last_run = now
        except Exception as e:
            logger.error(f"Ошибка проверки расписания: {e}")
        await asyncio.sleep(60)

# ==================== АВТОСОХРАНЕНИЕ ====================
async def auto_save_loop():
    while True:
        await asyncio.sleep(300)  # Каждые 5 минут
        for uid, data in user_data.items():
            if data.get('settings', {}).get('autosave', True):
                save_data()

# ==================== ЗАПУСК БОТА ====================
def main():
    load_data()

    app = Application.builder().token(BOT_TOKEN).build()

    # Команды
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("groups", groups_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    app.add_handler(CommandHandler("import", import_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("feedback", feedback_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("donate", donate_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))

    # Обработчики
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.PHOTO, message_handler))
    app.add_handler(MessageHandler(filters.VIDEO, message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, message_handler))

    print("=" * 70)
    print("🥕 SENDFLOW PRO v4.0 - ЗАПУЩЕН")
    print("=" * 70)
    print("📌 ВСЕ КНОПКИ РАБОТАЮТ")
    print("📌 24/7 РАССЫЛКА")
    print("📌 ПОДДЕРЖКА МЕДИА")
    print("📌 ПОДДЕРЖКА КНОПОК")
    print("📌 СПИНТАКС И ЭМОДЗИ")
    print("📌 РАСПИСАНИЕ")
    print("📌 ШАБЛОНЫ")
    print("=" * 70)

    # Запускаем фоновые задачи
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(check_scheduled_tasks())
    loop.create_task(auto_save_loop())

    app.run_polling()

if __name__ == '__main__':
    main()
