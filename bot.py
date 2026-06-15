import asyncio
import re
import json
import os
import time
import random
import shutil
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError, AuthKeyError, RPCError, ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError
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

# ==================== ХРАНЕНИЕ СЕССИЙ ====================
def save_session_db(user_id, phone):
    try:
        data = {'phone': phone, 'is_authorized': True, 'created_at': str(datetime.now()), 'last_used': str(datetime.now())}
        with open(os.path.join(SESSIONS_DIR, f'session_{user_id}.json'), 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

def update_ping_db(user_id):
    try:
        file_path = os.path.join(SESSIONS_DIR, f'session_{user_id}.json')
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            data['last_used'] = str(datetime.now())
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

def delete_session_db(user_id):
    try:
        file_path = os.path.join(SESSIONS_DIR, f'session_{user_id}.json')
        if os.path.exists(file_path):
            os.remove(file_path)
        session_file = os.path.join(SESSIONS_DIR, f'session_{user_id}.session')
        if os.path.exists(session_file):
            os.remove(session_file)
    except:
        pass

def has_session_db(user_id):
    file_path = os.path.join(SESSIONS_DIR, f'session_{user_id}.json')
    session_file = os.path.join(SESSIONS_DIR, f'session_{user_id}.session')
    if os.path.exists(file_path) and os.path.exists(session_file):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get('is_authorized', False)
        except:
            return False
    return False

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

# ==================== КЛИЕНТ ====================
async def get_client(uid):
    if uid in sessions:
        try:
            await sessions[uid].get_me()
            update_ping_db(uid)
            return sessions[uid]
        except:
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
            except:
                pass

# ==================== АВТОПОДПИСКА (НА ВСЁ) ====================
async def auto_join_all(client, group_entity, bot, uid) -> bool:
    """
    Ищет ссылки на чаты/группы/каналы в последних сообщениях и вступает
    """
    try:
        # Получаем последние 30 сообщений в чате
        messages = []
        async for msg in client.iter_messages(group_entity, limit=30):
            if msg.text and len(msg.text) > 10:
                messages.append(msg.text)
        
        # Ищем ссылки в сообщениях
        links = []
        
        # Паттерны для поиска (все возможные форматы)
        patterns = [
            # @username
            r'@([a-zA-Z0-9_]{5,32})',
            # t.me/username
            r't\.me/([a-zA-Z0-9_]{5,32})',
            # https://t.me/username
            r'https?://t\.me/([a-zA-Z0-9_]{5,32})',
            # Пригласительные ссылки
            r't\.me/joinchat/([a-zA-Z0-9_-]+)',
            r'https?://t\.me/joinchat/([a-zA-Z0-9_-]+)',
            r't\.me/\+([a-zA-Z0-9_-]+)',
            r'https?://t\.me/\+([a-zA-Z0-9_-]+)',
            # addlist ссылки
            r't\.me/addlist/([a-zA-Z0-9_-]+)',
            r'https?://t\.me/addlist/([a-zA-Z0-9_-]+)',
            # Русские фразы
            r'вступить\s+в\s+@([a-zA-Z0-9_]+)',
            r'перейти\s+в\s+@([a-zA-Z0-9_]+)',
            r'зайти\s+в\s+@([a-zA-Z0-9_]+)',
            r'join\s+@([a-zA-Z0-9_]+)',
            r'чат\s+@([a-zA-Z0-9_]+)',
            r'группа\s+@([a-zA-Z0-9_]+)',
            r'канал\s+@([a-zA-Z0-9_]+)',
        ]
        
        for msg in messages:
            msg_lower = msg.lower()
            for pattern in patterns:
                matches = re.findall(pattern, msg, re.IGNORECASE)
                for match in matches:
                    # Формируем ссылку
                    if match.startswith('joinchat') or '+' in match or 'addlist' in str(match):
                        link = f'https://t.me/joinchat/{match}'
                    else:
                        link = f'@{match}' if not match.startswith('@') else match
                    
                    if link not in links and len(link) > 3:
                        links.append(link)
        
        links = list(dict.fromkeys(links))
        
        if not links:
            return False
        
        await bot.send_message(uid, f"🔍 Найдены ссылки для вступления:\n" + "\n".join(links[:10]))
        
        # Вступаем в каждую найденную ссылку
        joined = []
        for link in links[:10]:
            try:
                if 'joinchat' in link or '+' in link or 'addlist' in link:
                    # Пригласительная ссылка
                    hash_part = link.split('/')[-1].split('?')[0]
                    await client(ImportChatInviteRequest(hash_part))
                    joined.append(link)
                    await bot.send_message(uid, f"✅ Вступил по ссылке: {link[:50]}...")
                else:
                    # Обычная ссылка @username
                    await client(JoinChannelRequest(link))
                    joined.append(link)
                    await bot.send_message(uid, f"✅ Вступил в: {link}")
                await asyncio.sleep(2)
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds)
                try:
                    if 'joinchat' in link or '+' in link:
                        hash_part = link.split('/')[-1].split('?')[0]
                        await client(ImportChatInviteRequest(hash_part))
                    else:
                        await client(JoinChannelRequest(link))
                    joined.append(link)
                except:
                    pass
            except Exception as e:
                await bot.send_message(uid, f"❌ Не вступил: {link[:50]}... - {str(e)[:50]}")
        
        return len(joined) > 0
        
    except Exception as e:
        print(f"Auto join error: {e}")
        return False

async def send_with_auto_join(uid, bid, client, group, text, bot):
    """
    Отправляет сообщение, при ошибке ищет ссылки и вступает
    """
    try:
        await client.send_message(group, text)
        return True, "OK"
        
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        return await send_with_auto_join(uid, bid, client, group, text, bot)
        
    except (ChatWriteForbiddenError, ChannelPrivateError, UserBannedInChannelError) as e:
        error_msg = str(e).lower()
        
        # Ключевые слова, указывающие на необходимость вступления
        join_keywords = [
            'join', 'subscribe', 'подпишись', 'подписаться', 
            'канал', 'channel', 'вступить', 'вступай', 
            'чат', 'группа', 'group', 'участником'
        ]
        
        if any(word in error_msg for word in join_keywords):
            await bot.send_message(uid, f"🔍 Требуется вступление для отправки в {group}")
            
            group_entity = await client.get_entity(group)
            joined = await auto_join_all(client, group_entity, bot, uid)
            
            if joined:
                await bot.send_message(uid, f"✅ Вступил в найденные чаты/группы, пробую отправить снова...")
                await asyncio.sleep(3)
                try:
                    await client.send_message(group, text)
                    return True, "OK после вступления"
                except Exception as send_err:
                    return False, f"Не отправилось после вступления: {str(send_err)[:50]}"
            else:
                return False, "Не удалось найти ссылки для вступления"
        else:
            return False, f"Нет прав для отправки: {str(e)[:100]}"
            
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
    [InlineKeyboardButton("🤖 АВТО-ВСТУПЛЕНИЕ", callback_data='help_auto')],
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
        new_id = len(user_data[uid].get('broadcasts', []))
        user_data[uid]['broadcasts'].append({'name': f'Рассылка {new_id+1}', 'text': None, 'groups': [], 'interval': 30, 'sent': 0})
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
        await send_msg(uid, context.bot, "🔧 2FA: пароль или /skip\n❌ Группа недоступна: добавь бота\n⚠️ Флуд: увеличь интервал", HELP_MENU)
    
    elif data == 'help_auto':
        await send_msg(uid, context.bot, "🤖 АВТО-ВСТУПЛЕНИЕ\n\nЕсли чат/группа/канал требует подписки или вступления, бот:\n1. Прочитает последние сообщения\n2. Найдёт все ссылки (t.me/..., @username)\n3. Автоматически вступит\n4. Повторит отправку\n\n✅ Работает со всеми типами ссылок!", HELP_MENU)
    
    elif data == 'clear_session':
        for tk in list(active_tasks.keys()):
            if tk.startswith(f"{uid}_"):
                try:
                    active_tasks[tk].cancel()
                except:
                    pass
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
        client = await get_client(uid)
        if client:
            await start_broadcast(uid, context.bot, bid, client)
            return
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_msg(uid, context.bot, "🔐 Номер телефона:\n+79123456789", CANCEL_BTN)
    
    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
            if tk in active_tasks:
                del active_tasks[tk]
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
        await send_msg(uid, context.bot, "✅ Склонировано", MAIN_MENU)
    
    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        tk = f"{uid}_{bid}"
        if tk in active_tasks:
            active_tasks[tk].cancel()
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
        await send_msg(uid, bot, "❌ Нет доступных групп")
        return
    
    bc['groups'] = valid
    save_data()
    await send_msg(uid, bot, f"🚀 ЗАПУСК 24/7\nГрупп: {len(valid)}\nИнтервал: {interval} сек\n\n✅ Авто-вступление включено!")
    
    tk = f"{uid}_{bid}"
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid, text, interval, media_path, has_photo, bot))
    active_tasks[tk] = task
    asyncio.create_task(keep_alive_loop(uid))

async def run_broadcast(uid, bid, client, groups, text, interval, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    try:
        while True:
            for group in groups:
                try:
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=text)
                    else:
                        success, _ = await send_with_auto_join(uid, bid, client, group, text, bot)
                        if not success:
                            continue
                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    save_data()
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except:
                    pass
                await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass

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
            await send_msg(uid, context.bot, "📲 Введите код:\ncode12345", CANCEL_BTN)
        except Exception as e:
            await send_msg(uid, context.bot, f"❌ {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'code':
        if not update.message.text:
            return
        text = update.message.text.strip().lower()
        if not text.startswith('code') or not text[4:].isdigit():
            await send_msg(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        code = text[4:]
        user_states[uid]['code'] = code
        user_states[uid]['step'] = '2fa'
        await send_msg(uid, context.bot, "🔐 Пароль 2FA или /skip", CANCEL_BTN)
    
    elif step == '2fa':
        if not update.message.text:
            return
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
    print("✅ БОТ ЗАПУЩЕН")
    print("🤖 АВТО-ВСТУПЛЕНИЕ ВКЛЮЧЕНО")
    print("📌 РАБОТАЕТ С ЧАТАМИ, ГРУППАМИ, КАНАЛАМИ")
    print("=" * 60)
    
    await start_server()

def main():
    asyncio.run(run())

if __name__ == '__main__':
    main()
