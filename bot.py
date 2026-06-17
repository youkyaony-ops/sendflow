import asyncio
import re
import json
import os
import time
import random
import shutil
import sqlite3
import threading
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    FloodWaitError,
    AuthKeyError,
    RPCError,
    ChatWriteForbiddenError,
    ChannelPrivateError,
    UserBannedInChannelError,
    UserAlreadyParticipantError,
    InviteRequestSentError,
    MessageDeleteForbiddenError,
    ChannelInvalidError,
    UsernameNotOccupiedError,
    UsernameInvalidError
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
BACKUP_FILE = 'user_data_backup.json'
SESSIONS_DIR = 'telegram_sessions'
MEDIA_DIR = 'media_files'
LOGS_DIR = 'logs'

for folder in [SESSIONS_DIR, MEDIA_DIR, LOGS_DIR]:
    if not os.path.exists(folder):
        os.makedirs(folder)

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}

# ==================== SQLite С БЛОКИРОВКОЙ ====================
db_lock = threading.Lock()

def get_db_connection():
    try:
        conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'), timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn
    except Exception as e:
        print(f"DB connection error: {e}")
        return None

def init_db():
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                user_id INTEGER PRIMARY KEY,
                phone TEXT,
                created_at TIMESTAMP,
                last_ping TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
        print("✅ SQLite инициализирован с WAL режимом")
    else:
        print("❌ Ошибка инициализации SQLite")

init_db()

def save_session_db(user_id, phone):
    for attempt in range(3):
        with db_lock:
            conn = None
            try:
                conn = get_db_connection()
                if not conn:
                    continue
                cursor = conn.cursor()
                cursor.execute('''
                    INSERT OR REPLACE INTO sessions (user_id, phone, created_at, last_ping)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, phone, datetime.now(), datetime.now()))
                conn.commit()
                return True
            except sqlite3.OperationalError as e:
                if attempt == 2:
                    print(f"Save session error: {e}")
                time.sleep(1)
            except Exception as e:
                print(f"Save session error: {e}")
                return False
            finally:
                if conn:
                    conn.close()
    return False

def update_ping_db(user_id):
    for attempt in range(3):
        with db_lock:
            conn = None
            try:
                conn = get_db_connection()
                if not conn:
                    continue
                cursor = conn.cursor()
                cursor.execute('UPDATE sessions SET last_ping = ? WHERE user_id = ?', (datetime.now(), user_id))
                conn.commit()
                return True
            except sqlite3.OperationalError as e:
                if attempt == 2:
                    print(f"Update ping error: {e}")
                time.sleep(1)
            except Exception as e:
                print(f"Update ping error: {e}")
                return False
            finally:
                if conn:
                    conn.close()
    return False

def delete_session_db(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
            conn.commit()
            return True
        except Exception as e:
            print(f"Delete session error: {e}")
            return False
        finally:
            if conn:
                conn.close()

def has_session_db(user_id):
    with db_lock:
        conn = None
        try:
            conn = get_db_connection()
            if not conn:
                return False
            cursor = conn.cursor()
            cursor.execute('SELECT user_id FROM sessions WHERE user_id = ?', (user_id,))
            result = cursor.fetchone() is not None
            return result
        except Exception as e:
            print(f"Has session error: {e}")
            return False
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
                    'settings': data.get('settings', {'notify': True, 'autosave': True, 'def_interval': 30}),
                    'sessions': data.get('sessions', {}),
                    'created_at': data.get('created_at', str(datetime.now())),
                    'total_sent': data.get('total_sent', 0),
                    'total_errors': data.get('total_errors', 0)
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        
        with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Save data error: {e}")
        return False

def load_data():
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
        return True
    except Exception as e:
        print(f"Load data error: {e}")
        user_data = {}
        return False

def save_user(uid):
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'settings': {'notify': True, 'autosave': True, 'def_interval': 30},
            'sessions': {},
            'created_at': str(datetime.now()),
            'total_sent': 0,
            'total_errors': 0
        }
        save_data()
    return user_data[uid]

def get_media_path(uid, bid):
    return os.path.join(MEDIA_DIR, f'user_{uid}_bc_{bid}.jpg')

def get_session_path(uid):
    return os.path.join(SESSIONS_DIR, f'session_{uid}.session')

# ==================== КЛИЕНТ С АВТО-ВОССТАНОВЛЕНИЕМ ====================
async def get_client(uid):
    if uid in sessions:
        try:
            await sessions[uid].get_me()
            update_ping_db(uid)
            return sessions[uid]
        except (AuthKeyError, ConnectionError, RPCError):
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        except Exception as e:
            print(f"Client check error for {uid}: {e}")
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]

    session_file = get_session_path(uid)
    client = TelegramClient(session_file, API_ID, API_HASH)

    try:
        await client.connect()
        if await client.is_user_authorized():
            sessions[uid] = client
            update_ping_db(uid)
            print(f"[CLIENT] Клиент загружен для {uid}")
            return client
        else:
            await client.disconnect()
            return None
    except Exception as e:
        print(f"Client connection error for {uid}: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return None

async def keep_alive_loop(uid):
    consecutive_failures = 0
    while True:
        await asyncio.sleep(300)
        if uid in sessions:
            try:
                await sessions[uid].get_me()
                update_ping_db(uid)
                consecutive_failures = 0
                print(f"[KEEP_ALIVE] Пинг для {uid} успешен")
            except Exception as e:
                consecutive_failures += 1
                print(f"[KEEP_ALIVE] Ошибка для {uid} (попытка {consecutive_failures}): {e}")
                if consecutive_failures >= 3:
                    print(f"[KEEP_ALIVE] Переподключение для {uid}")
                    await get_client(uid)
                    consecutive_failures = 0

# ==================== ТЕСТОВАЯ КОМАНДА ====================
async def test_subscribe(update: Update, context):
    uid = update.effective_user.id
    await update.message.reply_text(
        "📝 Введите @username канала для подписки:\n\n"
        "Пример: @durov\n\n"
        "Эта команда проверит, работает ли подписка через ваш аккаунт."
    )
    user_states[uid] = {'step': 'test_subscribe'}

async def test_subscribe_step(update: Update, context):
    uid = update.effective_user.id
    channel = update.message.text.strip()
    
    if not channel.startswith('@'):
        channel = '@' + channel
    
    client = await get_client(uid)
    if not client:
        await update.message.reply_text("❌ Нет активной сессии. Сначала авторизуйтесь через бота.")
        return
    
    await update.message.reply_text(f"🔄 Пробую подписаться на {channel}...")
    
    try:
        await client(JoinChannelRequest(channel))
        await update.message.reply_text(f"✅ УСПЕШНО ПОДПИСАЛСЯ на {channel}")
    except UserAlreadyParticipantError:
        await update.message.reply_text(f"ℹ️ Уже подписан на {channel}")
    except FloodWaitError as e:
        await update.message.reply_text(f"⏳ Флуд-контроль: жди {e.seconds} сек")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {str(e)}\n\nВозможно, канал приватный или требует подтверждения.")
    
    del user_states[uid]

# ==================== АВТОПОДПИСКА (ПОЛНОСТЬЮ ИСПРАВЛЕННАЯ) ====================

async def auto_subscribe_simple(client, group_entity, bot, uid) -> dict:
    """
    Полностью исправленная автоподписка
    Возвращает словарь с результатами
    """
    result = {
        'channels_found': 0,
        'subscribed': 0,
        'pending': 0,
        'buttons_pressed': 0,
        'already': 0,
        'errors': 0
    }
    
    try:
        await bot.send_message(uid, "🔍 Ищу сообщения антиспам-бота...")
        
        # Используем get_messages вместо iter_messages
        try:
            messages = await client.get_messages(group_entity, limit=30)
        except Exception as e:
            await bot.send_message(uid, f"⚠️ Ошибка получения сообщений: {str(e)[:50]}")
            return result
        
        if not messages:
            await bot.send_message(uid, "⚠️ Нет сообщений для анализа")
            return result
        
        all_channels = []
        buttons_pressed = 0
        
        for msg in messages:
            if not msg.text:
                continue
            
            # Проверяем наличие ключевых слов
            text_lower = msg.text.lower()
            keywords = ['подпишись', 'подписаться', 'subscribe', 'join', 'канал', 'channel', 'чтобы писать', 'необходимо подписаться', 'для продолжения']
            has_keyword = any(word in text_lower for word in keywords)
            
            if not has_keyword:
                continue
            
            await bot.send_message(uid, f"✅ Найдено сообщение антиспам-бота")
            await bot.send_message(uid, f"📝 {msg.text[:200]}...")
            
            # Ищем @username
            at_matches = re.findall(r'@([a-zA-Z0-9_]{5,32})', msg.text)
            for channel in at_matches:
                if channel not in all_channels:
                    all_channels.append(channel)
                    await bot.send_message(uid, f"🔗 Найден канал: @{channel}")
            
            # Ищем t.me/username
            tm_matches = re.findall(r't\.me/([a-zA-Z0-9_]{5,32})', msg.text)
            for channel in tm_matches:
                if channel not in all_channels:
                    all_channels.append(channel)
                    await bot.send_message(uid, f"🔗 Найден канал: {channel}")
            
            # Ищем joinchat ссылки
            jc_matches = re.findall(r't\.me/joinchat/([a-zA-Z0-9_-]+)', msg.text)
            for match in jc_matches:
                link = f'https://t.me/joinchat/{match}'
                if link not in all_channels:
                    all_channels.append(link)
                    await bot.send_message(uid, f"🔗 Найдена ссылка: {link[:50]}...")
            
            # ===== НАЖИМАЕМ НА КНОПКИ =====
            if msg.reply_markup:
                await bot.send_message(uid, "🔘 Найдены кнопки, нажимаю...")
                for row in msg.reply_markup.rows:
                    for button in row.buttons:
                        try:
                            if hasattr(button, 'data') and button.data:
                                await msg.click(button.data)
                            else:
                                await msg.click(button.text)
                            buttons_pressed += 1
                            await bot.send_message(uid, f"✅ Нажал кнопку: {button.text}")
                            await asyncio.sleep(1)
                        except Exception as e:
                            await bot.send_message(uid, f"❌ Ошибка при нажатии {button.text}: {str(e)[:50]}")
        
        result['channels_found'] = len(all_channels)
        result['buttons_pressed'] = buttons_pressed
        
        if not all_channels:
            await bot.send_message(uid, "⚠️ Не найдено каналов в сообщениях")
            return result
        
        await bot.send_message(uid, f"📊 Найдено {len(all_channels)} каналов")
        
        # Подписываемся
        subscribed = 0
        pending = 0
        already = 0
        
        for channel in all_channels[:10]:
            # Определяем тип ссылки
            if channel.startswith('https://t.me/joinchat/') or channel.startswith('t.me/joinchat/'):
                # Пригласительная ссылка
                if 'https://' not in channel:
                    channel = 'https://' + channel
                hash_part = channel.split('/')[-1]
                await bot.send_message(uid, f"🔄 Вступаю по ссылке: {channel[:50]}...")
                
                try:
                    await client(ImportChatInviteRequest(hash_part))
                    subscribed += 1
                    await bot.send_message(uid, f"✅ Вступил по ссылке")
                    await asyncio.sleep(2)
                except UserAlreadyParticipantError:
                    already += 1
                    await bot.send_message(uid, f"ℹ️ Уже участник")
                except InviteRequestSentError:
                    pending += 1
                    await bot.send_message(uid, f"📝 Отправлена заявка")
                except Exception as e:
                    await bot.send_message(uid, f"❌ Ошибка: {str(e)[:50]}")
            else:
                # Обычный @username
                channel_full = f'@{channel}' if not channel.startswith('@') else channel
                await bot.send_message(uid, f"🔄 Подписываюсь на {channel_full}")
                
                try:
                    await client(JoinChannelRequest(channel_full))
                    subscribed += 1
                    await bot.send_message(uid, f"✅ Подписался на {channel_full}")
                    await asyncio.sleep(2)
                except UserAlreadyParticipantError:
                    already += 1
                    await bot.send_message(uid, f"ℹ️ Уже подписан на {channel_full}")
                except InviteRequestSentError:
                    pending += 1
                    await bot.send_message(uid, f"📝 Отправлена заявка на {channel_full}")
                except FloodWaitError as e:
                    await bot.send_message(uid, f"⏳ Флуд, жду {e.seconds} сек")
                    await asyncio.sleep(e.seconds)
                except UsernameNotOccupiedError:
                    await bot.send_message(uid, f"❌ Канал {channel_full} не существует")
                except Exception as e:
                    await bot.send_message(uid, f"❌ Ошибка: {str(e)[:50]}")
        
        result['subscribed'] = subscribed
        result['pending'] = pending
        result['already'] = already
        
        await bot.send_message(uid, f"📊 ИТОГ: нажато кнопок - {buttons_pressed}, подписок - {subscribed}, заявок - {pending}, уже были - {already}")
        
        return result
        
    except Exception as e:
        await bot.send_message(uid, f"❌ Ошибка: {str(e)[:150]}")
        result['errors'] += 1
        return result

# ==================== ОТПРАВКА С АВТОПОДПИСКОЙ ====================

async def send_with_auto_join(uid, bid, client, group, text, bot):
    """
    Отправляет сообщение, при ошибке запускает автоподписку
    """
    try:
        await client.send_message(group, text)
        return True, "OK"
    except FloodWaitError as e:
        wait_time = min(e.seconds, 300)
        await bot.send_message(uid, f"⏳ Флуд-контроль: жду {wait_time} сек")
        await asyncio.sleep(wait_time)
        return await send_with_auto_join(uid, bid, client, group, text, bot)
    except (ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError, MessageDeleteForbiddenError) as e:
        error_msg = str(e).lower()
        await bot.send_message(uid, f"⚠️ Требуется подписка для {group}")
        
        try:
            group_entity = await client.get_entity(group)
            result = await auto_subscribe_simple(client, group_entity, bot, uid)
            
            if result['subscribed'] > 0 or result['pending'] > 0 or result['buttons_pressed'] > 0:
                await bot.send_message(uid, f"🔄 Повторная попытка через 3 секунды...")
                await asyncio.sleep(3)
                try:
                    await client.send_message(group, text)
                    return True, "OK после подписки"
                except Exception as send_err:
                    return False, f"Не отправилось: {str(send_err)[:50]}"
            else:
                return False, "Не удалось подписаться"
        except Exception as e2:
            return False, f"Ошибка: {str(e2)[:100]}"
    except Exception as e:
        error_msg = str(e).lower()
        # Проверяем любые ошибки, связанные с подпиской
        if 'write' in error_msg or 'forbidden' in error_msg or 'subscribe' in error_msg:
            try:
                group_entity = await client.get_entity(group)
                result = await auto_subscribe_simple(client, group_entity, bot, uid)
                if result['subscribed'] > 0 or result['pending'] > 0:
                    await asyncio.sleep(3)
                    try:
                        await client.send_message(group, text)
                        return True, "OK после подписки"
                    except:
                        pass
            except:
                pass
        return False, str(e)[:100]

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data='my_stats')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'text_{bid}'), InlineKeyboardButton("📷 ФОТО", callback_data=f'photo_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'groups_{bid}'), InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'interval_{bid}')],
        [InlineKeyboardButton("🎲 РАНДОМ", callback_data=f'random_{bid}'), InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data=f'loop_{bid}')],
        [InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data=f'schedule_{bid}'), InlineKeyboardButton("📋 ПРЕВЬЮ", callback_data=f'preview_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data=f'start_247_{bid}'), InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data=f'send_once_{bid}')],
        [InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_{bid}'), InlineKeyboardButton("📊 СТАТУС", callback_data=f'status_{bid}')],
        [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data=f'clone_{bid}'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_{bid}')],
        [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ", callback_data='add_group')],
    [InlineKeyboardButton("📋 СПИСОК", callback_data='list_groups')],
    [InlineKeyboardButton("🔄 ПРОВЕРИТЬ", callback_data='check_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data='remove_group')],
    [InlineKeyboardButton("📤 ЭКСПОРТ", callback_data='export_groups')],
    [InlineKeyboardButton("📥 ИМПОРТ", callback_data='import_groups')],
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
    [InlineKeyboardButton("🚀 СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔄 РЕЖИМЫ", callback_data='help_modes')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("💡 СОВЕТЫ", callback_data='help_tips')],
    [InlineKeyboardButton("❓ FAQ", callback_data='help_faq')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])
BACK_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]])

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
    msg = text or "🥓 <b>SendFlow</b>\n\nВыберите действие:"
    await send_msg(chat_id, bot, msg, MAIN_MENU)

async def show_broadcast(uid, bot, bid):
    bc = user_data[uid]['broadcasts'][bid]
    has_photo = os.path.exists(get_media_path(uid, bid))
    is_running = f"{uid}_{bid}" in active_tasks and not active_tasks[f"{uid}_{bid}"].done()
    
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
    
    await send_msg(uid, bot, txt, get_broadcast_actions(bid))

# ==================== КОМАНДЫ ====================
async def start(update: Update, context):
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    
    welcome = f"👋 Привет, {update.effective_user.first_name}!"
    if has_session_db(uid):
        welcome += "\n\n✅ У вас есть сохранённая сессия Telegram\n🔄 Рассылки будут работать 24/7"
    else:
        welcome += "\n\n⚠️ Для запуска рассылок потребуется авторизация (один раз)"
    
    await main_menu(uid, context.bot, welcome)

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
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        
        if not broadcasts:
            await send_msg(uid, context.bot, "📢 У вас нет рассылок\n\n➕ Создайте новую", MAIN_MENU)
            return
        
        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            task_key = f"{uid}_{i}"
            is_running = task_key in active_tasks and not active_tasks[task_key].done()
            status = "🟢" if is_running else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'select_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        
        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')

    elif data == 'new_broadcast':
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        
        if len(broadcasts) >= 20:
            await send_msg(uid, context.bot, "❌ Максимум 20 рассылок\nУдалите ненужные", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        new_broadcast = {
            'name': f'Рассылка {new_id+1}',
            'text': None,
            'groups': [],
            'interval': user_data[uid].get('settings', {}).get('def_interval', 30),
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
        await show_broadcast(uid, context.bot, new_id)

    elif data == 'my_groups':
        if uid not in user_data:
            save_user(uid)
        groups = user_data[uid].get('groups', [])
        
        if not groups:
            await send_msg(uid, context.bot, "📁 У вас нет сохранённых групп\n\n➕ Добавьте первую через кнопку ниже", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n"
            for i, g in enumerate(groups[:20], 1):
                txt += f"{i}. {g}\n"
            if len(groups) > 20:
                txt += f"\n... и ещё {len(groups)-20} групп"
            await send_msg(uid, context.bot, txt, GROUPS_MENU)

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
        txt += f"🔐 Сессия: {'✅' if has_session_db(uid) else '❌'}\n"
        txt += f"📅 Регистрация: {data_u.get('created_at', 'Неизвестно')[:10]}"
        await send_msg(uid, context.bot, txt, MAIN_MENU)

    elif data == 'settings':
        if uid not in user_data:
            save_user(uid)
        s = user_data[uid].get('settings', {})
        txt = f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        txt += f"🔔 Уведомления: {'✅ Вкл' if s.get('notify', True) else '❌ Выкл'}\n"
        txt += f"💾 Автосохранение: {'✅ Вкл' if s.get('autosave', True) else '❌ Выкл'}\n"
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', 30)} сек\n"
        txt += f"🔐 Сессия: {'✅' if has_session_db(uid) else '❌'}"
        await send_msg(uid, context.bot, txt, SETTINGS_MENU)

    elif data == 'help_menu':
        await send_msg(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)

    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой текст и группы\n3️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n4️⃣ Введи номер телефона (один раз)\n5️⃣ Введи код в формате: code12345\n\n✅ Рассылка работает 24/7!\n🤖 Автоподписка включена!"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>ТЕКСТ:</b> нажми '📝 ТЕКСТ' и отправь сообщение\n<b>ФОТО:</b> нажми '📷 ФОТО' и отправь фото (подпись = текст)\n<b>ГРУППЫ:</b> через запятую @group1, @group2\n<b>ИНТЕРВАЛ:</b> время между сообщениями (5-300 сек)\n<b>РАНДОМ:</b> случайная задержка\n<b>ЗАЦИКЛИТЬ:</b> бесконечный повтор\n\n🤖 АВТОПОДПИСКА: бот сам подписывается на каналы при необходимости!"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_modes':
        txt = "🔄 <b>РЕЖИМЫ РАБОТЫ</b>\n\n<b>🚀 ЗАПУСТИТЬ 24/7:</b>\nБесконечная отправка по кругу\nГруппа1 → Группа2 → ... → ГруппаN → Группа1\n\n<b>▶️ ОТПРАВИТЬ РАЗОМ:</b>\nОдно сообщение во все группы, после чего остановка\n\n<b>🎲 РАНДОМ:</b>\nСлучайная задержка между сообщениями\n\n<b>📅 РАСПИСАНИЕ:</b>\nАвтоматический запуск в указанное время"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345 (ОБЯЗАТЕЛЬНО с буквой code)\n<b>Автоподписка:</b> бот сам подпишется на каналы если потребуется!"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_tips':
        txt = "💡 <b>СОВЕТЫ</b>\n\n• Сессия автоматически поддерживается, не требует перезапуска\n• Можно запустить несколько рассылок одновременно\n• Все данные автоматически сохраняются\n• Для отмены действия используй кнопку ОТМЕНА\n• При проблемах с подпиской - бот подпишется автоматически"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'help_faq':
        txt = "❓ <b>FAQ</b>\n\n<b>Вопрос:</b> Нужно ли каждый раз вводить код?\n<b>Ответ:</b> Нет, сессия сохраняется автоматически\n\n<b>Вопрос:</b> Сколько можно создать рассылок?\n<b>Ответ:</b> До 20 рассылок\n\n<b>Вопрос:</b> Бот подписывается на каналы сам?\n<b>Ответ:</b> Да, если чат требует подписки - бот подпишется автоматически\n\n<b>Вопрос:</b> Что делать если бот не отвечает?\n<b>Ответ:</b> Напиши /start для перезапуска"
        await send_msg(uid, context.bot, txt, HELP_MENU)

    elif data == 'clear_session':
        for tk in list(active_tasks.keys()):
            if tk.startswith(f"{uid}_"):
                try:
                    active_tasks[tk].cancel()
                except:
                    pass
                await asyncio.sleep(0.3)
                if tk in active_tasks:
                    del active_tasks[tk]
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        session_file = get_session_path(uid)
        if os.path.exists(session_file):
            os.remove(session_file)
        delete_session_db(uid)
        await send_msg(uid, context.bot, "✅ Сессия очищена", SETTINGS_MENU)

    elif data.startswith('select_'):
        bid = int(data.split('_')[1])
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('text_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'text', 'bid': bid}
        await send_msg(uid, context.bot, "📝 Отправьте текст рассылки (можно с эмодзи):", CANCEL_BTN)

    elif data.startswith('photo_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'photo', 'bid': bid}
        await send_msg(uid, context.bot, "📷 Отправьте ФОТО для рассылки\n\nПодпись к фото станет текстом сообщения", CANCEL_BTN)

    elif data.startswith('groups_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'groups', 'bid': bid}
        
        saved_groups = user_data[uid].get('groups', [])
        if saved_groups:
            kb = []
            for g in saved_groups[:10]:
                kb.append([InlineKeyboardButton(f"📌 {g}", callback_data=f'select_saved_group_{bid}_{g}')])
            kb.append([InlineKeyboardButton("✏️ ВВЕСТИ ВРУЧНУЮ", callback_data=f'manual_groups_{bid}')])
            kb.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')])
            await context.bot.send_message(uid, "👥 <b>ВЫБЕРИТЕ ГРУППЫ</b>\n\nМожно выбрать из сохранённых или ввести вручную:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await send_msg(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2", CANCEL_BTN)

    elif data.startswith('manual_groups_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'groups', 'bid': bid}
        await send_msg(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2", CANCEL_BTN)

    elif data.startswith('select_saved_group_'):
        parts = data.split('_')
        bid = int(parts[3])
        group = '_'.join(parts[4:])
        groups = user_data[uid]['broadcasts'][bid].get('groups', [])
        if group not in groups:
            groups.append(group)
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Добавлена группа: {group}\n\nВсего групп: {len(groups)}")
        else:
            await send_msg(uid, context.bot, f"⚠️ Группа {group} уже есть")
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'interval', 'bid': bid}
        await send_msg(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)

    elif data.startswith('random_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'random', 'bid': bid}
        await send_msg(uid, context.bot, "🎲 Введите диапазон случайной задержки:\n\nФормат: мин-макс\nПример: 10-30\n0 - отключить", CANCEL_BTN)

    elif data.startswith('loop_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        bc['loop'] = not bc.get('loop', True)
        save_data()
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('schedule_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'schedule', 'bid': bid}
        await send_msg(uid, context.bot, "📅 Введите время для автоматического запуска:\n\nФормат: ЧЧ:ММ\nПример: 14:30\noff - отключить", CANCEL_BTN)

    elif data.startswith('preview_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_msg(uid, context.bot, "❌ Нет текста или фото для предпросмотра")
            return
        
        preview_text = "📋 <b>ПРЕДПРОСМОТР</b>\n\n"
        if bc.get('text'):
            preview_text += f"📝 <b>Текст:</b>\n{bc['text'][:300]}\n\n"
        if has_photo:
            preview_text += "📷 <b>Фото:</b> есть\n"
        
        await send_msg(uid, context.bot, preview_text)
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('start_247_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_msg(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!", BACK_BTN)
            await show_broadcast(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_msg(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BACK_BTN)
            await show_broadcast(uid, context.bot, bid)
            return
        
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            await send_msg(uid, context.bot, f"⚠️ Рассылка #{bid+1} уже запущена!", BACK_BTN)
            await show_broadcast(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await send_msg(uid, context.bot, "✅ Использую сохранённую сессию")
            await start_broadcast(uid, context.bot, bid, client, is_247=True)
            return
        
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится автоматически)", CANCEL_BTN)

    elif data.startswith('send_once_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_msg(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!", BACK_BTN)
            await show_broadcast(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_msg(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BACK_BTN)
            await show_broadcast(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await send_msg(uid, context.bot, "✅ Использую сохранённую сессию")
            await start_broadcast(uid, context.bot, bid, client, is_247=False)
            return
        
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится автоматически)", CANCEL_BTN)

    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
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
                
                if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                    user_data[uid]['broadcasts'][bid]['active'] = False
                    save_data()
            
            await send_msg(uid, context.bot, f"🛑 Рассылка #{bid+1} ОСТАНОВЛЕНА")
        else:
            await send_msg(uid, context.bot, f"❌ Рассылка #{bid+1} не активна")
        
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('status_'):
        bid = int(data.split('_')[1])
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
        await send_msg(uid, context.bot, txt)
        await show_broadcast(uid, context.bot, bid)

    elif data.startswith('clone_'):
        bid = int(data.split('_')[1])
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 20:
            await send_msg(uid, context.bot, "❌ Достигнут лимит рассылок (20)", MAIN_MENU)
            return
        
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original.get('name', f'Рассылка {bid+1}')}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', 30),
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
        
        old_media = get_media_path(uid, bid)
        new_media = get_media_path(uid, len(broadcasts))
        if os.path.exists(old_media):
            shutil.copy(old_media, new_media)
        
        await send_msg(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)

    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            active_tasks[task_key].cancel()
            if task_key in active_tasks:
                del active_tasks[task_key]
        
        media_path = get_media_path(uid, bid)
        if os.path.exists(media_path):
            os.remove(media_path)
        
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_msg(uid, context.bot, f"🗑 Рассылка #{bid+1} удалена", MAIN_MENU)

    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_msg(uid, context.bot, "➕ Введите ссылку на группу:\n\nПример: @group_name или https://t.me/group", CANCEL_BTN)

    elif data == 'list_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 Нет сохранённых групп", GROUPS_MENU)
        else:
            txt = "📋 <b>ВСЕ ГРУППЫ</b>\n\n" + "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
            await send_msg(uid, context.bot, txt, GROUPS_MENU)

    elif data == 'check_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 Нет групп для проверки", GROUPS_MENU)
            return
        
        await send_msg(uid, context.bot, f"🔄 Проверяю {len(groups)} групп...")
        
        client = await get_client(uid)
        if not client:
            await send_msg(uid, context.bot, "❌ Нет активной сессии Telegram\nСначала запустите любую рассылку для авторизации", GROUPS_MENU)
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
        await send_msg(uid, context.bot, txt, GROUPS_MENU)

    elif data == 'remove_group':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 Нет групп для удаления", GROUPS_MENU)
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
            await send_msg(uid, context.bot, f"✅ Удалена группа: {removed}", GROUPS_MENU)

    elif data == 'export_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 Нет групп для экспорта", GROUPS_MENU)
            return
        
        export_file = f"groups_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        with open(export_file, 'w', encoding='utf-8') as f:
            f.write("\n".join(groups))
        
        with open(export_file, 'rb') as f:
            await context.bot.send_document(uid, f, caption="📁 Экспорт групп")
        
        os.remove(export_file)

    elif data == 'import_groups':
        user_states[uid] = {'step': 'import_groups'}
        await send_msg(uid, context.bot, "📥 Отправьте текстовый файл со списком групп (по одной на строку)", CANCEL_BTN)

    elif data == 'toggle_notify':
        s = user_data[uid].get('settings', {})
        s['notify'] = not s.get('notify', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_msg(uid, context.bot, f"🔔 Уведомления: {'ВКЛЮЧЕНЫ' if s['notify'] else 'ВЫКЛЮЧЕНЫ'}", SETTINGS_MENU)

    elif data == 'toggle_autosave':
        s = user_data[uid].get('settings', {})
        s['autosave'] = not s.get('autosave', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_msg(uid, context.bot, f"💾 Автосохранение: {'ВКЛЮЧЕНО' if s['autosave'] else 'ВЫКЛЮЧЕНО'}", SETTINGS_MENU)

    elif data == 'def_interval':
        user_states[uid] = {'step': 'def_interval'}
        await send_msg(uid, context.bot, "⏱ Введите интервал по умолчанию (5-300 сек):", CANCEL_BTN)

    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Действие отменено")

# ==================== ЗАПУСК РАССЫЛКИ ====================
async def start_broadcast(uid, bot, bid, client, is_247=True):
    if uid not in user_data:
        save_user(uid)
    
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    text = bc.get('text', '')
    interval = bc.get('interval', 30)
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
        except:
            await send_msg(uid, bot, f"⚠️ {group} - недоступна")
    
    if not valid_groups:
        await send_msg(uid, bot, "❌ Нет доступных групп!", MAIN_MENU)
        return
    
    bc['groups'] = valid_groups
    bc['active'] = True
    save_data()
    
    media_info = " 📷" if has_photo else ""
    
    if is_247:
        await send_msg(uid, bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n🤖 Автоподписка включена!", MAIN_MENU)
        
        task_key = f"{uid}_{bid}"
        if task_key not in active_tasks or active_tasks[task_key].done():
            task = asyncio.create_task(run_broadcast(uid, bid, client, valid_groups, text, interval, random_min, random_max, media_path, has_photo, bot))
            active_tasks[task_key] = task
            print(f"[START] Запущена рассылка #{bid+1} для пользователя {uid}")
            
            # Запускаем keep-alive
            asyncio.create_task(keep_alive_loop(uid))
        else:
            await send_msg(uid, bot, f"⚠️ Рассылка #{bid+1} уже запущена!", MAIN_MENU)
    else:
        await send_msg(uid, bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
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
                await send_msg(uid, bot, f"❌ {group}: {str(e)[:50]}")
        await send_msg(uid, bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)

# ==================== БЕСКОНЕЧНАЯ РАССЫЛКА 24/7 ====================
async def run_broadcast(uid, bid, client, groups, text, interval, random_min, random_max, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    consecutive_errors = 0
    
    try:
        while True:
            # Проверка на остановку
            if not user_data[uid]['broadcasts'][bid].get('active', True):
                print(f"[STOP] Рассылка #{bid+1} для {uid} остановлена по флагу")
                break
            
            for group in groups:
                # Проверка на отмену
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
                        # Используем отправку с автоподпиской
                        success, _ = await send_with_auto_join(uid, bid, client, group, text, bot)
                        if not success:
                            await send_msg(uid, bot, f"⚠️ Пропускаю {group}: не удалось отправить")
                            continue
                    
                    sent += 1
                    
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['sent'] = sent
                        user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                        save_data()
                    
                    consecutive_errors = 0
                    print(f"[SEND] {uid} -> рассылка #{bid+1} -> {group} (всего: {sent})")
                    
                except FloodWaitError as e:
                    wait_time = min(e.seconds, 300)
                    print(f"[FLOOD] {uid} ждёт {wait_time} сек")
                    await asyncio.sleep(wait_time)
                except Exception as e:
                    consecutive_errors += 1
                    print(f"[ERROR] {uid}: {e}")
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['errors'] = user_data[uid]['broadcasts'][bid].get('errors', 0) + 1
                        user_data[uid]['total_errors'] = user_data[uid].get('total_errors', 0) + 1
                        save_data()
                    if consecutive_errors >= 10:
                        await send_msg(uid, bot, f"⚠️ Слишком много ошибок в рассылке #{bid+1}, проверьте группы")
                        break
                    await asyncio.sleep(5)
                
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
async def message_handler(update: Update, context):
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
        try:
            val = int(text)
            if 5 <= val <= 300:
                user_data[uid]['settings']['def_interval'] = val
                save_data()
                await send_msg(uid, context.bot, f"✅ Интервал по умолчанию: {val} сек", SETTINGS_MENU)
            else:
                await send_msg(uid, context.bot, "❌ Интервал от 5 до 300 сек", CANCEL_BTN)
                return
        except:
            await send_msg(uid, context.bot, "❌ Введите число", CANCEL_BTN)
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
                    g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
                    if not g.startswith('@'):
                        g = '@' + g
                    groups.append(g)
                    added += 1
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Импортировано {added} групп\nВсего групп: {len(groups)}", GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, "❌ Отправьте текстовый файл", CANCEL_BTN)
        del user_states[uid]
    
    # ДОБАВЛЕНИЕ ГРУППЫ
    elif step == 'add_group':
        if not update.message.text:
            return
        text = update.message.text.strip()
        group = text.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
        if not group.startswith('@'):
            group = '@' + group
        groups = user_data[uid].get('groups', [])
        if group not in groups:
            groups.append(group)
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Группа {group} добавлена!", GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, f"⚠️ Группа {group} уже есть", GROUPS_MENU)
        del user_states[uid]
    
    # РЕДАКТИРОВАНИЕ ТЕКСТА
    elif step == 'text':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        
        if len(text) > 4096:
            await send_msg(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        
        if bid >= len(user_data[uid].get('broadcasts', [])):
            await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
            del user_states[uid]
            return
        
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_msg(uid, context.bot, "✅ Текст сохранён!")
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ФОТО
    elif step == 'photo':
        bid = step_data['bid']
        
        if update.message.photo:
            photo = update.message.photo[-1]
            media_path = get_media_path(uid, bid)
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(media_path)
            
            if update.message.caption:
                user_data[uid]['broadcasts'][bid]['text'] = update.message.caption.strip()
                save_data()
            
            await send_msg(uid, context.bot, "✅ Фото сохранено!\n\nПодпись к фото сохранена как текст рассылки")
            print(f"[PHOTO] Сохранено фото для рассылки #{bid+1}")
        else:
            await send_msg(uid, context.bot, "❌ Отправьте ФОТО", CANCEL_BTN)
            return
        
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ГРУПП
    elif step == 'groups':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in raw:
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if groups:
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Сохранено {len(groups)} групп!")
        else:
            await send_msg(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ИНТЕРВАЛА
    elif step == 'interval':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        try:
            val = int(text)
            if 5 <= val <= 300:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['interval'] = val
                save_data()
                await send_msg(uid, context.bot, f"✅ Интервал: {val} сек")
            else:
                await send_msg(uid, context.bot, "❌ От 5 до 300 секунд", CANCEL_BTN)
                return
        except:
            await send_msg(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ РАНДОМА
    elif step == 'random':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        if text == '0':
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['random_min'] = 0
            user_data[uid]['broadcasts'][bid]['random_max'] = 0
            save_data()
            await send_msg(uid, context.bot, "✅ Рандом отключён")
            del user_states[uid]
            await show_broadcast(uid, context.bot, bid)
            return
        
        match = re.match(r'(\d+)-(\d+)', text)
        if match:
            min_val = int(match.group(1))
            max_val = int(match.group(2))
            if 0 < min_val < max_val <= 300:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['random_min'] = min_val
                user_data[uid]['broadcasts'][bid]['random_max'] = max_val
                save_data()
                await send_msg(uid, context.bot, f"✅ Рандом: {min_val}-{max_val} сек")
            else:
                await send_msg(uid, context.bot, "❌ Диапазон: мин < макс, макс ≤ 300", CANCEL_BTN)
                return
        else:
            await send_msg(uid, context.bot, "❌ Формат: 10-30", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ РАСПИСАНИЯ
    elif step == 'schedule':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        if text.lower() == 'off':
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['schedule'] = None
            save_data()
            await send_msg(uid, context.bot, "✅ Расписание отключено")
            del user_states[uid]
            await show_broadcast(uid, context.bot, bid)
            return
        
        match = re.match(r'(\d{1,2}):(\d{2})', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['schedule'] = f"{hour:02d}:{minute:02d}"
                save_data()
                await send_msg(uid, context.bot, f"✅ Расписание: {hour:02d}:{minute:02d}")
            else:
                await send_msg(uid, context.bot, "❌ Неверное время", CANCEL_BTN)
                return
        else:
            await send_msg(uid, context.bot, "❌ Формат: 14:30", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    # АВТОРИЗАЦИЯ И ЗАПУСК
    elif step in ['auth']:
        if not update.message.text:
            return
        bid = step_data['bid']
        is_247 = True
        phone = update.message.text.strip()
        
        if not phone.startswith('+'):
            await send_msg(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        if 'sessions' not in user_data[uid]:
            user_data[uid]['sessions'] = {}
        user_data[uid]['sessions']['phone'] = phone
        save_data()
        
        user_states[uid] = {'step': 'code', 'bid': bid, 'is_247': is_247, 'phone': phone}
        
        session_file = get_session_path(uid)
        client = TelegramClient(session_file, API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(phone)
            await send_msg(uid, context.bot, "📲 Введите КОД из Telegram:\n\nПРАВИЛЬНЫЙ ФОРМАТ: code12345\n(где 12345 - цифры из сообщения)\n\n⚠️ Если ввести просто 12345 - Telegram отклонит код!", CANCEL_BTN)
        except Exception as e:
            await send_msg(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'code':
        if not update.message.text:
            return
        text = update.message.text.strip().lower()
        
        if not text.startswith('code'):
            await send_msg(uid, context.bot, "❌ НЕВЕРНЫЙ ФОРМАТ!\n\nTelegram НЕ принимает код без буквы 'code'!\n\n✅ Правильный формат: code12345\n(где 12345 - цифры из Telegram)", CANCEL_BTN)
            return
        
        code = text[4:]
        if not code.isdigit():
            await send_msg(uid, context.bot, "❌ НЕВЕРНЫЙ ФОРМАТ!\n\nПосле 'code' должны идти только цифры!\n\nПример: code12345", CANCEL_BTN)
            return
        
        user_states[uid]['code'] = code
        user_states[uid]['step'] = '2fa'
        await send_msg(uid, context.bot, "🔐 Пароль 2FA (если есть) или /skip", CANCEL_BTN)
    
    elif step == '2fa':
        if not update.message.text:
            return
        password = None if update.message.text.strip() == '/skip' else update.message.text.strip()
        client = sessions.get(uid)
        if not client:
            await send_msg(uid, context.bot, "❌ Ошибка сессии, начните /start", MAIN_MENU)
            del user_states[uid]
            return
        
        bid = user_states[uid]['bid']
        is_247 = user_states[uid]['is_247']
        phone = user_states[uid]['phone']
        code = user_states[uid]['code']
        
        if bid >= len(user_data[uid].get('broadcasts', [])):
            await send_msg(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
            del user_states[uid]
            return
        
        bc = user_data[uid]['broadcasts'][bid]
        groups = bc.get('groups', [])
        msg = bc.get('text', '')
        interval = bc.get('interval', 30)
        random_min = bc.get('random_min', 0)
        random_max = bc.get('random_max', 0)
        media_path = get_media_path(uid, bid)
        has_photo = os.path.exists(media_path)
        
        try:
            await client.sign_in(phone, code=code)
        except SessionPasswordNeededError:
            if not password:
                await send_msg(uid, context.bot, "🔐 Введите пароль 2FA:", CANCEL_BTN)
                return
            try:
                await client.sign_in(password=password)
            except:
                await send_msg(uid, context.bot, "❌ Неверный пароль", CANCEL_BTN)
                return
        except PhoneCodeInvalidError:
            await send_msg(uid, context.bot, "❌ НЕВЕРНЫЙ КОД!\n\nКод должен быть в формате: code12345\nПожалуйста, начните авторизацию заново.", MAIN_MENU)
            del user_states[uid]
            return
        except Exception as e:
            await send_msg(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
            return
        
        # Сохраняем информацию об успешной авторизации
        user_data[uid]['sessions']['is_authorized'] = True
        save_session_db(uid, phone)
        save_data()
        
        # Проверка групп
        valid_groups = []
        for group in groups:
            try:
                await client.get_entity(group)
                valid_groups.append(group)
            except:
                await send_msg(uid, context.bot, f"⚠️ {group} - недоступна")
        
        if not valid_groups:
            await send_msg(uid, context.bot, "❌ Нет доступных групп!\nПроверьте что бот добавлен в группы", MAIN_MENU)
            del user_states[uid]
            return
        
        bc['groups'] = valid_groups
        bc['active'] = True
        save_data()
        
        media_info = " 📷" if has_photo else ""
        
        if is_247:
            await send_msg(uid, context.bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n✅ Сессия сохранена!\n🤖 Автоподписка включена!", MAIN_MENU)
            
            task_key = f"{uid}_{bid}"
            if task_key not in active_tasks or active_tasks[task_key].done():
                task = asyncio.create_task(run_broadcast(uid, bid, client, valid_groups, msg, interval, random_min, random_max, media_path, has_photo, context.bot))
                active_tasks[task_key] = task
                asyncio.create_task(keep_alive_loop(uid))
        else:
            await send_msg(uid, context.bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
            success = 0
            for group in valid_groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=msg)
                    else:
                        await client.send_message(group, msg)
                    success += 1
                    await asyncio.sleep(2)
                except:
                    pass
            await send_msg(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)
        
        del user_states[uid]

# ==================== HTTP СЕРВЕР ДЛЯ RENDER ====================
async def health_check(request):
    return web.Response(text="OK", status=200)

async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return web.Response(text="OK", status=200)
    except Exception as e:
        print(f"Webhook error: {e}")
        return web.Response(text="ERROR", status=500)

async def start_http_server():
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
    await asyncio.Event().wait()

# ==================== ЗАПУСК БОТА ====================
async def run_bot():
    global bot_app
    load_data()
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("skip", skip))
    bot_app.add_handler(CommandHandler("testsub", test_subscribe))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, message_handler))
    bot_app.add_handler(MessageHandler(filters.Document.ALL, message_handler))
    
    await bot_app.initialize()
    await bot_app.start()
    
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot_app.bot.set_webhook(webhook_url)
    print(f"[WEBHOOK] Установлен: {webhook_url}")
    
    print("=" * 70)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН")
    print("=" * 70)
    print("📝 ТЕКСТ + ФОТО - полная поддержка")
    print("🔐 СЕССИИ СОХРАНЯЮТСЯ В SQLite (WAL режим)")
    print("🔄 KEEP-ALIVE КАЖДЫЕ 5 МИНУТ")
    print("🚀 24/7 РАБОТА БЕЗ ПЕРЕЗАПУСКОВ")
    print("🤖 АВТОПОДПИСКА - ИЩЕТ КАНАЛЫ В СООБЩЕНИЯХ")
    print("📌 КОД ТОЛЬКО В ФОРМАТЕ: code12345")
    print("=" * 70)
    
    await start_http_server()

def main():
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
