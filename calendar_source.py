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
