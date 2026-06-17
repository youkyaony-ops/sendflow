import asyncio
import re
import json
import os
import time
import random
import shutil
import sqlite3
import threading
import logging
from datetime import datetime
from pyrogram import Client, filters
from pyrogram.errors import (
    FloodWait,
    InviteRequestSent,
    UserAlreadyParticipant,
    ChannelPrivate,
    ChatWriteForbidden,
    RPCError,
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded
)
from pyrogram.types import InlineKeyboardButton as PyroInlineButton, InlineKeyboardMarkup as PyroInlineMarkup
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ==================== ЛОГИРОВАНИЕ ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
ADMIN_ID = 7192049112  # Твой ID из алерта
DATA_FILE = 'user_data.json'
SESSIONS_DIR = 'telegram_sessions'
MEDIA_DIR = 'media_files'
MAX_GROUPS_PER_BROADCAST = 50
MAX_TOTAL_GROUPS = 100
MAX_BROADCASTS = 20

for folder in [SESSIONS_DIR, MEDIA_DIR]:
    os.makedirs(folder, exist_ok=True)

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

user_data = {}
active_tasks = {}
keep_alive_tasks = {}
pyro_clients = {}
user_states = {}

# ==================== SQLite ====================
db_lock = threading.Lock()

def get_db_connection():
    conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'), timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            phone TEXT,
            created_at TIMESTAMP,
            last_ping TIMESTAMP,
            session_string TEXT
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("✅ SQLite инициализирован с WAL режимом")

init_db()

def save_session_db(user_id, phone, session_string):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO sessions (user_id, phone, created_at, last_ping, session_string)
                VALUES (?, ?, ?, ?, ?)
            ''', (user_id, phone, datetime.now(), datetime.now(), session_string))
            conn.commit()
        except Exception as e:
            logger.error(f"Save session error: {e}")
        finally:
            if conn:
                conn.close()

def update_ping_db(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('UPDATE sessions SET last_ping = ? WHERE user_id = ?', (datetime.now(), user_id))
            conn.commit()
        except Exception as e:
            logger.error(f"Update ping error: {e}")
        finally:
            if conn:
                conn.close()

def delete_session_db(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"Delete session error: {e}")
        finally:
            if conn:
                conn.close()

def has_session_db(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM sessions WHERE user_id = ?', (user_id,))
            result = cursor.fetchone() is not None
            return result
        except Exception as e:
            logger.error(f"Has session error: {e}")
            return False
        finally:
            if conn:
                conn.close()

def get_session_string(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT session_string FROM sessions WHERE user_id = ?', (user_id,))
            row = cursor.fetchone()
            return row[0] if row else None
        except Exception as e:
            logger.error(f"Get session string error: {e}")
            return None
        finally:
            if conn:
                conn.close()

# ==================== ДАННЫЕ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
                    'created_at': str(datetime.now()),
                    'total_sent': data.get('total_sent', 0)
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Save data error: {e}")
        return False

def load_data():
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
        else:
            user_data = {}
        return True
    except Exception as e:
        logger.error(f"Load data error: {e}")
        user_data = {}
        return False

def save_user(uid):
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'created_at': str(datetime.now()),
            'total_sent': 0
        }
        save_data()
    return user_data[uid]

def get_media_path(uid, bid):
    return os.path.join(MEDIA_DIR, f'{uid}_{bid}.jpg')

# ==================== УВЕДОМЛЕНИЯ АДМИНУ ====================
async def alert_admin(bot, text: str):
    """Отправить уведомление админу в Telegram"""
    try:
        await bot.send_message(ADMIN_ID, f"🚨 SENDFLOW ALERT\n\n{text}", parse_mode='HTML')
    except Exception as e:
        logger.error(f"Alert admin failed: {e}")

# ==================== PYROGRAM КЛИЕНТ ====================
async def get_pyro_client(uid):
    if uid in pyro_clients:
        try:
            await pyro_clients[uid].get_me()
            update_ping_db(uid)
            return pyro_clients[uid]
        except Exception:
            try:
                await pyro_clients[uid].stop()
            except:
                pass
            del pyro_clients[uid]

    session_string = get_session_string(uid)
    if session_string:
        client = Client(
            name=f"user_{uid}",
            api_id=API_ID,
            api_hash=API_HASH,
            session_string=session_string,
            workdir=SESSIONS_DIR
        )
        try:
            await client.start()
            pyro_clients[uid] = client
            update_ping_db(uid)
            return client
        except Exception as e:
            logger.error(f"Pyro client start error for {uid}: {e}")
            try:
                await client.stop()
            except:
                pass
            return None
    return None

async def request_pyro_code(uid, phone, bot=None):
    """Запрос кода авторизации с детальной ошибкой"""
    client = Client(f"user_{uid}", API_ID, API_HASH, workdir=SESSIONS_DIR)
    try:
        await client.start()
        await client.send_code(phone)
        pyro_clients[uid] = client
        return True, None
    except Exception as e:
        error_msg = str(e)[:200]
        logger.error(f"Code request error for {uid}: {error_msg}")
        if bot:
            await alert_admin(bot, f"❌ Ошибка отправки кода для {uid}\nТелефон: {phone}\nОшибка: {error_msg}")
        try:
            await client.stop()
        except:
            pass
        return False, error_msg

async def sign_in_pyro(uid, code):
    client = pyro_clients.get(uid)
    if not client:
        return None
    try:
        await client.sign_in(code=code)
        session_string = await client.export_session_string()
        phone = client.phone_number
        save_session_db(uid, phone, session_string)
        return client
    except SessionPasswordNeeded:
        return "2fa_needed"
    except Exception as e:
        logger.error(f"Sign in error for {uid}: {e}")
        return None

async def sign_in_2fa(uid, password):
    client = pyro_clients.get(uid)
    if not client:
        return None
    try:
        await client.sign_in(password=password)
        session_string = await client.export_session_string()
        phone = client.phone_number
        save_session_db(uid, phone, session_string)
        return client
    except Exception as e:
        logger.error(f"2FA error for {uid}: {e}")
        return None

async def keep_alive_loop(uid):
    """Поддержание сессии живой"""
    while True:
        await asyncio.sleep(300)
        if uid in pyro_clients:
            try:
                await pyro_clients[uid].get_me()
                update_ping_db(uid)
            except:
                pass

# ==================== АВТОПОДПИСКА (УЛУЧШЕННАЯ) ====================

_join_cd = {}
_join_cd_sec = 300
_proactive_cache = {}
_proactive_cd = 43200
_last_clean = 0

def _gc():
    global _last_clean
    now = time.time()
    if now - _last_clean < 3600:
        return
    _last_clean = now
    for k in list(_join_cd.keys()):
        if _join_cd[k] < now - 3600:
            del _join_cd[k]
    for k in list(_proactive_cache.keys()):
        if _proactive_cache[k] < now - 43200:
            del _proactive_cache[k]

async def join_channel(client: Client, target: str, tag: str = "") -> int | None:
    _gc()
    key = (tag, target.lower())
    if time.time() - _join_cd.get(key, 0) < _join_cd_sec:
        return None
    try:
        r = await client.join_chat(target)
        if r and hasattr(r, "id"):
            _join_cd[key] = time.time()
            try:
                await client.archive_chats(r.id)
            except Exception:
                pass
            return r.id
    except FloodWait as e:
        _join_cd[key] = time.time() + max(int(e.value), _join_cd_sec)
    except Exception as e:
        s = str(e)
        _join_cd[key] = time.time()
        if "INVITE_REQUEST_SENT" in s:
            return 0
        if "USER_ALREADY_PARTICIPANT" in s:
            return 0
        logger.warning(f"[JOIN] join failed {target}: {e}")
    return None

async def click_verify_improved(client: Client, chat_id: int, bot=None, uid=None) -> bool:
    """
    Улучшенная версия: 
    1) Сначала прожимает все URL-кнопки (подписаться)
    2) Потом ищет кнопку подтверждения
    """
    for attempt in range(3):
        try:
            async for msg in client.get_chat_history(chat_id, limit=15):
                if not msg.reply_markup:
                    continue
                
                # Шаг 1: собираем и прожимаем все ссылки
                for row in msg.reply_markup.inline_keyboard:
                    for btn in row:
                        if btn.url and 't.me' in btn.url:
                            try:
                                await msg.click(btn.text)
                                await asyncio.sleep(1.5)
                                if bot and uid:
                                    await bot.send_message(uid, f"🔗 Перешёл: {btn.text[:30]}")
                            except Exception:
                                pass
                
                # Шаг 2: после всех ссылок ищем кнопку подтверждения
                await asyncio.sleep(2)
                async for msg2 in client.get_chat_history(chat_id, limit=5):
                    if msg2.reply_markup:
                        for row in msg2.reply_markup.inline_keyboard:
                            for btn in row:
                                t = (btn.text or "").lower()
                                if any(k in t for k in ["провер", "verify", "готов", "подписался", "вступил", "подтверд", "продолж"]):
                                    try:
                                        await msg2.click(btn.text)
                                        await asyncio.sleep(2)
                                        if bot and uid:
                                            await bot.send_message(uid, f"✅ Нажал: {btn.text}")
                                        return True
                                    except Exception:
                                        pass
        except Exception:
            pass
        await asyncio.sleep(3)
    return False

def _grab_links(msg) -> list:
    links = []
    text = msg.text or ""
    
    if msg.reply_markup:
        for row in msg.reply_markup.inline_keyboard:
            for btn in row:
                if btn.url and any(d in btn.url for d in ['t.me/', 'telegram.me/', 'telegram.dog/']):
                    links.append(btn.url)
    
    at_matches = re.findall(r'@([a-zA-Z0-9_]{5,32})', text)
    for match in at_matches:
        if not match.lower().endswith('bot'):
            links.append(f'@{match}')
    
    tm_matches = re.findall(r'(?:t\.me|telegram\.me|telegram\.dog)/([a-zA-Z0-9_]{5,32})', text)
    for match in tm_matches:
        links.append(f'https://t.me/{match}')
    
    jc_matches = re.findall(r'(?:t\.me|telegram\.me|telegram\.dog)/joinchat/([a-zA-Z0-9_-]+)', text)
    for match in jc_matches:
        links.append(f'https://t.me/joinchat/{match}')
    
    return list(dict.fromkeys(links))

async def auto_subscribe_pyro(client: Client, chat_id: int, bot, uid: int) -> bool:
    try:
        await bot.send_message(uid, "🔍 Ищу сообщения с требованием подписки...")
        
        targets = []
        
        async for msg in client.get_chat_history(chat_id, limit=30):
            if not msg.text:
                continue
            tl = msg.text.lower()
            if not any(p in tl for p in ["подписаться", "вступить", "subscribe", "join", "капча", "captcha", "verification", "доступ запрещён", "restricted"]):
                continue
            targets.extend(_grab_links(msg))
            await bot.send_message(uid, f"📝 Найдено сообщение: {msg.text[:100]}...")
        
        if not targets:
            await bot.send_message(uid, "⚠️ Не найдено ссылок для подписки")
            return False
        
        await bot.send_message(uid, f"🔗 Найдено {len(targets)} ссылок")
        
        subscribed = 0
        for raw in targets[:10]:
            raw = raw.strip()
            await bot.send_message(uid, f"🔄 Обрабатываю: {raw[:50]}...")
            
            if raw.startswith("https://t.me/") or raw.startswith("http://t.me/") or \
               raw.startswith("https://telegram.me/") or raw.startswith("http://telegram.dog/"):
                path = raw.split("/")[-1].strip("/")
                if path.startswith("+") or path.startswith("joinchat/"):
                    try:
                        await client.join_chat(raw)
                        subscribed += 1
                        await bot.send_message(uid, f"✅ Вступил по ссылке")
                        await asyncio.sleep(2)
                        continue
                    except Exception as e:
                        await bot.send_message(uid, f"❌ Ошибка: {str(e)[:50]}")
                        continue
                else:
                    raw = "@" + path.split("?")[0]
                    if raw.lower().endswith("bot"):
                        continue
            elif not raw.startswith('@') and not raw.startswith('https://'):
                raw = '@' + raw
            
            try:
                r = await join_channel(client, raw, str(uid))
                if r is not None:
                    subscribed += 1
                    await bot.send_message(uid, f"✅ Подписался на {raw}")
                    await asyncio.sleep(2)
                else:
                    await bot.send_message(uid, f"⚠️ Не удалось подписаться на {raw}")
            except Exception as e:
                await bot.send_message(uid, f"❌ Ошибка: {str(e)[:50]}")
        
        await bot.send_message(uid, "🔘 Проверяю кнопки подтверждения...")
        verified = await click_verify_improved(client, chat_id, bot, uid)
        if verified:
            await bot.send_message(uid, "✅ Нажал кнопку подтверждения")
        
        if subscribed > 0:
            await bot.send_message(uid, f"✅ Подписался на {subscribed} каналов/групп")
            return True
        else:
            await bot.send_message(uid, "⚠️ Не удалось подписаться")
            return False
        
    except Exception as e:
        await bot.send_message(uid, f"❌ Ошибка: {str(e)[:150]}")
        return False

# ==================== ОТПРАВКА С АВТОПОДПИСКОЙ ====================

async def send_with_auto_join(uid, bid, client: Client, chat_id, text, bot):
    try:
        await client.send_message(chat_id, text)
        return True, "OK"
    except FloodWait as e:
        wait_time = min(int(e.value), 600)
        await bot.send_message(uid, f"⏳ FloodWait: жду {wait_time} сек...")
        await asyncio.sleep(wait_time)
        return await send_with_auto_join(uid, bid, client, chat_id, text, bot)
    except (ChatWriteForbidden, ChannelPrivate) as e:
        await bot.send_message(uid, f"⚠️ Требуется подписка для {chat_id}")
        try:
            success = await auto_subscribe_pyro(client, chat_id, bot, uid)
            if success:
                await bot.send_message(uid, f"🔄 Повторная попытка через 3 секунды...")
                await asyncio.sleep(3)
                try:
                    await client.send_message(chat_id, text)
                    return True, "OK после подписки"
                except Exception as send_err:
                    return False, f"Не отправилось: {str(send_err)[:50]}"
            else:
                return False, "Не удалось подписаться"
        except Exception as e2:
            return False, f"Ошибка: {str(e2)[:100]}"
    except Exception as e:
        return False, str(e)[:100]

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'text_{bid}'), InlineKeyboardButton("📷 ФОТО", callback_data=f'photo_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'groups_{bid}'), InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'interval_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ", callback_data=f'start_{bid}'), InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_{bid}')],
        [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data=f'stats_{bid}')],
        [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data=f'clone_{bid}'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_{bid}')],
        [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data='add_group')],
    [InlineKeyboardButton("📋 СПИСОК", callback_data='list_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data='remove_group')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑 ОЧИСТИТЬ СЕССИЮ", callback_data='clear_session')],
    [InlineKeyboardButton("🔄 СТАТУС", callback_data='check_status')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def send_msg(chat_id, bot, text, kb=None):
    try:
        if kb:
            await bot.send_message(chat_id, text, reply_markup=kb, parse_mode='HTML')
        else:
            await bot.send_message(chat_id, text, parse_mode='HTML')
    except:
        pass

async def main_menu(chat_id, bot, text=None):
    msg = text or "🥓 SendFlow\n\nВыберите действие:"
    await send_msg(chat_id, bot, msg, MAIN_MENU)

async def show_broadcast(uid, bot, bid):
    bc = user_data[uid]['broadcasts'][bid]
    has_photo = os.path.exists(get_media_path(uid, bid))
    is_running = f"{uid}_{bid}" in active_tasks
    status = "🟢" if is_running else "🔴"
    txt = f"📢 <b>{bc['name']}</b>\n\nСтатус: {status}\n📝 Текст: {'✅' if bc.get('text') else '❌'}\n📷 Фото: {'✅' if has_photo else '❌'}\n👥 Групп: {len(bc.get('groups', []))}\n⏱ Интервал: {bc.get('interval', 30)} сек\n📨 Отправлено: {bc.get('sent', 0)}"
    await send_msg(uid, bot, txt, get_broadcast_actions(bid))

async def cleanup_user_tasks(uid, bot=None):
    """Останавливает все задачи пользователя и прибирает keep-alive"""
    for tk in list(active_tasks.keys()):
        if tk.startswith(f"{uid}_"):
            try:
                active_tasks[tk].cancel()
            except:
                pass
            await asyncio.sleep(0.3)
            if tk in active_tasks:
                del active_tasks[tk]
                if bot:
                    await alert_admin(bot, f"ℹ️ Рассылка {tk} пользователя {uid} принудительно остановлена")
    
    has_active = any(k.startswith(f"{uid}_") for k in active_tasks)
    if not has_active and uid in keep_alive_tasks:
        try:
            keep_alive_tasks[uid].cancel()
        except:
            pass
        await asyncio.sleep(0.1)
        if uid in keep_alive_tasks:
            del keep_alive_tasks[uid]

# ==================== КОМАНДЫ ====================
async def start(update: Update, context):
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    await main_menu(uid, context.bot, f"👋 Привет, {update.effective_user.first_name}!")

async def skip(update: Update, context):
    uid = update.effective_user.id
    if user_states.get(uid, {}).get('step') == 'waiting_2fa':
        update.message.text = '/skip'
        await message_handler(update, context)

# ==================== КНОПКИ ====================
async def button_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data

    try:
        await query.message.delete()
    except:
        pass

    if data == 'back_to_main':
        await main_menu(uid, context.bot)

    elif data == 'my_broadcasts':
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_msg(uid, context.bot, "📢 Нет рассылок", MAIN_MENU)
            return
        kb = []
        for i, bc in enumerate(broadcasts):
            is_running = f"{uid}_{i}" in active_tasks
            status = "🟢" if is_running else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {bc['name']}", callback_data=f'select_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        await context.bot.send_message(uid, "📋 ВАШИ РАССЫЛКИ", reply_markup=InlineKeyboardMarkup(kb))

    elif data == 'new_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= MAX_BROADCASTS:
            await send_msg(uid, context.bot, f"❌ Максимум {MAX_BROADCASTS} рассылок", MAIN_MENU)
            return
        new_id = len(broadcasts)
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}', 
            'text': None, 
            'groups': [], 
            'interval': 30, 
            'sent': 0,
            'created_at': str(datetime.now())
        })
        save_data()
        await show_broadcast(uid, context.bot, new_id)

    elif data == 'my_groups':
        groups = user_data[uid].get('groups', [])
        if groups:
            await send_msg(uid, context.bot, "📁 " + "\n".join(groups), GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, "📁 Нет групп", GROUPS_MENU)

    elif data == 'settings':
        await send_msg(uid, context.bot, "⚙️ НАСТРОЙКИ", SETTINGS_MENU)

    elif data == 'check_status':
        running = sum(1 for tk in active_tasks if tk.startswith(f"{uid}_"))
        await send_msg(uid, context.bot, f"📊 СТАТУС\n\n🟢 Работает: {running}\n🔐 Сессия: {'✅' if has_session_db(uid) else '❌'}", SETTINGS_MENU)

    elif data == 'help_menu':
        await send_msg(uid, context.bot, "❓ ПОМОЩЬ", HELP_MENU)

    elif data == 'help_quick':
        await send_msg(uid, context.bot, "🚀 1. Новая рассылка\n2. ТЕКСТ или ФОТО\n3. ГРУППЫ\n4. ЗАПУСТИТЬ\n5. Авторизация", HELP_MENU)

    elif data == 'help_create':
        await send_msg(uid, context.bot, "📝 ТЕКСТ: отправь сообщение\n📷 ФОТО: отправь фото\n👥 ГРУППЫ: @group1, @group2\n⏱ ИНТЕРВАЛ: 5-300 сек", HELP_MENU)

    elif data == 'help_errors':
        await send_msg(uid, context.bot, "🔧 2FA: пароль или /skip\n❌ Группа недоступна: добавь бота\n⚠️ Флуд: увеличь интервал\n🤖 Автоподписка: встроена на Pyrogram", HELP_MENU)

    elif data == 'clear_session':
        await cleanup_user_tasks(uid, context.bot)
        if uid in pyro_clients:
            try:
                await pyro_clients[uid].stop()
            except:
                pass
            del pyro_clients[uid]
        delete_session_db(uid)
        await send_msg(uid, context.bot, "✅ Сессия очищена", SETTINGS_MENU)
        await alert_admin(context.bot, f"🗑 Пользователь {uid} очистил сессию")

    elif data.startswith('select_'):
        bid = int(data.split('_')[1])
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('text_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'text', 'bid': bid}
        await send_msg(uid, context.bot, "📝 Отправьте текст:", CANCEL_BTN)

    elif data.startswith('photo_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'photo', 'bid': bid}
        await send_msg(uid, context.bot, "📷 Отправьте фото (подпись = текст):", CANCEL_BTN)

    elif data.startswith('groups_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'groups', 'bid': bid}
        await send_msg(uid, context.bot, f"👥 Группы через запятую (макс {MAX_GROUPS_PER_BROADCAST}):\n@group1, @group2", CANCEL_BTN)

    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'interval', 'bid': bid}
        await send_msg(uid, context.bot, "⏱ Интервал (5-300):", CANCEL_BTN)

    elif data.startswith('start_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        if not bc.get('text') and not has_photo:
            await send_msg(uid, context.bot, "❌ Нет текста или фото")
            await show_broadcast(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_msg(uid, context.bot, "❌ Нет групп")
            await show_broadcast(uid, context.bot, bid)
            return
        if f"{uid}_{bid}" in active_tasks:
            await send_msg(uid, context.bot, "⚠️ Уже запущена")
            await show_broadcast(uid, context.bot, bid)
            return
        client = await get_pyro_client(uid)
        if client:
            await start_broadcast(uid, context.bot, bid, client)
            return
        user_states[uid] = {'step': 'auth_phone', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789", CANCEL_BTN)

    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
            await asyncio.sleep(0.3)
            if tk in active_tasks:
                del active_tasks[tk]
            await send_msg(uid, context.bot, f"🛑 Рассылка #{bid+1} остановлена")
            await alert_admin(context.bot, f"🛑 Рассылка #{bid+1} пользователя {uid} остановлена")
        
        has_active = any(k.startswith(f"{uid}_") for k in active_tasks)
        if not has_active and uid in keep_alive_tasks:
            try:
                keep_alive_tasks[uid].cancel()
            except:
                pass
            if uid in keep_alive_tasks:
                del keep_alive_tasks[uid]
        
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('clone_'):
        bid = int(data.split('_')[1])
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= MAX_BROADCASTS:
            await send_msg(uid, context.bot, f"❌ Максимум {MAX_BROADCASTS} рассылок", MAIN_MENU)
            return
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original['name']}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', 30),
            'active': False,
            'sent': 0,
            'created_at': str(datetime.now())
        }
        user_data[uid]['broadcasts'].append(new_bc)
        old_media = get_media_path(uid, bid)
        new_media = get_media_path(uid, len(broadcasts))
        if os.path.exists(old_media):
            shutil.copy(old_media, new_media)
        save_data()
        await send_msg(uid, context.bot, "✅ Склонировано", MAIN_MENU)

    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
            await asyncio.sleep(0.3)
            if tk in active_tasks:
                del active_tasks[tk]
        
        has_active = any(k.startswith(f"{uid}_") for k in active_tasks)
        if not has_active and uid in keep_alive_tasks:
            try:
                keep_alive_tasks[uid].cancel()
            except:
                pass
            if uid in keep_alive_tasks:
                del keep_alive_tasks[uid]
        
        media = get_media_path(uid, bid)
        if os.path.exists(media):
            os.remove(media)
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_msg(uid, context.bot, "🗑 Удалено", MAIN_MENU)
        await alert_admin(context.bot, f"🗑 Пользователь {uid} удалил рассылку #{bid+1}")

    elif data.startswith('stats_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        is_running = f"{uid}_{bid}" in active_tasks
        stats_text = f"""📊 СТАТИСТИКА РАССЫЛКИ #{bid+1}

📝 Название: {bc['name']}
📨 Отправлено: {bc.get('sent', 0)}
⏱ Интервал: {bc.get('interval', 30)} сек
👥 Групп: {len(bc.get('groups', []))}
📷 Фото: {'✅' if os.path.exists(get_media_path(uid, bid)) else '❌'}
🔄 Статус: {'🟢 Активна' if is_running else '🔴 Остановлена'}
📅 Создана: {bc.get('created_at', 'неизвестно')}
"""
        await send_msg(uid, context.bot, stats_text, get_broadcast_actions(bid))

    elif data == 'add_group':
        groups = user_data[uid].get('groups', [])
        if len(groups) >= MAX_TOTAL_GROUPS:
            await send_msg(uid, context.bot, f"❌ Максимум {MAX_TOTAL_GROUPS} групп в общем списке", GROUPS_MENU)
            return
        user_states[uid] = {'step': 'add_group'}
        await send_msg(uid, context.bot, "➕ Ссылка на группу:\n@group_name", CANCEL_BTN)

    elif data == 'list_groups':
        groups = user_data[uid].get('groups', [])
        await send_msg(uid, context.bot, "📋 " + "\n".join(groups) if groups else "📁 Нет групп", GROUPS_MENU)

    elif data == 'remove_group':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 Нет групп", GROUPS_MENU)
            return
        kb = [[InlineKeyboardButton(f"❌ {g}", callback_data=f'del_{i}')] for i, g in enumerate(groups)]
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='my_groups')])
        await context.bot.send_message(uid, "🗑 ВЫБЕРИТЕ ГРУППУ", reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith('del_'):
        idx = int(data.split('_')[1])
        groups = user_data[uid].get('groups', [])
        if idx < len(groups):
            removed = groups.pop(idx)
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Удалена: {removed}", GROUPS_MENU)

    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Отменено")

# ==================== ЗАПУСК РАССЫЛКИ ====================
async def start_broadcast(uid, bot, bid, client: Client):
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    text = bc.get('text', '')
    interval = bc.get('interval', 30)
    media_path = get_media_path(uid, bid)
    has_photo = os.path.exists(media_path)

    valid = []
    for g in groups:
        try:
            await client.get_chat(g)
            valid.append(g)
        except:
            await send_msg(uid, bot, f"⚠️ {g} - недоступна")

    if not valid:
        await send_msg(uid, bot, "❌ Нет доступных групп")
        return

    bc['groups'] = valid
    save_data()
    await send_msg(uid, bot, f"🚀 ЗАПУСК 24/7\nГрупп: {len(valid)}\nИнтервал: {interval} сек\n\n✅ Автоподписка включена!")

    tk = f"{uid}_{bid}"
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid, text, interval, media_path, has_photo, bot))
    active_tasks[tk] = task
    
    if uid not in keep_alive_tasks or keep_alive_tasks[uid].done():
        keep_alive_tasks[uid] = asyncio.create_task(keep_alive_loop(uid))
    
    await alert_admin(bot, f"🚀 Пользователь {uid} запустил рассылку #{bid+1}\nГрупп: {len(valid)}\nИнтервал: {interval}с")

async def run_broadcast(uid, bid, client: Client, groups, text, interval, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    consecutive_errors = 0
    try:
        while True:
            for group in groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_photo(group, media_path, caption=text)
                    else:
                        success, msg = await send_with_auto_join(uid, bid, client, group, text, bot)
                        if not success:
                            consecutive_errors += 1
                            if consecutive_errors > 10:
                                await alert_admin(bot, f"⚠️ Слишком много ошибок в рассылке #{bid+1} пользователя {uid}")
                                consecutive_errors = 0
                            continue
                    consecutive_errors = 0
                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    save_data()
                except FloodWait as e:
                    wait_time = min(int(e.value), 600)
                    await alert_admin(bot, f"⏳ FloodWait {wait_time}с в рассылке #{bid+1} пользователя {uid}")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    logger.error(f"[BROADCAST] Ошибка: {e}")
                    consecutive_errors += 1
                    if consecutive_errors > 10:
                        await alert_admin(bot, f"⚠️ 10+ ошибок в рассылке #{bid+1} пользователя {uid}: {str(e)[:100]}")
                        consecutive_errors = 0
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info(f"[BROADCAST] Рассылка #{bid+1} для {uid} остановлена")
        await alert_admin(bot, f"ℹ️ Рассылка #{bid+1} пользователя {uid} остановлена (отменена)")

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    uid = update.effective_user.id
    save_user(uid)

    step_data = user_states.get(uid, {})
    step = step_data.get('step')

    if not step:
        await main_menu(uid, context.bot)
        return

    if step == 'add_group':
        if not update.message.text:
            return
        text = update.message.text.strip()
        g = text.replace('https://t.me/', '@').replace('t.me/', '@').replace('https://telegram.me/', '@').replace('telegram.dog/', '@')
        if not g.startswith('@'):
            g = '@' + g
        groups = user_data[uid].get('groups', [])
        if len(groups) >= MAX_TOTAL_GROUPS:
            await send_msg(uid, context.bot, f"❌ Максимум {MAX_TOTAL_GROUPS} групп", GROUPS_MENU)
            del user_states[uid]
            return
        if g not in groups:
            groups.append(g)
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ {g} добавлена", GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, f"⚠️ {g} уже есть", GROUPS_MENU)
        del user_states[uid]

    elif step == 'text':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        if len(text) > 4096:
            await send_msg(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_msg(uid, context.bot, "✅ Текст сохранён")
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)

    elif step == 'photo':
        bid = step_data['bid']
        if update.message.photo:
            photo = update.message.photo[-1]
            path = get_media_path(uid, bid)
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(path)
            if update.message.caption:
                user_data[uid]['broadcasts'][bid]['text'] = update.message.caption.strip()
                save_data()
            await send_msg(uid, context.bot, "✅ Фото сохранено")
        else:
            await send_msg(uid, context.bot, "❌ Отправьте фото", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)

    elif step == 'groups':
        if not update.message.text:
            return
        bid = step_data['bid']
        raw = [g.strip() for g in update.message.text.split(',') if g.strip()]
        
        if len(raw) > MAX_GROUPS_PER_BROADCAST:
            await send_msg(uid, context.bot, f"❌ Максимум {MAX_GROUPS_PER_BROADCAST} групп (у вас {len(raw)})", CANCEL_BTN)
            return
        
        groups = []
        for g in raw:
            g = g.replace('https://t.me/', '@').replace('t.me/', '@').replace('https://telegram.me/', '@').replace('telegram.dog/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        if groups:
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ {len(groups)} групп")
        else:
            await send_msg(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)

    elif step == 'interval':
        if not update.message.text:
            return
        bid = step_data['bid']
        try:
            val = int(update.message.text.strip())
            if 5 <= val <= 300:
                user_data[uid]['broadcasts'][bid]['interval'] = val
                save_data()
                await send_msg(uid, context.bot, f"✅ Интервал: {val} сек")
            else:
                await send_msg(uid, context.bot, "❌ От 5 до 300", CANCEL_BTN)
                return
        except:
            await send_msg(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)

    elif step == 'auth_phone':
        if not update.message.text:
            return
        phone = update.message.text.strip()
        if not phone.startswith('+'):
            await send_msg(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        bid = step_data['bid']
        success, err = await request_pyro_code(uid, phone, context.bot)
        if success:
            user_states[uid] = {'step': 'auth_code', 'bid': bid}
            await send_msg(uid, context.bot, "📲 Код отправлен. Введите code12345", CANCEL_BTN)
        else:
            await send_msg(uid, context.bot, f"❌ Ошибка: {err or 'неизвестная ошибка'}", MAIN_MENU)
            del user_states[uid]

    elif step == 'auth_code':
        if not update.message.text:
            return
        text = update.message.text.strip().lower()
        if not text.startswith('code'):
            await send_msg(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        code = text[4:]
        if not code.isdigit():
            await send_msg(uid, context.bot, "❌ Только цифры после code", CANCEL_BTN)
            return

        result = await sign_in_pyro(uid, code)
        if result == "2fa_needed":
            user_states[uid] = {'step': 'waiting_2fa', 'bid': user_states[uid]['bid']}
            await send_msg(uid, context.bot, "🔐 Введите пароль 2FA:", CANCEL_BTN)
            return
        elif result:
            bid = user_states[uid]['bid']
            await start_broadcast(uid, context.bot, bid, result)
            del user_states[uid]
        else:
            await send_msg(uid, context.bot, "❌ Неверный код или ошибка", MAIN_MENU)
            del user_states[uid]
            await alert_admin(context.bot, f"❌ Пользователь {uid} ввёл неверный код")

    elif step == 'waiting_2fa':
        if not update.message.text:
            return
        password = update.message.text.strip()
        client = pyro_clients.get(uid)
        if not client:
            await send_msg(uid, context.bot, "❌ Ошибка сессии", MAIN_MENU)
            del user_states[uid]
            return
        result = await sign_in_2fa(uid, password)
        if result:
            bid = user_states[uid]['bid']
            await start_broadcast(uid, context.bot, bid, result)
            del user_states[uid]
        else:
            await send_msg(uid, context.bot, "❌ Неверный пароль 2FA", CANCEL_BTN)

# ==================== HTTP СЕРВЕР ====================
async def health(request):
    return web.Response(text="OK", status=200)

async def webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return web.Response(text="OK", status=200)
    except:
        return web.Response(text="ERROR", status=500)

async def start_server():
    app = web.Application()
    app.router.add_get('/', health)
    app.router.add_get('/health', health)
    app.router.add_post(f'/webhook/{BOT_TOKEN}', webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    
    async def internal_keep_alive():
        import aiohttp
        while True:
            await asyncio.sleep(600)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{RENDER_URL}/health") as resp:
                        logger.info(f"[KEEP-ALIVE] Status: {resp.status}")
            except Exception as e:
                logger.warning(f"[KEEP-ALIVE] Failed: {e}")
    
    asyncio.create_task(internal_keep_alive())
    await asyncio.Event().wait()

# ==================== ЗАПУСК ====================
async def run():
    global bot_app
    load_data()

    bot_app = Application.builder().token(BOT_TOKEN).build()
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("skip", skip))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, message_handler))

    await bot_app.initialize()
    await bot_app.start()

    await bot_app.bot.set_webhook(f"{RENDER_URL}/webhook/{BOT_TOKEN}")

    print("=" * 60)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН (v2.1)")
    print("🤖 АВТОПОДПИСКА УЛУЧШЕНА")
    print("📊 СТАТИСТИКА И АЛЕРТЫ ДОБАВЛЕНЫ")
    print("🔒 ЛИМИТЫ ГРУПП И УТЕЧКИ ИСПРАВЛЕНЫ")
    print("🔍 ДЕТАЛЬНЫЕ ОШИБКИ АВТОРИЗАЦИИ")
    print(f"👤 АДМИН: {ADMIN_ID}")
    print("=" * 60)
    
    try:
        await bot_app.bot.send_message(ADMIN_ID, "✅ SendFlow бот запущен (v2.1)")
    except:
        pass

    await start_server()

def main():
    asyncio.run(run())

if __name__ == '__main__':
    main()
