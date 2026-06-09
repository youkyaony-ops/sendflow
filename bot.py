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
from telethon.errors import SessionPasswordNeededError, FloodWaitError, AuthKeyError
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
    if not os.path.exists(folder):
        os.makedirs(folder)

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}
last_ping = {}

# ==================== SQLite ДЛЯ СЕССИЙ (НАДЁЖНЕЕ) ====================
def init_sessions_db():
    conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'))
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            user_id INTEGER PRIMARY KEY,
            session_string TEXT,
            phone TEXT,
            created_at TIMESTAMP,
            last_ping TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

init_sessions_db()

def save_session_to_db(user_id, session_string, phone):
    conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO sessions (user_id, session_string, phone, created_at, last_ping)
        VALUES (?, ?, ?, ?, ?)
    ''', (user_id, session_string, phone, datetime.now(), datetime.now()))
    conn.commit()
    conn.close()

def get_session_from_db(user_id):
    conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'))
    cursor = conn.cursor()
    cursor.execute('SELECT session_string, phone FROM sessions WHERE user_id = ?', (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row if row else (None, None)

def update_session_ping(user_id):
    conn = sqlite3.connect(os.path.join(SESSIONS_DIR, 'sessions.db'))
    cursor = conn.cursor()
    cursor.execute('UPDATE sessions SET last_ping = ? WHERE user_id = ?', (datetime.now(), user_id))
    conn.commit()
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

async def get_client(uid):
    """Получение клиента с правильным хранением сессии"""
    if uid in sessions:
        try:
            await sessions[uid].get_me()
            return sessions[uid]
        except AuthKeyError:
            print(f"[AUTH] Ключ авторизации для {uid} устарел, пересоздаём...")
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
            # Сохраняем строку сессии в БД для надёжности
            session_string = client.session.save()
            save_session_to_db(uid, session_string, user_data[uid].get('sessions', {}).get('phone', ''))
            return client
    except Exception as e:
        print(f"[CLIENT] Ошибка для {uid}: {e}")
    
    return None

async def keep_alive(uid, bot):
    """Пинг Telegram каждые 5 минут, чтобы сессия не умирала"""
    while True:
        await asyncio.sleep(300)  # 5 минут
        if uid in sessions:
            try:
                await sessions[uid].get_me()
                update_session_ping(uid)
                print(f"[KEEP_ALIVE] Пинг для {uid} успешен")
            except Exception as e:
                print(f"[KEEP_ALIVE] Ошибка для {uid}: {e}")

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 МОИ ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'text_{bid}'), InlineKeyboardButton("📷 ФОТО", callback_data=f'photo_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'groups_{bid}'), InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'interval_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ", callback_data=f'start_{bid}'), InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_{bid}')],
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
    msg = text if text else "🥓 SendFlow\n\nВыберите действие:"
    await send_msg(chat_id, bot, msg, MAIN_MENU)

async def show_broadcast(uid, bot, bid):
    bc = user_data[uid]['broadcasts'][bid]
    has_photo = os.path.exists(get_media_path(uid, bid))
    is_running = f"{uid}_{bid}" in active_tasks and not active_tasks[f"{uid}_{bid}"].done()
    
    txt = f"📢 <b>{bc['name']}</b>\n\n"
    txt += f"Статус: {'🟢 РАБОТАЕТ' if is_running else '🔴 СТОП'}\n"
    txt += f"📝 Текст: {'✅' if bc.get('text') else '❌'}\n"
    txt += f"📷 Фото: {'✅' if has_photo else '❌'}\n"
    txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
    txt += f"⏱ Интервал: {bc.get('interval', 30)} сек\n"
    txt += f"📨 Отправлено: {bc.get('sent', 0)}"
    
    await send_msg(uid, bot, txt, get_broadcast_actions(bid))

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
            is_running = f"{uid}_{i}" in active_tasks and not active_tasks[f"{uid}_{i}"].done()
            status = "🟢" if is_running else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {bc['name']}", callback_data=f'select_{i}')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        await context.bot.send_message(uid, "📋 ВАШИ РАССЫЛКИ", reply_markup=InlineKeyboardMarkup(kb))
    
    elif data == 'new_broadcast':
        new_id = len(user_data[uid].get('broadcasts', []))
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}',
            'text': None, 'groups': [], 'interval': 30,
            'sent': 0, 'active': False
        })
        save_data()
        await show_broadcast(uid, context.bot, new_id)
    
    elif data == 'my_groups':
        groups = user_data[uid].get('groups', [])
        if groups:
            await send_msg(uid, context.bot, "📁 ВАШИ ГРУППЫ\n\n" + "\n".join(groups), GROUPS_MENU)
        else:
            await send_msg(uid, context.bot, "📁 Нет групп", GROUPS_MENU)
    
    elif data == 'settings':
        await send_msg(uid, context.bot, "⚙️ НАСТРОЙКИ\n\nСессия сохраняется и не требует перезапуска", SETTINGS_MENU)
    
    elif data == 'check_status':
        broadcasts = user_data[uid].get('broadcasts', [])
        running = 0
        for i in range(len(broadcasts)):
            if f"{uid}_{i}" in active_tasks and not active_tasks[f"{uid}_{i}"].done():
                running += 1
        has_session = uid in sessions
        await send_msg(uid, context.bot, f"📊 СТАТУС\n\n🟢 Работает: {running}\n📢 Всего: {len(broadcasts)}\n🔐 Сессия: {'✅ Активна' if has_session else '❌ Нет'}\n\nБот работает 24/7 без перезапусков", SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_msg(uid, context.bot, "❓ ПОМОЩЬ", HELP_MENU)
    
    elif data == 'help_quick':
        await send_msg(uid, context.bot, "🚀 1. Новая рассылка\n2. Настрой текст/фото\n3. Настрой группы\n4. Запустить\n5. Авторизация\n\n✅ Работает 24/7 без перезапусков!", HELP_MENU)
    
    elif data == 'help_create':
        await send_msg(uid, context.bot, "📝 ТЕКСТ: отправь сообщение\n📷 ФОТО: отправь фото (подпись = текст)\n👥 ГРУППЫ: @group1, @group2\n⏱ ИНТЕРВАЛ: 5-300 сек", HELP_MENU)
    
    elif data == 'help_errors':
        await send_msg(uid, context.bot, "🔧 2FA: пароль или /skip\n❌ Группа недоступна: добавь бота\n⚠️ Флуд: увеличь интервал", HELP_MENU)
    
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
        await send_msg(uid, context.bot, "👥 Группы через запятую:\n@group1, @group2", CANCEL_BTN)
    
    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'interval', 'bid': bid}
        await send_msg(uid, context.bot, "⏱ Интервал (5-300 сек):", CANCEL_BTN)
    
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
        
        if f"{uid}_{bid}" in active_tasks and not active_tasks[f"{uid}_{bid}"].done():
            await send_msg(uid, context.bot, "⚠️ Уже запущена")
            await show_broadcast(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
        if client:
            await start_broadcast(uid, context.bot, bid, client)
            return
        
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Номер телефона:\n+79123456789", CANCEL_BTN)
    
    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            if task_key in active_tasks:
                del active_tasks[task_key]
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_msg(uid, context.bot, f"🛑 Рассылка #{bid+1} остановлена")
        await show_broadcast(uid, context.bot, bid)
    
    elif data.startswith('clone_'):
        bid = int(data.split('_')[1])
        if len(user_data[uid]['broadcasts']) >= 10:
            await send_msg(uid, context.bot, "❌ Максимум 10 рассылок", MAIN_MENU)
            return
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original['name']}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', 30),
            'active': False, 'sent': 0
        }
        user_data[uid]['broadcasts'].append(new_bc)
        old_media = get_media_path(uid, bid)
        new_media = get_media_path(uid, len(user_data[uid]['broadcasts']) - 1)
        if os.path.exists(old_media):
            shutil.copy(old_media, new_media)
        save_data()
        await send_msg(uid, context.bot, "✅ Склонировано", MAIN_MENU)
    
    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
        media = get_media_path(uid, bid)
        if os.path.exists(media):
            os.remove(media)
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_msg(uid, context.bot, "🗑 Удалено", MAIN_MENU)
    
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_msg(uid, context.bot, "➕ Ссылка на группу:\n@group_name", CANCEL_BTN)
    
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
    
    # Проверяем группы
    valid = []
    for g in groups:
        try:
            await client.get_entity(g)
            valid.append(g)
        except:
            await send_msg(uid, bot, f"⚠️ {g} - недоступна")
    
    if not valid:
        await send_msg(uid, bot, "❌ Нет доступных групп")
        return
    
    bc['groups'] = valid
    bc['active'] = True
    save_data()
    
    await send_msg(uid, bot, f"🚀 ЗАПУСК 24/7\nГрупп: {len(valid)}\nИнтервал: {interval} сек\n\n✅ Рассылка будет работать без перезапусков!")
    
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid, text, interval, media_path, has_photo, bot))
    active_tasks[f"{uid}_{bid}"] = task
    
    # Запускаем пинг для поддержания сессии
    asyncio.create_task(keep_alive(uid, bot))

async def run_broadcast(uid, bid, client, groups, text, interval, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    
    try:
        while True:
            for group in groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=text)
                    else:
                        await client.send_message(group, text)
                    
                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                    save_data()
                    
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"Ошибка {uid}: {e}")
                    await asyncio.sleep(5)
                
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        user_data[uid]['broadcasts'][bid]['active'] = False
        save_data()

# ==================== ОБРАБОТЧИК ====================
async def message_handler(update: Update, context):
    uid = update.effective_user.id
    if uid not in user_data:
        save_user(uid)
    
    step_data = user_states.get(uid, {})
    step = step_data.get('step')
    
    if not step:
        await main_menu(uid, context.bot)
        return
    
    if step == 'add_group':
        text = update.message.text.strip()
        g = text.replace('https://t.me/', '@').replace('t.me/', '@')
        if not g.startswith('@'):
            g = '@' + g
        groups = user_data[uid].get('groups', [])
        if g not in groups:
            groups.append(g)
            user_data[uid]['groups'] = groups
            save_data()
            await send_msg(uid, context.bot, f"✅ {g} добавлена", GROUPS_MENU)
        del user_states[uid]
    
    elif step == 'text':
        bid = step_data['bid']
        text = update.message.text.strip()
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_msg(uid, context.bot, "✅ Текст сохранён")
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
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
            await send_msg(uid, context.bot, "✅ Фото сохранено")
        else:
            await send_msg(uid, context.bot, "❌ Отправьте фото", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    elif step == 'groups':
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
            await send_msg(uid, context.bot, f"✅ {len(groups)} групп")
        del user_states[uid]
        await show_broadcast(uid, context.bot, bid)
    
    elif step == 'interval':
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
        phone = update.message.text.strip()
        if not phone.startswith('+'):
            await send_msg(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        user_states[uid] = {'step': 'code', 'bid': step_data['bid'], 'phone': phone}
        
        session_file = get_session_path(uid)
        client = TelegramClient(session_file, API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(phone)
            await send_msg(uid, context.bot, "📲 Код: code12345", CANCEL_BTN)
        except Exception as e:
            await send_msg(uid, context.bot, f"❌ {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'code':
        match = re.search(r'(\d{5,6})', update.message.text.strip())
        code = match.group(1) if match else None
        if not code:
            await send_msg(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        
        user_states[uid]['code'] = code
        user_states[uid]['step'] = '2fa'
        await send_msg(uid, context.bot, "🔐 Пароль 2FA или /skip", CANCEL_BTN)
    
    elif step == '2fa':
        password = None if update.message.text.strip() == '/skip' else update.message.text.strip()
        client = sessions.get(uid)
        if not client:
            await send_msg(uid, context.bot, "❌ Ошибка", MAIN_MENU)
            del user_states[uid]
            return
        
        bid = user_states[uid]['bid']
        phone = user_states[uid]['phone']
        code = user_states[uid]['code']
        
        try:
            await client.sign_in(phone, code=code)
        except SessionPasswordNeededError:
            if not password:
                await send_msg(uid, context.bot, "🔐 Введите пароль:", CANCEL_BTN)
                return
            try:
                await client.sign_in(password=password)
            except:
                await send_msg(uid, context.bot, "❌ Неверный пароль", CANCEL_BTN)
                return
        except Exception as e:
            await send_msg(uid, context.bot, f"❌ {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
            return
        
        user_data[uid]['sessions'] = {'phone': phone, 'is_authorized': True}
        save_data()
        
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
    
    print("=" * 50)
    print("✅ БОТ ЗАПУЩЕН")
    print("=" * 50)
    print("📝 ТЕКСТ + ФОТО")
    print("🔄 РАБОТАЕТ 24/7 БЕЗ ПЕРЕЗАПУСКОВ")
    print("🔐 СЕССИЯ ПОДДЕРЖИВАЕТСЯ ПИНГОМ КАЖДЫЕ 5 МИНУТ")
    print("=" * 50)
    
    await start_server()

def main():
    asyncio.run(run())

if __name__ == '__main__':
    main()
