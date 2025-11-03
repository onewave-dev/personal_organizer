import json
from pathlib import Path
from datetime import time

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
def list_custom_reminders() -> list[str]:
    data = _load()
    return data.get("custom_reminders", [])

def add_custom_reminder(text: str) -> None:
    text = text.strip()
    if not text:
        return
    data = _load()
    arr = data.get("custom_reminders", [])
    arr.append(text)
    data["custom_reminders"] = arr
    _save(data)

def clear_custom_reminders() -> None:
    data = _load()
    data["custom_reminders"] = []
    _save(data)