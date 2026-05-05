#!/usr/bin/env python3
import os
import re
import json
import time
import logging
from pathlib import Path

import httpx
from groq import Groq

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TELEGRAM_BOT_TOKEN   = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_TARGET_CHAT = os.environ["TELEGRAM_TARGET_CHAT"]
SOURCE_CHANNELS      = os.environ.get("SOURCE_CHANNELS", "").split(",")
GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
GROQ_MODEL           = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

STATE_FILE   = Path("data/processed_ids.json")
TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT = (
    "Ban la mot bien tap vien tin tuc chuyen nghiep nguoi Viet.\n"
    "Nhiem vu: Viet lai tin tuc theo phong cach ro rang, hap dan bang tieng Viet.\n\n"
    "Quy tac:\n"
    "- Giu nguyen thong tin quan trong (so lieu, ten, thoi gian)\n"
    "- Viet lai tu nhien, khong dich may\n"
    "- Them emoji phu hop o dau moi doan neu can\n"
    "- Do dai: ngan gon, toi da 300 tu\n"
    "- Ket thuc bang hashtag lien quan (toi da 5 hashtag)\n"
    "- Khong ghi nguon, khong ghi URL, khong them loi dan\n"
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
        encoding="utf-8",
    )


def tg_post_json(method, payload):
    url = TELEGRAM_API + "/" + method
    resp = httpx.post(url, json=payload, timeout=30)
    data = resp.json()
    if not data.get("ok"):
        log.warning("TG warning [%s]: %s", method, data)
        return {}
    return data.get("result", {})


def tg_post_multipart(method, fields, files):
    url = TELEGRAM_API + "/" + method
    resp = httpx.post(url, data=fields, files=files, timeout=60)
    data = resp.json()
    if not data.get("ok"):
        log.warning("TG warning [%s]: %s", method, data)
        return {}
    return data.get("result", {})


def fetch_channel_messages(channel, last_id=0):
    name = channel.lstrip("@")
    msgs = []
    try:
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0)"}
        resp = httpx.get("https://t.me/s/" + name, headers=hdrs,
                         timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            log.warning("Cannot fetch %s: HTTP %s", channel, resp.status_code)
            return []
        html = resp.text

        id_re = re.compile(
            r'data-post="' + re.escape(name) + r'/([0-9]+)"',
        )
        txt_re = re.compile(
            r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            re.DOTALL,
        )
        photo_re = re.compile(
            r"background-image:url\('([^']+)'\)",
        )

        ids    = id_re.findall(html)
        texts  = txt_re.findall(html)
        photos = photo_re.findall(html)

        for i, mid_str in enumerate(ids):
            mid = int(mid_str)
            if mid <= last_id:
                continue
            raw = texts[i] if i < len(texts) else ""
            raw = re.sub(r"<br[ \t]*/?>", "\n", raw)
            raw = re.sub(r"<[^>]+>", "", raw).strip()
            photo = photos[i] if i < len(photos) else None
            if raw or photo:
                msgs.append({"id": mid, "text": raw,
                             "photo": photo, "channel": channel})

        log.info("Fetch %s: %d new", channel, len(msgs))
    except Exception as exc:
        log.error("Fetch error %s: %s", channel, exc)
    return sorted(msgs, key=lambda x: x["id"])


def rewrite_with_groq(text):
    if not text:
        return ""
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "Viet lai tin tuc sau:\n\n" + text},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        out = resp.choices[0].message.content.strip()
        log.info("Groq OK (%d chars)", len(out))
        return out
    except Exception as exc:
        log.error("Groq error: %s", exc)
        return ""


def download_image(url):
    try:
        r = httpx.get(url, timeout=20, follow_redirects=True)
        if r.status_code == 200:
            ct  = r.headers.get("content-type", "image/jpeg")
            ext = "jpg" if "jpeg" in ct else ct.split("/")[-1].split(";")[0]
            return r.content, ext
    except Exception as exc:
        log.warning("Image download failed %s: %s", url, exc)
    return None, None


def post_message(caption, photo_url=None):
    cap = caption[:1024] if len(caption) > 1024 else caption

    if photo_url:
        img, ext = download_image(photo_url)
        if img:
            fname = "photo." + (ext or "jpg")
            r = tg_post_multipart(
                "sendPhoto",
                {"chat_id": TELEGRAM_TARGET_CHAT,
                 "caption": cap, "parse_mode": "Markdown"},
                {"photo": (fname, img, "image/jpeg")},
            )
            if r:
                log.info("Posted photo (upload) id=%s", r.get("message_id"))
                return True
        r = tg_post_json("sendPhoto", {
            "chat_id": TELEGRAM_TARGET_CHAT,
            "photo": photo_url,
            "caption": cap,
            "parse_mode": "Markdown",
        })
        if r:
            log.info("Posted photo (url) id=%s", r.get("message_id"))
            return True

    txt = caption[:4096] if len(caption) > 4096 else caption
    r = tg_post_json("sendMessage", {
        "chat_id": TELEGRAM_TARGET_CHAT,
        "text": txt,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    })
    if r:
        log.info("Posted text id=%s", r.get("message_id"))
        return True
    return False


def run_bot():
    log.info("Bot starting | channels=%s | target=%s | model=%s",
             SOURCE_CHANNELS, TELEGRAM_TARGET_CHAT, GROQ_MODEL)
    state = load_state()
    posted = 0
    for channel in SOURCE_CHANNELS:
        channel = channel.strip()
        if not channel:
            continue
        log.info("--- %s ---", channel)
        msgs = fetch_channel_messages(channel, state.get(channel, 0))
        if not msgs:
            log.info("No new messages")
            continue
        for msg in msgs:
            mid   = msg["id"]
            text  = msg["text"]
            photo = msg.get("photo")
            log.info("Msg #%d photo=%s text=%.50s", mid, bool(photo), text)
            caption = rewrite_with_groq(text) if text else ""
            if not caption and not photo:
                log.warning("Skip #%d no content", mid)
                continue
            if post_message(caption, photo):
                state[channel] = max(state.get(channel, 0), mid)
                posted += 1
                save_state(state)
                time.sleep(3)
            else:
                log.warning("Failed #%d", mid)
    log.info("Done. Posted %d.", posted)
    save_state(state)


if __name__ == "__main__":
    run_bot()
