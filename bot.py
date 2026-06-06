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

# ==================== НАСТРОЙКА ====================
# Отключаем все лишние логи для Render
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

# Получаем переменные окружения для Render
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8')
API_ID = int(os.environ.get('API_ID', 31245848))
API_HASH = os.environ.get('API_HASH', '67336528977585e1457985dc1d0ceefb')
DATA_FILE = 'user_data.json'

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
                    'created_at': data.get('created_at', str(datetime.now()))
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        logger.error(f"Save error: {e}")
        return False

def load_data():
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
    except Exception as e:
        logger.error(f"Load error: {e}")
        user_data = {}

# ==================== КЛАВИАТУРЫ ====================
MAIN_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📢 МОИ РАССЫЛКИ", callback_data='my_broadcasts')],
    [InlineKeyboardButton("➕ НОВАЯ РАССЫЛКА", callback_data='new_broadcast')],
    [InlineKeyboardButton("📁 МОИ ГРУППЫ", callback_data='my_groups')],
    [InlineKeyboardButton("📊 СТАТИСТИКА", callback_data='my_stats')],
    [InlineKeyboardButton("⚙️ НАСТРОЙКИ", callback_data='settings')],
    [InlineKeyboardButton("❓ ПОМОЩЬ", callback_data='help_menu')]
])

BROADCAST_ACTIONS = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 ТЕКСТ", callback_data='edit_text'), InlineKeyboardButton("👥 ГРУППЫ", callback_data='edit_groups')],
    [InlineKeyboardButton("⏱ ИНТЕРВАЛ", callback_data='edit_interval'), InlineKeyboardButton("🎲 РАНДОМ", callback_data='edit_random')],
    [InlineKeyboardButton("🔄 ЗАЦИКЛИТЬ", callback_data='toggle_loop'), InlineKeyboardButton("📅 РАСПИСАНИЕ", callback_data='edit_schedule')],
    [InlineKeyboardButton("🚀 ЗАПУСТИТЬ 24/7", callback_data='start_247'), InlineKeyboardButton("▶️ ОТПРАВИТЬ РАЗОМ", callback_data='send_once')],
    [InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data='stop_broadcast'), InlineKeyboardButton("📊 СТАТУС", callback_data='bc_status')],
    [InlineKeyboardButton("📎 КЛОНИРОВАТЬ", callback_data='clone_broadcast'), InlineKeyboardButton("🗑 УДАЛИТЬ", callback_data='delete_broadcast')],
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

def save_user(uid):
    if uid not in user_data:
        user_data[uid] = {
            'broadcasts': [],
            'groups': [],
            'settings': {'notify': True, 'autosave': True, 'def_interval': 30},
            'created_at': str(datetime.now())
        }
        save_data()

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
            await send_safe(uid, context.bot, "📢 У вас нет рассылок\n\nСоздайте новую через кнопку '➕ НОВАЯ РАССЫЛКА'", MAIN_MENU)
            return
        
        kb = []
        for i, bc in enumerate(broadcasts):
            name = bc.get('name', f'Рассылка {i+1}')
            status = "🟢" if bc.get('active') else "🔴"
            kb.append([InlineKeyboardButton(f"{status} {name}", callback_data=f'bc_{i}')])
        kb.append([InlineKeyboardButton("🔙 НАЗАД", callback_data='back_to_main')])
        
        await context.bot.send_message(uid, "📋 <b>ВАШИ РАССЫЛКИ</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode='HTML')
    
    elif data == 'new_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Максимум 10 рассылок\nУдалите ненужные", MAIN_MENU)
            return
        
        new_id = len(broadcasts)
        user_data[uid]['broadcasts'].append({
            'name': f'Рассылка {new_id+1}',
            'text': None, 'groups': [], 'interval': 30,
            'active': False, 'loop': True, 'random_min': 0, 'random_max': 0,
            'sent': 0, 'errors': 0, 'schedule': None
        })
        save_data()
        await send_safe(uid, context.bot, f"✅ Создана рассылка #{new_id+1}\n\nНастройте параметры:", BROADCAST_ACTIONS)
    
    elif data == 'my_groups':
        groups = user_data[uid].get('groups', [])
        if not groups:
            await send_safe(uid, context.bot, "📁 У вас нет сохранённых групп\n\nДобавьте первую через кнопку '➕ ДОБАВИТЬ ГРУППУ'", GROUPS_MENU)
        else:
            txt = f"📁 <b>ВАШИ ГРУППЫ ({len(groups)})</b>\n\n" + "\n".join([f"• {g}" for g in groups[:15]])
            if len(groups) > 15:
                txt += f"\n\n... и ещё {len(groups)-15} групп"
            await send_safe(uid, context.bot, txt, GROUPS_MENU)
    
    elif data == 'my_stats':
        data_u = user_data[uid]
        bc = data_u.get('broadcasts', [])
        active = sum(1 for b in bc if b.get('active'))
        total_sent = sum(b.get('sent', 0) for b in bc)
        
        txt = f"📊 <b>ВАША СТАТИСТИКА</b>\n\n"
        txt += f"📢 Рассылок: {len(bc)} (🟢 {active} активных)\n"
        txt += f"📨 Отправлено: {total_sent} сообщений\n"
        txt += f"📁 Сохранено групп: {len(data_u.get('groups', []))}\n"
        txt += f"📅 Дата регистрации: {data_u.get('created_at', 'Неизвестно')[:10]}"
        await send_safe(uid, context.bot, txt, MAIN_MENU)
    
    elif data == 'settings':
        s = user_data[uid].get('settings', {})
        txt = f"⚙️ <b>НАСТРОЙКИ</b>\n\n"
        txt += f"🔔 Уведомления: {'✅ Вкл' if s.get('notify', True) else '❌ Выкл'}\n"
        txt += f"💾 Автосохранение: {'✅ Вкл' if s.get('autosave', True) else '❌ Выкл'}\n"
        txt += f"⏱ Интервал по умолч.: {s.get('def_interval', 30)} сек"
        await send_safe(uid, context.bot, txt, SETTINGS_MENU)
    
    elif data == 'help_menu':
        await send_safe(uid, context.bot, "❓ <b>ПОМОЩЬ</b>\n\nВыберите раздел:", HELP_MENU)
    
    elif data == 'help_quick':
        txt = "🚀 <b>БЫСТРЫЙ СТАРТ</b>\n\n1️⃣ Нажми '➕ НОВАЯ РАССЫЛКА'\n2️⃣ Настрой текст и группы\n3️⃣ Нажми '🚀 ЗАПУСТИТЬ 24/7'\n4️⃣ Авторизуйся в Telegram\n\nГотово! Рассылка работает 24/7"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_create':
        txt = "📢 <b>КАК СОЗДАТЬ РАССЫЛКУ</b>\n\n<b>Текст:</b> любое сообщение, до 4096 символов\n<b>Группы:</b> через запятую: @group1, @group2\n<b>Интервал:</b> время между сообщениями (5-300 сек)\n<b>Рандом:</b> случайная задержка\n<b>Зациклить:</b> бесконечный повтор"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data == 'help_errors':
        txt = "🔧 <b>ЧАСТЫЕ ОШИБКИ</b>\n\n<b>2FA:</b> введи пароль или /skip\n<b>Группа недоступна:</b> добавь бота в группу\n<b>Флуд:</b> увеличь интервал до 30+ сек\n<b>Неверный код:</b> формат code12345"
        await send_safe(uid, context.bot, txt, HELP_MENU)
    
    elif data.startswith('bc_'):
        bid = int(data.split('_')[1])
        user_data[uid]['current_bc'] = bid
        save_data()
        bc = user_data[uid]['broadcasts'][bid]
        status = "🟢 АКТИВНА" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"
        txt = f"📢 <b>{bc['name']}</b>\n\n"
        txt += f"Статус: {status}\n"
        txt += f"📝 Текст: {'✅' if bc.get('text') else '❌'}\n"
        txt += f"👥 Групп: {len(bc.get('groups', []))}\n"
        txt += f"⏱ Интервал: {bc.get('interval', 30)} сек\n"
        if bc.get('random_min') and bc.get('random_max'):
            txt += f"🎲 Рандом: {bc['random_min']}-{bc['random_max']} сек\n"
        txt += f"🔄 Зациклено: {'✅' if bc.get('loop', True) else '❌'}\n"
        txt += f"📨 Отправлено: {bc.get('sent', 0)}\n"
        txt += f"❌ Ошибок: {bc.get('errors', 0)}"
        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)
    
    elif data == 'edit_text':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_text', 'bid': bid}
        await send_safe(uid, context.bot, "📝 Введите текст рассылки:", CANCEL_BTN)
    
    elif data == 'edit_groups':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_groups', 'bid': bid}
        await send_safe(uid, context.bot, "👥 Введите группы через запятую:\n\nПример: @group1, @group2, https://t.me/group3", CANCEL_BTN)
    
    elif data == 'edit_interval':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_interval', 'bid': bid}
        await send_safe(uid, context.bot, "⏱ Введите интервал (5-300 секунд):", CANCEL_BTN)
    
    elif data == 'edit_random':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_random', 'bid': bid}
        await send_safe(uid, context.bot, "🎲 Введите диапазон случайной задержки:\n\nФормат: мин-макс\nПример: 10-30\n\nДля отключения введите 0", CANCEL_BTN)
    
    elif data == 'toggle_loop':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        bc['loop'] = not bc.get('loop', True)
        save_data()
        await send_safe(uid, context.bot, f"🔄 Зацикливание: {'ВКЛЮЧЕНО' if bc['loop'] else 'ВЫКЛЮЧЕНО'}")
        await button_handler(update, context)
    
    elif data == 'edit_schedule':
        bid = user_data[uid].get('current_bc', 0)
        user_states[uid] = {'step': 'edit_schedule', 'bid': bid}
        await send_safe(uid, context.bot, "📅 Введите время расписания (ЧЧ:ММ):\n\nПример: 14:30\n\nДля отключения введите 'off'", CANCEL_BTN)
    
    elif data == 'start_247':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        
        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!", BROADCAST_ACTIONS)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BROADCAST_ACTIONS)
            return
        if f"{uid}_{bid}" in active_tasks:
            await send_safe(uid, context.bot, "⚠️ Рассылка уже запущена!", BROADCAST_ACTIONS)
            return
        
        user_states[uid] = {'step': 'start_247', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона Telegram:\n\nПример: +79123456789", CANCEL_BTN)
    
    elif data == 'send_once':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        
        if not bc.get('text'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ТЕКСТ рассылки!", BROADCAST_ACTIONS)
            return
        if not bc.get('groups'):
            await send_safe(uid, context.bot, "❌ Сначала настройте ГРУППЫ для рассылки!", BROADCAST_ACTIONS)
            return
        
        user_states[uid] = {'step': 'send_once', 'bid': bid}
        await send_safe(uid, context.bot, "🔐 Введите номер телефона Telegram:\n\nПример: +79123456789", CANCEL_BTN)
    
    elif data == 'stop_broadcast':
        bid = user_data[uid].get('current_bc', 0)
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
            user_data[uid]['broadcasts'][bid]['active'] = False
            save_data()
            await send_safe(uid, context.bot, "🛑 Рассылка остановлена")
        else:
            await send_safe(uid, context.bot, "❌ Нет активной рассылки")
        await button_handler(update, context)
    
    elif data == 'bc_status':
        bid = user_data[uid].get('current_bc', 0)
        bc = user_data[uid]['broadcasts'][bid]
        status = "🟢 РАБОТАЕТ" if bc.get('active') else "🔴 ОСТАНОВЛЕНА"
        txt = f"📊 <b>СТАТУС РАССЫЛКИ</b>\n\n"
        txt += f"Имя: {bc['name']}\n"
        txt += f"Статус: {status}\n"
        txt += f"Отправлено: {bc.get('sent', 0)}\n"
        txt += f"Ошибок: {bc.get('errors', 0)}\n"
        txt += f"Групп: {len(bc.get('groups', []))}"
        await send_safe(uid, context.bot, txt, BROADCAST_ACTIONS)
    
    elif data == 'clone_broadcast':
        broadcasts = user_data[uid].get('broadcasts', [])
        if len(broadcasts) >= 10:
            await send_safe(uid, context.bot, "❌ Достигнут лимит рассылок (10)", MAIN_MENU)
            return
        
        bid = user_data[uid].get('current_bc', 0)
        original = user_data[uid]['broadcasts'][bid]
        new_bc = {
            'name': f"Копия {original['name']}",
            'text': original.get('text'),
            'groups': original.get('groups', []).copy(),
            'interval': original.get('interval', 30),
            'active': False,
            'loop': original.get('loop', True),
            'random_min': original.get('random_min', 0),
            'random_max': original.get('random_max', 0),
            'sent': 0, 'errors': 0, 'schedule': None
        }
        user_data[uid]['broadcasts'].append(new_bc)
        save_data()
        await send_safe(uid, context.bot, "✅ Рассылка склонирована!", MAIN_MENU)
    
    elif data == 'delete_broadcast':
        bid = user_data[uid].get('current_bc', 0)
        task_key = f"{uid}_{bid}"
        if task_key in active_tasks:
            active_tasks[task_key].cancel()
        user_data[uid]['broadcasts'].pop(bid)
        save_data()
        await send_safe(uid, context.bot, "🗑 Рассылка удалена", MAIN_MENU)
    
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
    
    elif data == 'cancel':
        if uid in user_states:
            del user_states[uid]
        await main_menu(uid, context.bot, "❌ Действие отменено")

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
    
    elif step == 'edit_text':
        bid = step_data['bid']
        if len(text) > 4096:
            await send_safe(uid, context.bot, "❌ Текст слишком длинный (макс 4096 символов)", CANCEL_BTN)
            return
        user_data[uid]['broadcasts'][bid]['text'] = text
        save_data()
        await send_safe(uid, context.bot, "✅ Текст сохранён!")
        del user_states[uid]
        await button_handler(update, context)
    
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
            user_data[uid]['broadcasts'][bid]['groups'] = groups
            save_data()
            await send_safe(uid, context.bot, f"✅ Сохранено {len(groups)} групп!")
        else:
            await send_safe(uid, context.bot, "❌ Не найдено групп", CANCEL_BTN)
            return
        del user_states[uid]
        await button_handler(update, context)
    
    elif step == 'edit_interval':
        bid = step_data['bid']
        try:
            val = int(text)
            if 5 <= val <= 300:
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
        await button_handler(update, context)
    
    elif step == 'edit_random':
        bid = step_data['bid']
        if text == '0':
            user_data[uid]['broadcasts'][bid]['random_min'] = 0
            user_data[uid]['broadcasts'][bid]['random_max'] = 0
            save_data()
            await send_safe(uid, context.bot, "✅ Рандом отключён")
            del user_states[uid]
            await button_handler(update, context)
            return
        
        match = re.match(r'(\d+)-(\d+)', text)
        if match:
            min_val = int(match.group(1))
            max_val = int(match.group(2))
            if 0 <= min_val < max_val <= 300:
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
        await button_handler(update, context)
    
    elif step == 'edit_schedule':
        bid = step_data['bid']
        if text.lower() == 'off':
            user_data[uid]['broadcasts'][bid]['schedule'] = None
            save_data()
            await send_safe(uid, context.bot, "✅ Расписание отключено")
            del user_states[uid]
            await button_handler(update, context)
            return
        
        match = re.match(r'(\d{1,2}):(\d{2})', text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            if 0 <= hour <= 23 and 0 <= minute <= 59:
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
        await button_handler(update, context)
    
    elif step in ['start_247', 'send_once']:
        bid = step_data['bid']
        is_247 = (step == 'start_247')
        
        if not text.startswith('+'):
            await send_safe(uid, context.bot, "❌ Формат: +79123456789", CANCEL_BTN)
            return
        
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
        
        user_states[uid] = {'step': 'waiting_code', 'bid': bid, 'is_247': is_247, 'phone': text}
        
        client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(text)
            await send_safe(uid, context.bot, "📲 Введите код из Telegram:\n\nФормат: code12345", CANCEL_BTN)
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
            await send_safe(uid, context.bot, f"🚀 ЗАПУСК 24/7\n\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек\n{'🎲 Рандом: ' + str(random_min) + '-' + str(random_max) + ' сек' if random_min else ''}", MAIN_MENU)
            task = asyncio.create_task(run_247(uid, bid, client, valid_groups, msg, interval, random_min, random_max))
            active_tasks[f"{uid}_{bid}"] = task
            user_data[uid]['broadcasts'][bid]['active'] = True
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
            await send_safe(uid, context.bot, f"✅ Отправлено: {success}/{len(valid_groups)}", MAIN_MENU)
            await client.disconnect()
            if uid in sessions:
                del sessions[uid]
        
        save_data()
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
                    user_data[uid]['broadcasts'][bid]['sent'] = sent
                    save_data()
                except FloodWaitError as e:
                    await asyncio.sleep(e.seconds)
                except:
                    pass
                
                delay = interval
                if random_min and random_max:
                    delay = random.randint(random_min, random_max)
                await asyncio.sleep(delay)
    except asyncio.CancelledError:
        user_data[uid]['broadcasts'][bid]['active'] = False
        save_data()
        try:
            await client.disconnect()
        except:
            pass
        if uid in sessions:
            del sessions[uid]

# ==================== ЗАПУСК ДЛЯ RENDER ====================
async def run_bot():
    load_data()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("skip", skip_cmd))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("=" * 50)
    print("✅ SENDFLOW БОТ ЗАПУЩЕН НА RENDER")
    print("=" * 50)
    
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    # Держим бота живым
    while True:
        await asyncio.sleep(3600)

def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        print("🛑 Бот остановлен")
    finally:
        loop.close()

if __name__ == '__main__':
    main()
