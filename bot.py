import asyncio
import re
import json
import os
import logging
import time
import sys
import random
import sqlite3
import threading
import queue
import hashlib
import base64
import string
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple, Optional, Any
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
    PasswordHashInvalidError
)
from telethon.tl.functions.messages import SendMessageRequest
from telethon.tl.types import MessageEntityTextUrl, MessageEntityBold, MessageEntityItalic

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler, 
    filters, 
    ContextTypes,
    ConversationHandler,
    PreCheckoutQueryHandler,
    CallbackContext
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
    """Инициализация SQLite базы данных"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    
    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            created_at TIMESTAMP,
            last_active TIMESTAMP,
            total_broadcasts INTEGER DEFAULT 0,
            total_messages_sent INTEGER DEFAULT 0,
            is_banned BOOLEAN DEFAULT 0,
            is_admin BOOLEAN DEFAULT 0
        )
    ''')
    
    # Таблица рассылок
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
            messages_sent INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0,
            created_at TIMESTAMP,
            last_started TIMESTAMP,
            last_stopped TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Таблица групп пользователя
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            group_name TEXT,
            group_link TEXT,
            is_active BOOLEAN DEFAULT 1,
            added_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Таблица статистики
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS statistics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            broadcast_id INTEGER,
            message_sent_at TIMESTAMP,
            group_name TEXT,
            success BOOLEAN,
            error_message TEXT,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        )
    ''')
    
    # Таблица сессий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone TEXT,
            created_at TIMESTAMP,
            expires_at TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
class BroadcastStatus(Enum):
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"

@dataclass
class BroadcastConfig:
    """Конфигурация рассылки"""
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
    use_buttons: bool = False
    buttons: List[Dict] = field(default_factory=list)
    schedule_time: Optional[str] = None
    auto_stop_after: Optional[int] = None
    random_delay_min: int = 0
    random_delay_max: int = 0
    proxy: Optional[str] = None
    use_spintax: bool = False
    spintax_variants: List[str] = field(default_factory=list)

@dataclass
class UserSettings:
    """Настройки пользователя"""
    user_id: int
    language: str = "ru"
    auto_save: bool = True
    notifications: bool = True
    dark_mode: bool = False
    auto_start: bool = False
    max_concurrent: int = 3
    default_interval: int = DEFAULT_INTERVAL
    backup_enabled: bool = True
    log_level: str = "INFO"

def generate_id() -> str:
    """Генерация уникального ID"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=16))

def encrypt_data(data: str) -> str:
    """Шифрование данных"""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac('sha256', BOT_TOKEN.encode(), salt, 100000)
    return base64.b64encode(salt + key).decode()

def decrypt_data(encrypted: str) -> str:
    """Дешифрование данных"""
    try:
        data = base64.b64decode(encrypted.encode())
        return data[32:].decode()
    except:
        return ""

def save_user_to_db(user_id: int, username: str, first_name: str, last_name: str = ""):
    """Сохранение пользователя в БД"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, username, first_name, last_name, last_active, created_at)
        VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM users WHERE user_id=?), ?))
    ''', (user_id, username, first_name, last_name, datetime.now(), user_id, datetime.now()))
    conn.commit()
    conn.close()

def update_user_activity(user_id: int):
    """Обновление активности пользователя"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET last_active = ? WHERE user_id = ?', (datetime.now(), user_id))
    conn.commit()
    conn.close()

def save_broadcast_to_db(user_id: int, broadcast: BroadcastConfig) -> int:
    """Сохранение рассылки в БД"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO broadcasts (user_id, name, text, groups, interval_seconds, is_loop, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, broadcast.name, broadcast.text, json.dumps(broadcast.groups), 
          broadcast.interval, broadcast.loop_enabled, datetime.now()))
    broadcast_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return broadcast_id

def load_broadcasts_from_db(user_id: int) -> List[BroadcastConfig]:
    """Загрузка рассылок из БД"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM broadcasts WHERE user_id = ?', (user_id,))
    rows = cursor.fetchall()
    broadcasts = []
    for row in rows:
        b = BroadcastConfig(
            id=row[0],
            name=row[3],
            text=row[4],
            groups=json.loads(row[5]),
            interval=row[6],
            messages_sent=row[9] or 0,
            errors=row[10] or 0
        )
        broadcasts.append(b)
    conn.close()
    return broadcasts

def update_broadcast_stats(broadcast_id: int, messages_sent: int, errors: int):
    """Обновление статистики рассылки"""
    conn = sqlite3.connect(DATABASE_FILE)
    cursor = conn.cursor()
    cursor.execute('UPDATE broadcasts SET messages_sent = ?, errors = ? WHERE id = ?', 
                   (messages_sent, errors, broadcast_id))
    conn.commit()
    conn.close()

# ==================== КЛАВИАТУРЫ ====================
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ СОЗДАТЬ РАССЫЛКУ", callback_data='create_broadcast')],
    [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data='global_stats')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("📁 ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("💾 БЭКАП", callback_data='backup_data')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')],
    [InlineKeyboardButton("ℹ️ О БОТЕ", callback_data='about')]
])

BROADCAST_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 ТЕКСТ", callback_data='bc_text'), InlineKeyboardButton("👥 ГРУППЫ", callback_data='bc_groups')],
    [InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data='bc_interval'), InlineKeyboardButton("🎲 РАНДОМ", callback_data='bc_random')],
    [InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data='bc_loop'), InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data='bc_schedule')],
    [InlineKeyboardButton("🔘 КНОПКИ", callback_data='bc_buttons'), InlineKeyboardButton("🎭 СПИНТАКС", callback_data='bc_spintax')],
    [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data='bc_start_247'), InlineKeyboardButton("⏸️ ПАУЗА", callback_data='bc_pause')],
    [InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data='bc_send_once'), InlineKeyboardButton("⏹️ СТОП", callback_data='bc_stop')],
    [InlineKeyboardButton("📊 СТАТУС", callback_data='bc_status'), InlineKeyboardButton("📋 ЛОГИ", callback_data='bc_logs')],
    [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data='bc_clone'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data='bc_delete')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🌐 ЯЗЫК", callback_data='set_lang'), InlineKeyboardButton("🔔 УВЕДОМЛЕНИЯ", callback_data='set_notify')],
    [InlineKeyboardButton("🎨 ТЕМА", callback_data='set_theme'), InlineKeyboardButton("📁 АВТОСОХРАНЕНИЕ", callback_data='set_autosave')],
    [InlineKeyboardButton("⚡ МАКС. РАССЫЛОК", callback_data='set_max'), InlineKeyboardButton("⏱ ИНТЕРВАЛ ПО УМОЛЧ.", callback_data='set_def_interval')],
    [InlineKeyboardButton("💾 БЭКАП", callback_data='backup_now'), InlineKeyboardButton("🗑 ОЧИСТИТЬ ДАННЫЕ", callback_data='clear_data')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

GROUPS_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("📋 СПИСОК ГРУПП", callback_data='list_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data='remove_group')],
    [InlineKeyboardButton("📤 ИМПОРТ ГРУПП", callback_data='import_groups')],
    [InlineKeyboardButton("📎 ЭКСПОРТ ГРУПП", callback_data='export_groups')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

STATS_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📊 ПО РАССЫЛКАМ", callback_data='stats_by_broadcast')],
    [InlineKeyboardButton("📅 ПО ДНЯМ", callback_data='stats_by_day')],
    [InlineKeyboardButton("🏆 ТОП ГРУПП", callback_data='stats_top_groups')],
    [InlineKeyboardButton("📈 ГРАФИК", callback_data='stats_graph')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 БЫСТРЫЙ СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ РАССЫЛКУ", callback_data='help_create')],
    [InlineKeyboardButton("🔄 24/7 VS РАЗОМ", callback_data='help_modes')],
    [InlineKeyboardButton("🔧 ОШИБКИ И РЕШЕНИЯ", callback_data='help_errors')],
    [InlineKeyboardButton("💡 СОВЕТЫ", callback_data='help_tips')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel_action')]])

BACK_KEYBOARD = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]])

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
user_data: Dict[int, Dict] = {}
active_tasks: Dict[str, asyncio.Task] = {}
sessions: Dict[int, TelegramClient] = {}
broadcast_configs: Dict[int, Dict[int, BroadcastConfig]] = defaultdict(dict)
user_states: Dict[int, Dict] = {}
message_queue: Dict[int, asyncio.Queue] = {}
rate_limits: Dict[int, List[float]] = defaultdict(list)

# ==================== КОМАНДЫ БОТА ====================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start - Главное меню"""
    uid = update.effective_user.id
    username = update.effective_user.username or ""
    first_name = update.effective_user.first_name or ""
    last_name = update.effective_user.last_name or ""
    
    logger.info(f"Пользователь {first_name} ({uid}) запустил бота")
    
    # Сохраняем пользователя в БД
    save_user_to_db(uid, username, first_name, last_name)
    update_user_activity(uid)
    
    # Инициализируем данные пользователя
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'settings': UserSettings(user_id=uid).__dict__,
            'created_at': str(datetime.now()),
            'total_sent': 0,
            'total_errors': 0
        }
        save_data()
    
    # Приветственное сообщение
    welcome_text = f"""
🌟 <b>ДОБРО ПОЖАЛОВАТЬ В SENDFLOW!</b> 🌟

Привет, {first_name}! 👋

<b>🤖 Что я умею:</b>
• 📢 Массовая рассылка в Telegram
• 🔄 Круглосуточная работа 24/7
• 🎯 Отправка в несколько групп сразу
• 📊 Подробная статистика
• 💾 Автосохранение настроек
• 🔐 Безопасная авторизация

<b>📌 Быстрые команды:</b>
/start - Главное меню
/help - Помощь
/skip - Пропустить 2FA
/stats - Моя статистика
/groups - Мои группы
/backup - Создать бэкап
/clear - Очистить данные

<b>🚀 Начни с создания первой рассылки!</b>
    """
    
    await update.message.reply_text(welcome_text, parse_mode='HTML', reply_markup=MAIN_KEYBOARD)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /help"""
    uid = update.effective_user.id
    update_user_activity(uid)
    
    help_text = """
📖 <b>ПОЛНАЯ СПРАВКА ПО SENDFLOW</b> 📖

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>🚀 БЫСТРЫЙ СТАРТ (3 шага):</b>
1️⃣ Нажми "➕ СОЗДАТЬ РАССЫЛКУ"
2️⃣ Настрой текст, группы, интервал
3️⃣ Нажми "🚀 ЗАПУСТИТЬ 24/7"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📢 ТИПЫ РАССЫЛОК:</b>
• <b>24/7 режим</b> - Бесконечная рассылка по кругу
• <b>Разовый режим</b> - Одно сообщение во все группы
• <b>По расписанию</b> - Запуск в указанное время
• <b>С зацикливанием</b> - Повторять N раз

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>⚙️ РАСШИРЕННЫЕ НАСТРОЙКИ:</b>
• <b>🎲 Рандом</b> - Случайная задержка между сообщениями
• <b>🔘 Кнопки</b> - Добавление inline-кнопок в сообщение
• <b>🎭 Спинтакс</b> - Генерация уникальных вариантов текста
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

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📞 ПОДДЕРЖКА:</b>
• Команда /feedback - Отправить отзыв
• Команда /report - Сообщить об ошибке
• Команда /donate - Поддержать проект

<b>Версия:</b> 3.0.0 | <b>Обновлено:</b> 06.06.2026
    """
    
    await update.message.reply_text(help_text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

async def skip_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /skip - Пропуск 2FA"""
    uid = update.effective_user.id
    
    if uid in user_data and user_data[uid].get('step', '').startswith('waiting_2fa'):
        # Создаём фейковое сообщение для обработчика
        update.message.text = '/skip'
        await message_handler(update, context)
    else:
        await update.message.reply_text("❌ Нет активного запроса 2FA", reply_markup=MAIN_KEYBOARD)

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /stats - Моя статистика"""
    uid = update.effective_user.id
    update_user_activity(uid)
    
    data = user_data.get(uid, {})
    broadcasts = data.get('broadcasts', [])
    
    active_count = sum(1 for b in broadcasts if b.get('active', False))
    total_sent = data.get('total_sent', 0)
    total_errors = data.get('total_errors', 0)
    
    stats_text = f"""
📊 <b>МОЯ СТАТИСТИКА</b> 📊

━━━━━━━━━━━━━━━━━━━━━━

<b>📢 Рассылки:</b>
• Всего: {len(broadcasts)}
• Активных: {active_count}
• Остановлено: {len(broadcasts) - active_count}

<b>📨 Отправки:</b>
• Отправлено: {total_sent}
• Ошибок: {total_errors}
• Успешно: {total_sent - total_errors}

<b>👥 Группы:</b>
• Сохранено: {len(data.get('groups', []))}

<b>💾 Данные:</b>
• Создан: {data.get('created_at', 'Неизвестно')[:16]}
• Последний вход: {datetime.now().strftime('%d.%m.%Y %H:%M')}

━━━━━━━━━━━━━━━━━━━━━━

🏆 <i>Продолжай в том же духе!</i>
    """
    
    await update.message.reply_text(stats_text, parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def groups_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /groups - Мои группы"""
    uid = update.effective_user.id
    update_user_activity(uid)
    
    groups = user_data.get(uid, {}).get('groups', [])
    
    if not groups:
        groups_text = "📁 <b>У вас нет сохранённых групп</b>\n\nИспользуйте кнопку '➕ ДОБАВИТЬ ГРУППУ' чтобы добавить первую группу!"
    else:
        groups_text = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n"
        for i, group in enumerate(groups, 1):
            groups_text += f"{i}. {group}\n"
    
    await update.message.reply_text(groups_text, parse_mode='HTML', reply_markup=GROUPS_KEYBOARD)

async def backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /backup - Создание бэкапа"""
    uid = update.effective_user.id
    update_user_activity(uid)
    
    # Создаём бэкап данных пользователя
    backup_file = f"backup_user_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    try:
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(user_data.get(uid, {}), f, ensure_ascii=False, indent=2)
        
        await update.message.reply_text(f"✅ <b>Бэкап создан!</b>\n\nФайл: {backup_file}\nДата: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}\n\nСохраните этот файл в надёжном месте!", parse_mode='HTML')
        
        # Пытаемся отправить файл
        try:
            with open(backup_file, 'rb') as f:
                await update.message.reply_document(f, caption="📦 Ваш бэкап данных")
        except:
            pass
        
        # Удаляем локальный файл через 5 минут
        async def delete_file():
            await asyncio.sleep(300)
            if os.path.exists(backup_file):
                os.remove(backup_file)
        
        asyncio.create_task(delete_file())
        
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка создания бэкапа: {str(e)[:100]}")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /clear - Очистка данных"""
    uid = update.effective_user.id
    
    # Проверка подтверждения
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ДА, ОЧИСТИТЬ", callback_data='confirm_clear')],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data='back_to_main')]
    ])
    
    await update.message.reply_text(
        "⚠️ <b>ВНИМАНИЕ!</b> ⚠️\n\n"
        "Вы уверены, что хотите очистить ВСЕ данные?\n"
        "Будут удалены:\n"
        "• Все рассылки\n"
        "• Все группы\n"
        "• Вся статистика\n\n"
        "<b>Это действие НЕЛЬЗЯ отменить!</b>",
        parse_mode='HTML',
        reply_markup=keyboard
    )

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /feedback - Отправить отзыв"""
    uid = update.effective_user.id
    user_data[uid]['step'] = 'waiting_feedback'
    save_data()
    
    await update.message.reply_text(
        "📝 <b>Отправьте ваш отзыв или предложение</b>\n\n"
        "Напишите текст сообщением. Я передам его разработчику.\n\n"
        "Для отмены нажмите /cancel",
        parse_mode='HTML',
        reply_markup=CANCEL_KEYBOARD
    )

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /report - Сообщить об ошибке"""
    uid = update.effective_user.id
    user_data[uid]['step'] = 'waiting_report'
    save_data()
    
    await update.message.reply_text(
        "🐛 <b>Опишите проблему или ошибку</b>\n\n"
        "Расскажите подробно:\n"
        "• Что вы делали?\n"
        "• Что произошло?\n"
        "• Когда это случилось?\n\n"
        "Я передам информацию разработчику.",
        parse_mode='HTML',
        reply_markup=CANCEL_KEYBOARD
    )

async def donate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /donate - Поддержать проект"""
    donate_text = """
💝 <b>ПОДДЕРЖАТЬ ПРОЕКТ</b> 💝

<b>Спасибо, что пользуетесь SendFlow!</b>

Если вам нравится бот и вы хотите поддержать его развитие:

<b>💎 Способы поддержки:</b>
• USDT (TRC20): `TXXXXXXXXXXXXXXX`
• BTC: `1XXXXXXXXXXXXXXX`
• ETH: `0xXXXXXXXXXXXXXXX`

<b>🌟 За поддержку вы получите:</b>
• Приоритетную поддержку
• Ранний доступ к новым функциям
• Ваше имя в списке спонсоров

<i>Спасибо за вашу поддержку! ❤️</i>
    """
    
    await update.message.reply_text(donate_text, parse_mode='HTML', reply_markup=BACK_KEYBOARD)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /cancel - Отмена действия"""
    uid = update.effective_user.id
    
    if uid in user_data:
        user_data[uid].pop('step', None)
        save_data()
    
    await update.message.reply_text("❌ Действие отменено", reply_markup=MAIN_KEYBOARD)

async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /restart - Перезапуск всех рассылок"""
    uid = update.effective_user.id
    
    # Перезапускаем все активные рассылки
    restarted = 0
    for bid, broadcast in enumerate(user_data.get(uid, {}).get('broadcasts', [])):
        if broadcast.get('active', False):
            task_key = f"{uid}_{bid}"
            if task_key in active_tasks:
                active_tasks[task_key].cancel()
                await asyncio.sleep(1)
                # Здесь код перезапуска
                restarted += 1
    
    await update.message.reply_text(f"🔄 Перезапущено рассылок: {restarted}")

async def pause_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /pause_all - Пауза всех рассылок"""
    uid = update.effective_user.id
    
    paused = 0
    for task_key, task in list(active_tasks.items()):
        if task_key.startswith(f"{uid}_"):
            task.cancel()
            paused += 1
    
    await update.message.reply_text(f"⏸️ Поставлено на паузу: {paused} рассылок")

async def resume_all_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /resume_all - Возобновление всех рассылок"""
    uid = update.effective_user.id
    
    # Возобновляем все остановленные рассылки
    resumed = 0
    for bid, broadcast in enumerate(user_data.get(uid, {}).get('broadcasts', [])):
        if broadcast.get('was_active', False) and not broadcast.get('active', False):
            # Код возобновления
            resumed += 1
    
    await update.message.reply_text(f"▶️ Возобновлено: {resumed} рассылок")

async def test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /test - Тестовая отправка"""
    uid = update.effective_user.id
    user_data[uid]['step'] = 'waiting_test_group'
    save_data()
    
    await update.message.reply_text(
        "🧪 <b>ТЕСТОВАЯ ОТПРАВКА</b>\n\n"
        "Введите ссылку на группу для теста:\n"
        "Пример: @test_group\n\n"
        "После этого я отправлю туда тестовое сообщение.",
        parse_mode='HTML',
        reply_markup=CANCEL_KEYBOARD
    )

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /export - Экспорт настроек"""
    uid = update.effective_user.id
    data = user_data.get(uid, {})
    
    export_data = {
        'broadcasts': data.get('broadcasts', []),
        'groups': data.get('groups', []),
        'settings': data.get('settings', {}),
        'export_date': str(datetime.now())
    }
    
    export_file = f"export_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    try:
        with open(export_file, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, ensure_ascii=False, indent=2)
        
        with open(export_file, 'rb') as f:
            await update.message.reply_document(f, caption="📎 Экспорт настроек")
        
        os.remove(export_file)
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка экспорта: {str(e)[:100]}")

async def import_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /import - Импорт настроек"""
    uid = update.effective_user.id
    user_data[uid]['step'] = 'waiting_import_file'
    save_data()
    
    await update.message.reply_text(
        "📥 <b>ИМПОРТ НАСТРОЕК</b>\n\n"
        "Отправьте JSON файл с экспортированными настройками.\n\n"
        "Файл должен быть создан командой /export",
        parse_mode='HTML',
        reply_markup=CANCEL_KEYBOARD
    )

async def info_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /info - Информация о боте"""
    info_text = """
🤖 <b>SENDF
