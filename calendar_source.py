from __future__ import annotations

import os
import json
import base64
from pathlib import Path
from typing import List, Dict
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

TOKEN_FILE = "token.json"
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

    if Path(TOKEN_FILE).exists():
        return Credentials.from_authorized_user_file(TOKEN_FILE, scopes=SCOPES)

    raise RuntimeError("Нет GCAL_TOKEN_B64/GCAL_TOKEN_JSON и не найден token.json")


def _list_calendars(service) -> Dict[str, str]:
    """
    Возвращает словарь {calendarId: summary} для всех календарей аккаунта.
    """
    result: Dict[str, str] = {}
    page_token = None
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for item in resp.get("items", []):
            cid = item.get("id")
            name = item.get("summary", "")
            if cid:
                result[cid] = name
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return result


def _effective_calendar_ids(service) -> List[str]:
    """
    Возвращает список календарей, исключая те, чьи имена заданы в GCAL_EXCLUDE_NAMES.
    """
    exclude_raw = os.getenv("GCAL_EXCLUDE_NAMES", "")
    exclude_names = {name.strip().lower() for name in exclude_raw.split(",") if name.strip()}

    calendars = _list_calendars(service)
    kept = [
        cid for cid, name in calendars.items()
        if name.lower() not in exclude_names
    ]
    # На случай, если исключили всё — оставим хотя бы primary
    if not kept:
        return ["primary"]
    return kept


def _sort_key_for_event(e: dict, tz: ZoneInfo) -> datetime:
    """Дата/время начала для сортировки."""
    s = e.get("start", {})
    s_raw = s.get("dateTime") or s.get("date")
    if not s_raw:
        return datetime.max.replace(tzinfo=tz)
    if "T" in s_raw:
        try:
            return datetime.fromisoformat(s_raw.replace("Z", "+00:00")).astimezone(tz)
        except Exception:
            return datetime.max.replace(tzinfo=tz)
    try:
        d = date.fromisoformat(s_raw)
        return datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz)
    except Exception:
        return datetime.max.replace(tzinfo=tz)


def _collect_events(service, calendar_ids: List[str], time_min_iso: str, time_max_iso: str) -> List[dict]:
    items: List[dict] = []
    for cid in calendar_ids:
        resp = service.events().list(
            calendarId=cid,
            timeMin=time_min_iso,
            timeMax=time_max_iso,
            singleEvents=True,
            orderBy="startTime",
        ).execute()
        items.extend(resp.get("items", []))
    return items


def fetch_today_events(tz_name: str) -> List[str]:
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    start = datetime(now.year, now.month, now.day, 0, 0, tzinfo=tz)
    end = start + timedelta(days=1)

    time_min = start.astimezone(ZoneInfo("UTC")).isoformat()
    time_max = end.astimezone(ZoneInfo("UTC")).isoformat()

    creds = _load_credentials()
    service = build("calendar", "v3", credentials=creds)

    cids = _effective_calendar_ids(service)
    items = _collect_events(service, cids, time_min, time_max)
    items.sort(key=lambda e: _sort_key_for_event(e, tz))

    out: List[str] = []
    for e in items:
        title = e.get("summary", "(без названия)")
        start_raw = e["start"].get("dateTime") or e["start"].get("date")
        end_raw = e["end"].get("dateTime") or e["end"].get("date")

        if start_raw and "T" in start_raw:
            st = datetime.fromisoformat(start_raw.replace("Z", "+00:00")).astimezone(tz)
            en = datetime.fromisoformat(end_raw.replace("Z", "+00:00")).astimezone(tz)
            out.append(f"{st:%H:%M}–{en:%H:%M} {title}")
        else:
            out.append(f" {title}")
    return out


def fetch_events_next_days(tz_name: str, start_offset_days: int, end_offset_days: int) -> List[str]:
    """
    События календарей в окне [сегодня+start_offset_days, сегодня+end_offset_days] включительно.
    """
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    day0 = datetime(now.year, now.month, now.day, 0, 0, tzinfo=tz)

    start = day0 + timedelta(days=start_offset_days)
    end_next = day0 + timedelta(days=end_offset_days + 1)

    time_min = start.astimezone(ZoneInfo("UTC")).isoformat()
    time_max = end_next.astimezone(ZoneInfo("UTC")).isoformat()

    creds = _load_credentials()
    service = build("calendar", "v3", credentials=creds)

    cids = _effective_calendar_ids(service)
    items = _collect_events(service, cids, time_min, time_max)
    items.sort(key=lambda e: _sort_key_for_event(e, tz))

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
            try:
                d = date.fromisoformat(start_raw) if start_raw else None
                if d:
                    out.append(f"{d:%d.%m} {title}")
                else:
                    out.append(f" {title}")
            except Exception:
                out.append(f" {title}")
    return out
