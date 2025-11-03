from __future__ import annotations
from typing import List
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, base64
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = "token.json"  # fallback для локальной отладки

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

def _load_credentials() -> Credentials:
    b64 = os.getenv("GCAL_TOKEN_B64")
    if b64:
        info = json.loads(base64.b64decode(b64).decode("utf-8"))
        return Credentials.from_authorized_user_info(info, scopes=SCOPES)

    raw = os.getenv("GCAL_TOKEN_JSON")
    if raw:
        info = json.loads(raw)
        return Credentials.from_authorized_user_info(info, scopes=SCOPES)

    # fallback — если захочешь продолжать хранить token.json локально
    if Path(TOKEN_FILE).exists():
        return Credentials.from_authorized_user_file(TOKEN_FILE, scopes=SCOPES)

    raise RuntimeError("Нет GCAL_TOKEN_B64/GCAL_TOKEN_JSON и не найден token.json")

def fetch_today_events(tz_name: str) -> List[str]:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start = datetime(now.year, now.month, now.day, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)

    start_utc = start.astimezone(ZoneInfo("UTC")).isoformat()
    end_utc   = end.astimezone(ZoneInfo("UTC")).isoformat()

    creds = _load_credentials()
    service = build("calendar", "v3", credentials=creds)

    events_result = service.events().list(
        calendarId="primary",
        timeMin=start_utc,
        timeMax=end_utc,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    items = events_result.get("items", [])
    out: List[str] = []
    for e in items:
        title = e.get("summary", "(без названия)")
        start_raw = e["start"].get("dateTime") or e["start"].get("date")
        end_raw   = e["end"].get("dateTime") or e["end"].get("date")
        if start_raw and "T" in start_raw:
            st = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz)
            en = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz)
            out.append(f"{st:%H:%M}–{en:%H:%M} {title}")
        else:
            out.append(f"(весь день) {title}")
    return out

def fetch_events_next_days(tz_name: str, start_offset_days: int, end_offset_days: int) -> List[str]:
    """
    События primary-календаря в окне [сегодня+start_offset_days, сегодня+end_offset_days] включительно.
    Реализовано через полуинтервал [start, end_next), где end_next = (end_day + 1 день).
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    day0 = datetime(now.year, now.month, now.day, 0, 0, tzinfo=tz)

    start = day0 + timedelta(days=start_offset_days)
    end_next = day0 + timedelta(days=end_offset_days + 1)

    # ——— ниже повторяем логику выборки из fetch_today_events, но с timeMin/timeMax:
    creds = _load_credentials()
    service = build("calendar", "v3", credentials=creds)

    time_min = start.astimezone(ZoneInfo("UTC")).isoformat()
    time_max = end_next.astimezone(ZoneInfo("UTC")).isoformat()

    events_result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    items = events_result.get("items", [])
    out: List[str] = []
    for e in items:
        title = e.get("summary", "(без названия)")
        start_raw = e["start"].get("dateTime") or e["start"].get("date")
        end_raw = e["end"].get("dateTime") or e["end"].get("date")

        if start_raw and "T" in start_raw:
            st = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz)
            en = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz)
            out.append(f"{st:%d.%m} {st:%H:%M}–{en:%H:%M} {title}")
        else:
            # событие «на весь день»
            try:
                # если дата без времени — просто показываем дату начала (локально)
                st = datetime.fromisoformat(start_raw) if start_raw else None
                if st and st.tzinfo is None:
                    st = st.replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
                if st:
                    out.append(f"{st:%d.%m} (весь день) {title}")
                else:
                    out.append(f"(весь день) {title}")
            except Exception:
                out.append(f"(весь день) {title}")
    return out