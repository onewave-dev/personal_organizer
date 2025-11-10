import os
import asyncio
import re
import unicodedata
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, ContextTypes, CommandHandler, JobQueue, CallbackQueryHandler, MessageHandler, filters
from telegram.error import BadRequest

# время и часовой пояс
from datetime import time as _t, datetime as _dt, timedelta as _td
from zoneinfo import ZoneInfo

import storage
from calendar_source import (
    fetch_today_events, fetch_events_next_days, fetch_events_struct,
    fetch_tasks_today, fetch_tasks_next_days, fetch_tasks_struct,
    fetch_events_struct_for_calendar, fetch_tasks_struct_for_list,
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

# --- ДОСТУПЫ / АВТОРИЗАЦИЯ ---
def _parse_ids_csv(value: str) -> set[int]:
    out = set()
    for part in (value or "").split(","):
        p = part.strip()
        if not p:
            continue
        try:
            out.add(int(p))
        except ValueError:
            pass
    return out

AUTHORIZED_USER_IDS = _parse_ids_csv(os.getenv("AUTHORIZED_USER_IDS", ""))
GUEST_USER_ID = int(os.getenv("GUEST_USER_ID", "0") or "0")
GUEST_CALENDAR_NAME = os.getenv("GUEST_CALENDAR_NAME", "").strip()
GUEST_TASKLIST_NAME = os.getenv("GUEST_TASKLIST_NAME", "").strip()

def is_allowed(user_id: int | None) -> bool:
    if user_id is None:
        return False
    # админ всегда допускается
    if is_admin(user_id):
        return True
    # если список пуст — допускаем только админа
    if not AUTHORIZED_USER_IDS:
        return False
    return int(user_id) in AUTHORIZED_USER_IDS

async def guard_auth_and_get_uid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int | None:
    """
    Возвращает user_id, если он авторизован.
    Если нет — отправляет понятное сообщение и возвращает None.
    """
    uid = update.effective_user.id if update.effective_user else None
    if is_allowed(uid):
        return uid

    # Ответ — в зависимости от типа апдейта:
    if update.message:
        await update.message.reply_text("❌ Вы не авторизованы для работы с этим ботом.")
    elif update.callback_query:
        try:
            await update.callback_query.answer("Вы не авторизованы для работы с этим ботом.", show_alert=True)
        except Exception:
            pass
    return None


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

    # События и задачи (структурированные)
    ev_today  = fetch_events_struct(TZ_NAME, 0, 0)
    ev_week   = fetch_events_struct(TZ_NAME, 1, 7)
    ev_month  = fetch_events_struct(TZ_NAME, 8, 31)
    ts_today  = fetch_tasks_struct(TZ_NAME, 0, 0)
    ts_week   = fetch_tasks_struct(TZ_NAME, 1, 7)
    ts_month  = fetch_tasks_struct(TZ_NAME, 8, 31)

    # Напоминания: нормализуем и фильтруем по видимости для админа
    all_rem = storage.list_custom_reminders()

    def _visible_for_admin(r: dict) -> bool:
        uid = r.get("user_id")
        shared = bool(r.get("share"))
        return (uid == ADMIN_ID) or (uid == GUEST_USER_ID) or shared

    all_rem = [r if isinstance(r, dict) else {"text": str(r)} for r in all_rem]
    all_rem = [r for r in all_rem if (r.get("text") or "").strip()]
    all_rem = [r for r in all_rem if _visible_for_admin(r)]

    # Разносим по окнам + недатированные отдельно
    rem_today: list[dict] = []
    rem_week:  list[dict] = []
    rem_month: list[dict] = []
    rem_undated: list[str] = []

    for r in all_rem:
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        due = r.get("due")
        if not due:
            rem_undated.append(txt)
            continue
        try:
            d = _dt.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            # кривая дата — считаем как бездатачное
            rem_undated.append(txt)
            continue

        if d == today:
            rem_today.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=1) <= d <= today + _td(days=7):
            rem_week.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=8) <= d <= today + _td(days=31):
            rem_month.append({"date": d, "title": txt, "time": ""})

    # Формируем текст
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

    # Недатированные — отдельным блоком
    if rem_undated:
        lines.append("")
        lines.append("📝 Без даты:")
        for txt in rem_undated:
            lines.append(f"• {txt}")

    return "\n".join(lines)


def build_guest_digest_text() -> str:
    now_dt = _dt.now(TZ)
    now_str = now_dt.strftime("%d.%m.%Y %H:%M")

    cal_name = GUEST_CALENDAR_NAME
    tl_name  = GUEST_TASKLIST_NAME

    ev_today  = fetch_events_struct_for_calendar(TZ_NAME, 0, 0, cal_name) if cal_name else []
    ev_week   = fetch_events_struct_for_calendar(TZ_NAME, 1, 7, cal_name) if cal_name else []
    ev_month  = fetch_events_struct_for_calendar(TZ_NAME, 8, 31, cal_name) if cal_name else []
    ts_today  = fetch_tasks_struct_for_list(TZ_NAME, 0, 0, tl_name) if tl_name else []
    ts_week   = fetch_tasks_struct_for_list(TZ_NAME, 1, 7, tl_name) if tl_name else []
    ts_month  = fetch_tasks_struct_for_list(TZ_NAME, 8, 31, tl_name) if tl_name else []

    # Напоминания, видимые гостю
    today = now_dt.date()
    all_rem = storage.list_custom_reminders()

    def _visible_for_guest(r: dict) -> bool:
        uid = r.get("user_id")
        shared = bool(r.get("share"))
        return (uid == GUEST_USER_ID) or (uid == ADMIN_ID and shared)

    rem = [r if isinstance(r, dict) else {"text": str(r)} for r in all_rem]
    rem = [r for r in rem if (r.get("text") or "").strip()]
    rem = [r for r in rem if _visible_for_guest(r)]

    rem_today: list[dict] = []
    rem_week: list[dict] = []
    rem_month: list[dict] = []
    rem_undated: list[str] = []

    for r in rem:
        txt = (r.get("text") or "").strip()
        if not txt:
            continue
        due = r.get("due")
        if not due:
            rem_undated.append(txt)
            continue
        try:
            d = _dt.strptime(due, "%Y-%m-%d").date()
        except ValueError:
            rem_undated.append(txt)
            continue

        if d == today:
            rem_today.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=1) <= d <= today + _td(days=7):
            rem_week.append({"date": d, "title": txt, "time": ""})
        elif today + _td(days=8) <= d <= today + _td(days=31):
            rem_month.append({"date": d, "title": txt, "time": ""})

    lines = [
        "🌅 Доброе утро!",
        f"Сейчас: {now_str}",
        "",
    ]

    def _append_section(title: str, items: list[dict]):
        lines.append(title)
        if not items:
            lines.append("• (пусто)")
            lines.append("")
            return
        items.sort(key=lambda x: (x["date"], x["time"] or "99:99"))
        for it in items:
            lines.append(_fmt_unified(it["date"], it["title"], it["time"]))
        lines.append("")

    _append_section("❗️Сегодня:", (ev_today + ts_today + rem_today))
    _append_section("🗓 В ближайшую неделю:", (ev_week + ts_week + rem_week))
    _append_section("🗓 В ближайший месяц:", (ev_month + ts_month + rem_month))

    if rem_undated:
        lines.append("📝 Без даты:")
        for txt in rem_undated:
            lines.append(f"• {txt}")
        lines.append("")

    return "\n".join(lines)


# копия дайджеста для повторных выводов
async def show_digest_copy(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int | None,
    with_menu: bool = False,   # ← по умолчанию БЕЗ кнопок
):
    """
    Выводит копию последнего дайджеста из кэша.
    Если with_menu=True — добавляет главное меню под дайджестом (только для главного экрана).
    ⚠️ Не строит новый дайджест — если кэш пуст, показывает подсказку обновить вручную.
    """
    text = context.bot_data.get("last_digest_text")
    if not text:
        text, _ = storage.get_last_digest()
    if not text:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Пока нет свежего дайджеста.\nНажми «↻ Обновить» или используй /testdigest.",
        )
        return

    reply_markup = build_main_menu(user_id) if with_menu else None
    await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup)

# --- ХЭЛПЕРЫ ---
#хэлпер - Строит НОВЫЙ дайджест, обновляет кэш и отправляет его сообщением

async def rebuild_and_show_digest(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int | None,
    with_menu: bool = True,
):
    digest_text = build_digest_text()
    context.bot_data["last_digest_text"] = digest_text
    storage.set_last_digest(digest_text)
    reply_markup = build_main_menu(user_id) if with_menu else None
    await context.bot.send_message(chat_id=chat_id, text=digest_text, reply_markup=reply_markup)

async def safe_edit(query, text: str, reply_markup=None):
    """Аккуратно правит сообщение, игнорируя 'Message is not modified'."""
    try:
        # Предварительная проверка на идентичность
        same_text = (query.message and (query.message.text or "") == (text or ""))
        same_kb = False
        if reply_markup or query.message.reply_markup:
            a = reply_markup.to_dict() if reply_markup else None
            b = query.message.reply_markup.to_dict() if query.message.reply_markup else None
            same_kb = (a == b)
        else:
            same_kb = True  # обе None

        if same_text and same_kb:
            await query.answer("Уже актуально")
            return

        await query.edit_message_text(text=text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            await query.answer("Уже актуально")
            return
        raise

async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    await update.message.reply_text("Тест ок ✅")

# 4) Отправка дайджеста
async def send_morning_digest(context: ContextTypes.DEFAULT_TYPE):
    chat_id = context.job.data["chat_id"]
    print(f"[digest] sending to {chat_id}") # лог
    digest_text = build_digest_text()
    context.bot_data["last_digest_text"] = digest_text
    storage.set_last_digest(digest_text)
    await context.bot.send_message(chat_id=chat_id, text=digest_text)

async def send_guest_morning_digest(context: ContextTypes.DEFAULT_TYPE):
    if not GUEST_USER_ID:
        return
    text = build_guest_digest_text()
    await context.bot.send_message(chat_id=GUEST_USER_ID, text=text)


# 5) Команда для мгновенной проверки дайджеста
async def cmd_testdigest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    """Прислать свежий дайджест по команде /testdigest."""
    uid = update.effective_user.id if update.effective_user else None

    # 1) Сгенерировать НОВЫЙ дайджест (подтянуть актуальные данные из календарей/тасков)
    digest_text = build_digest_text()

    # 2) Сохранить как «последний дайджест» для показа копии в других местах
    context.bot_data["last_digest_text"] = digest_text
    storage.set_last_digest(digest_text)

    # 3) Отправить сообщение с дайджестом + главное меню под ним
    await update.message.reply_text(
        digest_text,
        reply_markup=build_main_menu(uid),
    )
    context.user_data["at_root"] = True

async def cmd_testguestdigest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    if not is_admin(uid):
        return await update.message.reply_text("Недостаточно прав.")

    if not GUEST_USER_ID:
        return await update.message.reply_text("GUEST_USER_ID не задан.")

    text = build_guest_digest_text()
    # отправим как в «бою» — именно гостю
    try:
        await context.bot.send_message(chat_id=GUEST_USER_ID, text=text)
    except Exception as e:
        await update.message.reply_text(f"Не удалось отправить гостевой дайджест: {e}")
        return

    await update.message.reply_text("Гостевой дайджест отправлен.")

async def cmd_testguestdigesttome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    if not is_admin(uid):
        return await update.message.reply_text("Недостаточно прав.")

    # Собираем ровно тот же текст, что и для гостя
    text = build_guest_digest_text()
    if not text.strip():
        return await update.message.reply_text("Гостевой дайджест пуст (проверьте имена календаря и списка задач).")

    # Отправляем админу (инициатору команды)
    try:
        await context.bot.send_message(chat_id=uid, text=text)
    except Exception as e:
        await update.message.reply_text(f"Не удалось отправить дайджест: {e}")
        return

    await update.message.reply_text("Гостевой дайджест отправлен вам.")

# 5.1) Команда для установки времени дайджеста
async def cmd_settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Установить ежедневное время рассылки: /settime 07:45"""
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
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
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    """Показать текущее время рассылки"""
    t = storage.get_daily_time()
    await show_digest_copy(context, update.effective_chat.id, update.effective_user.id)
    await update.message.reply_text(f"Текущее время рассылки: {t.strftime('%H:%M')} ({TZ_NAME}).")

# 6) Регистрация ежедневной задачи
def register_daily_job(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    jq = context.job_queue
    if jq is None:
        return

    base_t = storage.get_daily_time()
    t_with_tz = _t(base_t.hour, base_t.minute, tzinfo=TZ)

    # основная задача
    name_main = f"morning_digest_{chat_id}"
    for job in jq.get_jobs_by_name(name_main):
        job.schedule_removal()
    jq.run_daily(
        callback=send_morning_digest,
        time=t_with_tz,
        name=name_main,
        data={"chat_id": chat_id},
    )

    # гостевая задача (если настроен гость)
    if GUEST_USER_ID:
        name_guest = f"guest_digest_{GUEST_USER_ID}"
        for job in jq.get_jobs_by_name(name_guest):
            job.schedule_removal()
        jq.run_daily(
            callback=send_guest_morning_digest,
            time=t_with_tz,
            name=name_guest,
            data={"chat_id": GUEST_USER_ID},
        )


# Регистрацию ежедневной рассылки делаем ПОСЛЕ того,
# как ты напишешь боту /start (чтобы знать твой chat_id).
# Перехватим /start как триггер регистрации job
async def cmd_start_and_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    cid = update.effective_chat.id
    storage.set_chat_id(cid)
    uid = update.effective_user.id if update.effective_user else None

    digest_text = build_digest_text()
    context.bot_data["last_digest_text"] = digest_text
    storage.set_last_digest(digest_text)
    try:
        register_daily_job(context, cid)
    except RuntimeError:
        await asyncio.sleep(0.5)
        register_daily_job(context, cid)

    await update.message.reply_text(
        digest_text,
        reply_markup=build_main_menu(uid),
    )
    context.user_data["at_root"] = True

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
# ── Маркер шаринга для гостя в конце текста (любой из m/M/м/М).
MARKER_SHARE_RE = re.compile(r"\s*@\s*[mMмМ]\s*$")
# дата в формате DD…MM…YYYY (любой нецифровой разделитель) перед опциональным @m
DATE_OPT_SHARE_RE = re.compile(
    r"^(?P<body>.*?)"
    r"(?:[, \t\r\n]+(?P<d>\d{1,2})\D(?P<mm>\d{1,2})\D(?P<y>\d{4}))?"
    r"(?:\s*@\s*[mMмМ])?\s*$",
    re.S
)

def _strip_share_marker(text: str) -> tuple[str, bool]:
    """Возвращает (очищенный_текст, share_flag) по маркеру @m/м в конце."""
    if not text:
        return "", False
    if MARKER_SHARE_RE.search(text):
        clean = MARKER_SHARE_RE.sub("", text).rstrip()
        return clean, True
    return text, False

def parse_reminder_input(raw_text: str) -> tuple[str, str | None]:
    """
    Возвращает (чистый_текст, due_iso | None).
    • Понимает DD-MM-YYYY / DD.MM.YYYY / DD/MM/YYYY и т.п.
    • Хвостовой маркер '@m' допускается и не мешает парсингу.
    """
    # ВАЖНО: нормализуем «экзотические» символы (тире, цифры, NBSP и т.д.)
    s = _normalize_all(raw_text or "")
    m = DATE_OPT_SHARE_RE.match(s)
    if not m:
        return s, None

    body = (m.group("body") or "").strip()
    # убираем маркер из тела, если вдруг попал внутрь
    body = MARKER_SHARE_RE.sub("", body).rstrip()

    d, mm, y = m.group("d"), m.group("mm"), m.group("y")
    if d and mm and y:
        try:
            dt = _dt(int(y), int(mm), int(d))
            return body, dt.strftime("%Y-%m-%d")
        except Exception:
            pass
    return body, None

async def cmd_addreminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n"
            "/addreminder Текст\n"
            "/addreminder Текст DD-MM-YYYY (дата в конце, можно «17/11/2025 @m»)"
        )
        return

    raw = " ".join(context.args)
    body, due_iso = parse_reminder_input(raw)
    if not body:
        await update.message.reply_text("Добавь текст напоминания перед датой 🙂")
        return

    # флаг расшаривания: админ управляет маркером @m, гость — всегда True
    norm_raw = _normalize_all(raw)
    is_admin_user = is_admin(update.effective_user.id)
    if is_admin_user:
        share_flag = bool(MARKER_SHARE_RE.search(norm_raw))
    else:
        share_flag = True

    try:
        storage.add_custom_reminder(body, due=due_iso, user_id=uid, share=share_flag)
    except ValueError as e:
        await update.message.reply_text(str(e))
        return


    if due_iso:
        pretty = _dt.strptime(due_iso, "%Y-%m-%d").strftime("%d-%m-%Y")
        await update.message.reply_text(f"Добавил напоминание: {body} (на {pretty})")
    else:
        await update.message.reply_text(f"Добавил напоминание: {body}")

    await rebuild_and_show_digest(context, update.effective_chat.id, update.effective_user.id, with_menu=True)


# просмотр напоминаний
async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Показывает все пользовательские напоминания:
      • без даты — обычные пункты
      • с датой — выводит дату в формате DD.MM.YYYY
    """
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    items = storage.list_user_reminders(uid)
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
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    storage.clear_custom_reminders()
    await rebuild_and_show_digest(context, update.effective_chat.id, update.effective_user.id, with_menu=True)
    await update.message.reply_text("Список напоминаний очищен.")

# Обработка кнопок 

async def on_main_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None

    text = context.bot_data.get("last_digest_text")
    if not text:
        await query.edit_message_text(
            text="(Пока нет свежего дайджеста — нажми «↻ Обновить» или /testdigest)",
            reply_markup=build_main_menu(uid),
        )
        context.user_data["at_root"] = True
        return

    await safe_edit(query, text, build_main_menu(uid))
    context.user_data["at_root"] = True

async def on_settings_menu(query, context: ContextTypes.DEFAULT_TYPE):
    uid = query.from_user.id if query.from_user else None
    chat_id = query.message.chat_id

    # Если пришли ИЗ главного меню — разово пришлём копию дайджеста без кнопок
    # if context.user_data.get("at_root", False):
    #     await show_digest_copy(context, chat_id, uid, with_menu=False)
    # Мы уже НЕ в корне
    context.user_data["at_root"] = False

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
                  "• /testdigest — прислать утренний дайджест сейчас\n"
                  "• /testguestdigest — отправить гостевой дайджест сейчас\n"
                  "• /testguestdigesttome — прислать гостевой дайджест мне (админу)"),
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
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
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
        # чистим возможный «хвост» от редактирования
        context.user_data.pop("editing_idx", None)
        await query.answer()
        uid = query.from_user.id
        chat_id = query.message.chat_id

        if context.user_data.get("at_root", False):
            await show_digest_copy(context, chat_id, uid, with_menu=False)
        context.user_data["at_root"] = False

        await context.bot.send_message(
            chat_id=chat_id,
            text=("Отправь одно сообщение с напоминанием:\n"
                "• Просто текст\n"
                "• Или: Текст DD-MM-YYYY (например, 07-11-2025)\n\n"
                "После отправки вернёшься в меню."),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]])
        )
        context.user_data["awaiting_reminder"] = True
        return


    if data == "rem:edit:start":
        await query.answer()
        # чистим возможный «хвост» от добавления
        context.user_data.pop("awaiting_reminder", None)
        uid = query.from_user.id
        chat_id = query.message.chat_id

        # если пришли из корня — показать копию дайджеста (без меню)
        if context.user_data.get("at_root", False):
            await show_digest_copy(context, chat_id, uid, with_menu=False)
        context.user_data["at_root"] = False

        # ОБЯЗАТЕЛЬНО: получить список прежде чем проверять
        items = storage.list_user_reminders(uid)

        if not items:
            await context.bot.send_message(
                chat_id=chat_id,
                text="У тебя пока нет собственных напоминаний.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]])
            )
            return

        buttons = [[InlineKeyboardButton(r.get("text","(без текста)"),
                                        callback_data=f"editrem:{i}")]
                for i, r in enumerate(items)]
        await context.bot.send_message(
            chat_id=chat_id,
            text="Выбери напоминание:",
            reply_markup=InlineKeyboardMarkup(buttons + [[InlineKeyboardButton("⬅️ Назад", callback_data="menu:root")]])
        )
        return
       

    # обработка выбора конкретного напоминания для редактирования
    if data.startswith("editrem:"):
        await query.answer()
        uid = query.from_user.id
        chat_id = query.message.chat_id

        # получить индекс выбранного напоминания
        try:
            idx = int(data.split(":")[1])
        except (ValueError, IndexError):
            return await query.answer("Ошибка: неверный индекс", show_alert=True)

        # ✅ загрузить список перед использованием
        items = storage.list_user_reminders(uid)

        if idx < 0 or idx >= len(items):
            return await query.answer("Напоминание не найдено", show_alert=True)

        rem = items[idx]
        text = rem.get("text", "(без текста)")
        due = rem.get("due")
        if due:
            text += f" ({due})"

        # сохранить индекс, чтобы потом понимать, что редактируем именно это напоминание
        context.user_data["editing_idx"] = idx

        await context.bot.send_message(
            chat_id=chat_id,
            text=(f"Ты выбрал напоминание:\n\n{text}\n\n"
                "Отправь новое напоминание в формате:\n"
                "• Просто текст\n"
                "• Или: Текст DD-MM-YYYY"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑 Удалить", callback_data=f"editremdel:{idx}")],
                [InlineKeyboardButton("⬅️ Назад", callback_data="rem:edit:start")]
            ])
        )
        return


    # удаление кастомного напоминания из UI
    if data.startswith("editremdel:"):
        await query.answer()
        uid = query.from_user.id
        ok = storage.delete_user_reminder(uid, int(data.split(":")[1]))
        # После удаления сразу перестроим дайджест 
        await rebuild_and_show_digest(context, 
                                      chat_id=query.message.chat_id, 
                                      user_id=uid, with_menu=True)

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
        await safe_edit(query, digest_text, build_main_menu(query.from_user.id))
        context.user_data["at_root"] = True
        return
    
async def on_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = await guard_auth_and_get_uid(update, context)
    if uid is None:
        return
    text = (update.effective_message.text or "").strip()


    is_admin_user = is_admin(update.effective_user.id)

    # --- обработка редактирования существующего напоминания ---
    if context.user_data.get("editing_idx") is not None:
        idx = context.user_data.get("editing_idx")

        body, iso = parse_reminder_input(text)

        # флаг расшаривания при редактировании
        norm_text = _normalize_all(text)
        if is_admin_user:
            share_flag = bool(MARKER_SHARE_RE.search(norm_text))
        else:
            share_flag = True

        ok = storage.update_user_reminder(
            user_id=uid,
            index_in_user_list=idx,
            new_text=body,
            new_due_iso=iso,
            new_share=share_flag,
        )

        context.user_data.pop("editing_idx", None)
        if ok:
            await rebuild_and_show_digest(context, update.effective_chat.id, update.effective_user.id, with_menu=True)
            await update.effective_message.reply_text("Изменено.")
        else:
            await update.effective_message.reply_text("Не удалось изменить.")
        return




    # --- добавление нового напоминания из свободного текста ---
    if context.user_data.get("awaiting_reminder"):
        body, iso = parse_reminder_input(text)
        if not body:
            await update.effective_message.reply_text("Пустое сообщение. Отправь текст напоминания.")
            return

        norm_text = _normalize_all(text)
        if is_admin_user:
            share_flag = bool(MARKER_SHARE_RE.search(norm_text))
        else:
            share_flag = True

        try:
            storage.add_custom_reminder(body, due=iso, user_id=uid, share=share_flag)
        except ValueError as e:
            await update.effective_message.reply_text(str(e))
            return

        context.user_data["awaiting_reminder"] = False
        await rebuild_and_show_digest(context, update.effective_chat.id, update.effective_user.id, with_menu=True)
        await update.effective_message.reply_text("✅ Напоминание добавлено.")
        return




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
    app.add_handler(CommandHandler("testguestdigest", cmd_testguestdigest))
    app.add_handler(CommandHandler("testguestdigesttome", cmd_testguestdigesttome))
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
    app.add_handler(CommandHandler("testguestdigest", cmd_testguestdigest))
    app.add_handler(CommandHandler("testguestdigesttome", cmd_testguestdigesttome))
    app.add_handler(CommandHandler("addreminder", cmd_addreminder))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("clearreminders", cmd_clearreminders))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_message))

    # 5) Запускаем long polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
