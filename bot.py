#!/usr/bin/env python3
#Telegram News Bot - Fetch từ channel nguồn, rewrite bằng Groq AI, đăng lên channel đích

import os
import json
import time
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx
from groq import Groq

# ─── Logging ────────────────────────────────────────────────────────────────

logging.basicConfig(
level=logging.INFO,
format=”%(asctime)s [%(levelname)s] %(message)s”,
datefmt=”%Y-%m-%d %H:%M:%S”,
)
log = logging.getLogger(**name**)

# ─── Config từ biến môi trường ───────────────────────────────────────────────

TELEGRAM_BOT_TOKEN   = os.environ[“TELEGRAM_BOT_TOKEN”]       # Bot token từ @BotFather
TELEGRAM_TARGET_CHAT = os.environ[“TELEGRAM_TARGET_CHAT”]     # @channel_username hoặc -100xxx
SOURCE_CHANNELS      = os.environ.get(“SOURCE_CHANNELS”, “”).split(”,”)  # vd: @guguwatcher,@otherchan
GROQ_API_KEY         = os.environ[“GROQ_API_KEY”]
GROQ_MODEL           = os.environ.get(“GROQ_MODEL”, “llama-3.3-70b-versatile”)

# File lưu ID tin nhắn đã xử lý (dùng trong GitHub Actions qua artifact/cache)

STATE_FILE = Path(“data/processed_ids.json”)

TELEGRAM_API = f”https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}”

# ─── Groq client ─────────────────────────────────────────────────────────────

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = “”“Bạn là một biên tập viên tin tức chuyên nghiệp người Việt.
Nhiệm vụ: Viết lại tin tức được cung cấp theo phong cách rõ ràng, hấp dẫn và dễ đọc bằng tiếng Việt.

Quy tắc:

- Giữ nguyên thông tin quan trọng (số liệu, tên, thời gian)
- Viết lại tự nhiên, không dịch máy
- Thêm emoji phù hợp ở đầu mỗi đoạn nếu cần
- Độ dài: ngắn gọn, tối đa 300 từ
- Kết thúc bằng hashtag liên quan (tối đa 5 hashtag)
- Không thêm lời dẫn như “Dưới đây là…” hay “Tin tức:”
- Trả về trực tiếp nội dung đã viết lại”””

def load_state() -> dict:
“”“Tải danh sách ID đã xử lý.”””
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
if STATE_FILE.exists():
try:
return json.loads(STATE_FILE.read_text())
except Exception:
pass
return {}

def save_state(state: dict) -> None:
“”“Lưu danh sách ID đã xử lý.”””
STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

def telegram_get(method: str, params: dict = None) -> dict:
“”“Gọi Telegram Bot API (GET).”””
url = f”{TELEGRAM_API}/{method}”
resp = httpx.get(url, params=params or {}, timeout=30)
resp.raise_for_status()
data = resp.json()
if not data.get(“ok”):
raise RuntimeError(f”Telegram API error: {data}”)
return data[“result”]

def telegram_post(method: str, payload: dict) -> dict:
“”“Gọi Telegram Bot API (POST).”””
url = f”{TELEGRAM_API}/{method}”
resp = httpx.post(url, json=payload, timeout=30)
resp.raise_for_status()
data = resp.json()
if not data.get(“ok”):
log.warning(f”Telegram API warning: {data}”)
return {}
return data.get(“result”, {})

def fetch_channel_updates(channel: str, offset: int = 0, limit: int = 20) -> list[dict]:
“””
Lấy tin nhắn từ channel/group mà bot đã được thêm vào.
Dùng getUpdates nếu channel là private, hoặc getChat + forwardedMessages.

```
Lưu ý: Bot phải là THÀNH VIÊN của channel nguồn để đọc được.
Phương pháp đơn giản nhất: bot forward tin về private chat, hoặc
dùng userbot (Telethon) - xem README để biết thêm.

Ở đây dùng getUpdates để lấy tin nhắn forwarded/posted đến bot.
"""
try:
    updates = telegram_get("getUpdates", {
        "offset": offset,
        "limit": limit,
        "timeout": 0,
        "allowed_updates": json.dumps(["channel_post", "message"])
    })
    return updates if isinstance(updates, list) else []
except Exception as e:
    log.error(f"Lỗi fetch updates: {e}")
    return []
```

def fetch_via_json_endpoint(channel: str, last_id: int = 0) -> list[dict]:
“””
Fetch tin từ public channel qua endpoint t.me/s/<channel> (scraping).
Hoạt động với public channel không cần userbot.
“””
channel_name = channel.lstrip(”@”)
messages = []

```
try:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
        "Accept": "application/json",
    }
    url = f"https://t.me/s/{channel_name}"
    resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
    
    if resp.status_code != 200:
        log.warning(f"Không thể fetch {channel}: HTTP {resp.status_code}")
        return []
    
    # Parse HTML để lấy tin nhắn (basic scraping)
    import re
    
    # Lấy message IDs và text từ HTML
    msg_pattern = re.compile(
        r'data-post=["\']' + re.escape(channel_name) + r'/(\d+)["\'].*?'
        r'<div class=["\']tgme_widget_message_text[^"\']*["\'][^>]*>(.*?)</div>',
        re.DOTALL
    )
    
    for match in msg_pattern.finditer(resp.text):
        msg_id = int(match.group(1))
        if msg_id <= last_id:
            continue
        
        # Clean HTML tags
        raw_html = match.group(2)
        text = re.sub(r'<br\s*/?>', '\n', raw_html)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.strip()
        
        if text and len(text) > 20:  # Bỏ qua tin quá ngắn
            messages.append({
                "id": msg_id,
                "text": text,
                "channel": channel,
                "url": f"https://t.me/{channel_name}/{msg_id}"
            })
    
    log.info(f"Fetch {channel}: {len(messages)} tin mới")
    
except Exception as e:
    log.error(f"Lỗi fetch {channel}: {e}")

return sorted(messages, key=lambda x: x["id"])
```

def rewrite_with_groq(text: str, source_url: str = “”) -> str:
“”“Viết lại nội dung tin tức bằng Groq AI.”””
try:
prompt = f”Viết lại tin tức sau:\n\n{text}”
if source_url:
prompt += f”\n\nNguồn: {source_url}”

```
    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": prompt}
        ],
        temperature=0.7,
        max_tokens=600,
    )
    result = response.choices[0].message.content.strip()
    log.info(f"Groq rewrite thành công ({len(result)} ký tự)")
    return result
    
except Exception as e:
    log.error(f"Lỗi Groq API: {e}")
    return ""
```

def post_to_telegram(text: str, source_url: str = “”) -> bool:
“”“Đăng tin lên Telegram channel đích.”””
try:
# Thêm attribution nếu có URL nguồn
footer = f”\n\n🔗 [Xem nguồn]({source_url})” if source_url else “”
message = text + footer

```
    # Giới hạn 4096 ký tự của Telegram
    if len(message) > 4096:
        message = message[:4090] + "..."
    
    payload = {
        "chat_id": TELEGRAM_TARGET_CHAT,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    
    result = telegram_post("sendMessage", payload)
    if result:
        log.info(f"✅ Đã đăng tin (msg_id: {result.get('message_id')})")
        return True
    return False
    
except Exception as e:
    log.error(f"Lỗi đăng Telegram: {e}")
    # Thử lại không có Markdown nếu lỗi parse
    try:
        payload["parse_mode"] = "HTML"
        payload["text"] = text.replace("*", "").replace("_", "")
        result = telegram_post("sendMessage", payload)
        return bool(result)
    except Exception:
        return False
```

def run_bot() -> None:
“”“Vòng lặp chính của bot.”””
log.info(“🤖 Bot khởi động”)
log.info(f”📡 Channels nguồn: {SOURCE_CHANNELS}”)
log.info(f”📢 Channel đích: {TELEGRAM_TARGET_CHAT}”)
log.info(f”🧠 Groq model: {GROQ_MODEL}”)

```
state = load_state()
posted_count = 0

for channel in SOURCE_CHANNELS:
    channel = channel.strip()
    if not channel:
        continue
    
    log.info(f"\n--- Xử lý channel: {channel} ---")
    
    last_id = state.get(channel, 0)
    messages = fetch_via_json_endpoint(channel, last_id)
    
    if not messages:
        log.info(f"Không có tin mới từ {channel}")
        continue
    
    log.info(f"Tìm thấy {len(messages)} tin mới từ {channel}")
    
    for msg in messages:
        msg_id  = msg["id"]
        text    = msg["text"]
        src_url = msg.get("url", "")
        
        log.info(f"Xử lý tin #{msg_id}: {text[:60]}...")
        
        # Rewrite bằng Groq
        rewritten = rewrite_with_groq(text, src_url)
        if not rewritten:
            log.warning(f"Bỏ qua tin #{msg_id} (Groq thất bại)")
            continue
        
        # Đăng lên Telegram
        success = post_to_telegram(rewritten, src_url)
        
        if success:
            state[channel] = max(state.get(channel, 0), msg_id)
            posted_count += 1
            save_state(state)
            
            # Delay tránh spam / rate limit
            time.sleep(3)
        else:
            log.warning(f"Không đăng được tin #{msg_id}")

log.info(f"\n✅ Hoàn thành! Đã đăng {posted_count} tin.")
save_state(state)
```

if **name** == “**main**”:
run_bot()
