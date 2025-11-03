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

def _ensure_file():
    if not DATA_PATH.exists():
        DATA_PATH.write_text(json.dumps(DEFAULT_DATA, ensure_ascii=False, indent=2), encoding="utf-8")

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
    Возвращает список напоминаний в виде словарей:
    [{"text": "Купить хлеб", "due": "2025-11-05"}, {"text": "Позвонить"}]
    При чтении старого формата (список строк) преобразует их в {"text": str}.
    """
    data = _load()
    arr = data.get("custom_reminders", [])
    out = []

    # миграция из старого формата (строки → словари)
    for item in arr:
        if isinstance(item, str):
            out.append({"text": item})
        elif isinstance(item, dict):
            # проверим корректность структуры
            text = item.get("text", "").strip()
            if not text:
                continue
            due = item.get("due")
            if due:
                try:
                    datetime.strptime(due, "%d-%m-%Y")
                    out.append({"text": text, "due": due})
                except ValueError:
                    out.append({"text": text})
            else:
                out.append({"text": text})
    return out


def add_custom_reminder(text: str, due: str | None = None) -> None:
    """
    Добавляет напоминание. Дата due (строка 'DD-MM-YYYY') — необязательная.
    Если дата указана, проверяет её формат.
    """
    text = text.strip()
    if not text:
        return

    if due:
        try:
            datetime.strptime(due, "%d-%m-%Y")
        except ValueError:
            raise ValueError("Дата должна быть в формате DD-MM-YYYY")

    data = _load()
    arr = data.get("custom_reminders", [])

    # при первом запуске в старом формате (строки) — преобразуем
    normalized = []
    for item in arr:
        if isinstance(item, str):
            normalized.append({"text": item})
        elif isinstance(item, dict):
            normalized.append(item)
    arr = normalized

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