import os
from fastapi import FastAPI, Request, Header, HTTPException
from dotenv import load_dotenv
from telegram import Update
from datetime import time as _t
import storage

from app import build_telegram_application, build_digest_text

load_dotenv()

# из .env
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "").rstrip("/")     # например: https://onewv.duckdns.org/organizer
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")         # путь внутри BASE, по умолчанию /webhook
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")             # любая длинная строка
EXPOSE_SET_WEBHOOK = os.getenv("EXPOSE_SET_WEBHOOK", "true").lower() == "true"

WEBHOOK_URL = f"{WEBHOOK_BASE}{WEBHOOK_PATH}" if WEBHOOK_BASE else ""

fastapi_app = FastAPI(title="Personal Organizer Bot")

tg_app = build_telegram_application()

@fastapi_app.on_event("startup")
async def _on_startup():
    await tg_app.initialize()
    await tg_app.start()
    try:
        chat_id = storage.get_chat_id()
        if chat_id:
            base_t = storage.get_daily_time()
            t_with_tz = _t(
                base_t.hour,
                base_t.minute,
                tzinfo=tg_app.bot.defaults.tzinfo if tg_app.bot.defaults else None
            )
            tg_app.job_queue.run_daily(
                callback=lambda ctx: ctx.bot.send_message(
                    chat_id=chat_id, text=build_digest_text()
                ),
                time=t_with_tz,
                name=f"morning_digest_{chat_id}",
                data={"chat_id": chat_id},
            )
    except Exception:
        pass

@fastapi_app.on_event("shutdown")
async def _on_shutdown():
    await tg_app.stop()
    await tg_app.shutdown()

@fastapi_app.get("/healthz")
async def healthz():
    return {"ok": True}

@fastapi_app.head("/healthz")
async def healthz_head():
    # Просто вернуть 200 без тела
    return {}

@fastapi_app.get("/set_webhook")
async def set_webhook():
    if not EXPOSE_SET_WEBHOOK:
        raise HTTPException(status_code=403, detail="disabled")
    if not WEBHOOK_URL or not WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="WEBHOOK_BASE/WEBHOOK_SECRET not set")
    await tg_app.bot.set_webhook(
        url=WEBHOOK_URL,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES
    )
    return {"ok": True, "url": WEBHOOK_URL}

@fastapi_app.post(WEBHOOK_PATH)
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(None)
):
    # Проверяем секрет от Telegram
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="bad secret")
    data = await request.json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.process_update(update)
    return {"ok": True}
