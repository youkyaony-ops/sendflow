import asyncio
import re
import json
import os
import logging
import time
import random
import shutil
from datetime import datetime, timedelta
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError, AuthKeyError, RPCError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ==================== НАСТРОЙКА ====================
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

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

# ==================== СОХРАНЕНИЕ ДАННЫХ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
                    'sessions': data.get('sessions', {}),
                    'created_at': data.get('created_at', str(datetime.now())),
                    'total_sent': data.get('total_sent', 0)
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        return True
    except:
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
    except:
        user_data = {}
        return False

def save_user(uid):
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'sessions': {},
            'created_at': str(datetime.now()),
            'total_sent': 0
        }
        save_data()
    return user_data[uid]

def get_media_path(uid, bid):
    return os.path.join(MEDIA_DIR, f'user_{uid}_bc_{bid}.jpg')

def get_session_path(uid):
    return os.path.join(SESSIONS_DIR, f'session_{uid}.session')

async def get_client_with_retry(uid, max_retries=5):
    """Получение клиента с автоматическими повторами при ошибке"""
    for attempt in range(max_retries):
        try:
            if uid in sessions:
                try:
                    await sessions[uid].get_me()
                    return sessions[uid]
                except AuthKeyError:
                    # Сессия умерла, удаляем
                    print(f"[AUTH] Сессия для {uid} умерла, удаляем...")
                    try:
                        await sessions[uid].disconnect()
                    except:
                        pass
                    del sessions[uid]
                except:
                    pass
            
            session_file = get_session_path(uid)
            client = TelegramClient(session_file, API_ID, API_HASH)
            
            await client.connect()
            if await client.is_user_authorized():
                sessions[uid] = client
                print(f"[CLIENT] Клиент для {uid} успешно подключён")
                return client
            else:
                await client.disconnect()
                return None
                
        except Exception as e:
            print(f"[CLIENT] Ошибка {attempt+1}/{max_retries} для {uid}: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(5)
            else:
                return None
    return None

async def auto_restart_broadcast(uid, bid, bot, reason=""):
    """Автоматический перезапуск рассылки при ошибке"""
    print(f"[AUTO_RESTART] Перезапуск рассылки {uid}_{bid}, причина: {reason}")
    
    # Останавливаем старую задачу
    task_key = f"{uid}_{bid}"
    if task_key in active_tasks:
        active_tasks[task_key].cancel()
        await asyncio.sleep(1)
        if task_key in active_tasks:
            del active_tasks[task_key]
    
    # Ждём немного
    await asyncio.sleep(3)
    
    # Получаем свежий клиент
    client = await get_client_with_retry(uid)
    
    if client:
        # Перезапускаем рассылку
        bc = user_data[uid]['broadcasts'][bid]
        groups = bc.get('groups', [])
        text = bc.get('text', '')
        interval = bc.get('interval', 30)
        media_path = get_media_path(uid, bid)
        has_photo = os.path.exists(media_path)
        
        valid_groups = []
        for group in groups:
            try:
                await client.get_entity(group)
                valid_groups.append(group)
            except:
                pass
        
        if valid_groups:
            bc['groups'] = valid_groups
            bc['active'] = True
            save_data()
            
            task = asyncio.create_task(run_broadcast(uid, bid, client, valid_groups, text, interval, media_path, has_photo, bot))
            active_tasks[task_key] = task
            await bot.send_message(uid, f"🔄 РАССЫЛКА #{bid+1} АВТОМАТИЧЕСКИ ВОССТАНОВЛЕНА!\n\nПричина: {reason}\nРабота продолжается.")
            print(f"[AUTO_RESTART] Рассылка {uid}_{bid} успешно перезапущена")
            return True
        else:
            await bot.send_message(uid, f"⚠️ НЕ УДАЛОСЬ ВОССТАНОВИТЬ РАССЫЛКУ #{bid+1}\n\nНет доступных групп. Проверьте что бот добавлен в группы.")
            return False
    else:
        await bot.send_message(uid, f"❌ НЕ УДАЛОСЬ ПЕРЕЗАПУСТИТЬ РАССЫЛКУ #{bid+1}\n\nСессия потеряна. Нажмите 'ОЧИСТИТЬ СЕССИЮ' и запустите рассылку заново.")
        return False

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
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data='list_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data='remove_group')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑 ОЧИСТИТЬ СЕССИЮ", callback_data='clear_session')],
    [InlineKeyboardButton("🔄 ПРОВЕРИТЬ СТАТУС", callback_data='check_status')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

HELP_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🚀 БЫСТРЫЙ СТАРТ", callback_data='help_quick')],
    [InlineKeyboardButton("📢 КАК СОЗДАТЬ", callback_data='help_create')],
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def send_safe(chat_id, bot, text, keyboard=None):
    try:
        if keyboard:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await bot.send_message(chat_id, text, parse_mode='HTML')
        return True
    except:
        return False

async def main_menu(chat_id, bot, text=None):
    msg = text if text else "🥓 <b>SendFlow</b>\n\nВыберите действие:"
    await send_safe(chat_id, bot, msg, MAIN_MENU)

async def show_broadcast_menu(uid, bot, bid):
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
    txt += f"📨 Отправлено: {bc.get('sent', 0)}"
    
    await send_safe(uid, bot, txt, get_broadcast_actions(bid))

# ==================== КОМАНДЫ ====================
async def start_cmd(update: Update, context):
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    await main_menu(uid, context.bot, f"👋 Привет, {update.effective_user.first_name}!")

async def skip_cmd(update: Update, context):
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
            await send_safe(uid, context.bot, "📢 У вас нет рассылок\n\n➕ Создайте новую", MAIN_MENU)
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
        
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Максимум 10 рассылок", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        new_broadcast = {
            'name': f'Рассылка {new_id+1}',
            'text': None,
            'groups': [],
            'interval': 30,
            'active': False,
            'sent': 0,
            'created_at': str(datetime.now())
        }
        user_data[uid]['broadcasts'].append(new_broadcast)
        save_data()
        await show_broadcast_menu(uid, context.bot, new_id)
    
    elif data == 'my_groups':
        if uid not in user_data:
            save_user(uid)
        groups = user_data[uid].get('groups', [])
        
        if not groups:
            await send_safe(uid, context.bot, "📁 У вас нет сохранённых групп", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n" + "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'settings':
        await send_safe(uid, context.bot, "⚙️ <b>НАСТРОЙКИ</b>", SETTINGS_MENU)
    
    elif data == 'check_status':
        broadcasts = user_data[uid].get('broadcasts', [])
        running = 0
        for i, bc in enumerate(broadcasts):
            task_key = f"{uid}_{i}"
            if task_key in active_tasks and not active_tasks[task_key].done():
                running += 1
        
        await send_safe(uid, context.bot, f"📊 <b>СТАТУС</b>\n\n🟢 Активных рассылок: {running}\n📢 Всего рассылок: {len(broadcasts)}\n\nРассылки работают автоматически и сами восстанавливаются при ошибках.", SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой ТЕКСТ или ФОТО\n3️⃣ Настрой ГРУППЫ\n4️⃣ Нажми '🚀 ЗАПУСТИТЬ'\n5️⃣ Авторизуйся (один раз)\n\n✅ Рассылка будет работать 24/7 и сама восстанавливаться!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>ТЕКСТ:</b> нажми '📝 ТЕКСТ' и отправь сообщение\n<b>ФОТО:</b> нажми '📷 ФОТО' и отправь фото (подпись = текст)\n<b>ГРУППЫ:</b> через запятую @group1, @group2\n<b>ИНТЕРВАЛ:</b> время между сообщениями (5-300 сек)\n\n⚡ Рассылка сама восстанавливается при ошибках!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345\n\n✅ Рассылка автоматически перезапускается при ошибках сессии!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'clear_session':
        for task_key in list(active_tasks.keys()):
            if task_key.startswith(f"{uid}_"):
                active_tasks[task_key].cancel()
                await asyncio.sleep(0.5)
                if task_key in active_tasks:
                    del active_tasks[task_key]
        
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
        
        if uid in user_data:
            for bc in user_data[uid].get('broadcasts', []):
                bc['active'] = False
            save_data()
        
        await send_safe(uid, context.bot, "🗑 Сессия очищена\n✅ Все рассылки остановлены\n\nТеперь можно запустить рассылку заново.", SETTINGS_MENU)
    
    elif data.startswith('select_'):
        bid = int(data.split('_')[1])
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('text_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_text', 'bid': bid}
        await send_safe(uid, context.bot, "📝 Отправьте текст рассылки:", CANCEL_BTN)
    
    elif data.startswith('photo_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_photo', 'bid': bid}
        await send_safe(uid, context.bot, "📷 Отправьте фото\n\nПодпись к фото станет текстом сообщения", CANCEL_BTN)
    
    elif data.startswith('groups_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2", CANCEL_BTN)
    
    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_interval', 'bid': bid}
        await send_safe(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)
    
    elif data.startswith('start_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        has_photo = os.path.exists(get_media_path(uid, bid))
        
        if not bc.get('text') and not has_photo:
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            await send_safe(uid, context.bot, "⚠️ Рассылка уже запущена")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        client = await get_client_with_retry(uid)
        if client:
            await start_broadcast(uid, context.bot, bid, client)
            return
        
        user_states[uid] = {'step': 'auth', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится)", CANCEL_BTN)
    
    elif data.startswith('stop_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            await asyncio.sleep(0.5)
            if task_key in active_tasks:
                del active_tasks[task_key]
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, f"🛑 Рассылка #{bid+1} остановлена")
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('clone_'):
        bid = int(data.split('_')[1])
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Максимум 10 рассылок", MAIN_MENU)
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
        await send_safe(uid, context.bot, "✅ Рассылка склонирована", MAIN_MENU)
    
    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            await asyncio.sleep(0.5)
            if task_key in active_tasks:
                del active_tasks[task_key]
        media = get_media_path(uid, bid)
        if os.path.exists(media):
            os.remove(media)
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, "🗑 Рассылка удалена", MAIN_MENU)
    
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_safe(uid, context.bot, "➕ Введите ссылку на группу:\n@group_name", CANCEL_BTN)
    
    elif data == 'list_groups':
        groups = user_data[uid].get('groups', [])
        if groups:
            await send_safe(uid, context.bot, "📋 " + "\n".join(groups), GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, "📁 Нет групп", GROUPS_MENU)
    
    elif data == 'remove_group':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет групп", GROUPS_MENU)
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
            await send_safe(uid, context.bot, f"✅ Удалена: {removed}", GROUPS_MENU)
    
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
    
    valid_groups = []
    for group in groups:
        try:
            await client.get_entity(group)
            valid_groups.append(group)
        except:
            await send_safe(uid, bot, f"⚠️ {group} - недоступна")
    
    if not valid_groups:
        await send_safe(uid, bot, "❌ Нет доступных групп!")
        return
    
    bc['groups'] = valid_groups
    bc['active'] = True
    save_data()
    
    await send_safe(uid, bot, f"🚀 ЗАПУСК 24/7\n\nГрупп: {len(valid_groups)}\nИнтервал: {interval} сек\n\n✅ Рассылка будет работать 24/7 и автоматически восстанавливаться при ошибках!")
    
    task_key = f"{uid}_{bid}"
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid_groups, text, interval, media_path, has_photo, bot))
    active_tasks[task_key] = task

async def run_broadcast(uid, bid, client, groups, text, interval, media_path, has_photo, bot):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    consecutive_errors = 0
    
    try:
        while True:
            if not user_data[uid]['broadcasts'][bid].get('active', True):
                break
            
            for group in groups:
                if not user_data[uid]['broadcasts'][bid].get('active', True):
                    break
                
                try:
                    await client.get_me()
                    
                    if has_photo and os.path.exists(media_path):
                        await client.send_file(group, media_path, caption=text)
                    else:
                        await client.send_message(group, text)
                    
                    sent += 1
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                    save_data()
                    consecutive_errors = 0
                    
                except FloodWaitError as e:
                    await asyncio.sleep(min(e.seconds, 60))
                except (AuthKeyError, ConnectionError, TimeoutError, RPCError) as e:
                    consecutive_errors += 1
                    print(f"[ERROR] {uid}: {e}, попытка {consecutive_errors}")
                    
                    if consecutive_errors >= 3:
                        # Автоматический перезапуск
                        await auto_restart_broadcast(uid, bid, bot, str(e))
                        return
                    await asyncio.sleep(10)
                except Exception as e:
                    print(f"[ERROR] {uid}: {e}")
                    await asyncio.sleep(5)
                
                await asyncio.sleep(interval)
            
            await asyncio.sleep(1)
            
    except asyncio.CancelledError:
        user_data[uid]['broadcasts'][bid]['active'] = False
        save_data()
        print(f"[STOP] Рассылка #{bid+1} для {uid} остановлена")

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
        group = text.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
        if not group.startswith('@'):
            group = '@' + group
        groups = user_data[uid].get('groups', [])
        if group not in groups:
            groups.append(group)
            user_data[uid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Группа {group} добавлена", GROUPS_MENU)
        del user_states[uid]
    
    elif step == 'edit_text':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        if len(text) > 4096:
            await send_safe(uid, context.bot, "❌ Текст слишком длинный", CANCEL_BTN)
            return
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_safe(uid, context.bot, "✅ Текст сохранён")
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif step == 'edit_photo':
        bid = step_data['bid']
        if update.message.photo:
            photo = update.message.photo[-1]
            media_path = get_media_path(uid, bid)
            file = await context.bot.get_file(photo.file_id)
            await file.download_to_drive(media_path)
            if update.message.caption:
                user_data[uid]['broadcasts'][bid]['text'] = update.message.caption.strip()
                save_data()
            await send_safe(uid, context.bot, "✅ Фото сохранено!")
        else:
            await send_safe(uid, context.bot, "❌ Отправьте фото", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif step == 'edit_groups':
        if not update.message.text:
            return
        bid = step_data['bid']
        raw = [g.strip() for g in update.message.text.split(',') if g.strip()]
        groups = []
        for g in raw:
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        if groups:
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Сохранено {len(groups)} групп")
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif step == 'edit_interval':
        if not update.message.text:
            return
        bid = step_data['bid']
        try:
            interval = int(update.message.text.strip())
            if 5 <= interval <= 300:
                user_data[uid]['broadcasts'][bid]['interval'] = interval
                save_data()
                await send_safe(uid, context.bot, f"✅ Интервал: {interval} сек")
            else:
                await send_safe(uid, context.bot, "❌ От 5 до 300", CANCEL_BTN)
                return
        except:
            await send_safe(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif step == 'auth':
        if not update.message.text:
            return
        bid = step_data['bid']
        phone = update.message.text.strip()
        if not phone.startswith('+'):
            await send_safe(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'phone': phone}
        
        session_file = get_session_path(uid)
        client = TelegramClient(session_file, API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(phone)
            await send_safe(uid, context.bot, "📲 Введите код:\ncode12345", CANCEL_BTN)
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'waiting_code':
        if not update.message.text:
            return
        match = re.search(r'(\d{5,6})', update.message.text.strip())
        code = match.group(1) if match else None
        if not code:
            await send_safe(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        
        user_states[uid]['code'] = code
        user_states[uid]['step'] = 'waiting_2fa'
        await send_safe(uid, context.bot, "🔐 Пароль 2FA (если есть) или /skip", CANCEL_BTN)
    
    elif step == 'waiting_2fa':
        if not update.message.text:
            return
        password = None if update.message.text.strip() == '/skip' else update.message.text.strip()
        client = sessions.get(uid)
        if not client:
            await send_safe(uid, context.bot, "❌ Ошибка сессии", MAIN_MENU)
            del user_states[uid]
            return
        
        bid = user_states[uid]['bid']
        phone = user_states[uid]['phone']
        code = user_states[uid]['code']
        
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
        
        user_data[uid]['sessions'] = {'phone': phone, 'is_authorized': True}
        save_data()
        
        await start_broadcast(uid, context.bot, bid, client)
        del user_states[uid]

# ==================== HTTP СЕРВЕР ====================
async def health_check(request):
    return web.Response(text="OK", status=200)

async def handle_webhook(request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot_app.bot)
        await bot_app.process_update(update)
        return web.Response(text="OK", status=200)
    except:
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
    await asyncio.Event().wait()

# ==================== ЗАПУСК ====================
async def run_bot():
    global bot_app
    load_data()
    
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("skip", skip_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, message_handler))
    
    await bot_app.initialize()
    await bot_app.start()
    
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot_app.bot.set_webhook(webhook_url)
    
    print("=" * 60)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН")
    print("=" * 60)
    print("🔥 РАССЫЛКА АВТОМАТИЧЕСКИ ВОССТАНАВЛИВАЕТСЯ ПРИ ОШИБКАХ")
    print("🔄 НЕ ОСТАНАВЛИВАЕТСЯ - РАБОТАЕТ 24/7")
    print("=" * 60)
    
    await start_http_server()

def main():
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
