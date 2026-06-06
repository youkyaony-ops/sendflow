import asyncio
import re
import json
import os
import logging
import time
import random
from datetime import datetime, timedelta
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКА ====================
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
BACKUP_FILE = 'user_data_backup.json'
SESSIONS_DIR = 'telegram_sessions'

# Создаём папку для сессий
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}
user_sessions = {}  # Сохраняем информацию о сессиях пользователей

# ==================== РАБОТА С СЕССИЯМИ ====================
def get_session_path(user_id):
    """Получить путь к файлу сессии пользователя"""
    return os.path.join(SESSIONS_DIR, f'session_{user_id}.session')

def save_session_info(user_id, phone, is_authorized=True):
    """Сохранить информацию о сессии пользователя"""
    if 'sessions' not in user_data.get(user_id, {}):
        if user_id not in user_data:
            user_data[user_id] = {}
        user_data[user_id]['sessions'] = {}
    
    user_data[user_id]['sessions'] = {
        'phone': phone,
        'is_authorized': is_authorized,
        'last_used': str(datetime.now()),
        'session_file': get_session_path(user_id)
    }
    save_data()
    print(f"[SESSION] Информация о сессии сохранена для {user_id}")

def get_session_info(user_id):
    """Получить информацию о сессии пользователя"""
    if user_id in user_data and 'sessions' in user_data[user_id]:
        return user_data[user_id]['sessions']
    return None

def has_valid_session(user_id):
    """Проверить, есть ли у пользователя сохранённая сессия"""
    session_info = get_session_info(user_id)
    if session_info and session_info.get('is_authorized'):
        session_file = session_info.get('session_file')
        if session_file and os.path.exists(session_file):
            # Проверяем, не устарела ли сессия (30 дней)
            last_used = session_info.get('last_used')
            if last_used:
                try:
                    last_used_date = datetime.fromisoformat(last_used)
                    if datetime.now() - last_used_date < timedelta(days=30):
                        return True
                except:
                    pass
    return False

async def get_or_create_client(user_id, phone=None):
    """Получить существующего клиента или создать нового"""
    # Если клиент уже в памяти
    if user_id in sessions:
        try:
            # Проверяем, жив ли клиент
            await sessions[user_id].get_me()
            return sessions[user_id]
        except:
            # Клиент мёрт, удаляем
            try:
                await sessions[user_id].disconnect()
            except:
                pass
            del sessions[user_id]
    
    # Пробуем загрузить сохранённую сессию
    session_file = get_session_path(user_id)
    client = TelegramClient(session_file, API_ID, API_HASH)
    
    try:
        await client.connect()
        if await client.is_user_authorized():
            # Сессия жива
            sessions[user_id] = client
            print(f"[SESSION] Загружена сохранённая сессия для {user_id}")
            
            # Обновляем информацию
            session_info = get_session_info(user_id)
            if session_info:
                session_info['last_used'] = str(datetime.now())
                session_info['is_authorized'] = True
                save_data()
            
            return client
        else:
            # Сессия не авторизована, нужно заново
            await client.disconnect()
            return None
    except Exception as e:
        print(f"[SESSION] Ошибка загрузки сессии для {user_id}: {e}")
        try:
            await client.disconnect()
        except:
            pass
        return None

async def create_new_session(user_id, phone):
    """Создать новую сессию"""
    session_file = get_session_path(user_id)
    client = TelegramClient(session_file, API_ID, API_HASH)
    await client.connect()
    sessions[user_id] = client
    return client

# ==================== СОХРАНЕНИЕ ДАННЫХ ====================
def save_data():
    """Принудительное сохранение всех данных"""
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
        print(f"[SAVE ERROR] {e}")
        return False

def load_data():
    """Загрузка всех данных"""
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
                print(f"[LOAD] Загружены данные для {len(user_data)} пользователей")
                return True
        elif os.path.exists(BACKUP_FILE):
            with open(BACKUP_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
                print(f"[LOAD] Загружены данные из бэкапа для {len(user_data)} пользователей")
                return True
        else:
            user_data = {}
            return True
    except Exception as e:
        print(f"[LOAD ERROR] {e}")
        user_data = {}
        return False

def save_user(uid):
    """Сохранение пользователя с проверкой"""
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
        [InlineKeyboardButton("📝 ТЕКСТ", callback_data=f'edit_text_{bid}'), InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'edit_groups_{bid}')],
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
    except Exception as e:
        logger.error(f"Send error: {e}")
        return False

async def main_menu(chat_id, bot, text=None):
    msg = text if text else "🥓 <b>SendFlow</b>\n\nВыберите действие:"
    await send_safe(chat_id, bot, msg, MAIN_MENU)

async def show_broadcast_menu(uid, bot, bid):
    """Показать меню рассылки с сохранёнными данными"""
    if uid not in user_data:
        save_user(uid)
    
    broadcasts = user_data[uid].get('broadcasts', [])
    if bid >= len(broadcasts):
        await send_safe(uid, bot, "❌ Рассылка не найдена", MAIN_MENU)
        return
    
    bc = broadcasts[bid]
    status = "🟢 АКТИВНА" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"
    txt = f"📢 <b>{bc.get('name', f'Рассылка {bid+1}')}</b>\n\n"
    txt += f"Статус: {status}\n"
    txt += f"📝 Текст: {'✅ Есть' if bc.get('text') else '❌ Нет'}\n"
    if bc.get('text'):
        preview = bc['text'][:50] + '...' if len(bc['text']) > 50 else bc['text']
        txt += f"   → {preview}\n"
    txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
    if bc.get('groups'):
        txt += f"   → {', '.join(bc['groups'][:3])}\n"
    txt += f"⏱ Интервал: {bc.get('interval', 30)} сек\n"
    if bc.get('random_min') and bc.get('random_max'):
        txt += f"🎲 Рандом: {bc['random_min']}-{bc['random_max']} сек\n"
    txt += f"🔄 Зациклено: {'✅' if bc.get('loop', True) else '❌'}\n"
    txt += f"📨 Отправлено: {bc.get('sent', 0)}\n"
    txt += f"❌ Ошибок: {bc.get('errors', 0)}"
    
    await send_safe(uid, bot, txt, get_broadcast_actions(bid))

# ==================== КОМАНДЫ ====================
async def start_cmd(update: Update, context):
    uid = update.effective_user.id
    load_data()
    save_user(uid)
    
    # Проверяем, есть ли сохранённая сессия
    if has_valid_session(uid):
        await send_safe(uid, context.bot, f"👋 Привет, {update.effective_user.first_name}!\n\n✅ У вас есть сохранённая сессия Telegram. Рассылки будут работать без повторной авторизации.")
    else:
        await send_safe(uid, context.bot, f"👋 Привет, {update.effective_user.first_name}!\n\n⚠️ Для запуска рассылок потребуется авторизация в Telegram (один раз).")
    
    await main_menu(uid, context.bot)

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
    
    # ГЛАВНОЕ МЕНЮ
    if data == 'back_to_main':
        await main_menu(uid, context.bot)
    
    elif data == 'my_broadcasts':
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        if not broadcasts:
            await send_safe(uid, context.bot, "📢 У вас нет рассылок\n\nСоздайте новую через кнопку '➕ НОВАЯ РАССЫЛКА'", MAIN_MENU)
            return
        
        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            status = "🟢" if bc.get('active') else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'select_bc_{i}')])
        kb.append([InlineKeyboardButton("➕ НОВАЯ", callback_data='new_broadcast')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        
        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif data == 'new_broadcast':
        if uid not in user_data:
            save_user(uid)
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Максимум 10 рассылок\nУдалите ненужные", MAIN_MENU)
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
        await show_broadcast_menu(uid, context.bot, new_id)
    
    elif data.startswith('select_bc_'):
        bid = int(data.split('_')[2])
        await show_broadcast_menu(uid, context.bot, bid)
    
    # ДЕЙСТВИЯ С РАССЫЛКОЙ
    elif data.startswith('edit_text_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_text', 'bid': bid}
        await send_safe(uid, context.bot, "📝 Введите текст рассылки:", CANCEL_BTN)
    
    elif data.startswith('edit_groups_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        
        saved_groups = user_data[uid].get('groups', [])
        if saved_groups:
            kb = [[InlineKeyboardButton(f"📌 {g}", callback_data=f'select_saved_group_{bid}_{g}')] for g in saved_groups[:10]]
            kb.append([InlineKeyboardButton("✏️ ВВЕСТИ ВРУЧНУЮ", callback_data=f'manual_groups_{bid}')])
            kb.append([InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')])
            await context.bot.send_message(uid, "👥 <b>ВЫБЕРИТЕ ГРУППЫ</b>\n\nМожно выбрать из сохранённых или ввести вручную:", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
        else:
            await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2, https://t.me/group3", CANCEL_BTN)
    
    elif data.startswith('manual_groups_'):
        bid = int(data.split('_')[2])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2, https://t.me/group3", CANCEL_BTN)
    
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
        
        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if f"{uid}_{bid}" in active_tasks:
            await send_safe(uid, context.bot, "⚠️ Рассылка уже запущена!")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        # Пробуем использовать сохранённую сессию
        existing_client = await get_or_create_client(uid)
        if existing_client and await existing_client.is_user_authorized():
            # Сессия уже есть, запускаем сразу
            await send_safe(uid, context.bot, "✅ Использую сохранённую сессию Telegram")
            await start_broadcast_with_client(uid, context.bot, bid, existing_client, is_247=True)
            return
        
        # Нужна авторизация
        user_states[uid] = {'step': 'start_247', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится и не потребуется в следующий раз)", CANCEL_BTN)
    
    elif data.startswith('send_once_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        
        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        # Пробуем использовать сохранённую сессию
        existing_client = await get_or_create_client(uid)
        if existing_client and await existing_client.is_user_authorized():
            await send_safe(uid, context.bot, "✅ Использую сохранённую сессию Telegram")
            await start_broadcast_with_client(uid, context.bot, bid, existing_client, is_247=False)
            return
        
        user_states[uid] = {'step': 'send_once', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона:\n+79123456789\n\n(Сессия сохранится и не потребуется в следующий раз)", CANCEL_BTN)
    
    elif data.startswith('stop_broadcast_'):
        bid = int(data.split('_')[2])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, "🛑 Рассылка остановлена")
        else:
            await send_safe(uid, context.bot, "❌ Нет активной рассылки")
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('bc_status_'):
        bid = int(data.split('_')[2])
        bc = user_data[uid]['broadcasts'][bid]
        status = "🟢 РАБОТАЕТ" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"
        txt = f"📊 <b>СТАТУС РАССЫЛКИ</b>\n\n"
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
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Достигнут лимит рассылок (10)", MAIN_MENU)
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
        await send_safe(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)
    
    elif data.startswith('delete_broadcast_'):
        bid = int(data.split('_')[2])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, "🗑 Рассылка удалена", MAIN_MENU)
    
    # ГРУППЫ
    elif data == 'my_groups':
        if uid not in user_data:
            save_user(uid)
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 У вас нет сохранённых групп\n\nДобавьте первую через кнопку '➕ ДОБАВИТЬ ГРУППУ'", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n" + "\n".join([f"• {g}" for g in groups[:15]])
            if len(groups) > 15:
                txt += f"\n\n... и ещё {len(groups)-15} групп"
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_safe(uid, context.bot, "➕ Введите ссылку на группу:\n\nПример: @group_name или https://t.me/group", CANCEL_BTN)
    
    elif data == 'list_groups':
        if uid not in user_data:
            save_user(uid)
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 Нет сохранённых групп", GROUPS_MENU)
        else:
            txt = "📋 <b>ВСЕ ГРУППЫ</b>\n\n" + "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'remove_group':
        if uid not in user_data:
            save_user(uid)
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
    
    elif data == 'my_stats':
        if uid not in user_data:
            save_user(uid)
        data_u = user_data[uid]
        bc = data_u.get('broadcasts', [])
        active = sum(1 for b in bc if b.get('active'))
        total_sent = sum(b.get('sent', 0) for b in bc)
        
        txt = f"📊 <b>ВАША СТАТИСТИКА</b>\n\n"
        txt += f"📢 Рассылок: {len(bc)} (🟢 {active} активных)\n"
        txt += f"📨 Отправлено: {total_sent} сообщений\n"
        txt += f"📁 Сохранено групп: {len(data_u.get('groups', []))}\n"
        
        # Добавляем информацию о сессии
        if has_valid_session(uid):
            txt += f"\n✅ Сессия Telegram сохранена"
        else:
            txt += f"\n⚠️ Сессия Telegram не сохранена"
        
        txt += f"\n📅 Дата регистрации: {data_u.get('created_at', 'Неизвестно')[:10]}"
        await send_safe(uid, context.bot, txt, MAIN_MENU)
    
    elif data == 'settings':
        if uid not in user_data:
            save_user(uid)
        s = user_data[uid].get('settings', {})
        txt = f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        txt += f"🔔 Уведомления: {'✅ Вкл' if s.get('notify', True) else '❌ Выкл'}\n"
        txt += f"💾 Автосохранение: {'✅ Вкл' if s.get('autosave', True) else '❌ Выкл'}\n"
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', 30)} сек"
        await send_safe(uid, context.bot, txt, SETTINGS_MENU)
    
    elif data == 'toggle_notify':
        if uid not in user_data:
            save_user(uid)
        s = user_data[uid].get('settings', {})
        s['notify'] = not s.get('notify', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"🔔 Уведомления: {'ВКЛЮЧЕНЫ' if s['notify'] else 'ВЫКЛЮЧЕНЫ'}", SETTINGS_MENU)
    
    elif data == 'toggle_autosave':
        if uid not in user_data:
            save_user(uid)
        s = user_data[uid].get('settings', {})
        s['autosave'] = not s.get('autosave', True)
        user_data[uid]['settings'] = s
        save_data()
        await send_safe(uid, context.bot, f"💾 Автосохранение: {'ВКЛЮЧЕНО' if s['autosave'] else 'ВЫКЛЮЧЕНО'}", SETTINGS_MENU)
    
    elif data == 'def_interval':
        user_states[uid] = {'step': 'def_interval'}
        await send_safe(uid, context.bot, "⏱ Введите интервал по умолчанию (5-300 сек):", CANCEL_BTN)
    
    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой текст и группы\n3️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n4️⃣ Авторизуйся в Telegram (ОДИН РАЗ)\n\n✅ В следующий раз авторизация не потребуется!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>Текст:</b> любое сообщение, до 4096 символов\n<b>Группы:</b> через запятую: @group1, @group2\n<b>Интервал:</b> время между сообщениями (5-300 сек)\n<b>Рандом:</b> случайная задержка\n<b>Зациклить:</b> бесконечный повтор\n\n💾 <b>Сессия сохраняется!</b> Не нужно каждый раз вводить код."
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345\n\n✅ Сессия сохранится после успешной авторизации!"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Действие отменено")

# ==================== ЗАПУСК РАССЫЛКИ С КЛИЕНТОМ ====================
async def start_broadcast_with_client(uid, bot, bid, client, is_247=True):
    """Запуск рассылки с уже существующим клиентом"""
    if uid not in user_data:
        save_user(uid)
    
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    msg = bc.get('text', '')
    interval = bc.get('interval', 30)
    random_min = bc.get('random_min', 0)
    random_max = bc.get('random_max', 0)
    
    # Проверка групп
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
    
    user_data[uid]['broadcasts'][bid]['groups'] = valid_groups
    save_data()
    
    if is_247:
        await send_safe(uid, bot, f"🚀 ЗАПУСК 24/7\n\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n✅ Использую сохранённую сессию", MAIN_MENU)
        task = asyncio.create_task(run_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max))
        active_tasks[f"{uid}_{bid}"] = task
        user_data[uid]['broadcasts'][bid]['active'] = True
        save_data()
    else:
        await send_safe(uid, bot, f"📤 ОТПРАВКА РАЗОМ\n\n👥 Групп: {len(valid_groups)}", MAIN_MENU)
        success = 0
        for group in valid_groups:
            try:
                await client.send_message(group, msg)
                success += 1
                await asyncio.sleep(2)
            except:
                pass
        await send_safe(uid, bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    save_user(uid)
    
    step_data = user_states.get(uid, {})
    step = step_data.get('step')
    
    if not step:
        await main_menu(uid, context.bot)
        return
    
    # НАСТРОЙКА ИНТЕРВАЛА ПО УМОЛЧАНИЮ
    if step == 'def_interval':
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
    
    # РЕДАКТИРОВАНИЕ ТЕКСТА
    elif step == 'edit_text':
        bid = step_data['bid']
        if len(text) > 4096:
            await send_safe(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        
        if uid not in user_data:
            save_user(uid)
        if bid >= len(user_data[uid].get('broadcasts', [])):
            await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
            del user_states[uid]
            return
        
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        
        await send_safe(uid, context.bot, "✅ Текст сохранён!")
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # РЕДАКТИРОВАНИЕ ГРУПП
    elif step == 'edit_groups':
        bid = step_data['bid']
        raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in raw:
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if groups:
            if uid not in user_data:
                save_user(uid)
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
        bid = step_data['bid']
        try:
            val = int(text)
            if 5 <= val <= 300:
                if uid not in user_data:
                    save_user(uid)
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
        bid = step_data['bid']
        if text == '0':
            if uid not in user_data:
                save_user(uid)
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
            if 0 <= min_val < max_val <= 300:
                if uid not in user_data:
                    save_user(uid)
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
        bid = step_data['bid']
        if text.lower() == 'off':
            if uid not in user_data:
                save_user(uid)
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
                if uid not in user_data:
                    save_user(uid)
                if bid >= len(user_data[uid].get('broadcasts', [])):
                    await send_safe(uid, context.bot, "❌ Рассылка не найдена", MAIN_MENU)
                    del user_states[uid]
                    return
                
                user_data[uid]['broadcasts'][bid]['schedule'] = f"{hour:02d}:{minute:02d}"
                save_data()
                await send_safe(uid, context.bot, f"✅ Расписание: {hour:02d}:{minute:02d}")
            else:
                await send_safe(uid, context.bot, "❌ Неверное время (0-23:0-59)", CANCEL_BTN)
                return
        else:
            await send_safe(uid, context.bot, "❌ Формат: 14:30", CANCEL_BTN)
            return
        del user_states[uid]
        await show_broadcast_menu(uid, context.bot, bid)
    
    # АВТОРИЗАЦИЯ И ЗАПУСК
    elif step in ['start_247', 'send_once']:
        bid = step_data['bid']
        is_247 = (step == 'start_247')
        
        if not text.startswith('+'):
            await send_safe(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        # Закрываем старую сессию если есть
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            del sessions[uid]
        
        user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'is_247': is_247, 'phone': text}
        
        # Создаём новую сессию
        client = await create_new_session(uid, text)
        
        try:
            await client.send_code_request(text)
            await send_safe(uid, context.bot, "📲 Введите код из Telegram:\n\nФормат: code12345\n\n✅ Сессия будет сохранена, в следующий раз код не потребуется!", CANCEL_BTN)
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
    
    elif step == 'waiting_code':
        match = re.search(r'(\d{5,6})', text)
        code = match.group(1) if match else None
        if not code:
            await send_safe(uid, context.bot, "❌ Формат: code12345", CANCEL_BTN)
            return
        
        user_states[uid]['code'] = code
        user_states[uid]['step'] = 'waiting_2fa'
        await send_safe(uid, context.bot, "🔐 Введите пароль 2FA (если есть)\n\nЕсли нет - отправьте /skip", CANCEL_BTN)
    
    elif step == 'waiting_2fa':
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
        
        if uid not in user_data:
            save_user(uid)
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
        
        # Сохраняем информацию о сессии
        save_session_info(uid, phone, is_authorized=True)
        print(f"[AUTH] Пользователь {uid} успешно авторизован, сессия сохранена")
        
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
            await send_safe(uid, context.bot, f"🚀 ЗАПУСК 24/7\n\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}\n\n✅ Сессия сохранена! При следующем запуске код не потребуется.", MAIN_MENU)
            task = asyncio.create_task(run_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max))
            active_tasks[f"{uid}_{bid}"] = task
            user_data[uid]['broadcasts'][bid]['active'] = True
            save_data()
        else:
            await send_safe(uid, context.bot, f"📤 ОТПРАВКА РАЗОМ\n\n👥 Групп: {len(valid_groups)}", MAIN_MENU)
            success = 0
            for group in valid_groups:
                try:
                    await client.send_message(group, msg)
                    success += 1
                    await asyncio.sleep(2)
                except:
                    pass
            await send_safe(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}\n\n✅ Сессия сохранена!", MAIN_MENU)
        
        del user_states[uid]

# ==================== БЕСКОНЕЧНАЯ РАССЫЛКА ====================
async def run_247(uid, bid, client, groups, text, interval, random_min, random_max):
    sent = 0
    try:
        while True:
            for group in groups:
                try:
                    await client.send_message(group, text)
                    sent += 1
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['sent'] = sent
                        user_data[uid]['total_sent'] = user_data[uid].get('total_sent', 0) + 1
                        save_data()
                    print(f"[SEND] {uid} -> {group} (#{sent})")
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                        user_data[uid]['broadcasts'][bid]['errors'] = user_data[uid]['broadcasts'][bid].get('errors', 0) + 1
                        user_data[uid]['total_errors'] = user_data[uid].get('total_errors', 0) + 1
                        save_data()
                
                delay = interval
                if random_min and random_max:
                    delay = random.randint(random_min, random_max)
                await asyncio.sleep(delay)
    except asyncio.CancelledError:
        if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
        print(f"[STOP] Рассылка {uid}_{bid} остановлена")

# ==================== ЗАПУСК ====================
def main():
    load_data()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("=" * 60)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН")
    print("=" * 60)
    print("📌 ВСЕ ДАННЫЕ СОХРАНЯЮТСЯ")
    print("📌 СЕССИИ TELEGRAM СОХРАНЯЮТСЯ")
    print("📌 ПРИ ПЕРЕЗАПУСКЕ НЕ НУЖНО ЗАНОВО ВХОДИТЬ")
    print(f"📁 СЕССИИ ХРАНЯТСЯ В ПАПКЕ: {SESSIONS_DIR}")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
