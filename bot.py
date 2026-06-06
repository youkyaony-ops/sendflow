import asyncio
import re
import logging
import json
import os
import traceback
from telethon import TelegramClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Отключаем лишние логи
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("telegram").setLevel(logging.ERROR)
logging.getLogger("telethon").setLevel(logging.ERROR)

# Настройка логирования ошибок
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.ERROR)
logger = logging.getLogger(__name__)

BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'

DATA_FILE = 'user_data.json'

user_data = {}
active_tasks = {}
temp_sessions = {}

def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
        except Exception as e:
            logger.error(f"Ошибка загрузки: {e}")
            user_data = {}

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

# Глобальный обработчик ошибок
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик ошибок"""
    try:
        # Логируем ошибку
        logger.error(f"Exception: {context.error}")
        logger.error(traceback.format_exc())
        
        # Отправляем сообщение пользователю если возможно
        if update and update.effective_user:
            uid = update.effective_user.id
            await context.bot.send_message(
                uid, 
                f"❌ Произошла ошибка: {str(context.error)[:100]}\n\nПопробуй /start заново"
            )
    except Exception as e:
        logger.error(f"Ошибка в обработчике ошибок: {e}")

async def show_main_menu(chat_id, bot, text=None):
    try:
        msg = "🥕 SendFlow\n\nИспользуй кнопки ниже"
        if text:
            msg = text + "\n\n" + msg
        await bot.send_message(chat_id, msg, reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        logger.error(f"Ошибка show_main_menu: {e}")

async def start(update: Update, context):
    try:
        uid = update.effective_user.id
        username = update.effective_user.first_name or str(uid)
        
        load_data()
        
        if uid not in user_data:
            user_data[uid] = {}
            save_data()
        
        status_text = f"👋 Привет, {username}!\n\n📊 Твои настройки:\n"
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
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")
        await update.message.reply_text("❌ Ошибка, попробуй еще раз /start")

async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    
    try:
        await query.message.delete()
    except:
        pass
    
    if data == 'set_text':
        try:
            user_data[uid] = user_data.get(uid, {})
            user_data[uid]['step'] = 'text'
            save_data()
            await context.bot.send_message(uid, "📝 Введи текст рассылки:")
        except Exception as e:
            logger.error(f"set_text ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка, попробуй еще раз")
    
    elif data == 'set_groups':
        try:
            user_data[uid] = user_data.get(uid, {})
            user_data[uid]['step'] = 'groups'
            save_data()
            await context.bot.send_message(uid, "🔗 Введи ссылки на группы через запятую\nПример: @group1, @group2")
        except Exception as e:
            logger.error(f"set_groups ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка, попробуй еще раз")
    
    elif data == 'set_interval':
        try:
            user_data[uid] = user_data.get(uid, {})
            user_data[uid]['step'] = 'interval'
            save_data()
            await context.bot.send_message(uid, "⏱ Введи интервал (5-120 сек):")
        except Exception as e:
            logger.error(f"set_interval ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка, попробуй еще раз")
    
    elif data == 'start_broadcast':
        try:
            if uid not in user_data:
                user_data[uid] = {}
                save_data()
            
            has_text = user_data[uid].get('text')
            has_groups = user_data[uid].get('groups') and len(user_data[uid]['groups']) > 0
            has_interval = user_data[uid].get('interval')
            
            missing = []
            if not has_text: missing.append("текст")
            if not has_groups: missing.append("группы")
            if not has_interval: missing.append("интервал")
            
            if missing:
                await context.bot.send_message(uid, f"❌ Не настроено: {', '.join(missing)}\n\nНастрой через кнопки ниже", reply_markup=MAIN_KEYBOARD)
                return
            
            if uid in active_tasks and not active_tasks[uid].done():
                await context.bot.send_message(uid, "⚠️ Рассылка уже запущена!", reply_markup=MAIN_KEYBOARD)
                return
            
            user_data[uid]['step'] = 'phone'
            save_data()
            await context.bot.send_message(uid, "🔐 Введи номер телефона с +\nПример: +77081234567")
        except Exception as e:
            logger.error(f"start_broadcast ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка запуска, попробуй еще раз")
    
    elif data == 'stop_broadcast':
        try:
            if uid in active_tasks and not active_tasks[uid].done():
                active_tasks[uid].cancel()
                await context.bot.send_message(uid, "🛑 Рассылка остановлена", reply_markup=MAIN_KEYBOARD)
            else:
                await context.bot.send_message(uid, "❌ Нет активной рассылки", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"stop_broadcast ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка остановки")
    
    elif data == 'status':
        try:
            d = user_data.get(uid, {})
            status_text = f"📊 Настройки:\n"
            status_text += f"📝 Текст: {'✅' if d.get('text') else '❌'}\n"
            status_text += f"🔗 Группы: {len(d.get('groups', [])) if d.get('groups') else '❌'}\n"
            status_text += f"⏱ Интервал: {d.get('interval', '❌')} сек\n"
            status_text += f"\n{'🟢 Активна' if uid in active_tasks and not active_tasks[uid].done() else '🔴 Не активна'}"
            await context.bot.send_message(uid, status_text, reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"status ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка получения статуса")
    
    elif data == 'reset_all':
        try:
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
            await context.bot.send_message(uid, "🗑 Всё сброшено", reply_markup=MAIN_KEYBOARD)
        except Exception as e:
            logger.error(f"reset_all ошибка: {e}")
            await context.bot.send_message(uid, "❌ Ошибка сброса")

async def handle_message(update: Update, context):
    try:
        uid = update.effective_user.id
        
        if not update.message or not update.message.text:
            return
        
        text = update.message.text.strip()
        
        if uid not in user_data:
            user_data[uid] = {}
            save_data()
        
        if 'step' not in user_data[uid]:
            await show_main_menu(uid, context.bot)
            return
        
        step = user_data[uid]['step']
        
        if step == 'text':
            user_data[uid]['text'] = text
            user_data[uid].pop('step')
            save_data()
            await show_main_menu(uid, context.bot, "✅ Текст сохранён!")
        
        elif step == 'groups':
            groups_raw = [g.strip() for g in text.split(',') if g.strip()]
            groups = []
            for g in groups_raw:
                g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
                if not g.startswith('@'):
                    g = '@' + g
                groups.append(g)
            
            if groups:
                user_data[uid]['groups'] = groups
                user_data[uid].pop('step')
                save_data()
                await show_main_menu(uid, context.bot, f"✅ {len(groups)} групп сохранено")
            else:
                await update.message.reply_text("❌ Нет групп, попробуй снова")
        
        elif step == 'interval':
            try:
                interval = int(text)
                if 5 <= interval <= 120:
                    user_data[uid]['interval'] = interval
                    user_data[uid].pop('step')
                    save_data()
                    await show_main_menu(uid, context.bot, f"✅ Интервал {interval} сек")
                else:
                    await update.message.reply_text("❌ От 5 до 120")
            except:
                await update.message.reply_text("❌ Введи число")
        
        elif step == 'phone':
            if not text.startswith('+'):
                await update.message.reply_text("❌ Номер с +, пример: +77081234567")
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
            
            client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
            
            try:
                await client.connect()
                await client.send_code_request(text)
                temp_sessions[uid] = client
                await update.message.reply_text("📲 Введи код: code12345")
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)[:100]}")
                user_data[uid].pop('step')
                save_data()
        
        elif step == 'code':
            match = re.search(r'code(\d+)', text.lower())
            code = match.group(1) if match else None
            if not code:
                await update.message.reply_text("❌ Формат: code12345")
                return
            
            user_data[uid]['code'] = code
            user_data[uid]['step'] = 'password'
            save_data()
            await update.message.reply_text("🔐 Пароль 2FA или /skip")
        
        elif step == 'password':
            password = None if text == '/skip' else text
            client = temp_sessions.get(uid)
            
            if not client:
                await update.message.reply_text("❌ Ошибка, начни /start")
                return
            
            groups = user_data[uid].get('groups', [])
            msg = user_data[uid].get('text', '')
            interval = user_data[uid].get('interval', 30)
            
            try:
                await client.sign_in(code=user_data[uid]['code'])
                if password:
                    await client.sign_in(password=password)
                
                await update.message.reply_text(f"🚀 Запуск рассылки\nГрупп: {len(groups)}\nИнтервал: {interval} сек")
                
                task = asyncio.create_task(run_broadcast(uid, context.bot, client, groups, msg, interval))
                active_tasks[uid] = task
                user_data[uid].pop('step', None)
                save_data()
                
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)[:100]}")
    
    except Exception as e:
        logger.error(f"handle_message ошибка: {e}")
        logger.error(traceback.format_exc())
        await update.message.reply_text("❌ Неизвестная ошибка, попробуй /start")

async def run_broadcast(uid, bot, client, groups, text, interval):
    total = len(groups)
    current = 0
    try:
        while True:
            for idx, group in enumerate(groups, 1):
                current = idx
                try:
                    await client.send_message(group, text)
                    if idx % 5 == 0:
                        await bot.send_message(uid, f"📨 {idx}/{total}")
                except Exception as e:
                    await bot.send_message(uid, f"❌ {group}: {str(e)[:50]}")
                await asyncio.sleep(interval)
            await bot.send_message(uid, f"🔄 Круг завершён")
    except asyncio.CancelledError:
        await bot.send_message(uid, f"🛑 Остановлено. Отправлено: {current}/{total}", reply_markup=MAIN_KEYBOARD)
    except Exception as e:
        await bot.send_message(uid, f"❌ Ошибка: {str(e)[:100]}")
    finally:
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            temp_sessions.pop(uid, None)
        if uid in active_tasks:
            active_tasks.pop(uid, None)

async def skip_command(update: Update, context):
    try:
        uid = update.effective_user.id
        if uid in user_data and user_data[uid].get('step') == 'password':
            await handle_message(update, context)
    except Exception as e:
        logger.error(f"skip_command ошибка: {e}")

def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавляем обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", skip_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Регистрируем глобальный обработчик ошибок
    app.add_error_handler(error_handler)
    
    print("✅ Бот запущен!")
    print("📁 Данные сохраняются в user_data.json")
    print("🛡️ Глобальный обработчик ошибок активен")
    
    app.run_polling()

if __name__ == '__main__':
    main()
