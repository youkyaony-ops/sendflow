import asyncio
import re
import os
import logging
from telethon import TelegramClient, errors
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = '7983828391:AAFKhi9Gqc2Mhi26n7662hYm6aDlOv0RYgU'
ADMIN_ID = 6301912178
API_ID = 31245848
API_HASH = '67336528977585e1457985dc1d0ceefb'

user_data = {}
active_tasks = {}
temp_sessions = {}

async def safe_edit_message(query, text, keyboard=None):
    """Безопасное редактирование сообщения"""
    try:
        if keyboard:
            await query.edit_message_text(text, reply_markup=keyboard)
        else:
            await query.edit_message_text(text)
    except Exception as e:
        logger.error(f"Ошибка редактирования: {e}")
        try:
            await query.message.reply_text(text, reply_markup=keyboard)
        except:
            pass

async def start(update: Update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        await update.message.reply_text("❌ Нет доступа")
        return
    
    # Очистка старых данных
    if uid in user_data:
        user_data.pop(uid, None)
    
    keyboard = [
        [InlineKeyboardButton("📝 Текст", callback_data='set_text')],
        [InlineKeyboardButton("🔗 Группы", callback_data='set_groups')],
        [InlineKeyboardButton("⏱ Интервал", callback_data='set_interval')],
        [InlineKeyboardButton("🚀 ЗАПУСТИТЬ", callback_data='start_broadcast')],
        [InlineKeyboardButton("🛑 ОСТАНОВИТЬ", callback_data='stop_broadcast')]
    ]
    await update.message.reply_text(
        "🥕 SendFlow\n\nНастрой рассылку:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_callback(update: Update, context):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    
    if uid != ADMIN_ID:
        await safe_edit_message(query, "❌ Нет доступа")
        return
    
    data = query.data
    
    if data == 'set_text':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'text'
        await safe_edit_message(query, "📝 Введи текст рассылки (можно с эмодзи):")
    
    elif data == 'set_groups':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'groups'
        await safe_edit_message(query, "🔗 Введи ссылки на группы через запятую\nПример: @group1, @group2, https://t.me/group3")
    
    elif data == 'set_interval':
        user_data[uid] = user_data.get(uid, {})
        user_data[uid]['step'] = 'interval'
        await safe_edit_message(query, "⏱ Введи интервал в секундах (5-120):\nРекомендуем 30-60")
    
    elif data == 'start_broadcast':
        if uid not in user_data:
            user_data[uid] = {}
        
        # Проверка настроек
        missing = []
        if 'groups' not in user_data[uid]:
            missing.append("группы")
        if 'text' not in user_data[uid]:
            missing.append("текст")
        if 'interval' not in user_data[uid]:
            missing.append("интервал")
        
        if missing:
            await safe_edit_message(query, f"❌ Сначала настрой: {', '.join(missing)}")
            return
        
        if uid in active_tasks and not active_tasks[uid].done():
            await safe_edit_message(query, "⚠️ Рассылка уже запущена")
            return
        
        user_data[uid]['step'] = 'phone'
        await safe_edit_message(query, "🔐 Авторизация\n\nВведи номер телефона с +\nПример: +77081234567")
    
    elif data == 'stop_broadcast':
        if uid in active_tasks and not active_tasks[uid].done():
            active_tasks[uid].cancel()
            await safe_edit_message(query, "🛑 Рассылка остановлена")
        else:
            await safe_edit_message(query, "❌ Нет активной рассылки")

async def validate_group(client, group):
    """Проверка доступности группы"""
    try:
        entity = await client.get_entity(group)
        return True, entity
    except errors.FloodWaitError as e:
        logger.warning(f"Flood wait {e.seconds}")
        return False, f"Флуд: жди {e.seconds}с"
    except errors.RPCError as e:
        logger.error(f"Ошибка группы {group}: {e}")
        return False, str(e)
    except Exception as e:
        return False, str(e)

async def handle_message(update: Update, context):
    uid = update.effective_user.id
    if uid != ADMIN_ID:
        return
    
    if not update.message or not update.message.text:
        return
    
    text = update.message.text.strip()
    
    if uid not in user_data or 'step' not in user_data[uid]:
        return
    
    step = user_data[uid]['step']
    
    if step == 'text':
        if not text:
            await update.message.reply_text("❌ Текст не может быть пустым")
            return
        user_data[uid]['text'] = text
        user_data[uid].pop('step')
        preview = text[:200] + "..." if len(text) > 200 else text
        await update.message.reply_text(f"✅ Текст сохранён:\n{preview}")
    
    elif step == 'groups':
        groups_raw = [g.strip() for g in text.split(',') if g.strip()]
        groups = []
        for g in groups_raw:
            # Очистка ссылок
            g = g.replace('https://t.me/', '@').replace('http://t.me/', '@').replace('t.me/', '@')
            if not g.startswith('@'):
                g = '@' + g
            groups.append(g)
        
        if not groups:
            await update.message.reply_text("❌ Список пуст. Попробуй снова")
            return
        
        user_data[uid]['groups'] = groups
        user_data[uid].pop('step')
        await update.message.reply_text(f"✅ Сохранено групп: {len(groups)}\n{', '.join(groups[:5])}")
        if len(groups) > 5:
            await update.message.reply_text(f"и ещё {len(groups)-5}...")
    
    elif step == 'interval':
        try:
            interval = int(text)
            if 5 <= interval <= 120:
                user_data[uid]['interval'] = interval
                user_data[uid].pop('step')
                await update.message.reply_text(f"✅ Интервал {interval} сек")
            else:
                await update.message.reply_text("❌ Число от 5 до 120")
        except ValueError:
            await update.message.reply_text("❌ Введи число")
    
    elif step == 'phone':
        if not text.startswith('+') or not text[1:].isdigit():
            await update.message.reply_text("❌ Номер должен начинаться с +\nПример: +77081234567")
            return
        
        # Закрываем старую сессию если есть
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            del temp_sessions[uid]
        
        user_data[uid]['phone'] = text
        user_data[uid]['step'] = 'code'
        
        session_name = f'session_{uid}'
        client = TelegramClient(session_name, API_ID, API_HASH)
        
        try:
            await client.connect()
            if not await client.is_user_authorized():
                await client.send_code_request(text)
            temp_sessions[uid] = client
            await update.message.reply_text("📲 Код отправлен\nВведи код в формате: code12345")
        except Exception as e:
            logger.error(f"Ошибка отправки кода: {e}")
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")
            user_data[uid].pop('step')
    
    elif step == 'code':
        match = re.search(r'code(\d+)', text.lower())
        code = match.group(1) if match else None
        
        if not code:
            await update.message.reply_text("❌ Формат: code12345\nГде 12345 - код из Telegram")
            return
        
        user_data[uid]['code'] = code
        user_data[uid]['step'] = 'password'
        await update.message.reply_text("🔐 Если есть двухфакторная аутентификация - введи пароль\nЕсли нет - отправь /skip")
    
    elif step == 'password':
        password = None if text == '/skip' else text
        
        client = temp_sessions.get(uid)
        if not client:
            await update.message.reply_text("❌ Сессия потеряна. Начни заново /start")
            user_data.pop(uid, None)
            return
        
        groups = user_data[uid].get('groups', [])
        msg = user_data[uid].get('text', '')
        interval = user_data[uid].get('interval', 30)
        
        try:
            # Авторизация
            await client.sign_in(code=user_data[uid]['code'])
            
            # Если требуется пароль
            if password:
                try:
                    await client.sign_in(password=password)
                except errors.PasswordHashInvalidError:
                    await update.message.reply_text("❌ Неверный пароль 2FA")
                    return
            
            # Проверка групп перед запуском
            await update.message.reply_text("🔍 Проверяю доступ к группам...")
            valid_groups = []
            invalid_groups = []
            
            for group in groups:
                success, result = await validate_group(client, group)
                if success:
                    valid_groups.append(group)
                else:
                    invalid_groups.append(f"{group}: {result[:50]}")
            
            if not valid_groups:
                await update.message.reply_text("❌ Нет доступных групп для рассылки")
                return
            
            # Предупреждение о недоступных группах
            if invalid_groups:
                await update.message.reply_text(f"⚠️ Недоступные группы:\n{chr(10).join(invalid_groups[:3])}")
            
            # Обновляем список групп только на доступные
            user_data[uid]['groups'] = valid_groups
            
            await update.message.reply_text(
                f"✅ Авторизация успешна!\n\n"
                f"🚀 Запускаю рассылку\n"
                f"📊 Групп: {len(valid_groups)}\n"
                f"⏱ Интервал: {interval} сек\n\n"
                f"Для остановки нажми кнопку 🛑 ОСТАНОВИТЬ"
            )
            
            # Запуск рассылки
            task = asyncio.create_task(run_broadcast(uid, context.bot, client, valid_groups, msg, interval))
            active_tasks[uid] = task
            user_data[uid].pop('step', None)
            
        except errors.FloodWaitError as e:
            await update.message.reply_text(f"❌ Флуд ожидание: {e.seconds} секунд. Попробуй позже")
            user_data[uid].pop('step', None)
        except errors.SessionPasswordNeededError:
            if not password:
                await update.message.reply_text("🔐 Требуется пароль 2FA. Введи пароль:")
                return
        except errors.PhoneCodeInvalidError:
            await update.message.reply_text("❌ Неверный код подтверждения")
            user_data[uid]['step'] = 'code'
        except Exception as e:
            logger.error(f"Ошибка авторизации: {e}")
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
            user_data.pop(uid, None)

async def run_broadcast(uid, bot, client, groups, text, interval):
    """Запуск рассылки"""
    total = len(groups)
    current = 0
    
    try:
        while True:
            for idx, group in enumerate(groups, 1):
                current = idx
                try:
                    await client.send_message(group, text)
                    logger.info(f"[+] {uid} -> {group} ({idx}/{total})")
                    
                    # Отправка статуса каждые 5 сообщений
                    if idx % 5 == 0 or idx == total:
                        await bot.send_message(
                            uid, 
                            f"📨 Прогресс: {idx}/{total}\nПоследняя: {group}"
                        )
                except errors.FloodWaitError as e:
                    logger.warning(f"Flood wait {e.seconds}")
                    await bot.send_message(uid, f"⚠️ Флуд: жди {e.seconds}с")
                    await asyncio.sleep(e.seconds)
                except errors.RPCError as e:
                    logger.error(f"RPC ошибка {group}: {e}")
                    await bot.send_message(uid, f"❌ Ошибка в {group}: {str(e)[:100]}")
                except Exception as e:
                    logger.error(f"Ошибка {group}: {e}")
                    await bot.send_message(uid, f"❌ Ошибка: {str(e)[:100]}")
                
                await asyncio.sleep(interval)
            
            # После завершения цикла - начинаем заново
            await bot.send_message(uid, f"🔄 Завершён круг по {total} группам. Начинаю новый...")
    
    except asyncio.CancelledError:
        await bot.send_message(uid, f"🛑 Рассылка остановлена. Отправлено: {current}/{total}")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        await bot.send_message(uid, f"❌ Критическая ошибка: {str(e)[:200]}")
    finally:
        # Очистка
        if uid in temp_sessions:
            try:
                await temp_sessions[uid].disconnect()
            except:
                pass
            del temp_sessions[uid]
        if uid in active_tasks:
            del active_tasks[uid]

def main():
    """Основная функция"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Добавление обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("skip", lambda u, c: None))  # Заглушка для /skip
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    logger.info("✅ SendFlow запущен")
    print("✅ SendFlow запущен")
    
    try:
        app.run_polling()
    except KeyboardInterrupt:
        print("\n🛑 Остановка...")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")

if __name__ == '__main__':
    main()