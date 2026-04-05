# maddy tiêu ít thôi — Tài liệu kỹ thuật cá nhân

> Viết cho chính mình — để sau này đọc lại hiểu, hoặc khi muốn giải thích cho người khác.

---

## 1. Sản phẩm này làm gì?

Bot Telegram tự động theo dõi chi tiêu ngân hàng của mình.

Luồng hoạt động đơn giản nhất:

```
Mình chuyển tiền / mua đồ
    → Ngân hàng báo cho SePay
        → SePay gọi vào bot
            → Bot hỏi mình "tiền này đi đâu?"
                → Mình bấm chọn danh mục
                    → Bot ghi vào Google Sheets + tính % ngân sách còn lại
```

---

## 2. Các thành phần trong hệ thống

```
┌─────────────────────────────────────────────────────────┐
│                      VPS (máy chủ)                       │
│                                                          │
│   main.py  ←──── SePay webhook                          │
│      │      ←──── Telegram message/button               │
│      │                                                   │
│      ├── handlers/sepay.py       (xử lý giao dịch mới)  │
│      ├── handlers/transaction.py (chọn danh mục)         │
│      ├── handlers/allocation.py  (set ngân sách)         │
│      ├── handlers/reports.py     (báo cáo)               │
│      │                                                   │
│      └── sheets.py  ←──────────────→  Google Sheets     │
│                                                          │
│   telegram_api.py  ←──────────────→  Telegram Bot       │
│   config.py        (các biến bí mật, đọc từ .env)       │
└─────────────────────────────────────────────────────────┘
```

---

## 3. VPS là gì và nó hoạt động thế nào?

VPS (Virtual Private Server) = một cái máy tính thuê trên cloud, chạy 24/7.

Cái bot này sống trên VPS đó. Khi VPS tắt → bot tắt → không nhận được giao dịch.

**Cách bot tự khởi động lại khi VPS reboot:**

Bot chạy như một *systemd service* tên `maddy-bot`. Systemd là hệ thống quản lý tiến trình của Linux — nó tự restart bot nếu bot crash, và tự chạy lại khi máy khởi động.

```bash
# Xem trạng thái bot
systemctl status maddy-bot

# Restart bot (sau khi deploy code mới)
systemctl restart maddy-bot

# Xem log realtime
journalctl -u maddy-bot -f
```

**Cron jobs — lịch tự động:**

Bot có 4 tác vụ tự động chạy theo giờ (cấu hình bằng `crontab`):

| Lịch | Tác vụ |
|------|--------|
| 23:00 mỗi ngày | Recap cuối ngày |
| 14:00 Chủ nhật | Báo cáo tuần |
| 02:00 ngày 1 mỗi tháng | Hỏi set ngân sách tháng mới |
| 14:00 ngày 28–31 (ngày cuối tháng) | Báo cáo tháng |

---

## 4. Code Python — mỗi file làm gì?

| File | Vai trò |
|------|---------|
| `main.py` | "Cổng vào" — nhận webhook từ SePay và Telegram, điều phối đến đúng handler |
| `config.py` | Đọc các biến bí mật từ file `.env` (token, ID...) |
| `sheets.py` | Mọi thứ liên quan đến Google Sheets: đọc, ghi, tính toán |
| `telegram_api.py` | Gửi tin nhắn, nút bấm, xóa tin nhắn qua Telegram |
| `handlers/sepay.py` | Nhận giao dịch từ SePay, ghi vào sheet, gửi nút chọn danh mục |
| `handlers/transaction.py` | Xử lý khi mình bấm chọn danh mục / sửa danh mục sai |
| `handlers/allocation.py` | Luồng set ngân sách theo tháng |
| `handlers/reports.py` | `/status`, `/today`, `/weekly`, báo cáo tháng, recap ngày |

---

## 5. Luồng xử lý một giao dịch (chi tiết)

```
[Ngân hàng báo SePay]
        ↓
[SePay POST đến: https://<vps>/webhook]
        ↓
main.py → handlers/sepay.py
  - Kiểm tra trùng lặp (in-memory, 5 phút)
  - Ghi dòng mới vào sheet "Đầu ra" (cột A–O)
  - Nếu >= 100.000đ → gửi cảnh báo 🚨
  - Gửi nút chọn danh mục (bucket)
        ↓
[Mình bấm chọn danh mục trên Telegram]
        ↓
main.py → handlers/transaction.py → handle_parent_selected()
  - Nếu có sub-category → hỏi tiếp
  - Nếu không → _finalize()
        ↓
_finalize()
  - Ghi category vào cột K, L, M, N của sheet
  - Tính % ngân sách đã dùng
  - Gửi xác nhận + nút "Wrong category?"
```

---

## 6. Google Sheets kết nối thế nào?

Sheets không kết nối trực tiếp với ngân hàng — chỉ có bot mới ghi vào.

**Cách kết nối:**
1. Tạo một *Service Account* trên Google Cloud (giống như tạo một "user robot")
2. Download file `credentials.json` của user robot đó
3. Share Google Sheet với email của user robot đó (như share bình thường)
4. Bot dùng thư viện `gspread` + file credentials để đọc/ghi sheet

**Các tab trong sheet:**

| Tab | Dữ liệu |
|-----|---------|
| `Đầu ra` | Mọi giao dịch chi tiêu |
| `Budget Config` | Ngân sách theo tháng và danh mục |
| `Sub-category Config` | Các nhãn con (vd: "grab", "bún bò"...) |
| `Bot State` | Lưu trạng thái hội thoại đang dở |
| `Monthly Reports` | Lưu báo cáo tháng đã tổng kết |

---

## 7. Các biến bí mật (`.env`)

File `.env` trên VPS chứa các thông tin nhạy cảm. **Không bao giờ commit file này lên GitHub.**

```env
BOT_TOKEN=...        # Token của Telegram bot (lấy từ @BotFather)
CHAT_ID=...          # ID Telegram của mình (để bot chỉ nhắn cho mình)
SHEET_ID=...         # ID của Google Sheet (lấy từ URL)
GOOGLE_CREDS=credentials.json
```

---

## 8. Bảo mật — những thứ cần biết

Bạn mình nói đúng. Đây là các điểm cần chú ý:

### 🔴 Rủi ro cao

**IP VPS bị lộ:**
- VPS có IP public — ai cũng có thể thử kết nối vào
- Hiện tại bot chỉ chạy trên port 8000 (localhost), không expose ra ngoài trực tiếp
- SePay gọi vào qua một reverse proxy (hoặc trực tiếp port 8000 nếu đang mở) — cần xác nhận cấu hình này

**Không có auth trên webhook:**
- Bất kỳ ai biết URL `/webhook` đều có thể gửi request giả lên bot
- Giải pháp đơn giản: thêm secret token vào header và bot kiểm tra trước khi xử lý

**SSH bằng password:**
- Hiện tại SSH vào VPS bằng password → dễ bị brute force
- Nên chuyển sang SSH key (an toàn hơn nhiều, lại không cần nhớ password)

### 🟡 Rủi ro trung bình

**`credentials.json` trên VPS:**
- File này cho phép đọc/ghi Google Sheet của mình
- Nếu ai lấy được file này → họ có thể đọc toàn bộ lịch sử chi tiêu
- Đảm bảo file này chỉ có quyền đọc cho user chạy bot: `chmod 600 credentials.json`

**Thông tin ngân hàng:**
- Bot KHÔNG lưu số tài khoản, số thẻ, hay mật khẩu ngân hàng
- Chỉ nhận `amount` + `description` từ SePay — không có thông tin đăng nhập

### 🟢 Đang ổn

- Bot chỉ nhắn tin với đúng `CHAT_ID` của mình (người khác nhắn bot không phản hồi)
- Dedup giao dịch trong bộ nhớ — tránh ghi trùng
- Không lưu password hay secret nào trong code

---

## 9. Muốn share / onboard người khác dùng

Bot này hiện **personal** — chỉ dành cho một người (hardcode `CHAT_ID`). Để người khác dùng cần:

### Cách đơn giản: chạy instance riêng

Mỗi người tự setup một bản riêng:

1. **Tạo Telegram Bot mới** qua @BotFather → lấy `BOT_TOKEN` mới
2. **Tạo Google Sheet mới** với cấu trúc tab giống bản gốc
3. **Tạo Service Account** trên Google Cloud → download `credentials.json`
4. **Thuê VPS** (hoặc chạy local với ngrok)
5. Copy code lên, tạo file `.env` với thông tin của họ
6. Kết nối SePay với bank của họ → trỏ webhook URL về VPS của họ

### Cách scale: multi-user (phức tạp hơn)

Nếu muốn một server phục vụ nhiều người:
- Cần thay `CHAT_ID` hardcode thành database
- Mỗi user có Sheet riêng, credentials riêng
- Cần thêm hệ thống đăng ký (/start command)
- Đây là bước cần Docker + kiến trúc rõ ràng hơn (như bạn mình đề cập)

**Hiện tại chưa cần làm bước này** — bot đang chạy ổn cho personal use.

---

## 10. Muốn deploy bản cập nhật (workflow hiện tại)

```bash
# Từ máy Mac, copy file đã sửa lên VPS
scp đường/dẫn/file.py root@<IP_VPS>:/root/maddy-bot/

# SSH vào VPS
ssh root@<IP_VPS>

# Restart bot để áp dụng code mới
systemctl restart maddy-bot

# Xem log để kiểm tra không có lỗi
journalctl -u maddy-bot -f
```

---

## 11. Khi bot gặp sự cố — checklist

| Triệu chứng | Kiểm tra |
|-------------|----------|
| Không nhận được tin nhắn giao dịch | `systemctl status maddy-bot` → bot có đang chạy không? |
| Bot crash liên tục | `journalctl -u maddy-bot -f` → đọc lỗi |
| Ghi sai số tiền vào sheet | Xem log dòng `DEBUG append_transaction` |
| Recap không đúng giờ | `crontab -l` → kiểm tra giờ (VPS dùng múi giờ +07) |
| Allocation không lưu | Xem log dòng `DEBUG write_budget_row` |

---

*Tài liệu này không chứa IP, token, hay bất kỳ thông tin nhạy cảm nào — an toàn để lưu hoặc share.*
