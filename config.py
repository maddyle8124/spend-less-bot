import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN   = os.environ["BOT_TOKEN"]
CHAT_ID     = os.environ["CHAT_ID"]
SHEET_ID    = os.environ["SHEET_ID"]
CREDS_FILE  = os.environ.get("GOOGLE_CREDS", "credentials.json")
TIMEZONE    = "Asia/Ho_Chi_Minh"
DAILY_BUCKET_ID = "daily_spending"

# Sheet tab names
class SHEETS:
    TRANSACTIONS    = "Đầu ra"
    BUDGET_CONFIG   = "Budget Config"
    SUBCATEGORY     = "Sub-category Config"
    MONTHLY_REPORTS = "Monthly Reports"
    BOT_STATE       = "Bot State"
    ARCHIVE         = "Archive"
