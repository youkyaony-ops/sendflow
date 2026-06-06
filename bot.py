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
BACKUP_FILE = 'user_data_backup.json'

# ==================== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ====================
user_data = {}
active_tasks = {}
sessions = {}
user_states = {}

# ==================== РАБОТА С ДАННЫМИ ====================
def save_data():
    """Сохранение всех данных пользователей с резервным копированием"""
    try:
        # Сохраняем основную копию
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            clean_data = {}
            for uid, data in user_data.items():
                clean_data[str(uid)] = {
                    'text': data.get('text'),
                    'groups': data.get('groups', []),
                    'interval': data.get('interval'),
                    'phone': data.get('phone'),
                    'created_at': data.get('created_at', str(datetime.now())),
                    'last_updated': str(datetime.now())
                }
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        
        # Создаём резервную копию
        with open(BACKUP_FILE, 'w', encoding='utf-8') as f:
            json.dump(clean_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"Данные сохранены для {len(user_data)} пользователей")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения: {e}")
        return False

def load_data():
    """Загрузка данных пользователей"""
    global user_data
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
                user_data = {int(k): v for k, v in loaded.items()}
                logger.info(f"Загружены данные для {len(user_data)} пользователей")
        else:
            user_data = {}
            logger.info("Файл данных не найден, создаю новый")
        return True
    except Exception as e:
        logger.error(f"Ошибка загрузки: {e}")
        user_data = {}
        return False

def backup_user_data(uid):
    """Создание бэкапа данных конкретного пользователя"""
    try:
        backup_dir = "user_backups"
        if not os.path.exists(backup_dir):
            os.makedirs(backup_dir)
        
        backup_file = f"{backup_dir}/user_{uid}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(backup_file, 'w', encoding='utf-8') as f:
            json.dump(user_data.get(uid, {}), f, ensure_ascii=False, indent=2)
        logger.info(f"Бэкап пользователя {uid} создан: {backup_file}")
        return True
    except Exception as e:
        logger.error(f"Ошибка бэкапа: {e}")
        return False

# ==================== КЛАВИАТУРЫ ====================
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📝 Установить текст", callback_data='set_text')],
    [InlineKeyboardButton("🔗 Установить группы", callback_data='set_groups')],
    [InlineKeyboardButton("⏱ Установить интервал", callback_data='set_interval')],
    [InlineKeyboardButton("📋 Показать текущие настройки", callback_data='show_settings')],
    [
        InlineKeyboardButton("▶️ ЗАПУСТИТЬ РАССЫЛКУ", callback_data='start_broadcast'),
        InlineKeyboardButton("⏹️ ОСТАНОВИТЬ", callback_data='stop_broadcast')
    ],
    [
        InlineKeyboardButton("📊 Статус рассылки", callback_data='broadcast_status'),
        InlineKeyboardButton("🗑 Сбросить всё", callback_data='reset_all')
    ],
    [InlineKeyboardButton("💾 Сохранить настройки", callback_data='save_settings')],
    [InlineKeyboardButton("📁 Помощь / Инструкция", callback_data='help')]
])

CANCEL_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("❌ ОТМЕНА", callback_data='cancel')]
])

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================
async def safe_send_message(chat_id, bot, text, keyboard=None):
    """Безопасная отправка сообщения с обработкой ошибок"""
    try:
        if keyboard:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
        else:
            await bot.send_message(chat_id, text)
        return True
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения {chat_id}: {e}")
        return False

async def return_to_main_menu(chat_id, bot, message=None):
    """Возврат в главное меню"""
    msg = "🥕 SendFlow - Бот для рассылки сообщений\n\nВыбери действие:"
    if message:
        msg = message + "\n\n" + msg
    await safe_send_message(chat_id, bot, msg, MAIN_KEYBOARD)

def validate_groups(groups_list):
    """Валидация списка групп"""
    valid = []
    invalid = []
    for g in groups_list:
        g = g.strip()
        if not g:
            continue
        # Приводим к правильному формату
        g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
        if not g.startswith('@'):
            g = '@' + g
        # Базовая проверка формата
        if re.match(r'^@[a-zA-Z0-9_]{5,32}$', g):
            valid.append(g)
        else:
            invalid.append(g)
    return valid, invalid

def validate_interval(interval):
    """Валидация интервала"""
    try:
        interval = int(interval)
        if 3 <= interval <= 300:
            return True, interval
        else:
            return False, "Интервал должен быть от 3 до 300 секунд"
    except ValueError:
        return False, "Введите целое число"

def validate_phone(phone):
    """Валидация номера телефона"""
    phone = phone.strip()
    if not phone.startswith('+'):
        phone = '+' + phone
    if re.match(r'^\+[0-9]{10,15}$', phone):
        return True, phone
    return False, "Неверный формат номера. Пример: +79123456789"

# ==================== ОБРАБОТЧИКИ КОМАНД ====================
async def start_command(update: Update, context):
    """Команда /start"""
    try:
        uid = update.effective_user.id
        username = update.effective_user.username or update.effective_user.first_name
        
        logger.info(f"Пользователь {username} ({uid}) запустил бота")
        
        load_data()
        
        if uid not in user_data:
            user_data[uid] = {
                'created_at': str(datetime.now()),
                'text': None,
                'groups': [],
                'interval': None,
                'phone': None
            }
            save_data()
            backup_user_data(uid)
        
        await return_to_main_menu(uid, context.bot, f"👋 Привет, {username}!")
        
    except Exception as e:
        logger.error(f"Ошибка в start_command: {e}")
        await update.message.reply_text("❌ Ошибка при запуске, попробуйте позже")

async def help_command(update: Update, context):
    """Команда /help"""
    help_text = """
📖 **ИНСТРУКЦИЯ ПО ИСПОЛЬЗОВАНИЮ**

**1. Настройка рассылки:**
   📝 Текст - введи сообщение для рассылки
   🔗 Группы - введи ссылки через запятую
   ⏱ Интервал - время между сообщениями (3-300 сек)

**2. Запуск:**
   ▶️ Нажми ЗАПУСТИТЬ РАССЫЛКУ
   📱 Введи номер телефона Telegram
   🔑 Введи код подтверждения (code12345)
   🔐 Если есть 2FA - введи пароль, иначе /skip

**3. Управление:**
   ⏹️ ОСТАНОВИТЬ - остановить активную рассылку
   📊 Статус - посмотреть прогресс
   💾 Сохранить - сохранить настройки

**4. Форматы ввода:**
   Группы: @group1, @group2, t.me/group3
   Код: code12345 или просто 12345
   Пароль: любой текст (если включена 2FA)

**ВНИМАНИЕ!** 
- Не превышайте лимиты Telegram (20-30 сообщений в минуту)
- Для отмены действия нажми ❌ ОТМЕНА
- Все настройки автоматически сохраняются
    """
    await update.message.reply_text(help_text, parse_mode='Markdown')

# ==================== ОБРАБОТЧИК КНОПОК ====================
async def button_callback(update: Update, context):
    """Обработка нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    
    try:
        await query.message.delete()
    except:
        pass
    
    # ===== ТЕКСТ =====
    if data == 'set_text':
        user_data[uid]['step'] = 'waiting_text'
        save_data()
        await context.bot.send_message(
            uid, 
            "📝 **Введи текст рассылки**\n\n"
            "Текст может содержать эмодзи, ссылки, форматирование.\n"
            "Просто отправь сообщение.\n\n"
            "Для отмены нажми кнопку ниже:",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='Markdown'
        )
    
    # ===== ГРУППЫ =====
    elif data == 'set_groups':
        user_data[uid]['step'] = 'waiting_groups'
        save_data()
        await context.bot.send_message(
            uid,
            "🔗 **Введи группы для рассылки**\n\n"
            "Форматы:\n"
            "• @username\n"
            "• t.me/username\n"
            "• https://t.me/username\n\n"
            "Пример: @group1, @group2, t.me/group3\n\n"
            "Группы разделяй запятыми!",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='Markdown'
        )
    
    # ===== ИНТЕРВАЛ =====
    elif data == 'set_interval':
        user_data[uid]['step'] = 'waiting_interval'
        save_data()
        await context.bot.send_message(
            uid,
            "⏱ **Введи интервал между сообщениями**\n\n"
            "Диапазон: от 3 до 300 секунд\n"
            "Рекомендуем: 30-60 секунд\n\n"
            "Пример: 30",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='Markdown'
        )
    
    # ===== ПОКАЗАТЬ НАСТРОЙКИ =====
    elif data == 'show_settings':
        settings = user_data.get(uid, {})
        settings_text = "📋 **ТЕКУЩИЕ НАСТРОЙКИ**\n\n"
        settings_text += f"📝 **Текст:** {'✅ Установлен' if settings.get('text') else '❌ Не установлен'}\n"
        if settings.get('text'):
            preview = settings['text'][:100] + '...' if len(settings['text']) > 100 else settings['text']
            settings_text += f"   → {preview}\n\n"
        
        settings_text += f"🔗 **Группы:** {len(settings.get('groups', []))} шт\n"
        if settings.get('groups'):
            settings_text += f"   → {', '.join(settings['groups'][:5])}\n"
            if len(settings['groups']) > 5:
                settings_text += f"   и ещё {len(settings['groups']) - 5}...\n"
        settings_text += "\n"
        
        settings_text += f"⏱ **Интервал:** {settings.get('interval', '❌ Не установлен')} сек\n\n"
        settings_text += f"📱 **Номер телефона:** {'✅ Указан' if settings.get('phone') else '❌ Не указан'}\n"
        settings_text += f"📅 **Создано:** {settings.get('created_at', 'Неизвестно')[:16]}\n"
        
        await context.bot.send_message(uid, settings_text, parse_mode='Markdown')
        await return_to_main_menu(uid, context.bot)
    
    # ===== ЗАПУСТИТЬ РАССЫЛКУ =====
    elif data == 'start_broadcast':
        settings = user_data.get(uid, {})
        
        # Проверка настроек
        errors = []
        if not settings.get('text'):
            errors.append("❌ Не установлен ТЕКСТ рассылки")
        if not settings.get('groups'):
            errors.append("❌ Не установлены ГРУППЫ для рассылки")
        if not settings.get('interval'):
            errors.append("❌ Не установлен ИНТЕРВАЛ между сообщениями")
        
        if errors:
            error_text = "⚠️ **Невозможно запустить рассылку!**\n\n" + "\n".join(errors) + "\n\nНастрой через кнопки меню"
            await context.bot.send_message(uid, error_text, parse_mode='Markdown')
            await return_to_main_menu(uid, context.bot)
            return
        
        if uid in active_tasks and not active_tasks[uid].done():
            await context.bot.send_message(uid, "⚠️ **Рассылка уже запущена!**\nНажми ОСТАНОВИТЬ если хочешь прекратить")
            await return_to_main_menu(uid, context.bot)
            return
        
        user_data[uid]['step'] = 'waiting_phone'
        save_data()
        await context.bot.send_message(
            uid,
            "🔐 **АВТОРИЗАЦИЯ TELEGRAM**\n\n"
            "Введи номер телефона, к которому привязан аккаунт Telegram\n\n"
            "Формат: +79123456789\n\n"
            "Для отмены нажми кнопку ниже:",
            reply_markup=CANCEL_KEYBOARD,
            parse_mode='Markdown'
        )
    
    # ===== ОСТАНОВИТЬ =====
    elif data == 'stop_broadcast':
        if uid in active_tasks and not active_tasks[uid].done():
            active_tasks[uid].cancel()
            await context.bot.send_message(uid, "🛑 **Рассылка остановлена пользователем**")
            logger.info(f"Пользователь {uid} остановил рассылку")
        else:
            await context.bot.send_message(uid, "❌ **Нет активной рассылки**")
        await return_to_main_menu(uid, context.bot)
    
    # ===== СТАТУС РАССЫЛКИ =====
    elif data == 'broadcast_status':
        if uid in active_tasks and not active_tasks[uid].done():
            await context.bot.send_message(uid, "🟢 **Рассылка АКТИВНА**\n\nИспользуй кнопку ОСТАНОВИТЬ для прекращения")
        else:
            await context.bot.send_message(uid, "🔴 **Рассылка НЕ АКТИВНА**\n\nНажми ЗАПУСТИТЬ чтобы начать")
        await return_to_main_menu(uid, context.bot)
    
    # ===== СБРОСИТЬ ВСЁ =====
    elif data == 'reset_all':
        if uid in user_data:
            user_data[uid] = {
                'created_at': str(datetime.now()),
                'text': None,
                'groups': [],
                'interval': None,
                'phone': None
            }
            save_data()
            backup_user_data(uid)
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
        await context.bot.send_message(uid, "🗑 **ВСЕ НАСТРОЙКИ СБРОШЕНЫ**\n\nМожешь начать заново")
        await return_to_main_menu(uid, context.bot)
    
    # ===== СОХРАНИТЬ НАСТРОЙКИ =====
    elif data == 'save_settings':
        if save_data():
            await context.bot.send_message(uid, "💾 **Настройки сохранены!**\n\nДанные записаны в файл и создана резервная копия")
        else:
            await context.bot.send_message(uid, "❌ **Ошибка сохранения!**\nПроверь логи")
        await return_to_main_menu(uid, context.bot)
    
    # ===== ПОМОЩЬ =====
    elif data == 'help':
        await help_command(update, context)
        await return_to_main_menu(uid, context.bot)
    
    # ===== ОТМЕНА =====
    elif data == 'cancel':
        if uid in user_data and 'step' in user_data[uid]:
            user_data[uid].pop('step', None)
            save_data()
        await context.bot.send_message(uid, "❌ **Действие отменено**")
        await return_to_main_menu(uid, context.bot)

# ==================== ОБРАБОТЧИК СООБЩЕНИЙ ====================
async def message_handler(update: Update, context):
    """Обработка текстовых сообщений"""
    try:
        uid = update.effective_user.id
        text = update.message.text.strip()
        
        if uid not in user_data:
            user_data[uid] = {
                'created_at': str(datetime.now()),
                'text': None,
                'groups': [],
                'interval': None,
                'phone': None
            }
            save_data()
        
        step = user_data[uid].get('step')
        
        # Если нет активного шага - показываем меню
        if not step:
            await return_to_main_menu(uid, context.bot)
            return
        
        # ===== ОБРАБОТКА ТЕКСТА =====
        if step == 'waiting_text':
            if len(text) < 5:
                await update.message.reply_text("❌ Текст слишком короткий (минимум 5 символов)")
                return
            if len(text) > 4096:
                await update.message.reply_text("❌ Текст слишком длинный (максимум 4096 символов)")
                return
            
            user_data[uid]['text'] = text
            user_data[uid].pop('step')
            save_data()
            backup_user_data(uid)
            
            await update.message.reply_text(f"✅ **Текст сохранён!**\n\n{text[:200]}")
            logger.info(f"Пользователь {uid} сохранил текст ({len(text)} символов)")
            await return_to_main_menu(uid, context.bot)
        
        # ===== ОБРАБОТКА ГРУПП =====
        elif step == 'waiting_groups':
            raw_groups = [g.strip() for g in text.split(',') if g.strip()]
            
            if not raw_groups:
                await update.message.reply_text("❌ Не найдено групп. Используй формат: @group1, @group2")
                return
            
            valid_groups, invalid_groups = validate_groups(raw_groups)
            
            if invalid_groups:
                await update.message.reply_text(f"⚠️ Неверный формат для: {', '.join(invalid_groups[:3])}\n\nОни будут пропущены")
            
            if not valid_groups:
                await update.message.reply_text("❌ Нет корректных групп! Используй формат: @username")
                return
            
            user_data[uid]['groups'] = valid_groups
            user_data[uid].pop('step')
            save_data()
            backup_user_data(uid)
            
            await update.message.reply_text(f"✅ **Сохранено групп:** {len(valid_groups)}\n\n{', '.join(valid_groups[:10])}")
            logger.info(f"Пользователь {uid} сохранил {len(valid_groups)} групп")
            await return_to_main_menu(uid, context.bot)
        
        # ===== ОБРАБОТКА ИНТЕРВАЛА =====
        elif step == 'waiting_interval':
            is_valid, result = validate_interval(text)
            
            if not is_valid:
                await update.message.reply_text(f"❌ {result}\nПопробуй снова:")
                return
            
            user_data[uid]['interval'] = result
            user_data[uid].pop('step')
            save_data()
            
            await update.message.reply_text(f"✅ **Интервал установлен:** {result} секунд")
            logger.info(f"Пользователь {uid} установил интервал {result} сек")
            await return_to_main_menu(uid, context.bot)
        
        # ===== ОБРАБОТКА НОМЕРА ТЕЛЕФОНА =====
        elif step == 'waiting_phone':
            is_valid, phone = validate_phone(text)
            
            if not is_valid:
                await update.message.reply_text(f"❌ {result}\nПример: +79123456789")
                return
            
            user_data[uid]['phone'] = phone
            user_data[uid]['step'] = 'waiting_code'
            save_data()
            
            # Создаём клиента
            client = TelegramClient(f'session_{uid}', API_ID, API_HASH)
            sessions[uid] = client
            
            try:
                await client.connect()
                await client.send_code_request(phone)
                await update.message.reply_text(
                    "📲 **Код подтверждения отправлен!**\n\n"
                    "Введи код в формате: code12345\n"
                    "или просто: 12345\n\n"
                    "Код приходит в Telegram на указанный номер"
                )
                logger.info(f"Код отправлен пользователю {uid}")
            except FloodWaitError as e:
                await update.message.reply_text(f"⏳ Слишком много попыток. Подожди {e.seconds} секунд")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
            except Exception as e:
                await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
                logger.error(f"Ошибка отправки кода {uid}: {e}")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
        
        # ===== ОБРАБОТКА КОДА =====
        elif step == 'waiting_code':
            # Извлекаем код из текста
            match = re.search(r'(\d{5,6})', text)
            code = match.group(1) if match else None
            
            if not code:
                await update.message.reply_text("❌ Неверный формат. Примеры:\ncode12345\n12345")
                return
            
            user_data[uid]['code'] = code
            user_data[uid]['step'] = 'waiting_2fa'
            save_data()
            
            await update.message.reply_text(
                "🔐 **Двухфакторная аутентификация**\n\n"
                "Если у тебя включена 2FA - введи пароль\n"
                "Если нет - отправь команду /skip\n\n"
                "Пароль можешь вводить в любом виде"
            )
        
        # ===== ОБРАБОТКА 2FA =====
        elif step == 'waiting_2fa':
            password = None if text == '/skip' else text
            client = sessions.get(uid)
            
            if not client:
                await update.message.reply_text("❌ Ошибка сессии. Начни заново /start")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
                return
            
            phone = user_data[uid].get('phone')
            code = user_data[uid].get('code')
            
            try:
                # Пытаемся войти с кодом
                await client.sign_in(phone, code=code)
                await update.message.reply_text("✅ **Авторизация успешна!**")
                
            except SessionPasswordNeededError:
                # Требуется 2FA пароль
                if not password:
                    await update.message.reply_text("🔐 **Введи пароль 2FA:**")
                    return
                try:
                    await client.sign_in(password=password)
                    await update.message.reply_text("✅ **Авторизация успешна!**")
                except Exception as e:
                    await update.message.reply_text(f"❌ **Неверный пароль!**\n{str(e)[:100]}")
                    return
            
            except PhoneCodeInvalidError:
                await update.message.reply_text("❌ **Неверный код!**\nНачни заново /start")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
                return
            
            except Exception as e:
                await update.message.reply_text(f"❌ **Ошибка входа:** {str(e)[:100]}")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
                return
            
            # ===== УСПЕШНАЯ АВТОРИЗАЦИЯ =====
            groups = user_data[uid].get('groups', [])
            msg = user_data[uid].get('text', '')
            interval = user_data[uid].get('interval', 30)
            
            await update.message.reply_text("🔍 **Проверяю доступ к группам...**")
            
            valid_groups = []
            invalid_groups = []
            
            for group in groups:
                try:
                    entity = await client.get_entity(group)
                    valid_groups.append(group)
                    await update.message.reply_text(f"✅ {group} - доступна")
                except Exception as e:
                    invalid_groups.append(f"{group}: {str(e)[:30]}")
                    await update.message.reply_text(f"❌ {group} - НЕ ДОСТУПНА")
            
            if not valid_groups:
                await update.message.reply_text("❌ **Нет доступных групп для рассылки!**\nПроверь что бот добавлен в группы")
                user_data[uid].pop('step')
                save_data()
                await return_to_main_menu(uid, context.bot)
                return
            
            if invalid_groups:
                await update.message.reply_text(f"⚠️ **Пропущено групп:** {len(invalid_groups)}")
            
            user_data[uid]['groups'] = valid_groups
            user_data[uid].pop('step')
            user_data[uid].pop('code', None)
            save_data()
            
            await update.message.reply_text(
                f"🚀 **ЗАПУСК РАССЫЛКИ!**\n\n"
                f"📊 **Групп:** {len(valid_groups)}\n"
                f"⏱ **Интервал:** {interval} сек\n"
                f"📝 **Текст:** {msg[:100]}...\n\n"
                f"Для остановки нажми кнопку ОСТАНОВИТЬ"
            )
            
            logger.info(f"Пользователь {uid} запустил рассылку на {len(valid_groups)} групп")
            
            # Запускаем рассылку
            task = asyncio.create_task(run_broadcast(uid, context.bot, client, valid_groups, msg, interval))
            active_tasks[uid] = task
    
    except Exception as e:
        logger.error(f"Ошибка в message_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка, попробуй /start")

# ==================== РАССЫЛКА ====================
async def run_broadcast(uid, bot, client, groups, text, interval):
    """Основная функция рассылки"""
    total = len(groups)
    sent = 0
    errors = 0
    start_time = time.time()
    
    try:
        while True:
            for idx, group in enumerate(groups, 1):
                sent = idx
                try:
                    await client.send_message(group, text)
                    logger.info(f"[+] {uid} -> {group} ({idx}/{total})")
                    
                    # Отправляем прогресс каждые 10 сообщений
                    if idx % 10 == 0 or idx == total:
                        elapsed = int(time.time() - start_time)
                        await bot.send_message(
                            uid, 
                            f"📨 **Прогресс рассылки**\n\n"
                            f"Отправлено: {idx}/{total}\n"
                            f"Последняя группа: {group}\n"
                            f"Времени прошло: {elapsed} сек\n"
                            f"Ошибок: {errors}"
                        )
                except FloodWaitError as e:
                    await bot.send_message(uid, f"⏳ Флуд контроль: жди {e.seconds} сек")
                    logger.warning(f"Flood wait {e.seconds} для {uid}")
                    await asyncio.sleep(e.seconds)
                    # Повторяем отправку
                    try:
                        await client.send_message(group, text)
                    except:
                        errors += 1
                except Exception as e:
                    errors += 1
                    await bot.send_message(uid, f"❌ {group}: {str(e)[:50]}")
                    logger.error(f"Ошибка отправки {uid} в {group}: {e}")
                
                await asyncio.sleep(interval)
            
            # Круг завершён
            elapsed = int(time.time() - start_time)
            await bot.send_message(
                uid, 
                f"🔄 **Круг завершён!**\n\n"
                f"Отправлено: {total} сообщений\n"
                f"Всего ошибок: {errors}\n"
                f"Времени затрачено: {elapsed} сек\n"
                f"Начинаю новый круг..."
            )
            logger.info(f"Пользователь {uid} завершил круг, ошибок: {errors}")
            errors = 0  # Сбрасываем счётчик ошибок для нового круга
    
    except asyncio.CancelledError:
        elapsed = int(time.time() - start_time)
        await bot.send_message(
            uid, 
            f"🛑 **РАССЫЛКА ОСТАНОВЛЕНА**\n\n"
            f"📨 Отправлено: {sent}/{total}\n"
            f"❌ Ошибок: {errors}\n"
            f"⏱ Времени прошло: {elapsed} сек"
        )
        logger.info(f"Рассылка остановлена для {uid}, отправлено {sent}/{total}")
    
    finally:
        # Очистка
        if uid in sessions:
            try:
                await sessions[uid].disconnect()
                logger.info(f"Сессия {uid} закрыта")
            except:
                pass
            sessions.pop(uid, None)
        if uid in active_tasks:
            active_tasks.pop(uid, None)

# ==================== ЗАПУСК ====================
def main():
    """Главная функция запуска бота"""
    print("=" * 60)
    print("🥕 SendFlow - Бот для рассылки сообщений")
    print("=" * 60)
    print(f"📁 Лог-файл: {LOG_FILE}")
    print(f"📁 Данные: {DATA_FILE}")
    print(f"📁 Бэкапы: user_backups/")
    print("=" * 60)
    print("📌 ПОРЯДОК РАБОТЫ:")
    print("1. Настрой текст, группы, интервал через кнопки")
    print("2. Нажми ЗАПУСТИТЬ РАССЫЛКУ")
    print("3. Введи номер телефона")
    print("4. Введи код из Telegram: code12345")
    print("5. Если есть 2FA - введи пароль, если нет - /skip")
    print("6. Рассылка автоматически запустится")
    print("=" * 60)
    
    load_data()
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("skip", message_handler))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    
    logger.info("Бот запущен успешно!")
    print("✅ Бот готов к работе!")
    print("=" * 60)
    
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
        logger.info("Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        logger.critical(f"Критическая ошибка: {e}")

if __name__ == '__main__':
    main()
