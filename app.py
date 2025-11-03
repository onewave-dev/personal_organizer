import os
import asyncio
import re
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, ContextTypes, CommandHandler, JobQueue

# время и часовой пояс
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import storage
from calendar_source import fetch_today_events, fetch_events_next_days

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
        "/addreminder Текст DD-MM-YYYY — добавить напоминание\n"
        "/list — показать напоминания\n"
        "/clearreminders — очистить список\n"
    )

# 3) /test — простая проверка
async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тест ок ✅")

# 4) Утренний дайджест
async def send_morning_digest(context: ContextTypes.DEFAULT_TYPE):
    now_dt = datetime.now(TZ)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")
    today = now_dt.date()
    today_iso = today.isoformat()

    # 1) Единый источник напоминаний
    all_rem = storage.list_custom_reminders()

    undated = [r for r in all_rem if "due" not in r]
    today_dated = [r for r in all_rem if r.get("due") == today_iso]

    # «В ближайшую неделю»: завтра..+7 дней
    w_start = today + timedelta(days=1)
    w_end = today + timedelta(days=7)
    week = []
    for r in all_rem:
        due = r.get("due")
        if not due:
            continue
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            continue
        if w_start <= d <= w_end:
            week.append(r)
    week.sort(key=lambda x: x["due"])

    # «В ближайший месяц»: +8..+31 дней
    m_start = today + timedelta(days=8)
    m_end = today + timedelta(days=31)
    month = []
    for r in all_rem:
        due = r.get("due")
        if not due:
            continue
        try:
            d = datetime.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            continue
        if m_start <= d <= m_end:
            month.append(r)
    month.sort(key=lambda x: x["due"])

    # 2) Календарь
    events_today = fetch_today_events(TZ_NAME)
    events_week  = fetch_events_next_days(TZ_NAME, 1, 7)
    events_month = fetch_events_next_days(TZ_NAME, 8, 31)

    # 3) Формируем текст
    lines = [
        "🌅 Доброе утро!",
        f"Сейчас: {now_str}",
        "",
    ]

    # Напоминания: без даты + «сегодня»
    if undated or today_dated:
        lines.append("🧷 Напоминания:")
        for x in undated:
            lines.append(f"• {x['text']}")
        for it in today_dated:
            lines.append(f"• {it['text']} (сегодня)")
    else:
        lines.append("🧷 Напоминаний пока нет.")

    lines.append("")

    # Сегодня в календаре
    if events_today:
        lines.append("📅 Сегодня в календаре:")
        lines += [f"• {e}" for e in events_today]
    else:
        lines.append("📅 Событий в календаре на сегодня не найдено.")

    # В ближайшую неделю
    if events_week or week:
        lines.append("")
        lines.append("⏭️ В ближайшую неделю:")
        for e in events_week:
            lines.append(f"• {e}")
        for it in week:
            due = it["due"]
            lines.append(f"• {due[8:10]}.{due[5:7]} {it['text']}")

    # В ближайший месяц
    if events_month or month:
        lines.append("")
        lines.append("📆 В ближайший месяц:")
        for e in events_month:
            lines.append(f"• {e}")
        for it in month:
            due = it["due"]
            lines.append(f"• {due[8:10]}.{due[5:7]} {it['text']}")

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

## добавление кастомного напоминания

# Разрешим запятую/пробелы перед датой и хвостовые пробелы после
DATE_TAIL_RE = re.compile(r"[, \u00A0]*(\d{2}-\d{2}-\d{4})\s*$")

# Маппинг «экзотических» дефисов/тире к обычному '-'
DASHES = {
    "\u2010": "-",  # hyphen
    "\u2011": "-",  # non-breaking hyphen
    "\u2012": "-",  # figure dash
    "\u2013": "-",  # en dash
    "\u2014": "-",  # em dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus sign
}

def _normalize_dashes(s: str) -> str:
    for bad, good in DASHES.items():
        if bad in s:
            s = s.replace(bad, good)
    return s

async def cmd_addreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addreminder Текст
    /addreminder Текст DD-MM-YYYY   (дата в конце, опционально)
    Допускаются запятая/пробел перед датой; «длинные» тире нормализуются.
    """
    raw = " ".join(context.args) if context.args else ""
    raw = raw.strip()
    if not raw:
        await update.message.reply_text(
            "Используй:\n"
            "• /addreminder Текст\n"
            "• /addreminder Текст DD-MM-YYYY (дата в конце)\n"
            "Примеры:\n"
            "• /addreminder Проверить почту\n"
            "• /addreminder Позвонить маме 07-11-2025"
        )
        return

    # Нормализуем «экзотические» дефисы/тире и NBSP
    raw_norm = _normalize_dashes(raw).replace("\u00A0", " ").strip()

    m = DATE_TAIL_RE.search(raw_norm)
    due_iso = None
    text = raw_norm

    if m:
        due_ddmmyyyy = m.group(1)  # например, "09-11-2025"
        # Текст — всё до начала матча, обрежем запятые/пробелы на конце
        text = raw_norm[: m.start()].rstrip(" ,\u00A0")

        if not text:
            await update.message.reply_text("Добавь текст напоминания перед датой 🙂")
            return

        # Валидируем и конвертируем DD-MM-YYYY → YYYY-MM-DD
        try:
            d = datetime.strptime(due_ddmmyyyy, "%d-%m-%Y")
            due_iso = d.strftime("%Y-%m-%d")
        except ValueError:
            await update.message.reply_text("Дата должна быть в формате DD-MM-YYYY (например, 07-11-2025).")
            return

    # Сохранение
    try:
        storage.add_custom_reminder(text, due=due_iso)  # due_iso может быть None
    except ValueError as e:
        await update.message.reply_text(str(e))
        return

    if due_iso:
        # Показываем пользователю дату в том же формате, что он ввёл
        await update.message.reply_text(f"Добавил напоминание: {text} (на {m.group(1)})")
    else:
        await update.message.reply_text(f"Добавил напоминание: {text}")

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

# для серверного запуска с webhook / 

def build_telegram_application() -> Application:
    """
    Фабрика: создаёт Application со всеми хэндлерами и готовым JobQueue,
    но ничего НЕ запускает. Используется FastAPI-обвязкой.
    """
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN отсутствует. Укажите его в .env")

    jq = JobQueue()
    app = Application.builder().token(BOT_TOKEN).job_queue(jq).build()

    # хэндлеры из твоего main()
    app.add_handler(CommandHandler("start", cmd_start_and_schedule))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("testdigest", cmd_testdigest))
    app.add_handler(CommandHandler("settime", cmd_settime))
    app.add_handler(CommandHandler("when", cmd_when))
    app.add_handler(CommandHandler("addreminder", cmd_addreminder))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))

    return app


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
