import json
from pathlib import Path
from datetime import time, timedelta, datetime
from typing import Optional, Iterable

DATA_PATH = Path("data.json")

DEFAULT_DATA = {
    "chat_id": None,
    "daily_time": "06:30",   # время по умолчанию
    "custom_reminders": [], 

}

# УТИЛИТЫ
def _ensure_file():
    if not DATA_PATH.exists():
        DATA_PATH.write_text(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2), encoding="utf-8")

def _norm_text(s: str) -> str:
    """
    Нормализуем текст для сравнения дублей:
    • убираем пробелы по краям
    • приводим к нижнему регистру
    """
    return (s or "").strip().lower()

def _load() -> dict:
    _ensure_file()
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))

def _save(data: dict) -> None:
    DATA_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

# --- chat_id ---
def get_chat_id() -> int | None:
    return _load().get("chat_id")

def set_chat_id(cid: int) -> None:
    data = _load()
    data["chat_id"] = cid
    _save(data)

# --- daily_time ---
def get_daily_time() -> time:
    raw = _load().get("daily_time", "06:30")
    hh, mm = map(int, raw.split(":"))
    return time(hh, mm)

def set_daily_time(raw: str) -> None:
    # Простейшая валидация HH:MM
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("Неверный формат")
    hh, mm = map(int, parts)
    if not (0 <= hh < 24 and 0 <= mm < 60):
        raise ValueError("Часы/минуты вне диапазона")
    data = _load()
    data["daily_time"] = f"{hh:02d}:{mm:02d}"
    _save(data)

# --- custom_reminders ---
def list_custom_reminders() -> list[dict]:
    """
    Возвращает список напоминаний как словари:
      {"text": "..."} или {"text": "...", "due": "YYYY-MM-DD"}
    Мигрируем старый формат DD-MM-YYYY → YYYY-MM-DD на лету.
    """
    data = _load()
    arr = data.get("custom_reminders", [])
    out: list[dict] = []

    for item in arr:
        if isinstance(item, str):
            out.append({"text": item})
            continue

        if not isinstance(item, dict):
            continue

        text = (item.get("text") or "").strip()
        if not text:
            continue

        due = item.get("due")
        if not due:
            out.append({"text": text})
            continue

        # Пытаемся распознать due:
        # 1) ISO (YYYY-MM-DD)
        try:
            datetime.strptime(due, "%Y-%m-%d")
            out.append({"text": text, "due": due})
            continue
        except ValueError:
            pass

        # 2) Старый формат DD-MM-YYYY → конвертируем в ISO
        try:
            d = datetime.strptime(due, "%d-%m-%Y")
            out.append({"text": text, "due": d.strftime("%Y-%m-%d")})
            continue
        except ValueError:
            # если дата битая — вернём без даты
            out.append({"text": text})

    return out


from datetime import datetime  # убедись, что импорт есть сверху

def _norm_text(s: str) -> str:
    return (s or "").strip().lower()

def add_custom_reminder(text: str, due: str | None = None) -> None:
    """
    Добавляет напоминание. Дата `due` — ISO 'YYYY-MM-DD' (опционально).
    Если дата указана, валидируем её и сохраняем как ISO.
    """
    text = (text or "").strip()
    if not text:
        return

    if due:
        try:
            datetime.strptime(due, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Дата должна быть в формате YYYY-MM-DD")

    data = _load()
    arr = data.get("custom_reminders", [])

    # Нормализуем уже хранящиеся записи (строки → dict)
    normalized: list[dict] = []
    for item in arr:
        if isinstance(item, str):
            normalized.append({"text": item})
        elif isinstance(item, dict):
            normalized.append(item)
    arr = normalized  # дальше работаем только с dict-ами

    # --- Проверка дубля: Тот же нормализованный текст + та же дата (или обе без даты) ---
    key_text = _norm_text(text)
    key_due = due  # ISO или None

    for it in arr:
        if not isinstance(it, dict):
            continue
        it_text = _norm_text(it.get("text", ""))
        it_due = it.get("due")  # ISO или None
        if it_text == key_text and it_due == key_due:
            if key_due:
                # Красиво отформатируем дату для сообщения
                try:
                    nice = datetime.strptime(key_due, "%Y-%m-%d").strftime("%d.%m.%Y")
                except ValueError:
                    nice = key_due
                raise ValueError(f"Такое напоминание уже есть: «{text}» ({nice})")
            else:
                raise ValueError(f"Такое напоминание уже есть: «{text}»")

    # Если дубля нет — добавляем
    new_item = {"text": text}
    if due:
        new_item["due"] = due
    arr.append(new_item)

    data["custom_reminders"] = arr
    _save(data)


def clear_custom_reminders() -> None:
    """Полностью очищает список напоминаний."""
    data = _load()
    data["custom_reminders"] = []
    _save(data)