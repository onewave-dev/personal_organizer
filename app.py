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
from calendar_source import (
    fetch_today_events, fetch_events_next_days, fetch_events_struct,
    fetch_tasks_today, fetch_tasks_next_days, fetch_tasks_struct
)

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
        [InlineKeyboardButton("↻ Обновить", callback_data="refresh_digest")],
        [InlineKeyboardButton("➕ Добавить своё напоминание", callback_data="rem:add:start")],
        [InlineKeyboardButton("✏️ Изменить свои напоминания", callback_data="rem:edit:start")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("⚙️ Настройки", callback_data="menu:settings")])
    return InlineKeyboardMarkup(rows)

def build_settings_menu(user_id: int | None) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton("⏰ Время дайджеста", callback_data="settings:settime")],
    ]
    if is_admin(user_id):
        rows.append([InlineKeyboardButton("[адм.]", callback_data="settings:admin")])
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")])
    return InlineKeyboardMarkup(rows)

def build_time_menu(current_time_str: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("−10 мин", callback_data="settings:time:-10"),
         InlineKeyboardButton("+10 мин", callback_data="settings:time:+10")],
        [InlineKeyboardButton("−1 час", callback_data="settings:time:-60"),
         InlineKeyboardButton("+1 час", callback_data="settings:time:+60")],
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

def _fmt_unified(d, title, t):
    dd = f"{d.day:02d}.{d.month:02d}"
    return f"• {dd} {title}" + (f" {t}" if t else "")



# --- формирование текста дайджеста ---

def build_digest_text() -> str:
    now_dt = _dt.now(TZ)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")
    today = now_dt.date()
    today_iso = today.isoformat()

    ev_today  = fetch_events_struct(TZ_NAME, 0, 0)
    ev_week   = fetch_events_struct(TZ_NAME, 1, 7)
    ev_month  = fetch_events_struct(TZ_NAME, 8, 31)
    ts_today  = fetch_tasks_struct(TZ_NAME, 0, 0)
    ts_week   = fetch_tasks_struct(TZ_NAME, 1, 7)
    ts_month  = fetch_tasks_struct(TZ_NAME, 8, 31)

    # 1) Напоминания (нормализуем старый формат строк → словари)
    all_rem = storage.list_custom_reminders()
    normalized = []
    for item in all_rem:
        if isinstance(item, dict):
            normalized.append(item)
        else:
            normalized.append({"text": str(item)})
    all_rem = normalized

    undated = [r for r in all_rem if not r.get("due")]
    today_dated = [r for r in all_rem if r.get("due") == today_iso]

    rem_today = []
    rem_week  = []
    rem_month = []
    for r in all_rem:
        txt = (r.get("text") or "").strip()
        due = r.get("due")
        if not txt:
            continue
        if not due:
            rem_today.append({"date": today, "title": txt, "time": ""})
            continue
        try:
            d = _dt.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            continue
        if d == today:
            rem_today.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=1) <= d <= today + _td(days=7):
            rem_week.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=8) <= d <= today + _td(days=31):
            rem_month.append({"date": d, "title": txt, "time": ""})

    # 3) Формируем текст
    lines = [
        "🌅 Доброе утро!",
        f"Сейчас: {now_str}",
        "",
        "Ваши события и напоминания.",
        "",
    ]

    today_items = ev_today + ts_today + rem_today
    today_items.sort(key=lambda x: (x["date"], x["time"] or "99:99"))
    lines.append("❗️Сегодня:")
    for it in today_items:
        lines.append(_fmt_unified(it["date"], it["title"], it["time"]))
    lines.append("")
    week_items = ev_week + ts_week + rem_week
    week_items.sort(key=lambda x: (x["date"], x["time"] or "99:99"))
    lines.append("🗓 В ближайшую неделю:")
    for it in week_items:
        lines.append(_fmt_unified(it["date"], it["title"], it["time"]))
    lines.append("")
    month_items = ev_month + ts_month + rem_month
    month_items.sort(key=lambda x: (x["date"], x["time"] or "99:99"))
    lines.append("🗓 В ближайший месяц:")
    for it in month_items:
        lines.append(_fmt_unified(it["date"], it["title"], it["time"]))


    return "\n".join(lines)

# копия дайджеста для повторных выводов
async def show_digest_copy(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int | None,
    with_menu: bool = False,   # ← по умолчанию БЕЗ кнопок
):
    """
    Выводит копию последнего дайджеста.
    Если with_menu=True — добавляет главное меню под дайджестом (только для главного экрана).
    """
    text = context.bot_data.get("last_digest_text")
    if not text:
        text = build_digest_text()
        context.bot_data["last_digest_text"] = text

    reply_markup = build_main_menu(user_id) if with_menu else None
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Тест ок ✅")

# 4) Отправка дайджеста
async def send_morning_digest(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    print(f"[digest] sending to {chat_id}") # лог
    digest_text = build_digest_text()
    context.bot_data["last_digest_text"] = digest_text
    await context.bot.send_message(chat_id=chat_id, text=digest_text)


# 5) Команда для мгновенной проверки дайджеста
async def cmd_testdigest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Прислать свежий дайджест по команде /testdigest."""
    uid = update.effective_user.id if update.effective_user else None

    # 1) Сгенерировать НОВЫЙ дайджест (подтянуть актуальные данные из календарей/тасков)
    digest_text = build_digest_text()

    # 2) Сохранить как «последний дайджест» для показа копии в других местах
    context.bot_data["last_digest_text"] = digest_text

    # 3) Отправить сообщение с дайджестом + главное меню под ним
    await update.message.reply_text(
        digest_text,
        reply_markup=build_main_menu(uid),
    )


# 5.1) Команда для установки времени дайджеста
async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить ежедневное время рассылки: /settime 07:45"""
    if not context.args:
        await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
        await update.message.reply_text("Укажи время: /settime HH:MM (например, 07:45)")
        return
    raw = context.args[0].strip()
    try:
        storage.set_daily_time(raw)
    except Exception:
        await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
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
    await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
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
    cid = update.effective_chat.id
    storage.set_chat_id(cid)
    uid = update.effective_user.id if update.effective_user else None

    digest_text = build_digest_text()
    context.bot_data["last_digest_text"] = digest_text

    try:
        register_daily_job(context, cid)
    except RuntimeError:
        await asyncio.sleep(0.5)
        register_daily_job(context, cid)

    await update.message.reply_text(
        digest_text,
        reply_markup=build_main_menu(uid),
    )

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
            dt = _dt(y_i, m_i, d_i)
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
        storage.add_custom_reminder(text, due=due_iso, user_id=update.effective_user.id)  # due_iso может быть None
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
        await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
        await update.message.reply_text("Пока нет пользовательских напоминаний.")
        return

    lines = ["📋 Твои пользовательские напоминания:"]

    for it in items:
        if isinstance(it, dict):
            text = it.get("text", "").strip()
            due = it.get("due")
            if due:
                try:
                    d = _dt.strptime(due, "%Y-%m-%d")
                    date_fmt = d.strftime("%d.%m.%Y")
                    lines.append(f"• {text} ({date_fmt})")
                except ValueError:
                    lines.append(f"• {text} (дата не распознана)")
            else:
                lines.append(f"• {text}")
        else:
            # поддержка старого формата (строк)
            lines.append(f"• {str(it)}")
    await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text("\n".join(lines))

# очистка списка
async def cmd_clearreminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    storage.clear_custom_reminders()
    await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text("Список напоминаний очищен.")

# Обработка кнопок 

async def on_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None

    text = context.bot_data.get("last_digest_text")
    if not text:
        text = build_digest_text()
        context.bot_data["last_digest_text"] = text

    await query.edit_message_text(
        text=text,
        reply_markup=build_main_menu(uid)
    )

async def on_settings_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None
    await query.answer()
    await query.edit_message_text(
        text="⚙️ Настройки:",
        reply_markup=build_settings_menu(uid)
    )

async def on_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    uid = query.from_user.id if query.from_user else None
    chat_id = query.message.chat_id

    # вход на экран выбора времени
    if data in ("settings:settime", "settings:time"):
        t = storage.get_daily_time()
        context.user_data["edit_time"] = t
        await query.answer()
        return await query.edit_message_text(
            text=f"⏰ Время дайджеста: {_fmt_time(t)} ({TZ.key})",
            reply_markup=build_time_menu(_fmt_time(t)),
        )

    # админ-пункт (без лишних сообщений)
    if data == "settings:admin":
        if not is_admin(uid):
            return await query.answer("Недостаточно прав", show_alert=True)
        await query.answer()
        return await query.edit_message_text(
            text=("🔒 Админ-меню\n\n"
                  "Тестовые команды:\n"
                  "• /test — проверить, что бот жив\n"
                  "• /testdigest — прислать утренний дайджест сейчас\n"),
            reply_markup=build_settings_menu(uid),
        )

    # кнопки корректировки времени и сохранение
    if data.startswith("settings:time:"):
        action = data.split(":")[2]  # "-10" | "+10" | "-60" | "+60" | "save"
        t = context.user_data.get("edit_time", storage.get_daily_time())

        if action == "-10":
            t = _shift_time(t, -10)
        elif action == "+10":
            t = _shift_time(t, +10)
        elif action == "-60":
            t = _shift_time(t, -60)
        elif action == "+60":
            t = _shift_time(t, +60)
        elif action == "save":
            # сохраняем строкой HH:MM и перерегистрируем джоб
            storage.set_daily_time(_fmt_time(t))
            context.user_data.pop("edit_time", None)
            register_daily_job(context, chat_id)
            await query.answer("Сохранено")
            # после сохранения вернёмся на экран настроек
            return await query.edit_message_text(
                text="⚙️ Настройки:",
                reply_markup=build_settings_menu(uid),
            )

        # если не save — просто обновили предпросмотр времени
        context.user_data["edit_time"] = t
        await query.answer()
        return await query.edit_message_text(
            text=f"⏰ Время дайджеста: {_fmt_time(t)} ({TZ.key})",
            reply_markup=build_time_menu(_fmt_time(t)),
        )


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if data == "menu:root":
        return await on_main_menu(query, context)
    
    if data == "menu:reminders":
        return await on_main_menu(query, context)

    if data == "menu:settings":
        return await on_settings_menu(query, context)
    
    # Маршрутизация всех кликов настроек в on_settings_action
    if data.startswith("settings:"):
        return await on_settings_action(update, context)

    # Ветвь напоминаний
    if data == "rem:add:start":
        await query.answer()
        context.user_data["awaiting_reminder"] = True

        return await query.edit_message_text(
            text=("Отправь одно сообщение с напоминанием:\n"
                "• Просто текст\n"
                "• Или: Текст DD-MM-YYYY (например, 07-11-2025)\n\n"
                "После отправки вернёшься в меню."),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]
            ])
        )

    if data == "rem:edit:start":
        await query.answer()
        uid = query.from_user.id
        items = storage.list_user_reminders(uid)

        if not items:
            return await query.edit_message_text(
                text="У тебя пока нет собственных напоминаний.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]])
            )

        buttons = [[InlineKeyboardButton(r["text"], callback_data=f"editrem:{i}")]
                for i, r in enumerate(items)]
        return await query.edit_message_text(
            text="Выбери напоминание:",
            reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]])
        )


    # обработка выбора конкретного напоминания для редактирования
    if data.startswith("editrem:"):
        await query.answer()
        uid = query.from_user.id
        idx = int(data.split(":")[1])
        items = storage.list_user_reminders(uid)

        if idx < 0 or idx >= len(items):
            return await query.edit_message_text(text="Неверный выбор.")

        r = items[idx]
        kb = [
            [InlineKeyboardButton("✏️ Редактировать", callback_data=f"editrem_edit:{idx}"),
            InlineKeyboardButton("❌ Удалить",       callback_data=f"editrem_del:{idx}")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="rem:edit:start")]
        ]
        return await query.edit_message_text(
            text=f"«{r.get('text','')}» ({r.get('due','без даты')})",
            reply_markup=InlineKeyboardMarkup(kb)
        )


    # удаление кастомного напоминания из UI
    if data.startswith("editrem_del:"):
        await query.answer()
        uid = query.from_user.id
        ok = storage.delete_user_reminder(uid, int(data.split(":")[1]))
        return await query.edit_message_text(
            text=("Удалено." if ok else "Не удалось удалить."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="rem:edit:start")]])
        )

    
    # Редактирование своего напоминания
    if data.startswith("editrem_edit:"):
        await query.answer()
        idx = int(data.split(":")[1])
        context.user_data["editing_idx"] = idx
        return await query.edit_message_text(
            text="Отправь новый текст (и при желании дату: DD-MM-YYYY) одним сообщением.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="rem:edit:start")]])
        )


    # Обновление дайджеста
    if data == "refresh_digest":
        await query.answer("Обновляю...")
        digest_text = build_digest_text()
        context.bot_data["last_digest_text"] = digest_text
        await query.edit_message_text(digest_text, reply_markup=build_main_menu(query.from_user.id))
        return
    
async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()

    #обработка редактирования существующего напоминания
    if context.user_data.get("editing_idx") is not None:
        idx = context.user_data.get("editing_idx")
        uid = update.effective_user.id
        # парсинг даты из текста напоминания 
        m = re.search(r"(.*)\s(\d{2}-\d{2}-\d{4})$", text)
        if m:
            body = m.group(1).strip()
            ddmmyyyy = m.group(2)
            try:
                iso = _dt.strptime(ddmmyyyy, "%d-%m-%Y").strftime("%Y-%m-%d")
            except ValueError:
                return await update.effective_message.reply_text("Дата должна быть в формате DD-MM-YYYY.")
        else:
            body = text
            iso = None
        # вызов обновления и ответ пользователю
        ok = storage.update_user_reminder(uid, idx, new_text=body, new_due_iso=iso)
        context.user_data.pop("editing_idx", None) # обнуляем индекс редактирование (после успешного редактирования)
        context.user_data["awaiting_reminder"] = False
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("⬅️ В раздел «Напоминания»", callback_data="menu:reminders")]
        ])

        if ok:
            await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
            return await update.effective_message.reply_text(
                "Изменено.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="menu:root")]])
            )
        else:
            await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
            return await update.effective_message.reply_text(
                "Не удалось изменить.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="menu:root")]])
            )

    if not context.user_data.get("awaiting_reminder"):
        return

    if not text:
        await update.effective_message.reply_text("Пустое сообщение. Отправь текст напоминания.")
        return

    m = re.search(r"(.*)\s(\d{2}-\d{2}-\d{4})$", text)
    if m:
        body = m.group(1).strip()
        ddmmyyyy = m.group(2)
        try:
            iso = _dt.strptime(ddmmyyyy, "%d-%m-%Y").strftime("%Y-%m-%d")
        except ValueError:
            await update.effective_message.reply_text("Дата должна быть в формате DD-MM-YYYY.")
            return
        storage.add_custom_reminder(body, iso, user_id=update.effective_user.id)
    else:
        storage.add_custom_reminder(text, user_id=update.effective_user.id)

    context.user_data["awaiting_reminder"] = False
    await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
    await update.effective_message.reply_text(
        "✅ Напоминание добавлено.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ В главное меню", callback_data="menu:root")]])
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
