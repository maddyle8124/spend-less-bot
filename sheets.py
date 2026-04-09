import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime, date
import pytz
from config import SHEET_ID, CREDS_FILE, TIMEZONE, DAILY_BUCKET_ID
from config import SHEETS as S

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_gc = None
_ss = None
_buckets_cache: dict = {}   # month_key -> list[dict]


def _get_spreadsheet():
    global _gc, _ss
    if _ss is None:
        creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
        _gc = gspread.authorize(creds)
        _ss = _gc.open_by_key(SHEET_ID)
    return _ss


def _sheet(name: str):
    return _get_spreadsheet().worksheet(name)


# ─── Formatting helpers ───────────────────────────────────────
def fmt_month(dt: datetime) -> str:
    tz = pytz.timezone(TIMEZONE)
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    local = dt.astimezone(tz)
    return local.strftime("%Y-%m")


def fmt_amount(n) -> str:
    n = int(round(float(n or 0)))
    return f"{n:,}đ".replace(",", ".")


def _next_row(ws, col: int = 1) -> int:
    """Find the next empty row by checking the given column (1-indexed).
    col_values stops at the last non-empty cell, so len() + 1 = next row.
    """
    return len(ws.col_values(col)) + 1


def _parse_amount(val) -> float:
    """Parse a sheet cell amount value safely.
    Handles: "50000", "50,000", "50.000" (VN), "50000.0" (float repr).
    VND has no decimal places so we round to int.
    """
    s = str(val).strip()
    if not s:
        return 0.0
    # If it looks like a plain float already (no thousands separator style)
    # e.g. "50000.0" — just parse directly
    try:
        return float(s)
    except ValueError:
        pass
    # Remove thousands separators (commas or dots used as thousand sep)
    # Determine separator style: if there's both , and ., the last one is decimal
    if "," in s and "." in s:
        # e.g. "50,000.00" → decimal is "."
        s = s.replace(",", "")
    elif "," in s:
        # e.g. "50,000" → comma is thousands sep
        s = s.replace(",", "")
    elif "." in s and s.count(".") == 1:
        # Could be decimal "50000.0" or thousands "50.000"
        # For VND: if digits after dot < 3, treat as decimal; else thousands
        parts = s.split(".")
        if len(parts[1]) == 3:
            s = s.replace(".", "")  # thousands separator
        # else leave as-is (decimal)
    return float(s) if s else 0.0


def calc_pct(spent: float, total: float) -> int:
    """Integer percentage; shows at least 1% when there is any spending."""
    if not total:
        return 0
    pct = int(spent / total * 100)
    if pct == 0 and spent > 0:
        pct = 1
    return min(pct, 100)


def make_bar(pct: int, length: int = 10) -> str:
    filled = round(min(pct, 100) / (100 / length))
    return "█" * filled + "░" * (length - filled)


def days_left_in_month() -> int:
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    last = date(now.year, now.month + 1, 1) if now.month < 12 else date(now.year + 1, 1, 1)
    return (last - now.date()).days


# ─── Bucket helpers ───────────────────────────────────────────
def get_active_buckets(month_key: str, force_refresh: bool = False) -> list[dict]:
    global _buckets_cache
    if not force_refresh and month_key in _buckets_cache:
        return _buckets_cache[month_key]
    ws = _sheet(S.BUDGET_CONFIG)
    rows = ws.get_all_values()[1:]  # skip header
    result = []
    for r in rows:
        if len(r) < 6:
            continue
        if r[0] == month_key and str(r[5]).upper() == "TRUE":
            result.append({
                "id":        r[1],
                "name":      r[2],
                "allocated": _parse_amount(r[3]),
                "daily_cap": _parse_amount(r[4]) or None,
            })
    _buckets_cache[month_key] = result
    return result


def invalidate_buckets_cache():
    global _buckets_cache
    _buckets_cache = {}


def get_bucket_status(bucket_id: str, month_key: str) -> dict:
    buckets = get_active_buckets(month_key)
    bkt = next((b for b in buckets if b["id"] == bucket_id), None)
    alloc = bkt["allocated"] if bkt else 0

    ws = _sheet(S.TRANSACTIONS)
    rows = ws.get_all_values()[1:]
    spent = 0
    for r in rows:
        if len(r) < 15:
            continue
        if r[14] != month_key:
            continue
        if r[10] != bucket_id:
            continue
        if str(r[13]).upper() != "TRUE":
            continue
        spent += _parse_amount(r[7])
    return {"spent": spent, "allocated": alloc, "remaining": alloc - spent}


def get_daily_status(tx_date: datetime) -> dict:
    tz = pytz.timezone(TIMEZONE)
    if tx_date.tzinfo is None:
        tx_date = pytz.utc.localize(tx_date)
    local = tx_date.astimezone(tz)
    date_str_1 = local.strftime("%Y-%m-%d")
    date_str_2 = local.strftime("%d/%m/%Y")
    date_str_3 = local.strftime("%m/%d/%Y")
    
    month_key = local.strftime("%Y-%m")
    
    buckets = get_active_buckets(month_key)
    bkt = next((b for b in buckets if b["id"] == DAILY_BUCKET_ID), None)
    cap = bkt["daily_cap"] if (bkt and bkt["daily_cap"]) else 100000

    ws = _sheet(S.TRANSACTIONS)
    rows = ws.get_all_values()[1:]
    spent = 0
    for r in rows:
        if len(r) < 14 or str(r[13]).upper() != "TRUE":
            continue
        if r[10] != DAILY_BUCKET_ID:
            continue
        r_str = str(r[1])
        if date_str_1 in r_str or date_str_2 in r_str or date_str_3 in r_str:
            spent += _parse_amount(r[7])
    
    remaining = cap - spent
    return {
        "spent": spent,
        "cap": cap,
        "remaining": remaining
    }


def get_sub_categories(bucket_id: str) -> list[dict]:
    ws = _sheet(S.SUBCATEGORY)
    rows = ws.get_all_values()[1:]
    res = []
    for r in rows:
        if len(r) >= 4 and r[0] == bucket_id and str(r[3]).upper() == "TRUE":
            res.append({"key": r[1], "label": r[2]})
    return res


def get_sub_label(bucket_id: str, key: str) -> str:
    subs = get_sub_categories(bucket_id)
    found = next((s for s in subs if s["key"] == key), None)
    return found["label"] if found else key


def bucket_label(bucket_id: str) -> str:
    tz = pytz.timezone(TIMEZONE)
    month_key = datetime.now(tz).strftime("%Y-%m")
    buckets = get_active_buckets(month_key)
    found = next((b for b in buckets if b["id"] == bucket_id), None)
    return found["name"] if found else bucket_id


def get_parent_from_sheet(row_num: int) -> str:
    ws = _sheet(S.TRANSACTIONS)
    return ws.cell(row_num, 11).value or ""


def save_custom_sub(bucket_id: str, label: str):
    import unicodedata, re
    # normalize key
    key = unicodedata.normalize("NFD", label.lower())
    key = re.sub(r"[\u0300-\u036f]", "", key)
    key = re.sub(r"[^a-z0-9_]", "", key)
    
    ws = _sheet(S.SUBCATEGORY)
    rows = ws.get_all_values()[1:]
    for r in rows:
        if len(r) >= 2 and r[0] == bucket_id and r[1] == key:
            return  # already exists
    next_row = _next_row(ws, col=1)
    ws.update(f"A{next_row}:D{next_row}", [[bucket_id, key, f"📦 {label}", "TRUE"]])


def find_budget_row(month_key: str, bucket_id: str) -> bool:
    ws = _sheet(S.BUDGET_CONFIG)
    rows = ws.get_all_values()[1:]
    for r in rows:
        if len(r) >= 2 and r[0] == month_key and r[1] == bucket_id:
            return True
    return False


def get_default_buckets() -> list[dict]:
    return [
        {"id": "daily_spending",   "name": "🛒 Daily Spending",   "daily_cap": 100000},
        {"id": "saving",           "name": "🏦 Saving",            "daily_cap": None},
        {"id": "work_supplements", "name": "💼 Work Supplements",  "daily_cap": None},
        {"id": "clothes",          "name": "👗 Clothes",           "daily_cap": None},
        {"id": "subscription",     "name": "📱 Subscription",      "daily_cap": None},
    ]


# -----------------------------------------------------
# Transaction Write
# -----------------------------------------------------
_processed_refs: dict[str, float] = {}  # ref_code → timestamp

def tx_exists(ref_code: str) -> bool:
    """In-memory dedup for SePay webhook retries (5-minute window).
    Does NOT check the sheet — avoids false positives from SePay's native
    Google Sheets integration writing rows before our bot does.
    """
    import time
    now = time.time()
    # Prune entries older than 5 minutes
    expired = [k for k, v in _processed_refs.items() if now - v > 300]
    for k in expired:
        del _processed_refs[k]
    if ref_code in _processed_refs:
        return True
    _processed_refs[ref_code] = now
    return False


def append_transaction(tx_date, description, amount, ref_code, month_key) -> int:
    ws = _sheet(S.TRANSACTIONS)
    # Use col B (date) to find the next truly empty row.
    # col_values truncates at the last non-empty cell, so len() = last used row.
    next_row = len(ws.col_values(2)) + 1  # col B is 1-indexed as 2

    row_data = [
        "",           # A: ID
        str(tx_date), # B: Ngày giao dịch
        "", "", "",   # C, D, E
        description,  # F: Nội dung
        "Tiền ra",    # G: Loại
        amount,       # H: Số tiền  ← explicit, never shifts columns
        ref_code,     # I: Mã tham chiếu
        0,            # J: Lũy kế
        "",           # K: Parent Category
        "",           # L: Sub-category
        "FALSE",      # M: Is Daily Spending
        "FALSE",      # N: Confirmed
        month_key,    # O: Month
    ]
    ws.update(f"A{next_row}:O{next_row}", [row_data])
    return next_row


def finalize_transaction(row_num: int, parent_category: str, sub_label: str):
    ws = _sheet(S.TRANSACTIONS)
    is_daily = "TRUE" if parent_category == DAILY_BUCKET_ID else "FALSE"
    
    ws.update_cell(row_num, 11, parent_category)
    ws.update_cell(row_num, 12, sub_label)
    ws.update_cell(row_num, 13, is_daily)
    ws.update_cell(row_num, 14, "TRUE")


def get_transaction_row(row_num: int) -> list:
    ws = _sheet(S.TRANSACTIONS)
    return ws.row_values(row_num)


def reset_transaction_row(row_num: int):
    """Clear finalized columns so a transaction can be re-categorized."""
    ws = _sheet(S.TRANSACTIONS)
    ws.update(f"K{row_num}:N{row_num}", [["", "", "FALSE", "FALSE"]])


# ─── Budget Config write ──────────────────────────────────────
def write_budget_row(month_key: str, bucket: dict):
    ws = _sheet(S.BUDGET_CONFIG)
    rows = ws.get_all_values()[1:]  # skip header
    for i, r in enumerate(rows):
        if len(r) >= 2 and r[0] == month_key and r[1] == bucket["id"]:
            row_num = i + 2  # +1 for 1-based index, +1 for header row
            ws.update(f"C{row_num}:F{row_num}", [[
                bucket["name"],
                bucket.get("allocated", 0),
                bucket.get("daily_cap") or "",
                "TRUE",
            ]])
            return
    # Row doesn't exist — append
    next_row = _next_row(ws, col=1)
    ws.update(f"A{next_row}:H{next_row}", [[
        month_key,
        bucket["id"],
        bucket["name"],
        bucket.get("allocated", 0),
        bucket.get("daily_cap") or "",
        "TRUE",
        "telegram",
        "",
    ]])


# ─── Bot State ─────────────────────────────────────────────────
def get_state(chat_id: str) -> dict | None:
    import json
    ws = _sheet(S.BOT_STATE)
    rows = ws.get_all_values()[1:]
    for r in rows:
        if len(r) >= 2 and str(r[0]) == str(chat_id):
            try:
                return json.loads(r[1])
            except Exception:
                return None
    return None


def set_state(chat_id: str, obj: dict):
    from datetime import datetime
    import json
    ws = _sheet(S.BOT_STATE)
    rows = ws.get_all_values()[1:]
    payload = json.dumps(obj, ensure_ascii=False)
    now_str = datetime.utcnow().isoformat()
    
    for i, r in enumerate(rows):
        if len(r) >= 1 and str(r[0]) == str(chat_id):
            ws.update_cell(i + 2, 2, payload) # Row index is 1-based + 1 for header
            ws.update_cell(i + 2, 3, now_str)
            return

    ws.append_row([str(chat_id), payload, now_str])


def clear_state(chat_id: str):
    set_state(chat_id, {})


# ─── Monthly Report archive ───────────────────────────────────
def archive_report(month_key: str, results: list[dict]):
    ws = _sheet(S.MONTHLY_REPORTS)
    from datetime import datetime
    now = datetime.utcnow().isoformat()
    for b in results:
        ws.append_row([month_key, b["name"], b["allocated"], b["spent"], b["remaining"], f"{b['pct']}%", now])
