import asyncio
import re
import json
import os
import logging
import time
import sys
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ==================== НАСТРОЙКА ЛОГИРОВАНИЯ ====================
LOG_FILE = f'bot_log_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# ==================== КОНФИГУРАЦИЯ ====================
BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'

user_data = {}
active_tasks = {}
sessions = {}

# ==================== РАБОТА С ДАННЫМИ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'text': data.get('text'),
                    'groups': data.get('groups', []),
                    'interval': data.get('interval'),
                    'phone': data.get('phone'),
                    'total_sent': data.get('total_sent', 0),
                    'created_at': data.get('created_at', str(datetime.now()))
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Данные сохранены")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
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
    except Exception as e:
        user_data = {}
        return False

# ==================== КЛАВИАТУРЫ ====================
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Текст", callback_data='set_text'), InlineKeyboardButton("🔗 Группы", callback_data='set_groups')],
    [InlineKeyboardButton("⏱ Интервал", callback_data='set_interval'), InlineKeyboardButton("📋 Настройки", callback_data='show_settings')],
    [InlineKeyboardButton("🔄 ЗАПУСТИТЬ 24/7", callback_data='start_broadcast'), InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data='stop_broadcast')],
    [InlineKeyboardButton("📊 Статистика", callback_data='stats'), InlineKeyboardButton("🗑 Сброс", callback_data='reset_all')],
    [InlineKeyboardButton("❓ Помощь", callback_data='help')]
])

CANCEL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]
])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ====================
async def return_to_main_menu(chat_id, bot, message=None):
    msg = "🥕 SendFlow - РАССЫЛКА 24/7\n\nСообщение отправляется БЕСКОНЕЧНО по кругу\nВыбери действие:"
    if message:
        msg = message + "\n\n" + msg
    await bot.send_message(chat_id, msg, reply_markup=MAIN_KEYBOARD)

# ==================== ОБРАБОТЧИКИ ====================
async def start_command(update: Update, context):
    uid = update.effective_user.id
    username = update.effective_user.first_name
    
    load_data()
    
    if uid not in user_data:
        user_data[uid] = {
            'created_at': str(datetime.now()),
            'text': None,
            'groups': [],
            'interval': None,
            'phone': None,
            'total_sent': 0
        }
        save_data()
    
    await return_to_main_menu(uid, context.bot, f"👋 Привет, {username}!")

async def help_command(update: Update, context):
    help_text = """
📖 **РАССЫЛКА 24/7 - ИНСТРУКЦИЯ**

⚙️ **НАСТРОЙКА:**
• Текст - введи сообщение
• Группы - введи ссылки через запятую
• Интервал - время между сообщениями

🔄 **РАБОТА:**
• После запуска сообщение отправляется В ГРУППЫ ПО КРУГУ
• Когда доходит до последней группы - начинает заново
• ТАК БУДЕТ БЕСКОНЕЧНО 24/7/365

📊 **УПРАВЛЕНИЕ:**
• ЗАПУСТИТЬ 24/7 - начать бесконечную рассылку
• ОСТАНОВИТЬ - полностью остановить
• Статистика - смотреть сколько отправлено

💾 **СОХРАНЕНИЕ:**
• Настройки сохраняются автоматически
• При перезапуске бота можно продолжить
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

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
        user_data[uid]['step'] = 'waiting_text'
        save_data()
        await context.bot.send_message(uid, "📝 **Введи текст для БЕСКОНЕЧНОЙ рассылки 24/7:**", reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    
    elif data == 'set_groups':
        user_data[uid]['step'] = 'waiting_groups'
        save_data()
        await context.bot.send_message(uid, "🔗 **Введи группы через запятую:**\n\nПример: @group1, @group2, t.me/group3\n\nСообщение будет ходить по этим группам ПО КРУГУ 24/7", reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    
    elif data == 'set_interval':
        user_data[uid]['step'] = 'waiting_interval'
        save_data()
        await context.bot.send_message(uid, "⏱ **Введи интервал (5-3600 секунд):**\n\nРекомендуем 30-60 секунд\n\nЭто время между отправками в разные группы", reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    
    elif data == 'show_settings':
        s = user_data.get(uid, {})
        txt = "📋 **НАСТРОЙКИ**\n\n"
        txt += f"📝 Текст: {'✅' if s.get('text') else '❌'}\n"
        if s.get('text'):
            txt += f"   → {s['text'][:50]}...\n\n"
        txt += f"🔗 Группы: {len(s.get('groups', []))} шт\n"
        if s.get('groups'):
            txt += f"   → {', '.join(s['groups'][:3])}\n\n"
        txt += f"⏱ Интервал: {s.get('interval', '❌')} сек\n"
        txt += f"📨 Отправлено: {s.get('total_sent', 0)}\n"
        if uid in active_tasks and not active_tasks[uid].done():
            txt += "\n🟢 **РАССЫЛКА АКТИВНА 24/7**"
        else:
            txt += "\n🔴 **РАССЫЛКА ОСТАНОВЛЕНА**"
        await context.bot.send_message(uid, txt, parse_mode='Markdown')
        await return_to_main_menu(uid, context.bot)
    
    elif data == 'stats':
        s = user_data.get(uid, {})
        txt = "📊 **СТАТИСТИКА 24/7**\n\n"
        txt += f"📨 Отправлено сообщений: {s.get('total_sent', 0)}\n"
        if uid in active_tasks and not active_tasks[uid].done():
            txt += "\n🟢 **СТАТУС: РАБОТАЕТ 24/7**\n"
            txt += f"🔄 Последний круг: сообщения ходят по {len(s.get('groups', []))} группам\n"
            txt += f"⏱ Интервал: {s.get('interval', '?')} сек\n"
            txt += "♾️ Рассылка будет идти БЕСКОНЕЧНО"
        else:
            txt += "\n🔴 **СТАТУС: ОСТАНОВЛЕНА**\n"
            txt += "Нажми ЗАПУСТИТЬ 24/7 для начала"
        await context.bot.send_message(uid, txt, parse_mode='Markdown')
        await return_to_main_menu(uid, context.bot)
    
    elif data == 'start_broadcast':
        s = user_data.get(uid, {})
        
        if not s.get('text'):
            await context.bot.send_message(uid, "❌ Сначала настрой ТЕКСТ", parse_mode='Markdown')
            await return_to_main_menu(uid, context.bot)
            return
        if not s.get('groups'):
            await context.bot.send_message(uid, "❌ Сначала настрой ГРУППЫ", parse_mode='Markdown')
            await return_to_main_menu(uid, context.bot)
            return
        if not s.get('interval'):
            await context.bot.send_message(uid, "❌ Сначала настрой ИНТЕРВАЛ", parse_mode='Markdown')
            await return_to_main_menu(uid, context.bot)
            return
        
        if uid in active_tasks and not active_tasks[uid].done():
            await context.bot.send_message(uid, "⚠️ **Рассылka УЖЕ запущена 24/7!**\nНажми ОСТАНОВИТЬ если хочешь прекратить", parse_mode='Markdown')
            await return_to_main_menu(uid, context.bot)
            return
        
        user_data[uid]['step'] = 'waiting_phone'
        save_data()
        await context.bot.send_message(uid, "🔐 **АВТОРИЗАЦИЯ TELEGRAM**\n\nВведи номер телефона с +\nПример: +79123456789\n\nПосле авторизации рассылка будет работать 24/7 БЕЗ ОСТАНОВКИ", reply_markup=CANCEL_KEYBOARD, parse_mode='Markdown')
    
    elif data == 'stop_broadcast':
        if uid in active_tasks and not active_tasks[uid].done():
            active_tasks[uid].cancel()
            await context.bot.send_message(uid, "🛑 **РАССЫЛКА 24/7 ОСТАНОВЛЕНА**\n\nЧтобы запустить снова - нажми ЗАПУСТИТЬ 24/7")
            logger.info(f"Рассылка остановлена для {uid}")
        else:
            await context.bot.send_message(uid, "❌ Нет активной рассылки")
        await return_to_main_menu(uid, context.bot)
    
    elif data == 'reset_all':
        if uid in user_data:
            user_data[uid] = {
                'created_at': str(datetime.now()),
                'text': None,
                'groups': [],
                'interval': None,
                'phone': None,
                'total_sent': 0
            }
            save_data()
        if uid in active_tasks:
            try:
                active_tasks[uid].cancel()
            except:
                pass
            active_tasks.pop(uid, None)
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            sessions.pop(uid, None)
        await context.bot.send_message(uid, "🗑 **ВСЕ НАСТРОЙКИ СБРОШЕНЫ**")
        await return_to_main_menu(uid, context.bot)
    
    elif data == 'help':
        await help_command(update, context)
        await return_to_main_menu(uid, context.bot)
    
    elif data == 'cancel':
        if uid in user_data and 'step' in user_data[uid]:
            user_data[uid].pop('step', None)
            save_data()
        await context.bot.send_message(uid, "❌ Действие отменено")
        await return_to_main_menu(uid, context.bot)

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    try:
        uid = update.effective_user.id
        text = update.message.text.strip()
        
        if uid not in user_data:
            user_data[uid] = {
                'created_at': str(datetime.now()),
                'text': None,
                'groups': [],
                'interval': None,
                'phone': None,
                'total_sent': 0
            }
            save_data()
        
        step = user_data[uid].get('step')
        
        if not step:
            await return_to_main_menu(uid, context.bot)
            return
        
        if step == 'waiting_text':
            user_data[uid]['text'] = text
            user_data[uid].pop('step')
            save_data()
            await update.message.reply_text(f"✅ **Текст сохранён!**\n\n{text[:200]}\n\nТеперь настрой группы и интервал")
            await return_to_main_menu(uid, context.bot)
        
        elif step == 'waiting_groups':
            raw = [g.strip() for g in text.split(',') if g.strip()]
            groups = []
            for g in raw:
                g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
                if not g.startswith('@'):
                    g = '@' + g
                groups.append(g)
            
            if groups:
                user_data[uid]['groups'] = groups
                user_data[uid].pop('step')
                save_data()
                await update.message.reply_text(f"✅ **Сохранено {len(groups)} групп**\n\n{', '.join(groups[:5])}\n\nСообщение будет ходить по ним ПО КРУГУ 24/7")
            else:
                await update.message.reply_text("❌ Нет групп, попробуй снова")
                return
            await return_to_main_menu(uid, context.bot)
        
        elif step == 'waiting_interval':
            try:
                interval = int(text)
                if 5 <= interval <= 3600:
                    user_data[uid]['interval'] = interval
                    user_data[uid].pop('step')
                    save_data()
                    await update.message.reply_text(f"✅ **Интервал {interval} секунд**\n\nСообщение будет отправляться каждые {interval} секунд БЕСКОНЕЧНО")
                else:
                    await update.message.reply_text("❌ От 5 до 3600 секунд")
                    return
            except:
                await update.message.reply_text("❌ Введи число")
                return
            await return_to_main_menu(uid, context.bot)
        
        elif step == 'waiting_phone':
            if not text.startswith('+'):
                await update.message.reply_text("❌ Номер с +, пример: +79123456789")
                return
            
            if uid in sessions:
                try:
                    await sessions[uid].disconnect()
                except:
                    pass
                sessions.pop(uid, None)
            
            user_data[uid]['phone'] = text
            user_data[uid]['step'] = 'waiting_code'
            save_data()
            
            client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
            sessions[uid] = client
            
            try:
                await client.connect()
                await client.send_code_request(text)
                await update.message.reply_text("📲 **Код отправлен!**\n\nВведи: code12345")
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)[:100]}")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
        
        elif step == 'waiting_code':
            match = re.search(r'(\d{5,6})', text)
            code = match.group(1) if match else None
            if not code:
                await update.message.reply_text("❌ Формат: code12345")
                return
            
            user_data[uid]['code'] = code
            user_data[uid]['step'] = 'waiting_2fa'
            save_data()
            await update.message.reply_text("🔐 **2FA**\n\nЕсли есть пароль - введи\nЕсли нет - отправь /skip")
        
        elif step == 'waiting_2fa':
            password = None if text == '/skip' else text
            client = sessions.get(uid)
            
            if not client:
                await update.message.reply_text("❌ Ошибка, начни /start")
                return
            
            groups = user_data[uid].get('groups', [])
            msg = user_data[uid].get('text', '')
            interval = user_data[uid].get('interval', 30)
            
            try:
                await client.sign_in(code=user_data[uid]['code'])
            except SessionPasswordNeededError:
                if not password:
                    await update.message.reply_text("🔐 Введи пароль 2FA:")
                    return
                try:
                    await client.sign_in(password=password)
                except:
                    await update.message.reply_text("❌ Неверный пароль")
                    return
            except Exception as e:
                await update.message.reply_text(f"❌ {str(e)[:100]}")
                return
            
            # Проверка групп
            valid_groups = []
            for group in groups:
                try:
                    await client.get_entity(group)
                    valid_groups.append(group)
                except:
                    await update.message.reply_text(f"⚠️ {group} - недоступна")
            
            if not valid_groups:
                await update.message.reply_text("❌ Нет доступных групп!")
                await return_to_main_menu(uid, context.bot)
                return
            
            user_data[uid]['groups'] = valid_groups
            user_data[uid].pop('step')
            user_data[uid].pop('code', None)
            save_data()
            
            await update.message.reply_text(
                f"✅ **АВТОРИЗАЦИЯ УСПЕШНА!**\n\n"
                f"🚀 **ЗАПУСК БЕСКОНЕЧНОЙ РАССЫЛКИ 24/7**\n\n"
                f"📊 Групп: {len(valid_groups)}\n"
                f"⏱ Интервал: {interval} сек\n"
                f"♾️ **Режим: БЕСКОНЕЧНЫЙ ЦИКЛ**\n\n"
                f"Сообщение будет ходить ПО КРУГУ:\n"
                f"Группа 1 → Группа 2 → ... → Группа {len(valid_groups)} → Группа 1 → И ТАК БЕСКОНЕЧНО\n\n"
                f"Для остановки нажми кнопку ОСТАНОВИТЬ"
            )
            
            # ЗАПУСКАЕМ БЕСКОНЕЧНУЮ РАССЫЛКУ
            task = asyncio.create_task(run_broadcast_247(uid, context.bot, client, valid_groups, msg, interval))
            active_tasks[uid] = task
    
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        await update.message.reply_text("❌ Ошибка, попробуй /start")

# ==================== БЕСКОНЕЧНАЯ РАССЫЛКА 24/7 ====================
async def run_broadcast_247(uid, bot, client, groups, text, interval):
    """БЕСКОНЕЧНАЯ рассылка 24/7 по кругу"""
    total = len(groups)
    sent_count = 0
    start_time = time.time()
    
    try:
        while True:  # БЕСКОНЕЧНЫЙ ЦИКЛ
            for idx, group in enumerate(groups, 1):
                sent_count += 1
                try:
                    await client.send_message(group, text)
                    logger.info(f"[+] {uid} -> {group} ({idx}/{total}) | Всего отправлено: {sent_count}")
                    
                    # Обновляем статистику
                    if uid in user_data:
                        user_data[uid]['total_sent'] = sent_count
                        save_data()
                    
                    # Отправляем статус каждые 10 сообщений
                    if sent_count % 10 == 0:
                        elapsed = int(time.time() - start_time)
                        hours = elapsed // 3600
                        minutes = (elapsed % 3600) // 60
                        await bot.send_message(
                            uid, 
                            f"📊 **СТАТУС 24/7**\n\n"
                            f"📨 Отправлено: {sent_count} сообщений\n"
                            f"🔄 Текущий круг: {idx}/{total}\n"
                            f"⏱ Работает: {hours}ч {minutes}м\n"
                            f"♾️ Рассылка продолжается БЕСКОНЕЧНО"
                        )
                except FloodWaitError as e:
                    await bot.send_message(uid, f"⏳ Флуд: жди {e.seconds} сек")
                    await asyncio.sleep(e.seconds)
                    try:
                        await client.send_message(group, text)
                        sent_count += 1
                    except:
                        pass
                except Exception as e:
                    logger.error(f"Ошибка {group}: {e}")
                    await bot.send_message(uid, f"❌ {group}: {str(e)[:50]}")
                
                await asyncio.sleep(interval)
            
            # Круг завершён, начинаем новый
            await bot.send_message(
                uid, 
                f"🔄 **КРУГ ЗАВЕРШЁН!**\n\n"
                f"Отправлено за круг: {total} сообщений\n"
                f"Всего отправлено: {sent_count}\n"
                f"♾️ **НАЧИНАЮ НОВЫЙ КРУГ 24/7**\n"
                f"Сообщение снова пойдёт по всем группам"
            )
            logger.info(f"Круг завершён для {uid}, всего отправлено: {sent_count}")
    
    except asyncio.CancelledError:
        elapsed = int(time.time() - start_time)
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        await bot.send_message(
            uid, 
            f"🛑 **РАССЫЛКА 24/7 ОСТАНОВЛЕНА**\n\n"
            f"📨 Всего отправлено: {sent_count} сообщений\n"
            f"⏱ Проработала: {hours}ч {minutes}м\n"
            f"📊 Групп в ротации: {total}\n\n"
            f"Чтобы запустить снова - нажми ЗАПУСТИТЬ 24/7"
        )
        logger.info(f"Бесконечная рассылка остановлена для {uid}, отправлено: {sent_count}")
    
    finally:
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            sessions.pop(uid, None)
        if uid in active_tasks:
            active_tasks.pop(uid, None)

# ==================== ЗАПУСК ====================
def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("skip", message_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("=" * 60)
    print("🥕 SendFlow - БЕСКОНЕЧНАЯ РАССЫЛКА 24/7")
    print("=" * 60)
    print("КАК РАБОТАЕТ:")
    print("1. Настрой текст, группы, интервал")
    print("2. Нажми ЗАПУСТИТЬ 24/7")
    print("3. Авторизуйся в Telegram")
    print("4. РАССЫЛКА ПОЙДЁТ БЕСКОНЕЧНО ПО КРУГУ")
    print("5. Группа1 → Группа2 → ... → ГруппаN → Группа1 → И ТАК ВСЕГДА")
    print("=" * 60)
    print("✅ Бот готов к БЕСКОНЕЧНОЙ работе 24/7!")
    print("=" * 60)
    
    app.run_polling()

if __name__ == '__main__':
    main()
