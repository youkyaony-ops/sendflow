import asyncio
import re
import logging
import json
import os
from telethon import TelegramClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
ADMIN_ID = 6301912178
API_ID = 39021931
API_HASH = '55227d81fae655ad385381539f67bf90'

# Файл для сохранения данных
DATA_FILE = 'user_data.json'

user_data = {}
active_tasks = {}
temp_sessions = {}

# Загрузка данных из файла
def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                # Конвертируем ключи обратно в int
                user_data = {int(k): v for k, v in loaded.items()}
                logger.info(f"Загружены данные для {len(user_data)} пользователей")
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            user_data = {}

# Сохранение данных в файл
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(user_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")

MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Текст рассылки", callback_data='set_text')],
    [InlineKeyboardButton("🔗 Группы для рассылки", callback_data='set_groups')],
    [InlineKeyboardButton("⏱ Интервал (сек)", callback_data='set_interval')],
    [
        InlineKeyboardButton("▶️ ЗАПУСТИТЬ", callback_data='start_broadcast'),
        InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data='stop_broadcast')
    ],
    [InlineKeyboardButton("📊 Статус настроек", callback_data='status')],
    [InlineKeyboardButton("🗑 Сбросить всё", callback_data='reset_all')]
])

async def show_main_menu(chat_id, bot, text=None):
    msg = "🥕 SendFlow\n\nИспользуй кнопки ниже для управления рассылкой"
    if text:
        msg = text + "\n\n" + msg
    await bot.send_message(chat_id, msg, reply_markup=MAIN_KEYBOARD)

async def start(update: Update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа")
        return
    
    # Загружаем последние данные
    load_data()
    
    if uid not in user_data:
        user_data[uid] = {}
        save_data()
    
    # Показываем текущие настройки
    status_text = "📊 ТВОИ НАСТРОЙКИ:\n"
    if user_data[uid].get('text'):
        status_text += f"✅ Текст: {user_data[uid]['text'][:50]}...\n"
    else:
        status_text += "❌ Текст: не настроен\n"
    
    if user_data[uid].get('groups'):
        status_text += f"✅ Группы: {len(user_data[uid]['groups'])} шт\n"
    else:
        status_text += "❌ Группы: не настроены\n"
    
    if user_data[uid].get('interval'):
        status_text += f"✅ Интервал: {user_data[uid]['interval']} сек\n"
    else:
        status_text += "❌ Интервал: не настроен\n"
    
    await show_main_menu(uid, context.bot, status_text)

async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    
    if uid != ADMIN_ID:
        await query.edit_message_text("❌ Нет доступа", reply_markup=MAIN_KEYBOARD)
        return
    
    data = query.data
    
    try:
        await query.message.delete()
    except:
        pass
    
    if data == 'set_text':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'text'
        save_data()
        await context.bot.send_message(uid, "📝 Введи текст рассылки (можно с эмодзи):")
    
    elif data == 'set_groups':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'groups'
        save_data()
        await context.bot.send_message(uid, "🔗 Введи ссылки на группы через запятую\nПример: @group1, @group2")
    
    elif data == 'set_interval':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'interval'
        save_data()
        await context.bot.send_message(uid, "⏱ Введи интервал в секундах (5-120):")
    
    elif data == 'start_broadcast':
        if uid not in user_data:
            user_data[uid] = {}
            save_data()
        
        # ПРЯМАЯ ПРОВЕРКА - читаем из словаря
        has_text = user_data[uid].get('text') is not None and user_data[uid]['text'] != ''
        has_groups = user_data[uid].get('groups') is not None and len(user_data[uid]['groups']) > 0
        has_interval = user_data[uid].get('interval') is not None
        
        missing = []
        if not has_text:
            missing.append("текст")
            logger.warning(f"Пользователь {uid}: нет текста")
        if not has_groups:
            missing.append("группы")
            logger.warning(f"Пользователь {uid}: нет групп")
        if not has_interval:
            missing.append("интервал")
            logger.warning(f"Пользователь {uid}: нет интервала")
        
        if missing:
            await context.bot.send_message(
                uid, 
                f"❌ НЕ НАСТРОЕНО: {', '.join(missing)}\n\n"
                f"Твои текущие данные:\n"
                f"Текст: {'✅' if has_text else '❌'}\n"
                f"Группы: {len(user_data[uid].get('groups', [])) if has_groups else '❌'}\n"
                f"Интервал: {user_data[uid].get('interval', '❌')}\n\n"
                f"Настрой через кнопки ниже",
                reply_markup=MAIN_KEYBOARD
            )
            return
        
        if uid in active_tasks and not active_tasks[uid].done():
            await context.bot.send_message(uid, "⚠️ РАССЫЛКА УЖЕ ЗАПУЩЕНА!", reply_markup=MAIN_KEYBOARD)
            return
        
        user_data[uid]['step'] = 'phone'
        save_data()
        await context.bot.send_message(uid, "🔐 Введи номер телефона с +\nПример: +77081234567")
    
    elif data == 'stop_broadcast':
        if uid in active_tasks and not active_tasks[uid].done():
            active_tasks[uid].cancel()
            await context.bot.send_message(uid, "🛑 РАССЫЛКА ОСТАНОВЛЕНА", reply_markup=MAIN_KEYBOARD)
        else:
            await context.bot.send_message(uid, "❌ НЕТ АКТИВНОЙ РАССЫЛКИ", reply_markup=MAIN_KEYBOARD)
    
    elif data == 'status':
        if uid not in user_data:
            user_data[uid] = {}
            save_data()
        
        d = user_data[uid]
        status_text = "📊 **ТЕКУЩИЕ НАСТРОЙКИ**\n\n"
        status_text += f"📝 Текст: {'✅ ЕСТЬ' if d.get('text') else '❌ НЕТ'}\n"
        if d.get('text'):
            preview = d['text'][:80] + "..." if len(d['text']) > 80 else d['text']
            status_text += f"   → {preview}\n"
        
        status_text += f"🔗 Группы: {len(d.get('groups', []))} шт\n" if d.get('groups') else "🔗 Группы: ❌ НЕТ\n"
        if d.get('groups'):
            status_text += f"   → {', '.join(d['groups'][:3])}\n"
        
        status_text += f"⏱ Интервал: {d.get('interval', '❌ НЕТ')} сек\n"
        
        if uid in active_tasks and not active_tasks[uid].done():
            status_text += "\n🟢 **РАССЫЛКА АКТИВНА**"
        else:
            status_text += "\n🔴 РАССЫЛКА НЕ АКТИВНА"
        
        await context.bot.send_message(uid, status_text, reply_markup=MAIN_KEYBOARD)
    
    elif data == 'reset_all':
        if uid in user_data:
            user_data.pop(uid)
        if uid in active_tasks:
            try:
                active_tasks[uid].cancel()
            except:
                pass
            active_tasks.pop(uid, None)
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            temp_sessions.pop(uid, None)
        save_data()
        await context.bot.send_message(uid, "🗑 ВСЕ НАСТРОЙКИ СБРОШЕНЫ", reply_markup=MAIN_KEYBOARD)

async def handle_message(update: Update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    if uid not in user_data:
        user_data[uid] = {}
        save_data()
    
    # Если нет активного шага - показываем меню
    if 'step' not in user_data[uid]:
        await show_main_menu(uid, context.bot)
        return
    
    step = user_data[uid]['step']
    
    if step == 'text':
        if not text:
            await update.message.reply_text("❌ Текст не может быть пустым")
            return
        user_data[uid]['text'] = text
        user_data[uid].pop('step')
        save_data()
        logger.info(f"Сохранён текст для {uid}: {text[:50]}...")
        await show_main_menu(uid, context.bot, f"✅ Текст сохранён!")
    
    elif step == 'groups':
        groups_raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in groups_raw:
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if not groups:
            await update.message.reply_text("❌ Список пуст. Попробуй снова")
            return
        
        user_data[uid]['groups'] = groups
        user_data[uid].pop('step')
        save_data()
        logger.info(f"Сохранены группы для {uid}: {len(groups)} шт")
        await show_main_menu(uid, context.bot, f"✅ Сохранено групп: {len(groups)}")
    
    elif step == 'interval':
        try:
            interval = int(text)
            if 5 <= interval <= 120:
                user_data[uid]['interval'] = interval
                user_data[uid].pop('step')
                save_data()
                logger.info(f"Сохранён интервал для {uid}: {interval}")
                await show_main_menu(uid, context.bot, f"✅ Интервал {interval} сек")
            else:
                await update.message.reply_text("❌ Число от 5 до 120\nПопробуй снова:")
        except ValueError:
            await update.message.reply_text("❌ Введи число\nПопробуй снова:")
    
    elif step == 'phone':
        if not text.startswith('+') or not text[1:].isdigit():
            await update.message.reply_text("❌ Номер должен начинаться с +\nПример: +77081234567")
            return
        
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            temp_sessions.pop(uid, None)
        
        user_data[uid]['phone'] = text
        user_data[uid]['step'] = 'code'
        save_data()
        
        session_name = f'session_{uid}'
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(text)
            temp_sessions[uid] = client
            await update.message.reply_text("📲 Введи код в формате: code12345")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
            user_data[uid].pop('step')
            save_data()
            await show_main_menu(uid, context.bot, "❌ Ошибка авторизации")
    
    elif step == 'code':
        match = re.search(r'code(\d+)', text.lower())
        code = match.group(1) if match else None
        
        if not code:
            await update.message.reply_text("❌ ФОРМАТ: code12345\nПопробуй снова:")
            return
        
        user_data[uid]['code'] = code
        user_data[uid]['step'] = 'password'
        save_data()
        await update.message.reply_text("🔐 Пароль 2FA (если есть) или /skip")
    
    elif step == 'password':
        password = None if text == '/skip' else text
        
        client = temp_sessions.get(uid)
        if not client:
            await update.message.reply_text("❌ Сессия потеряна")
            await show_main_menu(uid, context.bot)
            return
        
        groups = user_data[uid].get('groups', [])
        msg = user_data[uid].get('text', '')
        interval = user_data[uid].get('interval', 30)
        
        try:
            await client.sign_in(code=user_data[uid]['code'])
            
            if password:
                try:
                    await client.sign_in(password=password)
                except errors.PasswordHashInvalidError:
                    await update.message.reply_text("❌ Неверный пароль")
                    return
            
            await update.message.reply_text("🔍 Проверяю группы...")
            valid_groups = []
            
            for group in groups:
                try:
                    await client.get_entity(group)
                    valid_groups.append(group)
                except:
                    await update.message.reply_text(f"⚠️ {group} - недоступна")
            
            if not valid_groups:
                await update.message.reply_text("❌ Нет доступных групп!")
                await show_main_menu(uid, context.bot)
                return
            
            user_data[uid]['groups'] = valid_groups
            save_data()
            
            await update.message.reply_text(
                f"✅ ЗАПУСКАЮ РАССЫЛКУ\n"
                f"Групп: {len(valid_groups)}\n"
                f"Интервал: {interval} сек\n\n"
                f"Для остановки нажми кнопку ОСТАНОВИТЬ",
                reply_markup=MAIN_KEYBOARD
            )
            
            task = asyncio.create_task(run_broadcast(uid, context.bot, client, valid_groups, msg, interval))
            active_tasks[uid] = task
            user_data[uid].pop('step', None)
            save_data()
            
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
            await show_main_menu(uid, context.bot)

async def run_broadcast(uid, bot, client, groups, text, interval):
    total = len(groups)
    current = 0
    
    try:
        while True:
            for idx, group in enumerate(groups, 1):
                current = idx
                try:
                    await client.send_message(group, text)
                    logger.info(f"[+] {uid} -> {group}")
                    if idx % 10 == 0:
                        await bot.send_message(uid, f"📨 {idx}/{total}", reply_markup=MAIN_KEYBOARD)
                except Exception as e:
                    await bot.send_message(uid, f"❌ {group}: {str(e)[:50]}")
                await asyncio.sleep(interval)
            await bot.send_message(uid, f"🔄 Круг завершён", reply_markup=MAIN_KEYBOARD)
    except asyncio.CancelledError:
        await bot.send_message(uid, f"🛑 Остановлено. Отправлено: {current}/{total}", reply_markup=MAIN_KEYBOARD)
    finally:
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            temp_sessions.pop(uid, None)
        active_tasks.pop(uid, None)

async def skip_command(update: Update, context):
    uid = update.effective_user.id
    if uid == ADMIN_ID and uid in user_data and user_data[uid].get('step') == 'password':
        await handle_message(update, context)

def main():
    load_data()  # Загружаем данные при старте
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ SendFlow запущен")
    print(f"📁 Данные сохраняются в {DATA_FILE}")
    app.run_polling()

if __name__ == '__main__':
    main()
