import os
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, JobQueue

# время и часовой пояс
from datetime import datetime, time
from zoneinfo import ZoneInfo

import storage
from calendar_source import fetch_today_events

# 1) Загружаем .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TZ_NAME  = os.getenv("TZ", "Europe/Belgrade")
TZ = ZoneInfo(TZ_NAME)


# 2) /start — приветствие и проверка, что бот «живой»
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я — твой личный органайзер-бот. Команды:\n"
        "/test — проверить, что я работаю\n"
        "/testdigest — прислать утренний дайджест сейчас\n"
        "/when - показать, на какое время настроено ежедневное сообщение\n"
        "/settime - изменить время ежедневного сообщения\n"
        "/addreminder Текст — добавить напоминание\n"
        "/list — показать напоминания\n"
        "/clearreminders — очистить список\n"
    )

# 3) /test — простая проверка
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тест ок ✅")

# 4) Утренний дайджест
async def send_morning_digest(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(TZ).strftime("%d.%m.%Y %H:%M")
    reminders = storage.list_custom_reminders()
    events = fetch_today_events(TZ_NAME)  # сейчас вернёт []

    lines = [
        "🌅 Доброе утро!",
        f"Сейчас: {now}",
        "",
        "🧷 Напоминания:" if reminders else "🧷 Напоминаний пока нет.",
    ]
    if reminders:
        lines += [f"• {x}" for x in reminders]

    lines.append("")
    if events:
        lines.append("📅 Сегодня в календаре:")
        lines += [f"• {e}" for e in events]
    else:
        lines.append("📅 Событий в календаре на сегодня не найдено.")

    chat_id = context.job.data["chat_id"]
    await context.bot.send_message(chat_id=chat_id, text="\n".join(lines))



# 5) Команда для мгновенной проверки дайджеста
async def cmd_testdigest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Запускаем ту же функцию, но без JobQueue
    dummy_context = type("C", (), {})()
    dummy_context.bot = context.bot
    dummy_context.job = type("J", (), {"data": {"chat_id": update.effective_chat.id}})()
    await send_morning_digest(dummy_context)

# 5.1) Команда для установки времени дайджеста
async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить ежедневное время рассылки: /settime 07:45"""
    if not context.args:
        await update.message.reply_text("Укажи время: /settime HH:MM (например, 07:45)")
        return
    raw = context.args[0].strip()
    try:
        storage.set_daily_time(raw)
    except Exception:
        await update.message.reply_text("Неверный формат. Используй HH:MM (00–23:59).")
        return

    # Перерегистрируем задачу для этого чата
    cid = update.effective_chat.id
    storage.set_chat_id(cid)  # на всякий случай — сохраним чат
    register_daily_job(context, cid)

    await update.message.reply_text(f"Готово! Теперь утренний дайджест приходит в {raw} ({TZ_NAME}).")

# 5.2 Показать текущее время рассылки
async def cmd_when(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущее время рассылки"""
    t = storage.get_daily_time()
    await update.message.reply_text(f"Текущее время рассылки: {t.strftime('%H:%M')} ({TZ_NAME}).")

# 6) Регистрация ежедневной задачи
def register_daily_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    jq = context.job_queue
    if jq is None:
        return  # защитно, но после явного JobQueue почти не случится

    name = f"morning_digest_{chat_id}"

    for job in jq.get_jobs_by_name(name):
        job.schedule_removal()

    # забираем время из стораджа (например, 07:45) и добавляем tzinfo
    base_t = storage.get_daily_time()  # datetime.time(hour, minute) БЕЗ tzinfo
    t_with_tz = time(base_t.hour, base_t.minute, tzinfo=TZ)

    jq.run_daily(
        callback=send_morning_digest,
        time=t_with_tz,          # <-- tzinfo внутри
        name=name,
        data={"chat_id": chat_id},
        # timezone=TZ,           # <-- удалить для PTB 20.7
    )


# Регистрацию ежедневной рассылки делаем ПОСЛЕ того,
# как ты напишешь боту /start (чтобы знать твой chat_id).
# Перехватим /start как триггер регистрации job
async def cmd_start_and_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_start(update, context)
    cid = update.effective_chat.id
    storage.set_chat_id(cid)
    try:
        register_daily_job(context, cid)
    except RuntimeError:
        # если очередь ещё не готова — подождём чуть-чуть и попробуем снова
        await asyncio.sleep(0.5)
        register_daily_job(context, cid)

# добавление кастомного напоминания
async def cmd_addreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Напиши текст: /addreminder Купить воду")
        return
    storage.add_custom_reminder(text)
    await update.message.reply_text(f"Добавил напоминание {text}! Посмотреть все: /list")

# просмотр напоминаний
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items = storage.list_custom_reminders()
    if not items:
        await update.message.reply_text("Пока нет пользовательских напоминаний. Добавление: /addreminder ...")
        return
    lines = ["Твои напоминания:"]
    lines += [f"• {x}" for x in items]
    await update.message.reply_text("\n".join(lines))

# очистка списка
async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    storage.clear_custom_reminders()
    await update.message.reply_text("Список напоминаний очищен.")


# ЗАПУСК БОТА И ХЭНДЛЕРЫ
def main():
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN отсутствует. Укажите его в .env")
    # ЯВНО создаём очередь и отдаём её приложению
    jq = JobQueue()
    # 4) Создаём приложение и регистрируем хэндлеры
    app = Application.builder().token(BOT_TOKEN).job_queue(jq).build()

    app.add_handler(CommandHandler("start", cmd_start_and_schedule))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("testdigest", cmd_testdigest))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("when", cmd_when))
    app.add_handler(CommandHandler("addreminder", cmd_addreminder))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))


    # 5) Запускаем long polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
