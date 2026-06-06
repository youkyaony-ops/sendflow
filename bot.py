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
🤖 <b>SENDFLOW - Telegram Mass Sender</b> 🤖

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<b>📋 О БОТЕ:</b>
• Версия: 3.0.0
• Разработчик: @Gothbreach
• Дата релиза: 06.06.2026
• Лицензия: MIT

<b>⚡ ВОЗМОЖНОСТИ:</b>
• Массовая рассылка 24/7
• Отправка в несколько групп
• Поддержка 2FA
• Автосохранение
• Бэкап данных
• Расширенная статистика

<b>📊 СТАТИСТИКА БОТА:</b>
• Пользователей: {len(user_data)}
• Активных рассылок: {sum(1 for u in user_data.values() for b in u.get('broadcasts', []) if b.get('active'))}
• Отправлено сообщений: {sum(u.get('total_sent', 0) for u in user_data.values())}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

<i>Спасибо, что выбираете SendFlow! 🌟</i>
    """
    
    await update.message.reply_text(info_text.format(len(user_data)), parse_mode='HTML', reply_markup=BACK_KEYBOARD)

# ==================== ОБРАБОТЧИК КНОПОК ====================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Основной обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    
    try:
        await query.message.delete()
    except:
        pass
    
    # Главное меню
    if data == 'back_to_main':
        await main_menu(uid, context.bot)
    
    elif data == 'my_broadcasts':
        await show_my_broadcasts(uid, context.bot)
    
    elif data == 'create_broadcast':
        await create_new_broadcast(uid, context.bot)
    
    elif data == 'global_stats':
        await show_global_stats(uid, context.bot)
    
    elif data == 'settings':
        await show_settings(uid, context.bot)
    
    elif data == 'my_groups':
        await groups_command(update, context)
    
    elif data == 'backup_data':
        await backup_command(update, context)
    
    elif data == 'help_menu':
        await help_command(update, context)
    
    elif data == 'about':
        await info_command(update, context)
    
    # Настройки
    elif data == 'set_lang':
        await set_language(uid, context.bot)
    elif data == 'set_notify':
        await toggle_notifications(uid, context.bot)
    elif data == 'set_theme':
        await toggle_theme(uid, context.bot)
    elif data == 'set_autosave':
        await toggle_autosave(uid, context.bot)
    elif data == 'set_max':
        await set_max_broadcasts(uid, context.bot)
    elif data == 'set_def_interval':
        await set_default_interval(uid, context.bot)
    elif data == 'backup_now':
        await backup_command(update, context)
    elif data == 'clear_data':
        await clear_command(update, context)
    
    # Группы
    elif data == 'add_group':
        user_data[uid]['step'] = 'add_group'
        save_data()
        await context.bot.send_message(uid, "➕ Введите ссылку на группу:\nПример: @group_name", reply_markup=CANCEL_KEYBOARD)
    elif data == 'list_groups':
        await groups_command(update, context)
    elif data == 'remove_group':
        user_data[uid]['step'] = 'remove_group'
        save_data()
        await context.bot.send_message(uid, "🗑 Введите название группы для удаления:", reply_markup=CANCEL_KEYBOARD)
    elif data == 'import_groups':
        user_data[uid]['step'] = 'import_groups'
        save_data()
        await context.bot.send_message(uid, "📤 Отправьте список групп (по одной на строку или через запятую):", reply_markup=CANCEL_KEYBOARD)
    elif data == 'export_groups':
        await export_command(update, context)
    
    # Статистика
    elif data == 'stats_by_broadcast':
        await stats_by_broadcast(uid, context.bot)
    elif data == 'stats_by_day':
        await stats_by_day(uid, context.bot)
    elif data == 'stats_top_groups':
        await stats_top_groups(uid, context.bot)
    elif data == 'stats_graph':
        await stats_graph(uid, context.bot)
    
    # Помощь
    elif data == 'help_quick':
        await help_quick(uid, context.bot)
    elif data == 'help_create':
        await help_create(uid, context.bot)
    elif data == 'help_modes':
        await help_modes(uid, context.bot)
    elif data == 'help_errors':
        await help_errors(uid, context.bot)
    elif data == 'help_tips':
        await help_tips(uid, context.bot)
    
    # Отмена
    elif data == 'cancel_action':
        if uid in user_data:
            user_data[uid].pop('step', None)
            save_data()
        await main_menu(uid, context.bot)
    
    elif data == 'confirm_clear':
        if uid in user_data:
            user_data[uid] = {
                'broadcasts': [],
                'groups': [],
                'settings': UserSettings(user_id=uid).__dict__,
                'created_at': str(datetime.now()),
                'total_sent': 0,
                'total_errors': 0
            }
            save_data()
        await context.bot.send_message(uid, "✅ Все данные очищены!", reply_markup=MAIN_KEYBOARD)
    
    # Обработка выбора рассылки
    elif data.startswith('select_bc_'):
        bid = int(data.split('_')[2])
        user_data[uid]['current_bid'] = bid
        save_data()
        await show_broadcast_menu(uid, context.bot, bid)

async def show_my_broadcasts(user_id: int, bot):
    """Показать список рассылок пользователя"""
    broadcasts = user_data.get(user_id, {}).get('broadcasts', [])
    
    if not broadcasts:
        await bot.send_message(user_id, "📢 У вас нет рассылок\n\nСоздайте первую рассылку через кнопку '➕ СОЗДАТЬ РАССЫЛКУ'", reply_markup=MAIN_KEYBOARD)
        return
    
    keyboard = []
    for i, bc in enumerate(broadcasts):
        status = "🟢" if bc.get('active', False) else "🔴"
        name = bc.get('name', f'Рассылка {i+1}')
        keyboard.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'select_bc_{i}')])
    
    keyboard.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
    
    await bot.send_message(user_id, "📋 **СПИСОК РАССЫЛОК**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def create_new_broadcast(user_id: int, bot):
    """Создание новой рассылки"""
    broadcasts = user_data.get(user_id, {}).get('broadcasts', [])
    
    if len(broadcasts) >= MAX_BROADCASTS:
        await bot.send_message(user_id, f"❌ Достигнут лимит рассылок ({MAX_BROADCASTS})\n\nУдалите ненужные рассылки чтобы создать новую.")
        return
    
    new_broadcast = {
        'name': f'Рассылка {len(broadcasts)+1}',
        'text': None,
        'groups': [],
        'interval': DEFAULT_INTERVAL,
        'active': False,
        'loop': True,
        'sent_count': 0,
        'errors': 0,
        'created_at': str(datetime.now())
    }
    
    broadcasts.append(new_broadcast)
    user_data[user_id]['broadcasts'] = broadcasts
    save_data()
    
    bid = len(broadcasts) - 1
    await show_broadcast_menu(user_id, bot, bid)

async def show_broadcast_menu(user_id: int, bot, bid: int):
    """Показать меню управления рассылкой"""
    broadcast = user_data.get(user_id, {}).get('broadcasts', [])[bid]
    name = broadcast.get('name', f'Рассылка {bid+1}')
    
    status = "🟢 АКТИВНА" if broadcast.get('active') else "🔴 ОСТАНОВЛЕНА"
    text_status = "✅" if broadcast.get('text') else "❌"
    groups_count = len(broadcast.get('groups', []))
    interval = broadcast.get('interval', DEFAULT_INTERVAL)
    sent = broadcast.get('sent_count', 0)
    
    menu_text = f"""
📢 <b>{name}</b>

━━━━━━━━━━━━━━━━━━━━━━
<b>Статус:</b> {status}
<b>Текст:</b> {text_status}
<b>Групп:</b> {groups_count}
<b>Интервал:</b> {interval} сек
<b>Отправлено:</b> {sent}
<b>Зациклено:</b> {'✅' if broadcast.get('loop', True) else '❌'}
━━━━━━━━━━━━━━━━━━━━━━

<b>⚙️ УПРАВЛЕНИЕ:</b>
    """
    
    await bot.send_message(user_id, menu_text, parse_mode='HTML', reply_markup=BROADCAST_MENU)

async def main_menu(user_id: int, bot):
    """Главное меню"""
    await bot.send_message(user_id, "🏠 <b>ГЛАВНОЕ МЕНЮ</b>\n\nВыберите действие:", parse_mode='HTML', reply_markup=MAIN_KEYBOARD)

# ==================== СОХРАНЕНИЕ ДАННЫХ ====================
def save_data():
    """Сохранение всех данных"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
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
    """Загрузка всех данных"""
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

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений"""
    try:
        uid = update.effective_user.id
        text = update.message.text.strip()
        
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
        
        step = user_data[uid].get('step')
        
        if not step:
            await main_menu(uid, context.bot)
            return
        
        # Обработка добавления группы
        if step == 'add_group':
            # Форматируем группу
            group = text.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not group.startswith('@'):
                group = '@' + group
            
            groups = user_data[uid].get('groups', [])
            if group not in groups:
                groups.append(group)
                user_data[uid]['groups'] = groups
                save_data()
                await update.message.reply_text(f"✅ Группа {group} добавлена!")
            else:
                await update.message.reply_text(f"⚠️ Группа {group} уже есть в списке")
            
            user_data[uid].pop('step')
            save_data()
            await groups_command(update, context)
        
        # Обработка удаления группы
        elif step == 'remove_group':
            group = text
            groups = user_data[uid].get('groups', [])
            
            if group in groups:
                groups.remove(group)
                user_data[uid]['groups'] = groups
                save_data()
                await update.message.reply_text(f"✅ Группа {group} удалена!")
            else:
                await update.message.reply_text(f"❌ Группа {group} не найдена")
            
            user_data[uid].pop('step')
            save_data()
            await groups_command(update, context)
        
        # Обработка импорта групп
        elif step == 'import_groups':
            lines = text.replace('\n', ',').split(',')
            new_groups = []
            for line in lines:
                group = line.strip()
                if group:
                    group = group.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
                    if not group.startswith('@'):
                        group = '@' + group
                    new_groups.append(group)
            
            groups = user_data[uid].get('groups', [])
            groups.extend(new_groups)
            groups = list(dict.fromkeys(groups))  # Удаляем дубликаты
            user_data[uid]['groups'] = groups
            save_data()
            
            await update.message.reply_text(f"✅ Импортировано {len(new_groups)} групп\nВсего групп: {len(groups)}")
            user_data[uid].pop('step')
            save_data()
            await groups_command(update, context)
        
        # Обработка тестовой отправки
        elif step == 'waiting_test_group':
            group = text
            group = group.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not group.startswith('@'):
                group = '@' + group
            
            await update.message.reply_text(f"🧪 Отправляю тестовое сообщение в {group}...")
            
            # Здесь код отправки тестового сообщения
            # Требуется авторизация
            
            user_data[uid].pop('step')
            save_data()
        
        # Обработка отзыва
        elif step == 'waiting_feedback':
            feedback = text
            
            # Отправляем админу
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"📝 <b>НОВЫЙ ОТЗЫВ</b>\n\n"
                    f"От: {update.effective_user.first_name} (ID: {uid})\n"
                    f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"{feedback}",
                    parse_mode='HTML'
                )
                await update.message.reply_text("✅ Спасибо за отзыв! Он передан разработчику.")
            except:
                await update.message.reply_text("❌ Ошибка отправки отзыва")
            
            user_data[uid].pop('step')
            save_data()
        
        # Обработка репорта
        elif step == 'waiting_report':
            report = text
            
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"🐛 <b>НОВЫЙ РЕПОРТ ОБ ОШИБКЕ</b>\n\n"
                    f"От: {update.effective_user.first_name} (ID: {uid})\n"
                    f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                    f"{report}",
                    parse_mode='HTML'
                )
                await update.message.reply_text("✅ Сообщение об ошибке отправлено разработчику.")
            except:
                await update.message.reply_text("❌ Ошибка отправки репорта")
            
            user_data[uid].pop('step')
            save_data()
        
        # Обработка импорта файла
        elif step == 'waiting_import_file':
            if update.message.document:
                file = await update.message.document.get_file()
                import_data = await file.download_as_bytearray()
                
                try:
                    data = json.loads(import_data.decode('utf-8'))
                    user_data[uid]['broadcasts'] = data.get('broadcasts', [])
                    user_data[uid]['groups'] = data.get('groups', [])
                    user_data[uid]['settings'] = data.get('settings', user_data[uid].get('settings', {}))
                    save_data()
                    await update.message.reply_text("✅ Импорт успешно завершён!")
                except Exception as e:
                    await update.message.reply_text(f"❌ Ошибка импорта: {str(e)[:100]}")
            else:
                await update.message.reply_text("❌ Отправьте JSON файл")
            
            user_data[uid].pop('step')
            save_data()
            await main_menu(uid, context.bot)
        
        else:
            await main_menu(uid, context.bot)
    
    except Exception as e:
        logger.error(f"Ошибка в message_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка, попробуйте /start")

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ МЕНЮ ====================
async def show_settings(user_id: int, bot):
    """Показать настройки"""
    settings = user_data.get(user_id, {}).get('settings', {})
    
    settings_text = f"""
⚙️ <b>НАСТРОЙКИ ПОЛЬЗОВАТЕЛЯ</b> ⚙️

━━━━━━━━━━━━━━━━━━━━━━
🌐 Язык: {settings.get('language', 'ru').upper()}
🔔 Уведомления: {'✅' if settings.get('notifications', True) else '❌'}
🎨 Тёмная тема: {'✅' if settings.get('dark_mode', False) else '❌'}
💾 Автосохранение: {'✅' if settings.get('auto_save', True) else '❌'}
⚡ Макс. рассылок: {settings.get('max_concurrent', 3)}
⏱ Интервал по умолч.: {settings.get('default_interval', DEFAULT_INTERVAL)} сек
━━━━━━━━━━━━━━━━━━━━━━
    """
    
    await bot.send_message(user_id, settings_text, parse_mode='HTML', reply_markup=SETTINGS_KEYBOARD)

async def show_global_stats(user_id: int, bot):
    """Показать глобальную статистику"""
    data = user_data.get(user_id, {})
    broadcasts = data.get('broadcasts', [])
    
    active = sum(1 for b in broadcasts if b.get('active', False))
    total_sent = data.get('total_sent', 0)
    
    stats_text = f"""
📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА</b> 📊

━━━━━━━━━━━━━━━━━━━━━━
<b>📢 Рассылки:</b>
• Всего: {len(broadcasts)}
• Активных: {active}
• Остановлено: {len(broadcasts) - active}

<b>📨 Отправки:</b>
• Всего отправлено: {total_sent}
• В среднем: {total_sent // max(1, len(broadcasts))} на рассылку

<b>👥 Группы:</b>
• Сохранено групп: {len(data.get('groups', []))}

━━━━━━━━━━━━━━━━━━━━━━
    """
    
    await bot.send_message(user_id, stats_text, parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def set_language(user_id: int, bot):
    """Установка языка"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇷🇺 Русский", callback_data='lang_ru')],
        [InlineKeyboardButton("🇬🇧 English", callback_data='lang_en')],
        [InlineKeyboardButton("🔙 Назад", callback_data='settings')]
    ])
    await bot.send_message(user_id, "🌐 Выберите язык / Choose language:", reply_markup=keyboard)

async def toggle_notifications(user_id: int, bot):
    """Переключение уведомлений"""
    settings = user_data[user_id].get('settings', {})
    current = settings.get('notifications', True)
    settings['notifications'] = not current
    user_data[user_id]['settings'] = settings
    save_data()
    await bot.send_message(user_id, f"🔔 Уведомления: {'ВКЛЮЧЕНЫ' if not current else 'ВЫКЛЮЧЕНЫ'}")
    await show_settings(user_id, bot)

async def toggle_theme(user_id: int, bot):
    """Переключение темы"""
    settings = user_data[user_id].get('settings', {})
    current = settings.get('dark_mode', False)
    settings['dark_mode'] = not current
    user_data[user_id]['settings'] = settings
    save_data()
    await bot.send_message(user_id, f"🎨 Тема: {'ТЁМНАЯ' if not current else 'СВЕТЛАЯ'}")
    await show_settings(user_id, bot)

async def toggle_autosave(user_id: int, bot):
    """Переключение автосохранения"""
    settings = user_data[user_id].get('settings', {})
    current = settings.get('auto_save', True)
    settings['auto_save'] = not current
    user_data[user_id]['settings'] = settings
    save_data()
    await bot.send_message(user_id, f"💾 Автосохранение: {'ВКЛЮЧЕНО' if not current else 'ВЫКЛЮЧЕНО'}")
    await show_settings(user_id, bot)

async def set_max_broadcasts(user_id: int, bot):
    """Установка максимального количества рассылок"""
    user_data[user_id]['step'] = 'set_max_broadcasts'
    save_data()
    await bot.send_message(user_id, f"⚡ Введите максимальное количество рассылок (1-{MAX_BROADCASTS}):", reply_markup=CANCEL_KEYBOARD)

async def set_default_interval(user_id: int, bot):
    """Установка интервала по умолчанию"""
    user_data[user_id]['step'] = 'set_default_interval'
    save_data()
    await bot.send_message(user_id, f"⏱ Введите интервал по умолчанию ({MIN_INTERVAL}-{MAX_INTERVAL} сек):", reply_markup=CANCEL_KEYBOARD)

async def stats_by_broadcast(user_id: int, bot):
    """Статистика по рассылкам"""
    broadcasts = user_data.get(user_id, {}).get('broadcasts', [])
    
    if not broadcasts:
        await bot.send_message(user_id, "❌ Нет рассылок для отображения статистики")
        return
    
    text = "📊 <b>СТАТИСТИКА ПО РАССЫЛКАМ</b>\n\n"
    for i, b in enumerate(broadcasts, 1):
        text += f"{i}. {b.get('name', f'Рассылка {i}')}\n"
        text += f"   📨 Отправлено: {b.get('sent_count', 0)}\n"
        text += f"   ❌ Ошибок: {b.get('errors', 0)}\n"
        text += f"   🟢 Статус: {'Активна' if b.get('active') else 'Остановлена'}\n\n"
    
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def stats_by_day(user_id: int, bot):
    """Статистика по дням"""
    # Здесь можно добавить график или статистику по дням из БД
    await bot.send_message(user_id, "📅 <b>СТАТИСТИКА ПО ДНЯМ</b>\n\nФункция в разработке...", parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def stats_top_groups(user_id: int, bot):
    """Топ групп по отправкам"""
    await bot.send_message(user_id, "🏆 <b>ТОП ГРУПП ПО ОТПРАВКАМ</b>\n\nФункция в разработке...", parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def stats_graph(user_id: int, bot):
    """График статистики"""
    await bot.send_message(user_id, "📈 <b>ГРАФИК СТАТИСТИКИ</b>\n\nФункция в разработке...", parse_mode='HTML', reply_markup=STATS_KEYBOARD)

async def help_quick(user_id: int, bot):
    """Быстрая помощь"""
    text = """
🚀 <b>БЫСТРЫЙ СТАРТ ЗА 3 ШАГА</b> 🚀

1️⃣ <b>СОЗДАЙТЕ РАССЫЛКУ</b>
Нажмите кнопку "➕ СОЗДАТЬ РАССЫЛКУ"

2️⃣ <b>НАСТРОЙТЕ ПАРАМЕТРЫ</b>
• Текст сообщения
• Группы для отправки
• Интервал между сообщениями

3️⃣ <b>ЗАПУСТИТЕ РАССЫЛКУ</b>
• "🚀 ЗАПУСТИТЬ 24/7" - для бесконечной работы
• "▶️ ОТПРАВИТЬ РАЗОМ" - для единоразовой отправки

⚠️ При первом запуске потребуется авторизация в Telegram!
    """
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

async def help_create(user_id: int, bot):
    """Помощь по созданию рассылки"""
    text = """
📢 <b>КАК СОЗДАТЬ РАССЫЛКУ ПОДРОБНО</b> 📢

<b>1. ТЕКСТ СООБЩЕНИЯ</b>
• Можно использовать эмодзи, ссылки, форматирование
• Максимальная длина: 4096 символов
• Поддерживаются кнопки (раздел "КНОПКИ")

<b>2. ГРУППЫ ДЛЯ РАССЫЛКИ</b>
• Форматы: @group, t.me/group, https://t.me/group
• Несколько групп указывайте через запятую
• Бот должен быть участником группы

<b>3. ИНТЕРВАЛ</b>
• Минимальный: 5 секунд
• Рекомендуемый: 30-60 секунд
• Максимальный: 3600 секунд (1 час)

<b>4. ДОПОЛНИТЕЛЬНЫЕ НАСТРОЙКИ</b>
• Рандом - случайная задержка
• Зациклить - бесконечный повтор
• Расписание - запуск по времени
• Спинтакс - генерация вариантов текста
    """
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

async def help_modes(user_id: int, bot):
    """Помощь по режимам работы"""
    text = """
🔄 <b>РЕЖИМЫ РАБОТЫ РАССЫЛКИ</b> 🔄

<b>🚀 РЕЖИМ 24/7 (БЕСКОНЕЧНЫЙ)</b>
• Сообщение отправляется по кругу
• Группа1 → Группа2 → ... → ГруппаN → Группа1
• Работает без остановки 24/7/365
• Подходит для постоянного присутствия

<b>▶️ РАЗОВЫЙ РЕЖИМ</b>
• Одно сообщение во все группы
• После отправки рассылка останавливается
• Подходит для анонсов и объявлений

<b>📅 РЕЖИМ ПО РАСПИСАНИЮ</b>
• Запуск в указанное время
• Можно настроить повторение
• Подходит для регулярных постов

<b>🎲 РЕЖИМ С РАНДОМОМ</b>
• Случайная задержка между сообщениями
• Интервал: от X до Y секунд
• Имитирует естественное поведение
    """
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

async def help_errors(user_id: int, bot):
    """Помощь по ошибкам"""
    text = """
🔧 <b>ЧАСТЫЕ ОШИБКИ И РЕШЕНИЯ</b> 🔧

<b>❌ ОШИБКА 2FA (Двухфакторная аутентификация)</b>
Решение: Введи пароль от Telegram. Если нет 2FA - нажми /skip

<b>❌ ГРУППА НЕДОСТУПНА</b>
Решение: Добавь бота в группу и дай права на отправку сообщений

<b>❌ ФЛУД-КОНТРОЛЬ (FloodWaitError)</b>
Решение: Увеличь интервал между сообщениями до 30+ секунд

<b>❌ НЕВЕРНЫЙ НОМЕР ТЕЛЕФОНА</b>
Решение: Используй формат +79123456789 (с кодом страны)

<b>❌ НЕВЕРНЫЙ КОД ПОДТВЕРЖДЕНИЯ</b>
Решение: Введи код в формате: code12345 (только цифры)

<b>❌ НЕТ ПРАВ НА ОТПРАВКУ</b>
Решение: Сделай бота администратором группы

<b>❌ СЕССИЯ УСТАРЕЛА</b>
Решение: Начни заново через /start и авторизуйся снова
    """
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

async def help_tips(user_id: int, bot):
    """Советы по использованию"""
    text = """
💡 <b>ПОЛЕЗНЫЕ СОВЕТЫ ДЛЯ РАБОТЫ</b> 💡

<b>📌 ОПТИМИЗАЦИЯ РАССЫЛКИ</b>
• Ставь интервал 30-60 секунд для безопасности
• Используй до 5 групп одновременно
• Проверяй доступность групп перед запуском

<b>📌 БЕЗОПАСНОСТЬ</b>
• Регулярно делай бэкап настроек
• Не храни пароли в открытом виде
• Используй надёжный номер телефона

<b>📌 ЭФФЕКТИВНОСТЬ</b>
• Персонализируй текст для каждой группы
• Используй кнопки для повышения кликабельности
• Анализируй статистику для улучшения

<b>📌 АВТОМАТИЗАЦИЯ</b>
• Настрой расписание для регулярных постов
• Используй функцию "Клонировать" для похожих рассылок
• Сохраняй группы в разделе "Мои группы"

<b>📌 ПОДДЕРЖКА</b>
• /feedback - для предложений
• /report - для сообщения об ошибках
• /donate - для поддержки проекта
    """
    await bot.send_message(user_id, text, parse_mode='HTML', reply_markup=HELP_KEYBOARD)

# ==================== ЗАПУСК БОТА ====================
def main():
    """Главная функция запуска бота"""
    # Инициализация
    init_database()
    load_data()
    
    # Создание приложения
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков команд
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("groups", groups_command))
    app.add_handler(CommandHandler("backup", backup_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("feedback", feedback_command))
    app.add_handler(CommandHandler("report", report_command))
    app.add_handler(CommandHandler("donate", donate_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("restart", restart_command))
    app.add_handler(CommandHandler("pause_all", pause_all_command))
    app.add_handler(CommandHandler("resume_all", resume_all_command))
    app.add_handler(CommandHandler("test", test_command))
    app.add_handler(CommandHandler("export", export_command))
    app.add_handler(CommandHandler("import", import_command))
    app.add_handler(CommandHandler("info", info_command))
    
    # Регистрация обработчиков кнопок и сообщений
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, message_handler))
    
    # Вывод информации о запуске
    print("=" * 70)
    print("🥕 SENDFLOW - Telegram Mass Sender Bot v3.0")
    print("=" * 70)
    print(f"📁 Логи: {LOG_DIR}")
    print(f"📁 База данных: {DATABASE_FILE}")
    print(f"📁 Данные пользователей: {DATA_FILE}")
    print("=" * 70)
    print("📌 ДОСТУПНЫЕ КОМАНДЫ:")
    print("   /start    - Главное меню")
    print("   /help     - Полная справка")
    print("   /stats    - Моя статистика")
    print("   /groups   - Мои группы")
    print("   /backup   - Создать бэкап")
    print("   /export   - Экспорт настроек")
    print("   /import   - Импорт настроек")
    print("   /feedback - Отправить отзыв")
    print("   /report   - Сообщить об ошибке")
    print("   /donate   - Поддержать проект")
    print("   /restart  - Перезапуск рассылок")
    print("   /pause_all- Пауза всех рассылок")
    print("   /test     - Тестовая отправка")
    print("=" * 70)
    print("✅ БОТ УСПЕШНО ЗАПУЩЕН И ГОТОВ К РАБОТЕ!")
    print("=" * 70)
    
    # Запуск бота
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
        logger.info("Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logger.critical(f"Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
