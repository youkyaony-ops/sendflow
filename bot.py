import asyncio
import re
import json
import os
import logging
import time
import random
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
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

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}

# ==================== РАБОТА С ДАННЫМИ ====================
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
        print(f"[SAVE] Данные сохранены для {len(user_data)} пользователей")
        return True
    except Exception as e:
        print(f"Save error: {e}")
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
        print(f"Load error: {e}")
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
        print(f"[USER] Новый пользователь {uid} создан")
    return user_data[uid]

def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f'session_{user_id}.session')

def has_valid_session(user_id):
    if user_id not in user_data:
        return False
    session_info = user_data[user_id].get('sessions', {})
    if not session_info.get('is_authorized'):
        return False
    session_file = get_session_path(user_id)
    if not os.path.exists(session_file):
        return False
    return True

async def get_client(user_id):
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
    except:
        return None

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
        [InlineKeyboardButton("📝 ТЕКСТ + ФОТО", callback_data=f'edit_content_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'edit_groups_{bid}')],
        [InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'edit_interval_{bid}'), InlineKeyboardButton("🎲 РАНДОМ", callback_data=f'edit_random_{bid}')],
        [InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data=f'toggle_loop_{bid}'), InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data=f'edit_schedule_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data=f'start_247_{bid}'), InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data=f'send_once_{bid}')],
        [InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data=f'stop_broadcast_{bid}'), InlineKeyboardButton("📊 СТАТУС", callback_data=f'bc_status_{bid}')],
        [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data=f'clone_broadcast_{bid}'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_broadcast_{bid}')],
        [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("📋 ВСЕ ГРУППЫ", callback_data='list_groups')],
    [InlineKeyboardButton("🗑 УДАЛИТЬ ГРУППУ", callback_data='remove_group')],
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
    [InlineKeyboardButton("🔧 ОШИБКИ", callback_data='help_errors')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

CANCEL_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]])
BACK_BTN = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def send_safe(chat_id, bot, text, keyboard=None):
    try:
        if keyboard:
            await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode='HTML')
        else:
            await bot.send_message(chat_id, text, parse_mode='HTML')
        return True
    except Exception as e:
        print(f"Send error: {e}")
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
    
    status = "🟢 АКТИВНА" if is_running else "🔴 ОСТАНОВЛЕНА"
    
    media_info = ""
    if bc.get('has_photo'):
        media_info = "\n📷 <b>Фото:</b> есть"
    
    txt = f"📢 <b>{bc.get('name', f'Рассылка {bid+1}')}</b>\n\n"
    txt += f"Статус: {status}\n"
    txt += f"📝 Текст: {'✅' if bc.get('text') else '❌'}\n"
    if bc.get('text'):
        preview = bc['text'][:50] + '...' if len(bc['text']) > 50 else bc['text']
        txt += f"   → {preview}\n"
    txt += media_info
    txt += f"\n👥 Групп: {len(bc.get('groups', []))}\n"
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

# ==================== КОМАНДЫ ====================
async def start_cmd(update: Update, context):
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    
    welcome = f"👋 Привет, {update.effective_user.first_name}!"
    if has_valid_session(uid):
        welcome += "\n\n✅ У вас есть сохранённая сессия Telegram"
    else:
        welcome += "\n\n⚠️ Для запуска рассылок потребуется авторизация"
    
    await main_menu(uid, context.bot, welcome)

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
        
        if len(broadcasts) >= 20:
            await send_safe(uid, context.bot, "❌ Максимум 20 рассылок\nУдалите ненужные", MAIN_MENU)
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
            'created_at': str(datetime.now()),
            'has_photo': False,
            'photo_file_id': None,
            'photo_caption': None
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
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', 30)} сек\n"
        txt += f"🔐 Сессия: {'✅ Сохранена' if has_valid_session(uid) else '❌ Не сохранена'}"
        await send_safe(uid, context.bot, txt, SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Нажми '📝 ТЕКСТ + ФОТО' и отправь сообщение (текст + фото)\n3️⃣ Настрой группы\n4️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n5️⃣ Авторизуйся (один раз)\n\n✅ Рассылка работает 24/7!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>Текст + Фото:</b>\nОтправь сообщение с фото как обычно (можно с подписью)\nБот запомнит всё в точности как ты отправил!\n\n<b>Группы:</b> через запятую: @group1, @group2\n<b>Интервал:</b> 5-300 секунд\n\n💡 Бот должен быть участником всех групп!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data.startswith('select_bc_'):
        bid = int(data.split('_')[2])
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('edit_content_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_content', 'bid': bid}
        await send_safe(uid, context.bot, "📝 <b>ОТПРАВЬТЕ СООБЩЕНИЕ</b>\n\nМожно отправить:\n• Текст с эмодзи\n• Фото с подписью\n• Текст + Фото вместе\n\n<i>Бот запомнит всё в точности как вы отправили!</i>", CANCEL_BTN, parse_mode='HTML')
    
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
        await send_safe(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)
    
    elif data.startswith('edit_random_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_random', 'bid': bid}
        await send_safe(uid, context.bot, "🎲 Введите диапазон (мин-макс):\nПример: 10-30\n0 - отключить", CANCEL_BTN)
    
    elif data.startswith('toggle_loop_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        bc['loop'] = not bc.get('loop', True)
        save_data()
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('edit_schedule_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_schedule', 'bid': bid}
        await send_safe(uid, context.bot, "📅 Введите время (ЧЧ:ММ):\nПример: 14:30\noff - отключить", CANCEL_BTN)
    
    elif data.startswith('start_247_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        
        if not bc.get('text') and not bc.get('photo_file_id'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!\nНажми '📝 ТЕКСТ + ФОТО' и отправь сообщение", BACK_BTN)
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
        
        if not bc.get('text') and not bc.get('photo_file_id'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ или ФОТО для рассылки!\nНажми '📝 ТЕКСТ + ФОТО' и отправь сообщение", BACK_BTN)
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
        if len(broadcasts) >= 20:
            await send_safe(uid, context.bot, "❌ Достигнут лимит рассылок (20)", MAIN_MENU)
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
            'created_at': str(datetime.now()),
            'has_photo': original.get('has_photo', False),
            'photo_file_id': original.get('photo_file_id'),
            'photo_caption': original.get('photo_caption')
        }
        user_data[uid]['broadcasts'].append(new_bc)
        save_data()
        await send_safe(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)
    
    elif data.startswith('delete_broadcast_'):
        bid = int(data.split('_')[2])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            active_tasks[task_key].cancel()
            if task_key in active_tasks:
                del active_tasks[task_key]
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, f"🗑 Рассылка #{bid+1} удалена", MAIN_MENU)
    
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
        await send_safe(uid, context.bot, "⏱ Введите интервал по умолчанию (5-300 сек):", CANCEL_BTN)
    
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
async def start_broadcast_with_client(uid, bot, bid, client, is_247=True):
    if uid not in user_data:
        save_user(uid)
    
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    text = bc.get('text', '')
    interval = bc.get('interval', 30)
    random_min = bc.get('random_min', 0)
    random_max = bc.get('random_max', 0)
    has_photo = bc.get('has_photo', False)
    photo_file_id = bc.get('photo_file_id')
    photo_caption = bc.get('photo_caption', text)
    
    valid_groups = []
    for group in groups:
        try:
            await client.get_entity(group)
            valid_groups.append(group)
        except:
            await send_safe(uid, bot, f"⚠️ {group} - недоступна")
    
    if not valid_groups:
        await send_safe(uid, bot, "❌ Нет доступных групп!", MAIN_MENU)
        return
    
    bc['groups'] = valid_groups
    bc['active'] = True
    save_data()
    
    media_info = " 📷" if has_photo else ""
    
    if is_247:
        await send_safe(uid, bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}", MAIN_MENU)
        
        task_key = f"{uid}_{bid}"
        if task_key not in active_tasks or active_tasks[task_key].done():
            task = asyncio.create_task(run_broadcast_247(uid, bid, client, valid_groups, text, interval, random_min, random_max, has_photo, photo_file_id, photo_caption))
            active_tasks[task_key] = task
            print(f"[START] Запущена рассылка #{bid+1} для пользователя {uid}")
        else:
            await send_safe(uid, bot, f"⚠️ Рассылка #{bid+1} уже запущена!", MAIN_MENU)
    else:
        await send_safe(uid, bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
        success = 0
        for group in valid_groups:
            try:
                if has_photo and photo_file_id:
                    await client.send_file(group, photo_file_id, caption=photo_caption or text)
                else:
                    await client.send_message(group, text)
                success += 1
                await asyncio.sleep(2)
            except Exception as e:
                await send_safe(uid, bot, f"❌ {group}: {str(e)[:50]}")
        await send_safe(uid, bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)

# ==================== БЕСКОНЕЧНАЯ РАССЫЛКА 24/7 ====================
async def run_broadcast_247(uid, bid, client, groups, text, interval, random_min, random_max, has_photo=False, photo_file_id=None, photo_caption=None):
    sent = user_data[uid]['broadcasts'][bid].get('sent', 0)
    print(f"[START] Бесконечная рассылка #{bid+1} для {uid} запущена")
    
    try:
        while True:
            if not user_data[uid]['broadcasts'][bid].get('active', True):
                print(f"[STOP] Рассылка #{bid+1} для {uid} остановлена по флагу")
                break
            
            for group in groups:
                task = asyncio.current_task()
                if task and task.cancelled():
                    print(f"[CANCEL] Рассылка #{bid+1} для {uid} отменена")
                    break
                
                if not user_data[uid]['broadcasts'][bid].get('active', True):
                    break
                
                try:
                    if has_photo and photo_file_id:
                        await client.send_file(group, photo_file_id, caption=photo_caption or text)
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
                await send_safe(uid, context.bot, f"✅ Интервал по умолчанию: {val} сек", SETTINGS_MENU)
            else:
                await send_safe(uid, context.bot, "❌ Интервал от 5 до 300 сек", CANCEL_BTN)
                return
        except:
            await send_safe(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
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
            await send_safe(uid, context.bot, f"✅ Группа {group} добавлена!", GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, f"⚠️ Группа {group} уже есть", GROUPS_MENU)
        del user_states[uid]
    
    # РЕДАКТИРОВАНИЕ КОНТЕНТА (ТЕКСТ + ФОТО)
    elif step == 'edit_content':
        bid = step_data['bid']
        
        # Сохраняем текст если есть
        if update.message.text:
            text = update.message.text.strip()
            if len(text) > 4096:
                await send_safe(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
                return
            user_data[uid]['broadcasts'][bid]['text'] = text
            save_data()
            await send_safe(uid, context.bot, "✅ Текст сохранён!")
        
        # Сохраняем фото если есть
        if update.message.photo:
            file_id = update.message.photo[-1].file_id
            user_data[uid]['broadcasts'][bid]['has_photo'] = True
            user_data[uid]['broadcasts'][bid]['photo_file_id'] = file_id
            user_data[uid]['broadcasts'][bid]['photo_caption'] = update.message.caption
            save_data()
            await send_safe(uid, context.bot, "✅ Фото сохранено!")
            print(f"[PHOTO] Сохранено фото для рассылки #{bid+1}")
        
        # Если ничего не отправлено
        if not update.message.text and not update.message.photo:
            await send_safe(uid, context.bot, "❌ Отправьте текст или фото", CANCEL_BTN)
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
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if groups:
            if bid >= len(user_data[uid].get('broadcasts', [])):
                await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                del user_states[uid]
                return
            
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Сохранено {len(groups)} групп!")
        else:
            await send_safe(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ИНТЕРВАЛА
    elif step == 'edit_interval':
        if not update.message.text:
            return
        bid = step_data['bid']
        text = update.message.text.strip()
        try:
            val = int(text)
            if 5 <= val <= 300:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['interval'] = val
                save_data()
                await send_safe(uid, context.bot, f"✅ Интервал: {val} сек")
            else:
                await send_safe(uid, context.bot, "❌ От 5 до 300 секунд", CANCEL_BTN)
                return
        except:
            await send_safe(uid, context.bot, "❌ Введите число", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ РАНДОМА
    elif step == 'edit_random':
        if not update.message.text:
            return
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
            if 0 < min_val < max_val <= 300:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['random_min'] = min_val
                user_data[uid]['broadcasts'][bid]['random_max'] = max_val
                save_data()
                await send_safe(uid, context.bot, f"✅ Рандом: {min_val}-{max_val} сек")
            else:
                await send_safe(uid, context.bot, "❌ Диапазон: мин < макс, макс ≤ 300", CANCEL_BTN)
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
        
        match = re.match(r'(\d{1,2}):(\d{2})', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['schedule'] = f"{hour:02d}:{minute:02d}"
                save_data()
                await send_safe(uid, context.bot, f"✅ Расписание: {hour:02d}:{minute:02d}")
            else:
                await send_safe(uid, context.bot, "❌ Неверное время", CANCEL_BTN)
                return
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
        
        if not text.startswith('+'):
            await send_safe(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        
        user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'is_247': is_247, 'phone': text}
        
        session_file = get_session_path(uid)
        client = TelegramClient(session_file, API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(text)
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
        interval = bc.get('interval', 30)
        random_min = bc.get('random_min', 0)
        random_max = bc.get('random_max', 0)
        has_photo = bc.get('has_photo', False)
        photo_file_id = bc.get('photo_file_id')
        photo_caption = bc.get('photo_caption', msg)
        
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
        
        if uid not in user_data:
            save_user(uid)
        if 'sessions' not in user_data[uid]:
            user_data[uid]['sessions'] = {}
        user_data[uid]['sessions'] = {
            'phone': phone,
            'is_authorized': True,
            'last_used': str(datetime.now()),
            'session_file': get_session_path(uid)
        }
        save_data()
        
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
        
        bc['groups'] = valid_groups
        bc['active'] = True
        save_data()
        
        media_info = " 📷" if has_photo else ""
        
        if is_247:
            await send_safe(uid, context.bot, f"🚀 <b>ЗАПУСК 24/7</b>\n\n📢 Рассылка #{bid+1}\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек{media_info}\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n✅ Сессия сохранена!", MAIN_MENU)
            
            task_key = f"{uid}_{bid}"
            if task_key not in active_tasks or active_tasks[task_key].done():
                task = asyncio.create_task(run_broadcast_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max, has_photo, photo_file_id, photo_caption))
                active_tasks[task_key] = task
        else:
            await send_safe(uid, context.bot, f"📤 <b>ОТПРАВКА РАЗОМ</b>\n\n📢 Рассылка #{bid+1}\n👥 Групп: {len(valid_groups)}{media_info}", MAIN_MENU)
            success = 0
            for group in valid_groups:
                try:
                    if has_photo and photo_file_id:
                        await client.send_file(group, photo_file_id, caption=photo_caption or msg)
                    else:
                        await client.send_message(group, msg)
                    success += 1
                    await asyncio.sleep(2)
                except:
                    pass
            await send_safe(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)
        
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
    
    bot_app.add_handler(CommandHandler("start", start_cmd))
    bot_app.add_handler(CommandHandler("skip", skip_cmd))
    bot_app.add_handler(CallbackQueryHandler(button_handler))
    bot_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    bot_app.add_handler(MessageHandler(filters.PHOTO, message_handler))
    
    await bot_app.initialize()
    await bot_app.start()
    
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot_app.bot.set_webhook(webhook_url)
    print(f"[WEBHOOK] Установлен: {webhook_url}")
    
    print("=" * 60)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН")
    print("📝 ТЕКСТ + ФОТО - В ТОЧНОСТИ КАК ОТПРАВЛЕНО")
    print("💾 ДАННЫЕ СОХРАНЯЮТСЯ АВТОМАТИЧЕСКИ")
    print("🔄 РАССЫЛКИ НЕ ПРОПАДАЮТ ПОСЛЕ ПЕРЕЗАПУСКА")
    print("=" * 60)
    
    await start_http_server()

def main():
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
