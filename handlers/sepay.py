"""
handlers/sepay.py — SePay webhook handler
Triggered when a bank transaction arrives.
"""
import uuid
from datetime import datetime
import pytz

from config import CHAT_ID, TIMEZONE, DAILY_BUCKET_ID
import sheets as sh
import telegram_api as tg


async def handle_sepay_webhook(payload: dict):
    data = payload.get("data") if "data" in payload else payload

    # Try all known SePay field names for amount
    raw_amount = (
        data.get("transferAmount")
        or data.get("transfer_amount")
        or data.get("amount")
        or 0
    )
    amount = abs(float(raw_amount))

    tx_type = str(data.get("transferType") or data.get("transfer_type") or data.get("type") or "").lower()
    # Only track outgoing (debit) transactions
    # SePay uses transferType="out" or negative amounts for debits
    is_outgoing = "out" in tx_type or "debit" in tx_type or float(raw_amount) < 0
    if not is_outgoing and float(raw_amount) >= 0:
        # Incoming transfer — skip
        return

    description = (data.get("description") or data.get("content") or "Không có mô tả").strip()
    ref_code    = data.get("referenceCode") or data.get("reference_number") or str(uuid.uuid4())

    # Idempotency: skip duplicate webhook deliveries from SePay
    if sh.tx_exists(ref_code):
        return

    tz = pytz.timezone(TIMEZONE)
    raw_date = data.get("transactionDate") or data.get("transaction_date")
    if raw_date:
        try:
            tx_date = datetime.fromisoformat(str(raw_date))
        except Exception:
            tx_date = datetime.now(tz)
    else:
        tx_date = datetime.now(tz)

    month_key = sh.fmt_month(tx_date)

    # Write row to Sheet first — use returned row number directly
    row_num = sh.append_transaction(tx_date, description, amount, ref_code, month_key)

    buckets = sh.get_active_buckets(month_key)

    if not buckets:
        await tg.send_text(
            f"💸 *Cha-ching! -{sh.fmt_amount(amount)}* just left the building\n"
            f"`{description}`\n\n"
            f"⚠️ No budget set for {month_key} yet 😬\n"
            f"Run /allocate to set one up first!"
        )
        return

    sh.set_state(CHAT_ID, {"step": "await_parent", "row_num": row_num, "amount": amount, "description": description})

    # Big-spend alert fires BEFORE the category picker
    if amount >= 100_000:
        await tg.send_text(
            f"🚨 *{sh.fmt_amount(amount)}?! GIRL.*\n\n"
            f"Do you *want* to be broke? Do you enjoy poverty? "
            f"Your future self is out there somewhere, eating instant noodles, "
            f"watching this notification come in. 😤\n\n"
            f"_...okay fine. What's this even for?_"
        )

    buttons = tg.build_bucket_buttons(buckets, f"p_{row_num}")
    await tg.send_with_buttons(
        f"💸 *Cha-ching! -{sh.fmt_amount(amount)}*\n"
        f"`{description}`\n\n"
        f"Where did this money go? 🤔",
        buttons,
    )
