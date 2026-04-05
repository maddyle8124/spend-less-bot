"""
handlers/reports.py — /status, /today, weekly summary, monthly report
"""
from datetime import datetime, date, timedelta
import pytz

from config import CHAT_ID, TIMEZONE, DAILY_BUCKET_ID
import sheets as sh
import telegram_api as tg


async def send_daily_recap():
    """End-of-day check-in. Called by cron at ~11 PM."""
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    day = sh.get_daily_status(now)

    if day["spent"] == 0:
        await tg.send_text(
            f"🌙 *End of day — {now.strftime('%b %d')}*\n\n"
            f"You spent absolutely *nothing* on daily expenses today. "
            f"Either you're incredibly disciplined or you ate someone else's food. "
            f"Either way — we respect it. 😌\n\n"
            f"Sleep well, you financially responsible human. 💤"
        )
        return

    pct = sh.calc_pct(day["spent"], day["cap"])

    if day["spent"] > day["cap"]:
        overspent = day["spent"] - day["cap"]
        await tg.send_text(
            f"🌙 *End of day — {now.strftime('%b %d')}*\n\n"
            f"Daily spending: *{sh.fmt_amount(day['spent'])}* ({pct}% of limit)\n"
            f"You went over by *{sh.fmt_amount(overspent)}* 😔\n\n"
            f"I'm not angry. I'm just... deeply, profoundly disappointed. "
            f"Your future self is out there somewhere, eating instant noodles, "
            f"wondering where it all went wrong. And it went wrong *today*.\n\n"
            f"So tell me:\n"
            f"1️⃣ What's your excuse this time?\n"
            f"2️⃣ What will you do differently tomorrow?\n\n"
            f"_(Just reply — I'm listening. Reluctantly.)_"
        )
        sh.set_state(CHAT_ID, {"step": "await_daily_excuse", "date": now.strftime("%Y-%m-%d"), "overspent": overspent})
    else:
        remaining = day["cap"] - day["spent"]
        await tg.send_text(
            f"🌙 *End of day — {now.strftime('%b %d')}*\n\n"
            f"Daily spending: *{sh.fmt_amount(day['spent'])}* ({pct}% of limit)\n"
            f"You had *{sh.fmt_amount(remaining)}* left — and you didn't touch it. 🎉\n\n"
            f"I am *so* proud of you. Genuinely. "
            f"Every day like this is a brick in the foundation of your financial freedom. "
            f"Your future self — the one with savings, options, and *choices* — "
            f"is smiling right now.\n\n"
            f"Keep going. You're closer than you think. 💪✨"
        )


async def handle_daily_excuse(text: str, state: dict):
    """User replied to the end-of-day disappointment message."""
    overspent = state.get("overspent", 0)
    sh.clear_state(CHAT_ID)
    await tg.send_text(
        f"Okay. I hear you. 🫂\n\n"
        f"No judgment — just data. You overspent by *{sh.fmt_amount(overspent)}* today. "
        f"But you *acknowledged* it, and that already puts you ahead of most people.\n\n"
        f"Tomorrow is a fresh {sh.fmt_amount(sh.get_daily_status(datetime.now(pytz.timezone(TIMEZONE)))['cap'])}. "
        f"Go get it. 🌅"
    )


async def send_today_status():
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    day = sh.get_daily_status(now)
    pct = sh.calc_pct(day["spent"], day["cap"])

    msg  = f"🍜 *Daily spending — {now.strftime('%b %d')}*\n\n"
    msg += f"{sh.make_bar(pct)} {pct}%\n"
    msg += f"Spent: *{sh.fmt_amount(day['spent'])}* of {sh.fmt_amount(day['cap'])}\n"
    msg += f"Left:  *{sh.fmt_amount(day['remaining'])}*\n\n"

    if pct >= 100:
        msg += "🔴 You're cooked. No more spending today."
    elif pct >= 80:
        msg += f"🟡 Almost there — *{sh.fmt_amount(day['remaining'])}* left. Stay strong."
    elif day['spent'] == 0:
        msg += "✨ Not a single dong spent yet. Hero behavior."
    else:
        msg += f"💪 You're doing great. *{sh.fmt_amount(day['remaining'])}* left to play with."

    await tg.send_text(msg)


async def send_monthly_status():
    tz        = pytz.timezone(TIMEZONE)
    month_key = sh.fmt_month(datetime.now(tz))
    buckets   = sh.get_active_buckets(month_key)

    if not buckets:
        await tg.send_text(f"⚠️ No budget for {month_key} yet.\nRun /allocate to set one up!")
        return

    days_left   = sh.days_left_in_month()
    msg         = f"📊 *Budget check — {month_key}*\n_{days_left} days left in the month_\n\n"
    total_alloc = total_spent = 0

    for b in buckets:
        s   = sh.get_bucket_status(b["id"], month_key)
        pct = sh.calc_pct(s["spent"], b["allocated"])
        ico = "🔴" if pct >= 100 else "🟡" if pct >= 80 else "✅"
        msg += f"{ico} {b['name']}\n{sh.make_bar(pct)} {pct}%\n"
        msg += f"{sh.fmt_amount(s['spent'])} / {sh.fmt_amount(b['allocated'])} · left *{sh.fmt_amount(s['remaining'])}*\n\n"
        total_alloc += b["allocated"]
        total_spent += s["spent"]

    msg += f"─────────────────────\nTotal: {sh.fmt_amount(total_spent)} / {sh.fmt_amount(total_alloc)}"
    await tg.send_text(msg)


async def run_weekly_summary():
    tz  = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    month_key = sh.fmt_month(now)

    dow   = now.weekday()  # Mon=0, Sun=6
    start = (now - timedelta(days=dow)).replace(hour=0, minute=0, second=0, microsecond=0)

    ws   = sh._sheet(sh.S.TRANSACTIONS)
    rows = ws.get_all_values()[1:]

    by_bucket: dict[str, float] = {}
    by_sub:    dict[str, float] = {}

    for r in rows:
        if len(r) < 14 or str(r[13]).upper() != "TRUE":
            continue
        try:
            d = datetime.fromisoformat(str(r[1]))
            if d.tzinfo is None:
                d = tz.localize(d)  # stored as Vietnam local time
            else:
                d = d.astimezone(tz)
        except Exception:
            continue
        if d < start or d > now:
            continue
        parent = r[10] or ""
        sub    = r[11] or "Other"
        amt    = sh._parse_amount(r[7])
        by_bucket[parent] = by_bucket.get(parent, 0) + amt
        key = f"{parent}||{sub}"
        by_sub[key] = by_sub.get(key, 0) + amt

    buckets    = sh.get_active_buckets(month_key)
    week_start = start.strftime("%d/%m")
    week_end   = now.strftime("%d/%m")
    msg = f"📊 *Week recap ({week_start} – {week_end})*\n\n"

    total_week = sum(by_bucket.values())
    if total_week == 0:
        await tg.send_text(f"📊 *Week recap ({week_start} – {week_end})*\n\n✨ Nothing spent this week. You're a legend.")
        return

    for b in buckets:
        week_spent = by_bucket.get(b["id"], 0)
        if week_spent == 0:
            continue
        week_budget = round(b["allocated"] / 4.3)
        pct  = sh.calc_pct(week_spent, week_budget)
        flag = "⚠️" if week_spent > week_budget else "✅"
        msg += f"{b['name']}: *{sh.fmt_amount(week_spent)}* / ~{sh.fmt_amount(week_budget)} {flag}\n"

        if b["id"] == DAILY_BUCKET_ID:
            subs = sh.get_sub_categories(b["id"])
            for sub in subs:
                k   = f"{b['id']}||{sub['label']}"
                amt = by_sub.get(k, 0)
                if amt == 0:
                    continue
                s_pct = sh.calc_pct(amt, week_spent)
                msg += f"  {sub['label']}   {sh.fmt_amount(amt)}  {sh.make_bar(s_pct, 5)}\n"
        msg += "\n"

    msg += f"─────────────────────\nWeek total: *{sh.fmt_amount(total_week)}*"
    await tg.send_text(msg)


async def run_monthly_report():
    tz        = pytz.timezone(TIMEZONE)
    now       = datetime.now(tz)
    month_key = sh.fmt_month(now)
    buckets   = sh.get_active_buckets(month_key)

    if not buckets:
        await tg.send_text(f"⚠️ No budget data for {month_key}. Did you even /allocate?")
        return

    prev_date = datetime(now.year if now.month > 1 else now.year - 1,
                         now.month - 1 if now.month > 1 else 12, 1, tzinfo=tz)
    prev_key  = sh.fmt_month(prev_date)

    ws   = sh._sheet(sh.S.TRANSACTIONS)
    rows = ws.get_all_values()[1:]

    this_txns = [r for r in rows if len(r) >= 15 and r[14] == month_key and str(r[13]).upper() == "TRUE"]
    prev_txns = [r for r in rows if len(r) >= 15 and r[14] == prev_key  and str(r[13]).upper() == "TRUE"]

    total_alloc = total_spent = 0
    results = []
    for b in buckets:
        s    = sh.get_bucket_status(b["id"], month_key)
        prev = sh.get_bucket_status(b["id"], prev_key)
        total_alloc += b["allocated"]
        total_spent += s["spent"]
        pct = sh.calc_pct(s["spent"], b["allocated"])
        results.append({**b, "spent": s["spent"], "remaining": s["remaining"], "pct": pct, "prev_spent": prev["spent"]})

    sub_totals: dict[str, float] = {}
    for r in this_txns:
        k = r[11] or "Other"
        sub_totals[k] = sub_totals.get(k, 0) + sh._parse_amount(r[7])
    top3 = sorted(sub_totals.items(), key=lambda x: -x[1])[:3]

    daily_totals: dict[str, float] = {}
    for r in this_txns:
        try:
            d = datetime.fromisoformat(str(r[1])).astimezone(tz).strftime("%d/%m")
        except Exception:
            continue
        daily_totals[d] = daily_totals.get(d, 0) + sh._parse_amount(r[7])

    heaviest = max(daily_totals.items(), key=lambda x: x[1]) if daily_totals else None

    daily_bkt = next((b for b in buckets if b["id"] == DAILY_BUCKET_ID), None)
    daily_cap = daily_bkt["daily_cap"] if daily_bkt and daily_bkt.get("daily_cap") else 100_000
    good_days = sum(1 for v in daily_totals.values() if v < daily_cap * 0.8)

    month_disp  = now.strftime("%m/%Y")
    surplus     = total_alloc - total_spent
    surplus_pct = sh.calc_pct(surplus, total_alloc) if surplus > 0 else 0

    msg  = f"📅 *MONTHLY AUTOPSY — {month_disp}*\n─────────────────────────────\n\n"
    msg += f"💰 Total spent: *{sh.fmt_amount(total_spent)}* / {sh.fmt_amount(total_alloc)}\n"
    msg += f"Surviving funds: {sh.fmt_amount(surplus)} ({surplus_pct}% intact)\n\n"
    msg += "*BY BUCKET:*\n"
    for b in results:
        flag = " 🔴" if b["pct"] >= 100 else " ⚠️" if b["pct"] >= 80 else " ✅"
        msg += f"{b['name']}  {sh.fmt_amount(b['spent'])} / {sh.fmt_amount(b['allocated'])}  {b['pct']}%{flag}\n"

    if top3:
        msg += "\n*TOP SPENDING CATEGORIES:*\n"
        for i, (sub, amt) in enumerate(top3):
            msg += f"{i+1}. {sub}   {sh.fmt_amount(amt)}\n"

    up   = [b for b in results if b["prev_spent"] > 0 and b["spent"] > b["prev_spent"] * 1.2]
    down = [b for b in results if b["prev_spent"] > 0 and b["spent"] < b["prev_spent"] * 0.8]
    if up or down:
        msg += "\n*VS LAST MONTH:*\n"
        for b in up:
            chg = round(((b["spent"] - b["prev_spent"]) / b["prev_spent"]) * 100)
            msg += f"📈 {b['name']} +{chg}% (oof)\n"
        for b in down:
            chg = round(((b["prev_spent"] - b["spent"]) / b["prev_spent"]) * 100)
            msg += f"📉 {b['name']} -{chg}% (nice)\n"

    if heaviest:
        msg += f"\n📅 Heaviest day: {heaviest[0]} ({sh.fmt_amount(heaviest[1])})\n"
    msg += f"🧾 Transactions: {len(this_txns)}"
    if prev_txns:
        diff = len(this_txns) - len(prev_txns)
        msg += f" ({'+' if diff >= 0 else ''}{diff} vs last month)"

    msg += "\n\n*WINS 💪*\n"
    saving_b = next((b for b in results if b["id"] == "saving"), None)
    if saving_b and saving_b["pct"] >= 100:
        msg += "→ Saving goal hit 100% 🎉\n"
    if good_days > 0:
        msg += f"→ {good_days} days kept Daily under {round(daily_cap * 0.8 / 1000)}k\n"
    if not saving_b or saving_b["pct"] == 0:
        msg += "→ uhh... at least you're alive 🫡\n"

    over_budget = [b for b in results if b["pct"] > 100]
    near_limit  = [b for b in results if 80 <= b["pct"] <= 100]
    msg += "\n*WATCH NEXT MONTH ⚠️*\n"
    if not over_budget and not near_limit:
        msg += "→ All buckets in the green 🎉 Who ARE you?\n"
    else:
        for b in over_budget:
            msg += f"→ {b['name']} blew by {b['pct'] - 100}% 🫠\n"
        for b in near_limit:
            msg += f"→ {b['name']} at {b['pct']}% — skating on thin ice\n"

    msg += "\n─────────────────────────────\nRun /allocate to set next month's budget 🗓️"

    sh.archive_report(month_key, results)
    await tg.send_text(msg)
