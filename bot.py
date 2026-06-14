import asyncio
import re
import json
import os
import time
import random
import shutil
import sqlite3
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
    UserBannedInChannelError
)
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
SESSIONS_DIR = 'telegram_sessions'
MEDIA_DIR = 'media_files'

for folder in [SESSIONS_DIR, MEDIA_DIR]:
    os.makedirs(folder, exist_ok=True)

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}

# ==================== SQLite ДЛЯ СЕССИЙ ====================
conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'), check_same_thread=False)
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

def save_session_db(user_id, phone):
    cursor.execute('INSERT OR REPLACE INTO sessions (user_id, phone, created_at, last_ping) VALUES (?, ?, ?, ?)',
                   (user_id, phone, datetime.now(), datetime.now()))
    conn.commit()

def update_ping_db(user_id):
    cursor.execute('UPDATE sessions SET last_ping = ? WHERE user_id = ?', (datetime.now(), user_id))
    conn.commit()

def delete_session_db(user_id):
    cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    conn.commit()

def has_session_db(user_id):
    cursor.execute('SELECT user_id FROM sessions WHERE user_id = ?', (user_id,))
    return cursor.fetchone() is not None

# ==================== ДАННЫЕ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
                    'created_at': data.get('created_at', str(datetime.now())),
                    'total_sent': data.get('total_sent', 0)
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
    except:
        pass

def load_data():
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
        else:
            user_data = {}
    except:
        user_data = {}

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

def get_session_path(uid):
    return os.path.join(SESSIONS_DIR, f'session_{uid}.session')

# ==================== КЛИЕНТ И KEEP-ALIVE ====================
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
        except:
            pass
    
    session_file = get_session_path(uid)
    client = TelegramClient(session_file, API_ID, API_HASH)
    
    try:
        await client.connect()
        if await client.is_user_authorized():
            sessions[uid] = client
            update_ping_db(uid)
            return client
    except:
        pass
    return None

async def keep_alive_loop(uid):
    while True:
        await asyncio.sleep(300)
        if uid in sessions:
            try:
                await sessions[uid].get_me()
                update_ping_db(uid)
                print(f"[KEEP_ALIVE] Пинг для {uid} успешен")
            except Exception as e:
                print(f"[KEEP_ALIVE] Ошибка для {uid}: {e}")
                await get_client(uid)

# ==================== НОВЫЕ ФУНКЦИИ ====================

async def resolve_invite_list(link: str, client) -> list:
    """
    Получает список групп из invite-ссылки вида https://t.me/addlist/...
    """
    groups = []
    try:
        if 'addlist' in link:
            hash_part = link.split('/')[-1].split('?')[0]
            updates = await client(ImportChatInviteRequest(hash_part))
            if hasattr(updates, 'chats') and updates.chats:
                for chat in updates.chats:
                    if hasattr(chat, 'username') and chat.username:
                        groups.append(f'@{chat.username}')
                    elif hasattr(chat, 'id'):
                        groups.append(str(chat.id))
        return groups
    except Exception as e:
        print(f"Ошибка парсинга invite list: {e}")
        return []

async def auto_subscribe_to_channels(client, required_channels: list) -> dict:
    """
    Автоматическая подписка на каналы
    """
    results = {'success': [], 'failed': []}
    
    for channel in required_channels:
        try:
            channel = channel.strip()
            if 't.me/' in channel:
                channel = channel.split('t.me/')[-1]
                if '?' in channel:
                    channel = channel.split('?')[0]
            if not channel.startswith('@'):
                channel = '@' + channel
            
            await client(JoinChannelRequest(channel))
            results['success'].append(channel)
            print(f"[SUBSCRIBE] Подписался на {channel}")
            await asyncio.sleep(1)
            
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            try:
                await client(JoinChannelRequest(channel))
                results['success'].append(channel)
            except:
                results['failed'].append(channel)
        except Exception as e:
            results['failed'].append(channel)
            print(f"[SUBSCRIBE] Ошибка подписки на {channel}: {e}")
    
    return results

async def parse_required_subscriptions(text: str) -> list:
    """
    Извлекает ссылки на каналы из текста сообщения об ошибке
    """
    channels = []
    
    patterns = [
        r'@([a-zA-Z0-9_]{5,32})',
        r't\.me/([a-zA-Z0-9_]{5,32})',
        r'https?://t\.me/([a-zA-Z0-9_]{5,32})',
        r'на каналы?:?\s*([@a-zA-Z0-9_|,\s]+)',
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                match = match[0]
            clean_match = match.replace('|', ' ').replace(',', ' ').strip()
            for part in clean_match.split():
                part = part.strip()
                if part.startswith('@'):
                    channels.append(part)
                elif re.match(r'^[a-zA-Z0-9_]{5,32}$', part):
                    channels.append(f'@{part}')
    
    channels = list(dict.fromkeys(channels))
    return channels

async def add_groups_from_invite(uid, bot, link, bid):
    """
    Добавляет группы из invite-ссылки в рассылку
    """
    client = await get_client(uid)
    if not client:
        await bot.send_message(uid, "❌ Нет активной сессии. Сначала запустите рассылку или авторизуйтесь.")
        return False
    
    await bot.send_message(uid, f"🔄 Обработка ссылки: {link}")
    
    try:
        groups = []
        
        if 'addlist' in link:
            groups = await resolve_invite_list(link, client)
            if not groups:
                await bot.send_message(uid, "❌ Не удалось получить группы из addlist-ссылки. Возможно, ссылка недействительна.")
                return False
                
        elif 'joinchat' in link or '/+' in link:
            try:
                hash_part = link.split('/')[-1].split('?')[0]
                updates = await client(ImportChatInviteRequest(hash_part))
                if hasattr(updates, 'chats') and updates.chats:
                    for chat in updates.chats:
                        if hasattr(chat, 'username') and chat.username:
                            groups.append(f'@{chat.username}')
                        elif hasattr(chat, 'id'):
                            groups.append(str(chat.id))
            except Exception as e:
                await bot.send_message(uid, f"❌ Ошибка вступления по ссылке: {str(e)[:100]}")
                return False
                
        else:
            group = link.replace('https://t.me/', '@').replace('t.me/', '@')
            if not group.startswith('@'):
                group = '@' + group
            groups = [group]
        
        if not groups:
            await bot.send_message(uid, "❌ Не удалось получить группы из ссылки")
            return False
        
        current_groups = user_data[uid]['broadcasts'][bid].get('groups', [])
        new_groups = list(set(current_groups + groups))
        user_data[uid]['broadcasts'][bid]['groups'] = new_groups
        save_data()
        
        await bot.send_message(uid, f"✅ Добавлено {len(groups)} групп из ссылки\n\nВсего групп: {len(new_groups)}")
        
        group_list = "\n".join(groups[:10])
        if len(groups) > 10:
            group_list += f"\n... и ещё {len(groups)-10}"
        await bot.send_message(uid, f"📋 Добавленные группы:\n{group_list}")
        
        return True
        
    except Exception as e:
        await bot.send_message(uid, f"❌ Ошибка: {str(e)[:200]}")
        return False

async def send_with_subscribe_check(uid, bid, client, group, text, bot):
    """
    Отправляет сообщение в группу, если нужно - подписывается на каналы
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await client.send_message(group, text)
            return True, "Отправлено"
            
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
            continue
            
        except (ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError) as e:
            error_msg = str(e).lower()
            
            if 'need to join' in error_msg or 'subscribe' in error_msg or 'write forbidden' in error_msg:
                try:
                    channels = await parse_required_subscriptions(error_msg)
                    
                    if channels:
                        results = await auto_subscribe_to_channels(client, channels)
                        
                        if results['success']:
                            await bot.send_message(uid, f"✅ Автоматически подписался на каналы:\n" + "\n".join(results['success']))
                            try:
                                await client.send_message(group, text)
                                return True, "Отправлено после подписки"
                            except Exception as send_err:
                                return False, f"Не удалось отправить после подписки: {str(send_err)[:50]}"
                        else:
                            await bot.send_message(uid, f"❌ Не удалось подписаться на каналы:\n" + "\n".join(results['failed']))
                            return False, "Требуется ручная подписка"
                    else:
                        return False, "Требуется подписка, но каналы не найдены"
                        
                except Exception as sub_err:
                    return False, f"Ошибка при проверке подписки: {str(sub_err)[:50]}"
            else:
                return False, str(e)[:100]
                
        except Exception as e:
            if attempt == max_retries - 1:
                return False, str(e)[:100]
            await asyncio.sleep(2)
    
    return False, "Превышено количество попыток"

# ==================== ОБНОВЛЕННЫЕ КЛАВИАТУРЫ ====================

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'text_{bid}'), InlineKeyboardButton("📷 ФОТО", callback_data=f'photo_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'groups_{bid}'), InlineKeyboardButton("📎 ADD LIST", callback_data=f'invite_{bid}')],
        [InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'interval_{bid}'), InlineKeyboardButton("🚀 ЗАПУСТИТЬ", callback_data=f'start_{bid}')],
        [InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_{bid}'), InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data=f'clone_{bid}')],
        [InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_{bid}'), InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 МОИ ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data='list_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data='remove_group')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑 ОЧИСТИТЬ СЕССИЮ", callback_data='clear_session')],
    [InlineKeyboardButton("🔄 СТАТУС", callback_data='check_status')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 БЫСТРЫЙ СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("📎 ADD LIST", callback_data='help_invite')],
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
    msg = text if text else "🥓 <b>SendFlow</b>\n\nВыберите действие:"
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
    txt += f"📨 Отправлено: {bc.get('sent', 0)}"
    
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
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_msg(uid, context.bot, "📢 У вас нет рассылок\n\n➕ Создайте новую", MAIN_MENU)
            return
        
        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            is_running = f"{uid}_{i}" in active_tasks and not active_tasks[f"{uid}_{i}"].done()
            status = "🟢" if is_running else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'select_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        
        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif data == 'new_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 20:
            await send_msg(uid, context.bot, "❌ Максимум 20 рассылок\nУдалите ненужные", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}',
            'text': None,
            'groups': [],
            'interval': 30,
            'active': False,
            'sent': 0,
            'created_at': str(datetime.now())
        })
        save_data()
        await show_broadcast(uid, context.bot, new_id)
    
    elif data == 'my_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_msg(uid, context.bot, "📁 У вас нет сохранённых групп", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n" + "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
            await send_msg(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'settings':
        await send_msg(uid, context.bot, "⚙️ <b>НАСТРОЙКИ</b>", SETTINGS_MENU)
    
    elif data == 'check_status':
        running = 0
        for tk in active_tasks:
            if tk.startswith(f"{uid}_") and not active_tasks[tk].done():
                running += 1
        has_sesh = has_session_db(uid)
        await send_msg(uid, context.bot, f"📊 <b>СТАТУС</b>\n\n🟢 Работает рассылок: {running}\n🔐 Сессия: {'✅ Активна' if has_sesh else '❌ Нет'}\n\nБот работает 24/7 автоматически", SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_msg(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой ТЕКСТ или ФОТО\n3️⃣ Настрой ГРУППЫ\n4️⃣ Нажми '🚀 ЗАПУСТИТЬ'\n5️⃣ Введи номер телефона\n6️⃣ Введи КОД в формате: code12345\n\n✅ Рассылка работает 24/7!"
        await send_msg(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>ТЕКСТ:</b> нажми '📝 ТЕКСТ' и отправь сообщение\n<b>ФОТО:</b> нажми '📷 ФОТО' и отправь фото (подпись = текст)\n<b>ГРУППЫ:</b> через запятую @group1, @group2 или через кнопку ADD LIST\n<b>ИНТЕРВАЛ:</b> время между сообщениями (5-300 сек)"
        await send_msg(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> Вводите строго в формате: code12345"
        await send_msg(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_invite':
        txt = "📎 <b>ADD LIST - ИНСТРУКЦИЯ</b>\n\n<b>Что это?</b>\nКнопка позволяет добавить сразу несколько групп по одной ссылке.\n\n<b>Поддерживаемые форматы:</b>\n• https://t.me/addlist/xxxxx (список групп)\n• https://t.me/joinchat/xxxxx\n• https://t.me/+xxxxx\n\n<b>Как использовать:</b>\n1. Нажми '📎 ADD LIST'\n2. Вставь ссылку-приглашение\n3. Бот автоматически добавит все группы из списка\n\n<b>Авто-подписка:</b>\nЕсли чат требует подписки на каналы, бот подпишется автоматически!"
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
        
        if uid in user_data:
            user_data[uid]['sessions'] = {}
            for bc in user_data[uid].get('broadcasts', []):
                bc['active'] = False
            save_data()
        
        await send_msg(uid, context.bot, "🗑 Сессия очищена\n✅ Все рассылки остановлены\n\nТеперь можно запустить рассылку заново", SETTINGS_MENU)
    
    elif data.startswith('select_'):
        bid = int(data.split('_')[1])
        await show_broadcast(uid, context.bot, bid)
    
    elif data.startswith('text_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'text', 'bid': bid}
        await send_msg(uid, context.bot, "📝 Отправьте текст рассылки:", CANCEL_BTN)
    
    elif data.startswith('photo_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'photo', 'bid': bid}
        await send_msg(uid, context.bot, "📷 Отправьте фото\n\nПодпись к фото станет текстом сообщения", CANCEL_BTN)
    
    elif data.startswith('groups_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'groups', 'bid': bid}
        await send_msg(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2\n\nИли используйте кнопку ADD LIST для массового добавления", CANCEL_BTN)
    
    elif data.startswith('invite_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'invite', 'bid': bid}
        await send_msg(uid, context.bot, "📎 Вставьте ссылку-приглашение:\n\nПоддерживаются форматы:\n• https://t.me/addlist/xxxxx (список групп)\n• https://t.me/joinchat/xxxxx\n• https://t.me/+xxxxx\n• https://t.me/groupname", CANCEL_BTN)
    
    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'interval', 'bid': bid}
        await send_msg(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)
    
    elif data.startswith('start_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_msg(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО")
            await show_broadcast(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_msg(uid, context.bot, "❌ Сначала настройте ГРУППЫ")
            await show_broadcast(uid, context.bot, bid)
            return
        
        if f"{uid}_{bid}" in active_tasks and not active_tasks[f"{uid}_{bid}"].done():
            await send_msg(uid, context.bot, "⚠️ Рассылка уже запущена")
            await show_broadcast(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await start_broadcast(uid, context.bot, bid, client)
            return
        
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789", CANCEL_BTN)
    
    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
            await asyncio.sleep(0.3)
            if tk in active_tasks:
                del active_tasks[tk]
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_msg(uid, context.bot, f"🛑 Рассылка #{bid+1} остановлена")
        await show_broadcast(uid, context.bot, bid)
    
    elif data.startswith('clone_'):
        bid = int(data.split('_')[1])
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 20:
            await send_msg(uid, context.bot, "❌ Максимум 20 рассылок", MAIN_MENU)
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
        await send_msg(uid, context.bot, "✅ Рассылка склонирована", MAIN_MENU)
    
    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
            await asyncio.sleep(0.3)
            if tk in active_tasks:
                del active_tasks[tk]
        media = get_media_path(uid, bid)
        if os.path.exists(media):
            os.remove(media)
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_msg(uid, context.bot, "🗑 Рассылка удалена", MAIN_MENU)
    
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_msg(uid, context.bot, "➕ Введите ссылку на группу:\n@group_name", CANCEL_BTN)
    
    elif data == 'list_groups':
        groups = user_data[uid].get('groups', [])
        if groups:
            await send_msg(uid, context.bot, "📋 " + "\n".join(groups), GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, "📁 Нет групп", GROUPS_MENU)
    
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
async def start_broadcast(uid, bot, bid, client):
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    text = bc.get('text', '')
    interval = bc.get('interval', 30)
    media_path = get_media_path(uid, bid)
    has_photo = os.path.exists(media_path)
    
    valid = []
    for g in groups:
        try:
            await client.get_entity(g)
            valid.append(g)
        except:
            await send_msg(uid, bot, f"⚠️ {g} - недоступна")
    
    if not valid:
        await send_msg(uid, bot, "❌ Нет доступных групп!")
        return
    
    bc['groups'] = valid
    bc['active'] = True
    save_data()
    
    await send_msg(uid, bot, f"🚀 ЗАПУСК 24/7\n\nГрупп: {len(valid)}\nИнтервал: {interval} сек\n\n✅ Будет автоматически подписываться на каналы при необходимости!")
    
    tk = f"{uid}_{bid}"
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid, text, interval, media_path, has_photo, bot))
    active_tasks[tk] = task
    
    asyncio.create_task(keep_alive_loop(uid))

async def run_broadcast(uid, bid, client, groups, text, interval, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    consecutive_errors = 0
    
    try:
        while True:
            for group in groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=text)
                    else:
                        success, result = await send_with_subscribe_check(uid, bid, client, group, text, bot)
                        if not success:
                            await bot.send_message(uid, f"⚠️ {group}: {result}")
                            continue
                    
                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                    save_data()
                    consecutive_errors = 0
                    
                except FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                except (AuthKeyError, ConnectionError, RPCError) as e:
                    consecutive_errors += 1
                    if consecutive_errors >= 3:
                        new_client = await get_client(uid)
                        if new_client:
                            client = new_client
                            consecutive_errors = 0
                            print(f"[RECOVERY] Клиент восстановлен для {uid}")
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[ERROR] {uid}: {e}")
                    await asyncio.sleep(5)
                
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        user_data[uid]['broadcasts'][bid]['active'] = False
        save_data()
        print(f"[STOP] Рассылка #{bid+1} для {uid} остановлена")

# ==================== ОБРАБОТЧИК ====================
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
        g = text.replace('https://t.me/', '@').replace('t.me/', '@')
        if not g.startswith('@'):
            g = '@' + g
        groups = user_data[uid].get('groups', [])
        if g not in groups:
            groups.append(g)
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Группа {g} добавлена", GROUPS_MENU)
        del user_states[uid]
    
    elif step == 'text':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        if len(text) > 4096:
            await send_msg(uid, context.bot, "❌ Текст слишком длинный", CANCEL_BTN)
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
        groups = []
        for g in raw:
            g = g.replace('https://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        if groups:
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ Сохранено {len(groups)} групп")
        else:
            await send_msg(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    elif step == 'invite':
        if not update.message.text:
            return
        bid = step_data['bid']
        link = update.message.text.strip()
        await add_groups_from_invite(uid, context.bot, link, bid)
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
    
    elif step == 'auth':
        if not update.message.text:
            return
        bid = step_data['bid']
        phone = update.message.text.strip()
        if not phone.startswith('+'):
            await send_msg(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        user_states[uid] = {'step': 'code', 'bid': bid, 'phone': phone}
        
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
            await send_msg(uid, context.bot, "❌ Ошибка сессии", MAIN_MENU)
            del user_states[uid]
            return
        
        bid = user_states[uid]['bid']
        phone = user_states[uid]['phone']
        code = user_states[uid]['code']
        
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
        
        save_session_db(uid, phone)
        await start_broadcast(uid, context.bot, bid, client)
        del user_states[uid]

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
    print("✅ SENDFLOW БОТ ЗАПУЩЕН")
    print("=" * 60)
    print("📝 ТЕКСТ + ФОТО - полная поддержка")
    print("📎 ADD LIST - массовое добавление групп")
    print("🔄 АВТО-ПОДПИСКА на каналы")
    print("🔐 СЕССИИ СОХРАНЯЮТСЯ В SQLite")
    print("🚀 24/7 РАБОТА БЕЗ ПЕРЕЗАПУСКОВ")
    print("=" * 60)
    
    await start_server()

def main():
    asyncio.run(run())

if __name__ == '__main__':
    main()
