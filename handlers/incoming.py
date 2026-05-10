"""
handlers/incoming.py — incoming transaction flow (salary, refund, other income)
"""
from datetime import datetime
import pytz

from config import CHAT_ID, TIMEZONE
import sheets as sh
import telegram_api as tg


async def handle_incoming_webhook(amount: float, description: str, row_num: int):
    sh.set_state(CHAT_ID, {
        "step":        "await_income_type",
        "row_num":     row_num,
        "amount":      amount,
        "description": description,
    })
    buttons = [
        [{"text": "💼 Salary",  "callback_data": f"inc_{row_num}_salary"}],
        [{"text": "↩️ Refund",  "callback_data": f"inc_{row_num}_refund"}],
        [{"text": "📥 Other",   "callback_data": f"inc_{row_num}_other"}],
    ]
    await tg.send_with_buttons(
        f"💰 *+{sh.fmt_amount(amount)} nhận được*\n"
        f"`{description}`\n\n"
        f"Khoản này là gì?",
        buttons,
    )


async def handle_income_type_selected(parts: list[str], message_id: int):
    # callback_data: inc_{rowNum}_{type}
    row_num     = int(parts[1])
    income_type = parts[2]
    state       = sh.get_state(CHAT_ID) or {}
    amount      = state.get("amount", 0)
    description = state.get("description", "")

    if income_type == "refund":
        tz        = pytz.timezone(TIMEZONE)
        month_key = sh.fmt_month(datetime.now(tz))
        buckets   = sh.get_active_buckets(month_key)

        sh.set_state(CHAT_ID, {**state, "step": "await_refund_bucket"})
        buttons = tg.build_bucket_buttons(buckets, f"refbkt_{row_num}")
        await tg.edit_message(message_id, f"↩️ *Refund +{sh.fmt_amount(amount)}* — bucket nào được hoàn?")
        await tg.send_with_buttons("Chọn bucket:", buttons)
        return

    # Salary or Other — finalize immediately, no budget impact
    label = "💼 Salary" if income_type == "salary" else "📥 Other income"
    bucket_id = income_type  # stored as-is, not in budget config → no budget impact
    await _finalize_incoming(row_num, bucket_id, label, amount, message_id)


async def handle_refund_bucket_selected(parts: list[str], message_id: int):
    # callback_data: refbkt_{rowNum}_{bucketId}
    row_num   = int(parts[1])
    bucket_id = "_".join(parts[2:])
    state     = sh.get_state(CHAT_ID) or {}
    amount    = state.get("amount", 0)

    bucket_name = sh.bucket_label(bucket_id)
    await _finalize_incoming(row_num, bucket_id, f"↩️ Refund → {bucket_name}", amount, message_id)


async def _finalize_incoming(row_num: int, bucket_id: str, label: str, amount: float, message_id: int | None):
    sh.finalize_transaction(row_num, bucket_id, label)
    sh.clear_state(CHAT_ID)

    tz        = pytz.timezone(TIMEZONE)
    month_key = sh.fmt_month(datetime.now(tz))

    msg = f"✅ *{label}* logged\n💰 +{sh.fmt_amount(amount)}\n\n"

    # Show updated bucket status only if it's a real budget bucket (refund)
    buckets     = sh.get_active_buckets(month_key)
    is_real_bkt = any(b["id"] == bucket_id for b in buckets)

    if is_real_bkt:
        bkt = sh.get_bucket_status(bucket_id, month_key)
        pct = sh.calc_pct(bkt["spent"], bkt["allocated"])
        bucket_name = sh.bucket_label(bucket_id)
        msg += f"{sh.make_bar(pct)} {pct}%\n"
        msg += f"{bucket_name}: {sh.fmt_amount(bkt['spent'])} / {sh.fmt_amount(bkt['allocated'])}\n"
        msg += f"Remaining: *{sh.fmt_amount(bkt['remaining'])}*"
    else:
        msg += "Đã ghi vào sheet. Không ảnh hưởng budget. 👍"

    if message_id:
        await tg.edit_message(message_id, msg)
    else:
        await tg.send_text(msg)
