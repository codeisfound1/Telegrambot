#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Telegram News Bot
# Fetch tu channel nguon -> rewrite bang Groq AI -> dang len channel dich

import os
import re
import json
import time
import logging
from pathlib import Path

import httpx
from groq import Groq

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# --- Config tu bien moi truong ---
TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_TARGET_CHAT = os.environ["TELEGRAM_TARGET_CHAT"]
SOURCE_CHANNELS      = os.environ.get("SOURCE_CHANNELS", "").split(",")
GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
GROQ_MODEL           = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

STATE_FILE   = Path("data/processed_ids.json")
TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN

# --- Groq client ---
groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "Ban la mot bien tap vien tin tuc chuyen nghiep nguoi Viet.\n"
    "Nhiem vu: Viet lai tin tuc duoc cung cap theo phong cach ro rang, hap dan va de doc bang tieng Viet.\n\n"
    "Quy tac:\n"
    "- Giu nguyen thong tin quan trong (so lieu, ten, thoi gian)\n"
    "- Viet lai tu nhien, khong dich may\n"
    "- Them emoji phu hop o dau moi doan neu can\n"
    "- Do dai: ngan gon, toi da 300 tu\n"
    "- Ket thuc bang hashtag lien quan (toi da 5 hashtag)\n"
    "- Khong them loi dan nhu 'Duoi day la...' hay 'Tin tuc:'\n"
    "- Tra ve truc tiep noi dung da viet lai"
)


def load_state():
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


def telegram_get(method, params=None):
    url  = TELEGRAM_API + "/" + method
    resp = httpx.get(url, params=params or {}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError("Telegram API error: " + str(data))
    return data["result"]


def telegram_post(method, payload):
    url  = TELEGRAM_API + "/" + method
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        log.warning("Telegram API warning: %s", data)
        return {}
    return data.get("result", {})


def fetch_channel_messages(channel, last_id=0):
    """Scrape tin moi tu public Telegram channel qua t.me/s/<channel>."""
    channel_name = channel.lstrip("@")
    messages = []

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)",
            "Accept": "text/html,application/xhtml+xml",
        }
        url  = "https://t.me/s/" + channel_name
        resp = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)

        if resp.status_code != 200:
            log.warning("Khong the fetch %s: HTTP %s", channel, resp.status_code)
            return []

        html = resp.text

        id_pattern = re.compile(
            r'data-post=["\']' + re.escape(channel_name) + r'/(\d+)["\']',
            re.IGNORECASE
        )
        txt_pattern = re.compile(
            r'class=["\']tgme_widget_message_text[^"\']*["\'][^>]*>(.*?)</div>',
            re.DOTALL | re.IGNORECASE
        )

        ids   = id_pattern.findall(html)
        texts = txt_pattern.findall(html)

        for msg_id_str, raw_html in zip(ids, texts):
            msg_id = int(msg_id_str)
            if msg_id <= last_id:
                continue

            text = re.sub(r'<br\s*/?>', '\n', raw_html)
            text = re.sub(r'<[^>]+>', '', text).strip()

            if text and len(text) > 20:
                messages.append({
                    "id":      msg_id,
                    "text":    text,
                    "channel": channel,
                    "url":     "https://t.me/" + channel_name + "/" + msg_id_str,
                })

        log.info("Fetch %s: %d tin moi", channel, len(messages))

    except Exception as exc:
        log.error("Loi fetch %s: %s", channel, exc)

    return sorted(messages, key=lambda x: x["id"])


def rewrite_with_groq(text, source_url=""):
    """Viet lai noi dung bang Groq AI."""
    try:
        prompt = "Viet lai tin tuc sau:\n\n" + text
        if source_url:
            prompt += "\n\nNguon: " + source_url

        response = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        result = response.choices[0].message.content.strip()
        log.info("Groq rewrite thanh cong (%d ky tu)", len(result))
        return result

    except Exception as exc:
        log.error("Loi Groq API: %s", exc)
        return ""


def post_to_telegram(text, source_url=""):
    """Dang tin len Telegram channel dich."""
    footer  = ("\n\n[Xem nguon](" + source_url + ")") if source_url else ""
    message = text + footer

    if len(message) > 4096:
        message = message[:4090] + "..."

    payload = {
        "chat_id":                  TELEGRAM_TARGET_CHAT,
        "text":                     message,
        "parse_mode":               "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        result = telegram_post("sendMessage", payload)
        if result:
            log.info("Da dang tin (msg_id: %s)", result.get("message_id"))
            return True
        return False

    except Exception as exc:
        log.error("Loi dang Telegram (Markdown): %s", exc)
        try:
            payload["parse_mode"] = None
            payload["text"]       = text
            result = telegram_post("sendMessage", payload)
            return bool(result)
        except Exception as exc2:
            log.error("Loi dang Telegram (plain): %s", exc2)
            return False


def run_bot():
    log.info("Bot khoi dong")
    log.info("Channels nguon: %s", SOURCE_CHANNELS)
    log.info("Channel dich  : %s", TELEGRAM_TARGET_CHAT)
    log.info("Groq model    : %s", GROQ_MODEL)

    state        = load_state()
    posted_count = 0

    for channel in SOURCE_CHANNELS:
        channel = channel.strip()
        if not channel:
            continue

        log.info("--- Xu ly channel: %s ---", channel)
        last_id  = state.get(channel, 0)
        messages = fetch_channel_messages(channel, last_id)

        if not messages:
            log.info("Khong co tin moi tu %s", channel)
            continue

        log.info("Tim thay %d tin moi tu %s", len(messages), channel)

        for msg in messages:
            msg_id  = msg["id"]
            text    = msg["text"]
            src_url = msg.get("url", "")

            log.info("Xu ly tin #%d: %s...", msg_id, text[:60])

            rewritten = rewrite_with_groq(text, src_url)
            if not rewritten:
                log.warning("Bo qua tin #%d (Groq that bai)", msg_id)
                continue

            success = post_to_telegram(rewritten, src_url)

            if success:
                state[channel] = max(state.get(channel, 0), msg_id)
                posted_count  += 1
                save_state(state)
                time.sleep(3)
            else:
                log.warning("Khong dang duoc tin #%d", msg_id)

    log.info("Hoan thanh! Da dang %d tin.", posted_count)
    save_state(state)


if __name__ == "__main__":
    run_bot()
