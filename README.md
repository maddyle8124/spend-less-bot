# 💸 Spend Less Bot

A Telegram bot that hooks into your bank (via [SePay](https://sepay.vn)) and makes you feel bad about every purchase. In a loving way.

**Features:**
- Auto-categorize transactions as they arrive from SePay webhook
- Monthly budget buckets with daily spending caps
- Incoming transaction flow (salary / refund / other)
- `/log` for manual transaction entry
- `/status`, `/today`, `/weekly`, `/report`, `/audit` commands
- All data stored in Google Sheets — no database needed

---

## Stack

- **FastAPI** — webhook receiver (SePay + Telegram share one `/webhook` endpoint)
- **Google Sheets** — storage via gspread
- **Telegram Bot API** — notifications + inline keyboard UI
- **VPS + nginx + systemd** — deployment
- **Cloudflare** — SSL termination (if VPS uses self-signed cert)

---

## Setup

### 1. Clone & install

```bash
git clone https://github.com/maddyle8124/spend-less-bot.git
cd spend-less-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment variables

Create a `.env` file:

```env
BOT_TOKEN=your_telegram_bot_token
CHAT_ID=your_telegram_chat_id
SHEET_ID=your_google_spreadsheet_id
GOOGLE_CREDS=credentials.json
```

### 3. Google Sheets credentials

1. Create a [Google Cloud service account](https://console.cloud.google.com/iam-admin/serviceaccounts)
2. Enable **Google Sheets API** and **Google Drive API**
3. Download the JSON key → save as `credentials.json` in the project root
4. Share your spreadsheet with the service account email

### 4. Google Sheets structure

Your spreadsheet needs these tabs (exact names matter):

| Tab name | Purpose |
|---|---|
| `Đầu ra` | All transactions |
| `Budget Config` | Monthly bucket allocations |
| `Sub-category Config` | Sub-categories per bucket |
| `Bot State` | Conversation state (managed by bot) |
| `Monthly Reports` | Archived monthly reports |

**Transactions sheet columns (A→R):**

`ID · Date · · · · Description · Type · Amount · RefCode · Running total · Category · Sub-category · IsDaily · Confirmed · Month · Notes · Occasion · Direction`

**Budget Config columns (A→H):**

`Month · BucketID · Name · Allocated · DailyCap · Active · Source · Notes`

### 5. Set up Telegram webhook

```bash
curl -X POST "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url": "https://yourdomain.com/webhook"}'
```

### 6. Run locally

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Deploy on VPS (Ubuntu + nginx + systemd)

### nginx config

```nginx
server {
    listen 80;
    server_name yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

> Use Cloudflare proxy (orange cloud) if your VPS has a self-signed cert — Cloudflare handles SSL termination for free.

### systemd service

Create `/etc/systemd/system/maddy-bot.service`:

```ini
[Unit]
Description=maddy spend less pls
After=network.target

[Service]
User=root
WorkingDirectory=/root/maddy-bot
EnvironmentFile=/root/maddy-bot/.env
ExecStart=/root/maddy-bot/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable maddy-bot
systemctl start maddy-bot
```

### Cron jobs (scheduled reports)

```bash
crontab -e
```

Add (adjust times to your timezone):

```
# Daily recap — 10PM ICT
0 15 * * * curl -s -X POST http://localhost:8000/trigger/daily-recap

# Weekly summary — Monday 8AM ICT
0 1 * * 1 curl -s -X POST http://localhost:8000/trigger/weekly

# Monthly report — 1st of month 8AM ICT
0 1 1 * * curl -s -X POST http://localhost:8000/trigger/monthly-report

# Monthly allocation — 1st of month 8AM ICT (runs after report)
5 1 1 * * curl -s -X POST http://localhost:8000/trigger/monthly-allocation
```

### SePay webhook

In SePay dashboard → Webhook → set URL to `https://yourdomain.com/webhook`

---

## Bot commands

| Command | Description |
|---|---|
| `/status` | Monthly budget overview |
| `/today` | Daily spending status |
| `/allocate` | Set up budget buckets for the month |
| `/weekly` | Weekly spending summary |
| `/report` | Full monthly breakdown |
| `/audit` | Find uncategorized transactions |
| `/log <amount> <description>` | Log a transaction manually |

---

## Architecture notes

- SePay and Telegram share a single `/webhook` endpoint — differentiated by payload shape
- Bot state (pending category selection) stored in Google Sheets `Bot State` tab
- Optimistic UI: bot replies to Telegram immediately, then writes to Sheets in background
- Google Sheets OAuth connection warmed up on startup to avoid cold-start delay on first transaction
- Direction-aware accounting: refunds logged with `direction=in`, spent = `sum(out) - sum(in)` per bucket
