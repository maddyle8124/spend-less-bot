"""
handlers/transaction.py — finalize + confirm transaction labeling
"""
from datetime import datetime
import pytz
from config import CHAT_ID, DAILY_BUCKET_ID, TIMEZONE
import sheets as sh
import telegram_api as tg

_LARGE_TX = 100_000  # alert threshold in VND


async def handle_parent_selected(parts: list[str], message_id: int):
    # callback_data: p_{rowNum}_{bucketId}
    row_num   = int(parts[1])
    bucket_id = "_".join(parts[2:])

    await tg.delete_message(message_id)

    subs = sh.get_sub_categories(bucket_id)
    if not subs:
        if bucket_id == "other":
            prev_state = sh.get_state(CHAT_ID) or {}
            sh.set_state(CHAT_ID, {**prev_state, "step": "await_freetext", "row_num": row_num,
                                   "parent_category": bucket_id, "message_id": None})
            await tg.send_text("📦 *Other* — khoản này là gì? _(Gõ mô tả ngắn)_")
        else:
            await _finalize(row_num, bucket_id, "", None)
        return

    prev_state = sh.get_state(CHAT_ID) or {}
    buttons    = tg.build_sub_buttons(subs, f"s_{row_num}")
    buttons.append([{"text": "📦 Other", "callback_data": f"s_{row_num}_other"}])

    resp       = await tg.send_with_buttons(f"✏️ *{sh.bucket_label(bucket_id)}* — what specifically?", buttons)
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
    row_num = int(parts[1])
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


async def handle_void_transaction(parts: list[str], message_id: int):
    """User tapped '🗑️ Void (duplicate)' in audit — remove from all bucket totals."""
    row_num = int(parts[1])
    row = sh.get_transaction_row(row_num)
    amount = sh._parse_amount(row[7]) if len(row) > 7 else 0
    desc   = row[5] if len(row) > 5 else ""

    sh.void_transaction(row_num)
    await tg.edit_message(
        message_id,
        f"🗑️ *Voided* · -{sh.fmt_amount(amount)}\n`{desc}`\n\n"
        f"_This transaction no longer counts toward any bucket._"
    )


async def handle_keeprow(parts: list[str], message_id: int):
    """User tapped '✅ Keep' — acknowledge, do nothing."""
    row_num = int(parts[1])
    row = sh.get_transaction_row(row_num)
    amount = sh._parse_amount(row[7]) if len(row) > 7 else 0
    await tg.edit_message(message_id, f"✅ Kept · -{sh.fmt_amount(amount)}\n_No changes made._")


async def handle_recategorize(parts: list[str], message_id: int):
    """User tapped 'Wrong category?' — reset the row and re-show the bucket picker."""
    import asyncio
    row_num = int(parts[1])
    row = sh.get_transaction_row(row_num)
    amount = sh._parse_amount(row[7]) if len(row) > 7 else 0
    description = row[5] if len(row) > 5 else ""

    tz = pytz.timezone(TIMEZONE)
    raw_date = row[1] if len(row) > 1 else ""
    try:
        tx_date = datetime.fromisoformat(str(raw_date))
        if tx_date.tzinfo is None:
            tx_date = tz.localize(tx_date)
    except Exception:
        tx_date = datetime.now(tz)

    month_key = sh.fmt_month(tx_date)
    buckets   = sh.get_active_buckets(month_key)

    # ── Show UI immediately ───────────────────────────────────
    buttons = tg.build_bucket_buttons(buckets, f"p_{row_num}")
    await tg.edit_message(message_id, f"↩️ *Re-categorize: -{sh.fmt_amount(amount)}*\n`{description}`\n\nWhere did this actually go?")
    await tg.send_with_buttons("Pick a category:", buttons)

    # ── Background: reset sheet + save state ─────────────────
    state = {
        "step":        "await_parent",
        "row_num":     row_num,
        "amount":      amount,
        "description": description,
        "tx_date":     tx_date.isoformat(),
    }
    asyncio.create_task(_recat_background(row_num, state))


async def _recat_background(row_num: int, state: dict):
    sh.reset_transaction_row(row_num)
    sh.set_state(CHAT_ID, state)


async def _finalize(row_num: int, parent_category: str, sub_label: str, message_id: int | None):
    state = sh.get_state(CHAT_ID) or {}
    sh.clear_state(CHAT_ID)

    if message_id:
        await tg.delete_message(message_id)
    sub_msg_id = state.get("sub_msg_id")
    if sub_msg_id:
        await tg.delete_message(sub_msg_id)

    tz          = pytz.timezone(TIMEZONE)
    amount      = state.get("amount") or 0
    tx_date_str = state.get("tx_date")
    tx_date     = datetime.fromisoformat(tx_date_str) if tx_date_str else datetime.now(tz)
    parent_name = sh.bucket_label(parent_category)
    sub_disp    = f" · {sub_label}" if sub_label else ""

    # ── 1. Reply immediately — user doesn't wait ──────────────
    await tg.send_text(f"✅ *{parent_name}{sub_disp}*  💸 -{sh.fmt_amount(amount)}")

    # ── 2. Background: write + compute status + follow-up ─────
    import asyncio
    asyncio.create_task(_finalize_background(row_num, parent_category, sub_label, amount, tx_date, tz))


async def _finalize_background(row_num: int, parent_category: str, sub_label: str,
                                amount: float, tx_date: datetime, tz):
    sh.finalize_transaction(row_num, parent_category, sub_label)

    month_key = sh.fmt_month(tx_date)
    is_daily  = parent_category == DAILY_BUCKET_ID

    if amount >= _LARGE_TX and not is_daily:
        await tg.send_text(
            f"👀 *{sh.fmt_amount(amount)} on {sh.bucket_label(parent_category)}?* "
            f"Not daily spending, so I'll allow it. Low-key proud of you. 💅"
        )

    recat_button = [[{"text": "🔄 Wrong category?", "callback_data": f"recat_{row_num}"}]]

    if is_daily:
        day = sh.get_daily_status(tx_date)
        bkt = sh.get_bucket_status(parent_category, month_key)
        pct = sh.calc_pct(day["spent"], day["cap"])

        msg  = f"{sh.make_bar(pct)} {pct}%\n"
        msg += f"Today: *{sh.fmt_amount(day['spent'])}* / {sh.fmt_amount(day['cap'])}"
        msg += f"  ·  Monthly left: *{sh.fmt_amount(bkt['remaining'])}*\n\n"
        if pct >= 100:
            msg += "🔴 Daily limit BLOWN. Put the wallet down."
        elif pct >= 80:
            msg += f"🟡 Only *{sh.fmt_amount(day['remaining'])}* left today — be wise."
        else:
            msg += f"💪 *{sh.fmt_amount(day['remaining'])}* left today."
    else:
        bkt = sh.get_bucket_status(parent_category, month_key)
        pct = sh.calc_pct(bkt["spent"], bkt["allocated"])
        parent_name = sh.bucket_label(parent_category)

        msg  = f"{sh.make_bar(pct)} {pct}%\n"
        msg += f"{parent_name}: *{sh.fmt_amount(bkt['spent'])}* / {sh.fmt_amount(bkt['allocated'])}"
        msg += f"  ·  Left: *{sh.fmt_amount(bkt['remaining'])}*"
        if bkt["remaining"] <= 0:
            msg += "\n🔴 Bucket EMPTY. You cooked."
        elif pct >= 80:
            msg += "\n🟠 Running low."

    await tg.send_with_buttons(msg, recat_button)
