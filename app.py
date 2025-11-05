import os
import asyncio
import re
import unicodedata
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, JobQueue, CallbackQueryHandler, MessageHandler, filters

# время и часовой пояс
from datetime import time as _t, datetime as _dt, timedelta as _td
from zoneinfo import ZoneInfo

import storage
from calendar_source import fetch_today_events, fetch_events_next_days

# 1) Загружаем .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
TZ_NAME  = os.getenv("TZ", "Europe/Belgrade")
TZ = ZoneInfo(TZ_NAME)
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

#1.1) проверка user id
def is_admin(user_id: int | None) -> bool:
    try:
        return user_id is not None and int(user_id) == ADMIN_ID
    except Exception:
        return False

# --- КЛАВИАТУРЫ ---

def build_main_menu(user_id: int | None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("🧷 Напоминания", callback_data="menu:reminders"),
         InlineKeyboardButton("⚙️ Настройки",   callback_data="menu:settings")],
    ]
    return InlineKeyboardMarkup(rows)

def build_settings_menu(user_id: int | None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⏰ Время дайджеста", callback_data="settings:settime")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("[адм.]", callback_data="settings:admin")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")])
    return InlineKeyboardMarkup(rows)

def build_reminders_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 Показать список", callback_data="rem:list")],
        [InlineKeyboardButton("➕ Добавить",        callback_data="rem:add:start")],
        [InlineKeyboardButton("🧹 Очистить",       callback_data="rem:clear")],
        [InlineKeyboardButton("⬅️ Назад",          callback_data="menu:root")],
    ])

def build_time_menu(current_time_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("−10 мин", callback_data="settings:time:-10"),
         InlineKeyboardButton("+10 мин", callback_data="settings:time:+10")],
        [InlineKeyboardButton("✅ Сохранить", callback_data="settings:time:save")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="menu:settings")],
    ])


# --- ФУНКЦИИ НАСТРОЙКИ ВРЕМЕНИ ---


def _fmt_time(t: _t) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"

def _shift_time(t: _t, minutes: int) -> _t:
    base = _dt(2000, 1, 1, t.hour, t.minute)
    shifted = base + _td(minutes=minutes)
    return _t(shifted.hour, shifted.minute)


# 2) /start — приветствие и проверка, что бот «живой»
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(
        "Главное меню:",
#         "Привет! Я — твой личный органайзер-бот. Команды:\n"
#         "/test — проверить, что я работаю\n"
#         "/testdigest — прислать утренний дайджест сейчас\n"
#         "/when - показать, на какое время настроено ежедневное сообщение\n"
#         "/settime - изменить время ежедневного сообщения\n"
#         "/addreminder Текст DD-MM-YYYY — добавить напоминание\n"
#         "/list — показать напоминания\n"
#         "/clearreminders — очистить список\n"
        reply_markup=build_main_menu(uid),
    )

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
    # Нормализуем: строки → {"text": "..."} для совместимости со старым форматом
    norm = []
    for it in all_rem:
        if isinstance(it, dict):
            norm.append(it)
        else:
            norm.append({"text": str(it)})
    all_rem = norm

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
    base_t = storage.get_daily_time()
    t_with_tz = _t(base_t.hour, base_t.minute, tzinfo=TZ)

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

# ── Нормализация: приводим «экзотические» тире и цифры к ASCII, NBSP → пробел
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), ord("-"))
# Арабско-индоцифры → ASCII
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩٠۱۲۳۴۵۶۷۸۹", "01234567890123456789")

def _normalize_all(s: str) -> str:
    if not s:
        return ""
    # NFKC часто лечит странные формы символов
    s = unicodedata.normalize("NFKC", s)
    # цифры → ASCII
    s = s.translate(_DIGITS)
    # тире → '-'
    s = s.translate(_DASHES)
    # NBSP → обычный пробел
    s = s.replace("\u00A0", " ")
    # уберём двойные пробелы по краям
    return s.strip()

# ── Дата в КОНЦЕ: берём DD<нецифра>MM<нецифра>YYYY, перед ней могут быть пробелы/запятые/переносы
DATE_TAIL_RE = re.compile(r"[, \t\r\n]*(\d{1,2})\D(\d{1,2})\D(\d{4})\s*$")

async def cmd_addreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addreminder Текст
    /addreminder Текст DD-MM-YYYY  (дата — последний токен; допускаются любые разделители)
    Логика: берём ПОСЛЕДНИЙ аргумент как кандидат даты; если это DD-MM-YYYY, парсим; иначе — считаем, что даты нет.
    """
    if not context.args:
        await update.message.reply_text(
            "Используй:\n"
            "• /addreminder Текст\n"
            "• /addreminder Текст DD-MM-YYYY (дата в конце)\n"
            "Примеры:\n"
            "• /addreminder Проверить почту\n"
            "• /addreminder Позвонить маме 07-11-2025"
        )
        return

    # Нормализуем аргументы (цифры → ASCII, длинные тире → '-', NBSP → пробел), отбрасываем пустые
    args_norm = [_normalize_all(a) for a in context.args if _normalize_all(a)]
    if not args_norm:
        await update.message.reply_text("Текст напоминания пуст.")
        return

    candidate = args_norm[-1]                 # ПОСЛЕДНИЙ токен — кандидат на дату
    digit_parts = re.split(r"\D+", candidate) # режем по ЛЮБОЙ не-цифре ( '-', '–', '/', и т.п.)
    due_iso = None

    if len(digit_parts) == 3 and all(p.isdigit() for p in digit_parts):
        d_str, m_str, y_str = digit_parts
        try:
            d_i, m_i, y_i = int(d_str), int(m_str), int(y_str)
            # строгая проверка календаря
            dt = datetime(y_i, m_i, d_i)
            due_iso = dt.strftime("%Y-%m-%d")
            text = " ".join(args_norm[:-1]).rstrip(" ,\t\r\n")
            if not text:
                await update.message.reply_text("Добавь текст напоминания перед датой 🙂")
                return
        except Exception as e:
            # Диагностика, чтобы увидеть причину (временно)
            dbg = f"DEBUG: candidate={candidate!r} parts={digit_parts!r} err={e!r}"
            await update.message.reply_text(dbg)
            await update.message.reply_text("Дата должна быть в формате DD-MM-YYYY (например, 07-11-2025).")
            return
    else:
        # Дата не распознана — трактуем как напоминание БЕЗ даты (никакой ошибки)
        text = " ".join(args_norm).strip()

    try:
        storage.add_custom_reminder(text, due=due_iso)  # due_iso может быть None
    except ValueError as e:
        await update.message.reply_text(str(e))
        return

    if due_iso:
        await update.message.reply_text(
            f"Добавил напоминание: {text} (на {d_str.zfill(2)}-{m_str.zfill(2)}-{y_str})"
        )
    else:
        await update.message.reply_text(f"Добавил напоминание: {text}")


# просмотр напоминаний
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает все пользовательские напоминания:
      • без даты — обычные пункты
      • с датой — выводит дату в формате DD.MM.YYYY
    """
    items = storage.list_custom_reminders()
    if not items:
        await update.message.reply_text("Пока нет пользовательских напоминаний.")
        return

    lines = ["📋 Твои пользовательские напоминания:"]

    for it in items:
        if isinstance(it, dict):
            text = it.get("text", "").strip()
            due = it.get("due")
            if due:
                try:
                    d = datetime.strptime(due, "%Y-%m-%d")
                    date_fmt = d.strftime("%d.%m.%Y")
                    lines.append(f"• {text} ({date_fmt})")
                except ValueError:
                    lines.append(f"• {text} (дата не распознана)")
            else:
                lines.append(f"• {text}")
        else:
            # поддержка старого формата (строк)
            lines.append(f"• {str(it)}")

    await update.message.reply_text("\n".join(lines))

# очистка списка
async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    storage.clear_custom_reminders()
    await update.message.reply_text("Список напоминаний очищен.")

# Обработка кнопок 

async def on_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None
    await query.edit_message_text("Главное меню:", reply_markup=build_main_menu(uid))

async def on_settings_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None
    await query.edit_message_text("⚙️ Настройки:", reply_markup=build_settings_menu(uid))


async def on_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id if query.from_user else None

    if data == "settings:settime" or data == "settings:time":
        t = storage.get_daily_time()
        context.user_data["edit_time"] = t
        await query.answer()
        await query.edit_message_text(
            f"⏰ Время дайджеста: {_fmt_time(t)} ({TZ.key})",
            reply_markup=build_time_menu(_fmt_time(t)),
        )
        return

    if data.startswith("settings:time:"):
        action = data.split(":")[2]  # "-10" | "+10" | "save"
        t = context.user_data.get("edit_time", storage.get_daily_time())
        if action == "-10":
            t = _shift_time(t, -10)
            context.user_data["edit_time"] = t
        elif action == "+10":
            t = _shift_time(t, +10)
            context.user_data["edit_time"] = t
        elif action == "save":
            storage.set_daily_time(t)
            context.user_data.pop("edit_time", None)
        await query.answer("Сохранено" if action == "save" else "")
        await query.edit_message_text(
            f"⏰ Время дайджеста: {_fmt_time(t)} ({TZ.key})",
            reply_markup=build_time_menu(_fmt_time(t)),
        )
        return

    if data == "settings:admin":
        if not is_admin(uid):
            await query.answer("Недостаточно прав", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            "🔒 Админ-меню\n\n"
            "Тестовые команды:\n"
            "• /test — проверить, что бот жив\n"
            "• /testdigest — прислать утренний дайджест сейчас\n",
            reply_markup=build_settings_menu(uid),
        )
        return

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data == "menu:root":
        return await on_main_menu(query, context)
    
    if data == "menu:reminders":
        await query.answer()
        return await query.edit_message_text(
            "🧷 Раздел «Напоминания»",
            cmd_testdigest(update, context), 
            reply_markup=build_reminders_menu()
        )

    if data == "menu:settings":
        return await on_settings_menu(query, context)
    
    # Маршрутизация всех кликов настроек в on_settings_action
    if data.startswith("settings:"):
        return await on_settings_action(update, context)

    # Ветвь напоминаний
    if data == "rem:list":
        await query.answer()
        items = storage.list_custom_reminders()
        if not items:
            text = "Пока нет пользовательских напоминаний."
        else:
            lines = ["📋 Твои пользовательские напоминания:"]
            for it in items:
                if isinstance(it, dict):
                    txt = (it.get("text") or "").strip()
                    due = it.get("due")
                    if due:
                        try:
                            d = _dt.strptime(due, "%Y-%m-%d").strftime("%d.%m.%Y")
                            lines.append(f"• {txt} ({d})")
                        except Exception:
                            lines.append(f"• {txt} (дата не распознана)")
                    else:
                        lines.append(f"• {txt}")
                else:
                    lines.append(f"• {str(it)}")
            text = "\n".join(lines)

        return await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:reminders")]
            ])
        )
    if data == "rem:add:start":
        await query.answer()
        context.user_data["awaiting_reminder"] = True
        return await query.edit_message_text(
            "Отправь одно сообщение с напоминанием:\n"
            "• Просто текст\n"
            "• Или: Текст DD-MM-YYYY (например, 07-11-2025)\n\n"
            "После отправки вернёшься в меню.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Отмена", callback_data="menu:reminders")]
            ])
        )

    if data == "rem:clear":
        await query.answer()
        storage.clear_custom_reminders()
        return await query.edit_message_text(
            "Список напоминаний очищен.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:reminders")]
            ])
        )

async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_reminder"):
        return

    text = (update.effective_message.text or "").strip()
    if not text:
        await update.effective_message.reply_text("Пустое сообщение. Отправь текст напоминания.")
        return

    import re
    from datetime import datetime as _dt
    m = re.search(r"(.*)\s(\d{2}-\d{2}-\d{4})$", text)
    if m:
        body = m.group(1).strip()
        ddmmyyyy = m.group(2)
        try:
            iso = _dt.strptime(ddmmyyyy, "%d-%m-%Y").strftime("%Y-%m-%d")
        except ValueError:
            await update.effective_message.reply_text("Дата должна быть в формате DD-MM-YYYY.")
            return
        storage.add_custom_reminder(body, iso)
    else:
        storage.add_custom_reminder(text)

    context.user_data["awaiting_reminder"] = False
    await update.effective_message.reply_text(
        "✅ Напоминание добавлено.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ В раздел «Напоминания»", callback_data="menu:reminders")]
        ])
    )


# для серверного запуска с webhook из server.py

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
    app.add_handler(CommandHandler("addreminder", cmd_addreminder))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))

    return app



# ЗАПУСК БОТА И ХЭНДЛЕРЫ - ЛОКАЛЬНО
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
    app.add_handler(CommandHandler("addreminder", cmd_addreminder))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))


    # 5) Запускаем long polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
