"""
main.py — FastAPI entry point
Receives SePay webhooks and Telegram updates via a single /webhook endpoint.
"""
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse
import asyncio

from config import CHAT_ID
import sheets as sh
import telegram_api as tg
from handlers.sepay       import handle_sepay_webhook
from handlers.transaction import handle_parent_selected, handle_sub_selected, handle_freetext_sub, handle_recategorize
from handlers.allocation  import (
    start_monthly_allocation, handle_alloc_callback,
    handle_alloc_amount_input, handle_new_bucket_name, handle_new_bucket_amount,
)
from handlers.reports     import send_monthly_status, send_today_status, run_weekly_summary, run_monthly_report, send_daily_recap, handle_daily_excuse

app = FastAPI(title="maddy tiêu ít thôi")


@app.on_event("startup")
async def on_startup():
    await tg.set_my_commands()


# ─── Webhook endpoint ─────────────────────────────────────────
@app.post("/webhook")
async def webhook(request: Request, bg: BackgroundTasks):
    """
    Single endpoint handling both SePay and Telegram payloads.
    Returns 200 immediately — processing runs in background.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    bg.add_task(_process, body)
    return JSONResponse({"ok": True})          # ← 200 right away, no 302


# ─── Scheduled triggers (call via cron on VPS) ───────────────
@app.post("/trigger/weekly")
async def trigger_weekly():
    asyncio.create_task(run_weekly_summary())
    return {"ok": True}


@app.post("/trigger/monthly-report")
async def trigger_monthly_report():
    asyncio.create_task(run_monthly_report())
    return {"ok": True}


@app.post("/trigger/monthly-allocation")
async def trigger_monthly_allocation():
    asyncio.create_task(start_monthly_allocation())
    return {"ok": True}


@app.post("/trigger/daily-recap")
async def trigger_daily_recap():
    asyncio.create_task(send_daily_recap())
    return {"ok": True}


# ─── Health check ─────────────────────────────────────────────
@app.get("/")
async def health():
    return {"status": "ok", "bot": "maddy tiêu ít thôi"}


# ─── Main dispatcher ──────────────────────────────────────────
async def _process(body: dict):
    try:
        # --- Telegram update ---
        if "update_id" in body:
            if "callback_query" in body:
                await _handle_callback(body["callback_query"])
            elif "message" in body:
                await _handle_message(body["message"])
        # --- SePay webhook ---
        else:
            await handle_sepay_webhook(body)
    except Exception as e:
        import traceback
        print("ERROR:", traceback.format_exc())
        await tg.send_text(f"⚠️ Bot gặp lỗi: `{e}`")


async def _handle_callback(cb: dict):
    await tg.answer_callback(cb["id"])
    data       = cb.get("data") or ""
    message_id = cb["message"]["message_id"]
    parts      = data.split("_")
    prefix     = parts[0]

    if prefix == "p":
        await handle_parent_selected(parts, message_id)
    elif prefix == "s":
        await handle_sub_selected(parts, message_id)
    elif prefix == "al":
        await handle_alloc_callback(parts, message_id)
    elif prefix == "recat":
        await handle_recategorize(parts, message_id)


async def _handle_message(message: dict):
    if message.get("from", {}).get("is_bot"):
        return                                  # ignore bot echoes

    text  = (message.get("text") or "").strip()
    state = sh.get_state(CHAT_ID) or {}

    # Commands take priority
    if text.startswith("/"):
        await _handle_command(text)
        return

    # Multi-step state machine
    step = state.get("step")
    if step == "await_freetext":
        await handle_freetext_sub(text, state)
    elif step == "await_alloc_amount":
        await handle_alloc_amount_input(text, state)
    elif step == "await_new_bucket_name":
        await handle_new_bucket_name(text, state)
    elif step == "await_new_bucket_amount":
        await handle_new_bucket_amount(text, state)
    elif step == "await_daily_excuse":
        await handle_daily_excuse(text, state)
    else:
        await tg.send_text(
            "🤖 *maddy spend less pls*\n\n"
            "/status — how broke are you this month?\n"
            "/today — how much can you still eat today?\n"
            "/allocate — set your budget buckets\n"
            "/weekly — weekly damage report\n"
            "/report — full monthly autopsy"
        )


async def _handle_command(text: str):
    cmd = text.split()[0].lower()
    if   cmd == "/status":   await send_monthly_status()
    elif cmd == "/today":    await send_today_status()
    elif cmd == "/allocate": await start_monthly_allocation()
    elif cmd == "/weekly":   await run_weekly_summary()
    elif cmd == "/report":   await run_monthly_report()
    else:
        await tg.send_text("Unknown command. Try /status, /today, /allocate, /weekly, or /report.")
