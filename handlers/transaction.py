"""
handlers/transaction.py — finalize + confirm transaction labeling
"""
from datetime import datetime
import pytz
from config import CHAT_ID, DAILY_BUCKET_ID, TIMEZONE
import sheets as sh
import telegram_api as tg

_LARGE_TX = 100_000  # alert threshold in VND
_MAX_ROW   = 100_000  # sanity cap — no real sheet will have 100k rows


def _parse_row_num(parts: list[str], min_parts: int = 2) -> int | None:
    """Return validated row number from callback parts, or None if invalid."""
    if len(parts) < min_parts:
        return None
    raw = parts[1]
    if not raw.isdigit():
        return None
    n = int(raw)
    if n < 2 or n > _MAX_ROW:   # row 1 is the header
        return None
    return n


async def handle_parent_selected(parts: list[str], message_id: int):
    # callback_data: p_{rowNum}_{bucketId}
    row_num = _parse_row_num(parts, min_parts=3)
    if row_num is None:
        return
    bucket_id = "_".join(parts[2:])

    sh.finalize_transaction(row_num, bucket_id, "")

    subs = sh.get_sub_categories(bucket_id)
    if not subs:
        await _finalize(row_num, bucket_id, "", message_id)
        return

    prev_state = sh.get_state(CHAT_ID) or {}
    buttons    = tg.build_sub_buttons(subs, f"s_{row_num}")
    buttons.append([{"text": "📦 Other", "callback_data": f"s_{row_num}_other"}])

    await tg.edit_message(message_id, f"✏️ *{sh.bucket_label(bucket_id)}* — what specifically?")
    resp       = await tg.send_with_buttons("Pick a sub-category:", buttons)
    sub_msg_id = resp.get("result", {}).get("message_id")

    sh.set_state(CHAT_ID, {
        **prev_state,
        "step":            "await_sub",
        "row_num":         row_num,
        "parent_category": bucket_id,
        "message_id":      message_id,
        "sub_msg_id":      sub_msg_id,
    })


async def handle_sub_selected(parts: list[str], message_id: int):
    # callback_data: s_{rowNum}_{subKey}
    row_num = _parse_row_num(parts, min_parts=3)
    if row_num is None:
        return
    sub_key = "_".join(parts[2:])
    state   = sh.get_state(CHAT_ID)
    parent  = (state or {}).get("parent_category") or sh.get_parent_from_sheet(row_num)

    if sub_key == "other":
        sh.set_state(CHAT_ID, {**(state or {}), "step": "await_freetext", "row_num": row_num, "message_id": message_id})
        await tg.send_text("📝 What is this exactly? _(just type it)_")
        return

    sub_display = sh.get_sub_label(parent, sub_key)
    await _finalize(row_num, parent, sub_display, message_id)


async def handle_freetext_sub(text: str, state: dict):
    row_num = state["row_num"]
    parent  = state.get("parent_category") or sh.get_parent_from_sheet(row_num)
    sh.save_custom_sub(parent, text)
    await _finalize(row_num, parent, f"📦 {text}", state.get("message_id"))


async def handle_recategorize(parts: list[str], message_id: int):
    """User tapped 'Wrong category?' — reset the row and re-show the bucket picker."""
    row_num = _parse_row_num(parts, min_parts=2)
    if row_num is None:
        return
    row = sh.get_transaction_row(row_num)
    amount = sh._parse_amount(row[7]) if len(row) > 7 else 0
    description = row[5] if len(row) > 5 else ""

    # Reset finalized columns so the transaction isn't double-counted
    sh.reset_transaction_row(row_num)

    tz = pytz.timezone(TIMEZONE)
    month_key = sh.fmt_month(datetime.now(tz))
    buckets = sh.get_active_buckets(month_key)

    sh.set_state(CHAT_ID, {"step": "await_parent", "row_num": row_num, "amount": amount, "description": description})

    buttons = tg.build_bucket_buttons(buckets, f"p_{row_num}")
    await tg.edit_message(message_id, f"↩️ *Re-categorize: -{sh.fmt_amount(amount)}*\n`{description}`\n\nWhere did this actually go?")
    await tg.send_with_buttons("Pick a category:", buttons)


async def _finalize(row_num: int, parent_category: str, sub_label: str, message_id: int | None):
    sh.finalize_transaction(row_num, parent_category, sub_label)

    state = sh.get_state(CHAT_ID) or {}
    sh.clear_state(CHAT_ID)

    # Delete sub-category picker message if present
    sub_msg_id = state.get("sub_msg_id")
    if sub_msg_id:
        await tg.delete_message(sub_msg_id)

    tz = pytz.timezone(TIMEZONE)

    amount = state.get("amount") or 0
    if not amount:
        row    = sh.get_transaction_row(row_num)
        amount = sh._parse_amount(row[7]) if len(row) > 7 else 0

    tx_date_str = state.get("tx_date")
    tx_date     = datetime.fromisoformat(tx_date_str) if tx_date_str else datetime.now(tz)
    month_key   = sh.fmt_month(tx_date)
    is_daily    = parent_category == DAILY_BUCKET_ID
    parent_name = sh.bucket_label(parent_category)
    sub_disp    = f" · {sub_label}" if sub_label else ""

    # ── Big-spend alert (non-daily only — daily alert fires in sepay.py) ──
    if amount >= _LARGE_TX and not is_daily:
        await tg.send_text(
            f"👀 *{sh.fmt_amount(amount)} on {parent_name}?* Not daily spending, so I'll allow it. Low-key proud of you. 💅"
        )

    # ── Confirmation message ───────────────────────────────────
    msg = f"✅ *Logged: {parent_name}{sub_disp}*\n💸 -{sh.fmt_amount(amount)}\n\n"

    if is_daily:
        day = sh.get_daily_status(tx_date)
        pct = sh.calc_pct(day["spent"], day["cap"])
        bkt = sh.get_bucket_status(parent_category, month_key)

        msg += f"{sh.make_bar(pct)} {pct}%\n"
        msg += f"Today: {sh.fmt_amount(day['spent'])} spent of {sh.fmt_amount(day['cap'])}\n"
        msg += f"Monthly bucket left: *{sh.fmt_amount(bkt['remaining'])}*\n\n"

        if pct >= 100:
            msg += "🔴 Daily limit BLOWN. Put the wallet down."
        elif pct >= 80:
            msg += f"🟡 Getting spicy — only *{sh.fmt_amount(day['cap'] - day['spent'])}* left today!"
        else:
            msg += f"💪 Still got *{sh.fmt_amount(day['cap'] - day['spent'])}* to burn today. Be wise."
    else:
        bkt = sh.get_bucket_status(parent_category, month_key)
        pct = sh.calc_pct(bkt["spent"], bkt["allocated"])

        msg += f"{sh.make_bar(pct)} {pct}%\n"
        msg += f"{parent_name}: {sh.fmt_amount(bkt['spent'])} / {sh.fmt_amount(bkt['allocated'])}\n"
        msg += f"Remaining: *{sh.fmt_amount(bkt['remaining'])}*"

        if bkt["remaining"] <= 0:
            msg += "\n🔴 This bucket is EMPTY. You cooked."
        elif pct >= 80:
            msg += "\n🟠 Running low — tread carefully!"

    recat_button = [[{"text": "🔄 Wrong category?", "callback_data": f"recat_{row_num}"}]]
    await tg.send_with_buttons(msg, recat_button)
