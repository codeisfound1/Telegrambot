# 🤖 Telegram News Bot

Bot tự động **theo dõi Telegram channel nguồn** → **viết lại bằng Groq AI** → **đăng lên channel của bạn**.  
Chạy hoàn toàn miễn phí trên **GitHub Actions** (mỗi 30 phút một lần).

-----

## ✨ Tính năng

|Tính năng               |Chi tiết                                     |
|------------------------|---------------------------------------------|
|📡 Theo dõi nhiều channel|Cấu hình danh sách channel nguồn tùy ý       |
|🧠 Rewrite bằng AI       |Dùng Groq API (llama-3.3-70b hoặc model khác)|
|📢 Đăng Telegram         |Tự động post lên channel/group đích          |
|⏱️ Tự động hóa           |GitHub Actions chạy mỗi 30 phút, miễn phí    |
|💾 State tracking        |Lưu ID tin đã xử lý, không đăng trùng        |
|🔄 Cập nhật dễ           |Thêm/bớt channel nguồn chỉ cần sửa Secret    |

-----

## 📋 Yêu cầu

- Tài khoản **GitHub** (miễn phí)
- **Telegram Bot Token** (miễn phí từ @BotFather)
- **Groq API Key** (miễn phí tại [console.groq.com](https://console.groq.com))
- Channel nguồn phải là **public** (hoặc bot được thêm vào làm admin)

-----

## 🚀 Hướng dẫn cài đặt

### Bước 1 — Tạo Telegram Bot

1. Mở Telegram, tìm **@BotFather**
1. Gửi lệnh `/newbot`
1. Đặt tên và username cho bot
1. Sao chép **Bot Token** (dạng `1234567890:ABC...`)

### Bước 2 — Thêm bot vào channel đích

1. Vào **Settings** của channel đích
1. Chọn **Administrators** → **Add Administrator**
1. Tìm username bot vừa tạo
1. Cấp quyền **Post Messages**

> 💡 Để lấy Chat ID của channel private:  
> Gọi `https://api.telegram.org/bot<TOKEN>/getUpdates` sau khi gửi 1 tin vào channel.

### Bước 3 — Lấy Groq API Key

1. Đăng ký tại [console.groq.com](https://console.groq.com)
1. Vào **API Keys** → **Create API Key**
1. Sao chép key (dạng `gsk_...`)

### Bước 4 — Fork và cấu hình repo

1. **Fork** repo này về tài khoản của bạn
1. Vào **Settings** → **Secrets and variables** → **Actions**
1. Thêm các **Secrets** sau:

|Secret Name           |Giá trị                                           |
|----------------------|--------------------------------------------------|
|`TELEGRAM_BOT_TOKEN`  |Token từ @BotFather                               |
|`TELEGRAM_TARGET_CHAT`|`@your_channel` hoặc `-100xxxxxxxxx`              |
|`SOURCE_CHANNELS`     |`@guguwatcher,@channel2` (phân cách bằng dấu phẩy)|
|`GROQ_API_KEY`        |Key từ console.groq.com                           |

1. *(Tùy chọn)* Thêm **Variable**: `GROQ_MODEL` = `llama-3.3-70b-versatile`

### Bước 5 — Bật GitHub Actions

1. Vào tab **Actions** trong repo
1. Nhấn **“I understand my workflows, go ahead and enable them”**
1. Chọn workflow **“🤖 Telegram News Bot”**
1. Nhấn **“Enable workflow”**

-----

## 🔧 Chạy thủ công để test

1. Tab **Actions** → chọn **“🤖 Telegram News Bot”**
1. Nhấn **“Run workflow”** → **“Run workflow”**
1. Xem log để kiểm tra kết quả

-----

## 📁 Cấu trúc project

```
telegram-news-bot/
├── .github/
│   └── workflows/
│       └── bot.yml          # GitHub Actions workflow
├── data/
│   └── processed_ids.json   # ID tin đã xử lý (auto-generated)
├── bot.py                   # Script chính
├── requirements.txt         # Dependencies Python
├── .env.example             # Mẫu biến môi trường
├── .gitignore
└── README.md
```

-----

## ⚙️ Tùy chỉnh nâng cao

### Thay đổi tần suất chạy

Sửa file `.github/workflows/bot.yml`, phần `cron`:

```yaml
schedule:
  - cron: "*/30 * * * *"   # Mỗi 30 phút (mặc định)
  - cron: "0 * * * *"      # Mỗi giờ
  - cron: "0 8,12,18 * * *" # Lúc 8h, 12h, 18h mỗi ngày
```

> ⚠️ GitHub không chạy workflow nhanh hơn 5 phút một lần.

### Thay đổi phong cách viết lại

Sửa biến `SYSTEM_PROMPT` trong `bot.py`:

```python
SYSTEM_PROMPT = """Bạn là chuyên gia tài chính...
Viết lại tin với phong cách phân tích chuyên sâu..."""
```

### Thêm/bớt channel nguồn

Chỉ cần cập nhật Secret `SOURCE_CHANNELS` trong GitHub:

```
@guguwatcher,@cryptonews,@technews_vn
```

### Đổi model Groq

Cập nhật Variable `GROQ_MODEL` hoặc Secret trong GitHub:

|Model                    |Tốc độ    |Chất lượng|
|-------------------------|----------|----------|
|`llama-3.3-70b-versatile`|Trung bình|⭐⭐⭐⭐⭐     |
|`llama-3.1-8b-instant`   |Rất nhanh |⭐⭐⭐       |
|`mixtral-8x7b-32768`     |Nhanh     |⭐⭐⭐⭐      |
|`gemma2-9b-it`           |Nhanh     |⭐⭐⭐       |

-----

## 🛠️ Chạy local để test

```bash
# 1. Clone repo
git clone https://github.com/your-username/telegram-news-bot.git
cd telegram-news-bot

# 2. Tạo môi trường ảo
python -m venv .venv
source .venv/bin/activate    # Linux/Mac
# .venv\Scripts\activate    # Windows

# 3. Cài dependencies
pip install -r requirements.txt

# 4. Cấu hình biến môi trường
cp .env.example .env
# Sửa file .env với thông tin thật

# 5. Chạy bot
export $(cat .env | xargs)   # Linux/Mac
python bot.py
```

-----

## 🐛 Xử lý lỗi thường gặp

### Bot không fetch được tin từ channel nguồn

- Kiểm tra channel có **public** không (URL dạng `t.me/channel_name`)
- Một số channel có chặn bot/scraping → thử channel khác

### Telegram trả về lỗi `chat not found`

- Đảm bảo bot đã được thêm vào channel đích làm **admin**
- Kiểm tra `TELEGRAM_TARGET_CHAT` đúng định dạng: `@username` hoặc `-100xxx`

### Groq lỗi `rate limit`

- Groq free tier giới hạn ~30 request/phút
- Bot đã có delay 3 giây giữa mỗi tin
- Nếu cần xử lý nhiều tin cùng lúc, tăng delay hoặc nâng gói Groq

### GitHub Actions không chạy

- Kiểm tra workflow đã được **Enable** chưa (tab Actions)
- Repo phải có ít nhất 1 commit gần đây (GitHub tắt scheduled workflows sau 60 ngày không hoạt động)

-----

## 📊 Giới hạn miễn phí

|Dịch vụ         |Giới hạn miễn phí              |
|----------------|-------------------------------|
|GitHub Actions  |2,000 phút/tháng (~67 lần/ngày)|
|Groq API        |~14,400 request/ngày           |
|Telegram Bot API|30 message/giây                |

-----

## 🔐 Bảo mật

- **Không bao giờ** commit file `.env` lên GitHub
- Tất cả API key được lưu trong **GitHub Secrets** (được mã hóa)
- `.gitignore` đã loại trừ `.env`

-----

## 📜 License

MIT License — Sử dụng tự do, vui lòng giữ credit.
