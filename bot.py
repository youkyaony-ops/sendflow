import asyncio
import re
import json
import os
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

BOT_TOKEN = '8135472813:AAHiugVNCzgRuIAxG4L_3MppCW0Is01VHH8'
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'
DATA_FILE = 'user_data.json'

user_data = {}
active_tasks = {}
sessions = {}

# ==================== КНОПКИ ====================
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Текст", callback_data='set_text')],
    [InlineKeyboardButton("🔗 Группы", callback_data='set_groups')],
    [InlineKeyboardButton("⏱ Интервал", callback_data='set_interval')],
    [InlineKeyboardButton("▶️ ЗАПУСТИТЬ", callback_data='start'), InlineKeyboardButton("⏹️ СТОП", callback_data='stop')],
    [InlineKeyboardButton("📊 Статус", callback_data='status'), InlineKeyboardButton("🗑 Сброс", callback_data='reset')]
])

# ==================== РАБОТА С ДАННЫМИ ====================
def save_data():
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            # Убираем step и code из сохранения, чтобы не было конфликтов
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[uid] = {k: v for k, v in data.items() if k not in ['step', 'code']}
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
    except:
        pass

def load_data():
    global user_data
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
        except:
            user_data = {}

# ==================== МЕНЮ ====================
async def main_menu(chat_id, bot, text="🥕 SendFlow\n\nВыбери действие:"):
    await bot.send_message(chat_id, text, reply_markup=MAIN_KEYBOARD)

# ==================== КОМАНДА START ====================
async def start(update: Update, context):
    uid = update.effective_user.id
    load_data()
    if uid not in user_data:
        user_data[uid] = {}
        save_data()
    await main_menu(uid, context.bot, f"👋 Привет, {update.effective_user.first_name}!")

# ==================== ОБРАБОТЧИК КНОПОК ====================
async def button_handler(update: Update, context):
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
        await context.bot.send_message(uid, "📝 Отправь текст рассылки:")
    
    elif data == 'set_groups':
        user_data[uid]['step'] = 'waiting_groups'
        save_data()
        await context.bot.send_message(uid, "🔗 Отправь ссылки на группы через запятую\nПример: @group1, @group2, t.me/group3")
    
    elif data == 'set_interval':
        user_data[uid]['step'] = 'waiting_interval'
        save_data()
        await context.bot.send_message(uid, "⏱ Отправь интервал в секундах (5-120):")
    
    elif data == 'start':
        # Проверяем настройки
        text = user_data[uid].get('text')
        groups = user_data[uid].get('groups', [])
        interval = user_data[uid].get('interval')
        
        if not text:
            await context.bot.send_message(uid, "❌ Сначала настрой ТЕКСТ через кнопку")
            await main_menu(uid, context.bot)
            return
        if not groups:
            await context.bot.send_message(uid, "❌ Сначала настрой ГРУППЫ через кнопку")
            await main_menu(uid, context.bot)
            return
        if not interval:
            await context.bot.send_message(uid, "❌ Сначала настрой ИНТЕРВАЛ через кнопку")
            await main_menu(uid, context.bot)
            return
        
        if uid in active_tasks and not active_tasks[uid].done():
            await context.bot.send_message(uid, "⚠️ Рассылка уже запущена!")
            await main_menu(uid, context.bot)
            return
        
        # Начинаем авторизацию
        user_data[uid]['step'] = 'waiting_phone'
        save_data()
        await context.bot.send_message(uid, "🔐 Введи номер телефона Telegram с +\nПример: +79123456789")
    
    elif data == 'stop':
        if uid in active_tasks and not active_tasks[uid].done():
            active_tasks[uid].cancel()
            await context.bot.send_message(uid, "🛑 Рассылка остановлена")
        else:
            await context.bot.send_message(uid, "❌ Нет активной рассылки")
        await main_menu(uid, context.bot)
    
    elif data == 'status':
        d = user_data.get(uid, {})
        status = f"📊 ТВОИ НАСТРОЙКИ:\n\n"
        status += f"📝 Текст: {'✅ Есть' if d.get('text') else '❌ Нет'}\n"
        if d.get('text'):
            preview = d['text'][:50] + '...' if len(d['text']) > 50 else d['text']
            status += f"   → {preview}\n"
        status += f"🔗 Группы: {len(d.get('groups', []))} шт\n"
        status += f"⏱ Интервал: {d.get('interval', '❌')} сек\n"
        status += f"\n{'🟢 РАССЫЛКА АКТИВНА' if uid in active_tasks and not active_tasks[uid].done() else '🔴 РАССЫЛКА НЕ АКТИВНА'}"
        await context.bot.send_message(uid, status)
        await main_menu(uid, context.bot)
    
    elif data == 'reset':
        if uid in user_data:
            user_data[uid] = {}
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
        await context.bot.send_message(uid, "🗑 Все настройки сброшены")
        await main_menu(uid, context.bot)

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    uid = update.effective_user.id
    text = update.message.text.strip()
    
    if uid not in user_data:
        user_data[uid] = {}
        save_data()
    
    step = user_data[uid].get('step')
    
    # Если нет активного шага - показываем меню
    if not step:
        await main_menu(uid, context.bot)
        return
    
    # ===== ТЕКСТ =====
    if step == 'waiting_text':
        user_data[uid]['text'] = text
        user_data[uid].pop('step')
        save_data()
        await update.message.reply_text(f"✅ Текст сохранён!\n\n{text[:200]}")
        await main_menu(uid, context.bot)
    
    # ===== ГРУППЫ =====
    elif step == 'waiting_groups':
        raw_groups = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in raw_groups:
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if groups:
            user_data[uid]['groups'] = groups
            user_data[uid].pop('step')
            save_data()
            await update.message.reply_text(f"✅ Сохранено {len(groups)} групп:\n{', '.join(groups[:5])}")
        else:
            await update.message.reply_text("❌ Не найдено групп, попробуй снова")
            return
        await main_menu(uid, context.bot)
    
    # ===== ИНТЕРВАЛ =====
    elif step == 'waiting_interval':
        try:
            interval = int(text)
            if 5 <= interval <= 120:
                user_data[uid]['interval'] = interval
                user_data[uid].pop('step')
                save_data()
                await update.message.reply_text(f"✅ Интервал {interval} секунд")
            else:
                await update.message.reply_text("❌ Число от 5 до 120")
                return
        except:
            await update.message.reply_text("❌ Введи число")
            return
        await main_menu(uid, context.bot)
    
    # ===== НОМЕР ТЕЛЕФОНА =====
    elif step == 'waiting_phone':
        if not text.startswith('+') or len(text) < 10:
            await update.message.reply_text("❌ Неверный формат. Введи номер с +, например: +79123456789")
            return
        
        # Сохраняем номер
        user_data[uid]['phone'] = text
        user_data[uid]['step'] = 'waiting_code'
        save_data()
        
        # Создаём клиента
        client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
        sessions[uid] = client
        
        try:
            await client.connect()
            await client.send_code_request(text)
            await update.message.reply_text("📲 Код отправлен!\nВведи его в формате: code12345")
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
            user_data[uid].pop('step')
            save_data()
            await main_menu(uid, context.bot)
    
    # ===== КОД ПОДТВЕРЖДЕНИЯ =====
    elif step == 'waiting_code':
        match = re.search(r'(\d{5,6})', text)
        code = match.group(1) if match else None
        
        if not code:
            await update.message.reply_text("❌ Неверный формат. Введи: code12345")
            return
        
        user_data[uid]['code'] = code
        user_data[uid]['step'] = 'waiting_2fa'
        save_data()
        await update.message.reply_text("🔐 Если есть пароль 2FA - введи его\nЕсли нет - отправь /skip")
    
    # ===== 2FA ПАРОЛЬ =====
    elif step == 'waiting_2fa':
        password = None if text == '/skip' else text
        client = sessions.get(uid)
        
        if not client:
            await update.message.reply_text("❌ Ошибка сессии. Начни заново /start")
            user_data[uid].pop('step')
            save_data()
            await main_menu(uid, context.bot)
            return
        
        phone = user_data[uid].get('phone')
        code = user_data[uid].get('code')
        
        try:
            # Вход с кодом
            await client.sign_in(phone, code=code)
            
        except SessionPasswordNeededError:
            # Требуется 2FA
            if not password:
                await update.message.reply_text("🔐 Введи пароль 2FA:")
                return
            try:
                await client.sign_in(password=password)
            except Exception as e:
                await update.message.reply_text(f"❌ Неверный пароль: {str(e)[:50]}")
                return
        
        except PhoneCodeInvalidError:
            await update.message.reply_text("❌ Неверный код! Начни /start заново")
            user_data[uid].pop('step')
            save_data()
            await main_menu(uid, context.bot)
            return
        
        # ==== УСПЕШНАЯ АВТОРИЗАЦИЯ ====
        groups = user_data[uid].get('groups', [])
        msg = user_data[uid].get('text', '')
        interval = user_data[uid].get('interval', 30)
        
        await update.message.reply_text("✅ Авторизация успешна!\n\n🔍 Проверяю доступ к группам...")
        
        # Проверяем группы
        valid_groups = []
        for group in groups:
            try:
                await client.get_entity(group)
                valid_groups.append(group)
                await update.message.reply_text(f"✅ {group} - доступна")
            except:
                await update.message.reply_text(f"❌ {group} - НЕ ДОСТУПНА")
        
        if not valid_groups:
            await update.message.reply_text("❌ Нет доступных групп для рассылки!")
            user_data[uid].pop('step')
            save_data()
            await main_menu(uid, context.bot)
            return
        
        user_data[uid]['groups'] = valid_groups
        user_data[uid].pop('step')
        user_data[uid].pop('code', None)
        save_data()
        
        await update.message.reply_text(f"🚀 ЗАПУСК РАССЫЛКИ!\n\n📊 Групп: {len(valid_groups)}\n⏱ Интервал: {interval} сек\n\nДля остановки нажми кнопку СТОП")
        
        # Запускаем рассылку
        task = asyncio.create_task(send_broadcast(uid, context.bot, client, valid_groups, msg, interval))
        active_tasks[uid] = task

# ==================== РАССЫЛКА ====================
async def send_broadcast(uid, bot, client, groups, text, interval):
    total = len(groups)
    sent = 0
    
    try:
        while True:
            for idx, group in enumerate(groups, 1):
                sent = idx
                try:
                    await client.send_message(group, text)
                    print(f"[+] {uid} -> {group} ({idx}/{total})")
                    
                    if idx % 10 == 0 or idx == total:
                        await bot.send_message(uid, f"📨 Прогресс: {idx}/{total}\nПоследняя: {group}")
                except Exception as e:
                    await bot.send_message(uid, f"❌ {group}: {str(e)[:50]}")
                
                await asyncio.sleep(interval)
            
            await bot.send_message(uid, f"🔄 Круг по {total} группам завершён! Начинаю новый круг...")
    
    except asyncio.CancelledError:
        await bot.send_message(uid, f"🛑 РАССЫЛКА ОСТАНОВЛЕНА\n📨 Отправлено: {sent}/{total}")
    
    finally:
        # Очистка
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
            except:
                pass
            sessions.pop(uid, None)
        if uid in active_tasks:
            active_tasks.pop(uid, None)

# ==================== SKIP КОМАНДА ====================
async def skip(update: Update, context):
    uid = update.effective_user.id
    if uid in user_data and user_data[uid].get('step') == 'waiting_2fa':
        # Отправляем /skip как обычное сообщение
        update.message.text = '/skip'
        await message_handler(update, context)

# ==================== ЗАПУСК ====================
def main():
    load_data()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", skip))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    print("=" * 40)
    print("✅ Бот SendFlow ЗАПУЩЕН")
    print("=" * 40)
    print("📌 Порядок работы:")
    print("1. Настрой текст, группы, интервал через кнопки")
    print("2. Нажми ЗАПУСТИТЬ")
    print("3. Введи номер телефона")
    print("4. Введи код: code12345")
    print("5. Если есть 2FA - введи пароль, если нет - /skip")
    print("6. Рассылка поехала!")
    print("=" * 40)
    
    app.run_polling()

if __name__ == '__main__':
    main()
