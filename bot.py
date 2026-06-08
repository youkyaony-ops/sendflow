import asyncio
import re
import json
import os
import random
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from aiohttp import web

# ==================== НАСТРОЙКА ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'
SESSIONS_DIR = 'telegram_sessions'

PORT = int(os.environ.get('PORT', 8080))
RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL', 'https://sendflow-12.onrender.com')

if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

user_data = {}
active_tasks = {}
sessions = {}
user_states = {}

# ==================== ДАННЫЕ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'broadcasts': data.get('broadcasts', []),
                    'groups': data.get('groups', []),
                    'sessions': data.get('sessions', {}),
                    'created_at': data.get('created_at', str(datetime.now()))
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
            'created_at': str(datetime.now())
        }
        save_data()
    return user_data[uid]

def get_session_path(user_id):
    return os.path.join(SESSIONS_DIR, f'session_{user_id}.session')

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
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')]
])

def get_broadcast_actions(bid):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📨 СООБЩЕНИЕ", callback_data=f'message_{bid}')],
        [InlineKeyboardButton("👥 ГРУППЫ", callback_data=f'groups_{bid}'), InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data=f'interval_{bid}')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ", callback_data=f'start_{bid}'), InlineKeyboardButton("⏹️ СТОП", callback_data=f'stop_{bid}')],
        [InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data=f'delete_{bid}'), InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
    ])

GROUPS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("➕ ДОБАВИТЬ ГРУППУ", callback_data='add_group')],
    [InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')]
])

SETTINGS_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🗑 ОЧИСТИТЬ СЕССИЮ", callback_data='clear_session')],
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
    broadcasts = user_data[uid].get('broadcasts', [])
    if bid >= len(broadcasts):
        await send_safe(uid, bot, "❌ Рассылка не найдена", MAIN_MENU)
        return
    
    bc = broadcasts[bid]
    task_key = f"{uid}_{bid}"
    is_running = task_key in active_tasks and not active_tasks[task_key].done()
    
    status = "🟢 АКТИВНА" if is_running else "🔴 ОСТАНОВЛЕНА"
    txt = f"📢 <b>{bc.get('name', f'Рассылка {bid+1}')}</b>\n\n"
    txt += f"Статус: {status}\n"
    txt += f"📨 Сообщение: {'✅' if bc.get('source_chat_id') and bc.get('source_msg_id') else '❌'}\n"
    txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
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
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Максимум 10 рассылок", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}',
            'source_chat_id': None,
            'source_msg_id': None,
            'groups': [],
            'interval': 30,
            'active': False,
            'sent': 0,
            'errors': 0,
            'created_at': str(datetime.now())
        })
        save_data()
        await show_broadcast_menu(uid, context.bot, new_id)
    
    elif data == 'my_groups':
        groups = user_data[uid].get('groups', [])
        txt = "📁 <b>ВАШИ ГРУППЫ</b>\n\n"
        if groups:
            txt += "\n".join([f"{i+1}. {g}" for i, g in enumerate(groups)])
        else:
            txt += "Нет сохранённых групп\n\n➕ Добавьте через кнопку ниже"
        await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'settings':
        await send_safe(uid, context.bot, "⚙️ <b>НАСТРОЙКИ</b>\n\nОчистите сессию если нужно перелогиниться", SETTINGS_MENU)
    
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
        await send_safe(uid, context.bot, "🗑 Сессия очищена", SETTINGS_MENU)
    
    elif data.startswith('select_'):
        bid = int(data.split('_')[1])
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('message_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'waiting_message', 'bid': bid}
        await send_safe(uid, context.bot, "📨 <b>ОТПРАВЬТЕ СООБЩЕНИЕ</b>\n\nОтправьте текст, фото, видео - что хотите рассылать\n\n<i>Бот запомнит и будет пересылать</i>", CANCEL_BTN, parse_mode='HTML')
    
    elif data.startswith('groups_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2\n\nМожно использовать сохранённые группы из меню", CANCEL_BTN)
    
    elif data.startswith('interval_'):
        bid = int(data.split('_')[1])
        user_states[uid] = {'step': 'edit_interval', 'bid': bid}
        await send_safe(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)
    
    elif data.startswith('start_'):
        bid = int(data.split('_')[1])
        bc = user_data[uid]['broadcasts'][bid]
        
        if not bc.get('source_chat_id') or not bc.get('source_msg_id'):
            await send_safe(uid, context.bot, "❌ Сначала отправьте сообщение для рассылки!\nНажмите '📨 СООБЩЕНИЕ'")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ!\nНажмите '👥 ГРУППЫ'")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks and not active_tasks[task_key].done():
            await send_safe(uid, context.bot, "⚠️ Рассылка уже запущена")
            await show_broadcast_menu(uid, context.bot, bid)
            return
        
        client = await get_client(uid)
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
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, f"🛑 Рассылка #{bid+1} остановлена")
        await show_broadcast_menu(uid, context.bot, bid)
    
    elif data.startswith('delete_'):
        bid = int(data.split('_')[1])
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, f"🗑 Рассылка #{bid+1} удалена")
        await main_menu(uid, context.bot)
    
    elif data == 'add_group':
        user_states[uid] = {'step': 'add_group'}
        await send_safe(uid, context.bot, "➕ Введите ссылку на группу:\n@group_name или https://t.me/group", CANCEL_BTN)
    
    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Отменено")

# ==================== ЗАПУСК РАССЫЛКИ ====================
async def start_broadcast(uid, bot, bid, client):
    bc = user_data[uid]['broadcasts'][bid]
    groups = bc.get('groups', [])
    interval = bc.get('interval', 30)
    source_chat_id = bc.get('source_chat_id')
    source_msg_id = bc.get('source_msg_id')
    
    # Проверка групп и получение сущностей
    valid_groups = []
    for group in groups:
        try:
            entity = await client.get_entity(group)
            valid_groups.append(entity)
        except Exception as e:
            await send_safe(uid, bot, f"⚠️ {group} - недоступна: {str(e)[:50]}")
    
    if not valid_groups:
        await send_safe(uid, bot, "❌ Нет доступных групп!\n\nПроверьте:\n1. Правильно ли указан юзернейм\n2. Добавлен ли бот в группу")
        return
    
    bc['groups'] = [g.username if hasattr(g, 'username') else str(g.id) for g in valid_groups]
    bc['active'] = True
    save_data()
    
    await send_safe(uid, bot, f"🚀 <b>РАССЫЛКА ЗАПУЩЕНА!</b>\n\n✅ Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек\n🔄 Режим: пересылка\n\nСообщения начали отправляться!", MAIN_MENU)
    
    task_key = f"{uid}_{bid}"
    task = asyncio.create_task(run_broadcast(uid, bid, client, valid_groups, interval, source_chat_id, source_msg_id))
    active_tasks[task_key] = task

async def run_broadcast(uid, bid, client, groups, interval, source_chat_id, source_msg_id):
    sent = 0
    
    try:
        while True:
            for group in groups:
                try:
                    # Получаем сообщение для пересылки
                    message = await client.get_messages(source_chat_id, ids=source_msg_id)
                    
                    if message:
                        # Пересылаем
                        await client.forward_messages(group, message)
                        sent += 1
                        
                        if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
                            user_data[uid]['broadcasts'][bid]['sent'] = sent
                            save_data()
                    else:
                        print(f"[ERROR] Сообщение не найдено для {uid}")
                    
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except Exception as e:
                    print(f"[ERROR] {e}")
                
                await asyncio.sleep(interval)
                
    except asyncio.CancelledError:
        if uid in user_data and bid < len(user_data[uid].get('broadcasts', [])):
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    uid = update.effective_user.id
    save_user(uid)
    
    step_data = user_states.get(uid, {})
    step = step_data.get('step')
    
    if not step:
        await main_menu(uid, context.bot)
        return
    
    # ДОБАВЛЕНИЕ ГРУППЫ
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
            await send_safe(uid, context.bot, f"✅ Группа {group} добавлена!\n\nТеперь используйте её в рассылке через кнопку ГРУППЫ", GROUPS_MENU)
        else:
            await send_safe(uid, context.bot, f"⚠️ Группа {group} уже есть", GROUPS_MENU)
        del user_states[uid]
    
    # ПОЛУЧЕНИЕ СООБЩЕНИЯ ДЛЯ РАССЫЛКИ
    elif step == 'waiting_message':
        bid = step_data['bid']
        
        # Сохраняем информацию о сообщении
        user_data[uid]['broadcasts'][bid]['source_chat_id'] = update.effective_chat.id
        user_data[uid]['broadcasts'][bid]['source_msg_id'] = update.effective_message.message_id
        save_data()
        
        await send_safe(uid, context.bot, f"✅ <b>СООБЩЕНИЕ СОХРАНЕНО!</b>\n\nТеперь настройте группы и интервал", parse_mode='HTML')
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
    
    # АВТОРИЗАЦИЯ
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
            await send_safe(uid, context.bot, "📲 Введите код из Telegram:\n\nФормат: code12345", CANCEL_BTN)
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
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
        await send_safe(uid, context.bot, "🔐 Если есть пароль 2FA - введите его\nЕсли нет - отправьте /skip", CANCEL_BTN)
    
    elif step == 'waiting_2fa':
        if not update.message.text:
            return
        password = None if update.message.text.strip().lower() == '/skip' else update.message.text.strip()
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
            if password is None:
                await send_safe(uid, context.bot, "🔐 Требуется пароль 2FA. Введите пароль:", CANCEL_BTN)
                return
            try:
                await client.sign_in(password=password)
            except:
                await send_safe(uid, context.bot, "❌ Неверный пароль", CANCEL_BTN)
                return
        except Exception as e:
            await send_safe(uid, context.bot, f"❌ Ошибка: {str(e)[:100]}", MAIN_MENU)
            del user_states[uid]
            return
        
        user_data[uid]['sessions'] = {'phone': phone, 'is_authorized': True, 'last_used': str(datetime.now())}
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
    bot_app.add_handler(MessageHandler(filters.ALL, message_handler))
    
    await bot_app.initialize()
    await bot_app.start()
    
    webhook_url = f"{RENDER_URL}/webhook/{BOT_TOKEN}"
    await bot_app.bot.set_webhook(webhook_url)
    
    print("=" * 60)
    print("✅ БОТ ЗАПУЩЕН")
    print("📨 ПЕРЕСЫЛКА СООБЩЕНИЙ")
    print("=" * 60)
    
    await start_http_server()

def main():
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
