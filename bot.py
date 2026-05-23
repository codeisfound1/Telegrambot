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
TELEGRAM_TARGET_CHATS    = [c.strip() for c in os.environ["TELEGRAM_TARGET_CHAT"].split(",") if c.strip()]
TELEGRAM_TARGET_CHATS_EN = [c.strip() for c in os.environ.get("TELEGRAM_TARGET_CHAT_EN", "").split(",") if c.strip()]
SOURCE_CHANNELS      = os.environ.get("SOURCE_CHANNELS", "").split(",")
GROQ_API_KEY         = os.environ["GROQ_API_KEY"]
GROQ_MODEL           = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

STATE_FILE   = Path("data/processed_ids.json")
TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT_VI = (
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

SYSTEM_PROMPT_EN = (
    "You are a professional news editor.\n"
    "Task: Rewrite the given news in clear, engaging English.\n\n"
    "Rules:\n"
    "- Keep all key facts (numbers, names, dates)\n"
    "- Write naturally, not like a translation\n"
    "- Add relevant emojis at the start of paragraphs if suitable\n"
    "- Length: concise, max 100 words\n"
    "- End with up to 5 relevant hashtags\n"
    "- Do not include source, URL, or any introduction\n"
    "- Return only the rewritten content"
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

        # Split HTML into per-message blocks keyed by message ID
        # Each block starts at data-post="channel/ID" and ends before the next
        block_re = re.compile(
            r'data-post="' + re.escape(name) + r'/([0-9]+)"',
        )
        positions = [(m.group(1), m.start()) for m in block_re.finditer(html)]

        for idx, (mid_str, pos) in enumerate(positions):
            mid = int(mid_str)
            if mid <= last_id:
                continue
            # Slice just this message block
            end   = positions[idx + 1][1] if idx + 1 < len(positions) else len(html)
            block = html[pos:end]

            # --- Photo: only from tgme_widget_message_photo_wrap ---
            # This div wraps real post photos, NOT emoji/stickers/icons
            photo_re = re.compile(
                r'tgme_widget_message_photo_wrap[^>]+style="[^"]*'
                r'background-image:url\(\'(https://[^\']+)\'\)',
            )
            pm = photo_re.search(block)
            photo = pm.group(1) if pm else None

            # --- Text ---
            txt_re = re.compile(
                r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
                re.DOTALL,
            )
            tm  = txt_re.search(block)
            raw = ""
            if tm:
                raw = re.sub(r"<br[ \t]*/?>", "\n", tm.group(1))
                raw = re.sub(r"<[^>]+>", "", raw).strip()

            if raw or photo:
                msgs.append({"id": mid, "text": raw,
                             "photo": photo, "channel": channel})
                log.info("  #%d photo=%s text_len=%d", mid, bool(photo), len(raw))

        log.info("Fetch %s: %d new messages", channel, len(msgs))
    except Exception as exc:
        log.error("Fetch error %s: %s", channel, exc)
    return sorted(msgs, key=lambda x: x["id"])


def rewrite_with_groq(text, lang="vi"):
    if not text:
        return ""
    prompt   = ("Viet lai tin tuc sau:\n\n" if lang == "vi"
                else "Rewrite the following news:\n\n") + text
    sys_prompt = SYSTEM_PROMPT_VI if lang == "vi" else SYSTEM_PROMPT_EN
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=600,
        )
        out = resp.choices[0].message.content.strip()
        log.info("Groq OK lang=%s (%d chars)", lang, len(out))
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


def post_message(chat_id, caption, photo_url=None, img_cache=None):
    cap = caption[:1024] if len(caption) > 1024 else caption

    if photo_url:
        img = img_cache
        ext = "jpg"
        if img is None:
            img, ext = download_image(photo_url)
        if img:
            fname = "photo." + (ext or "jpg")
            r = tg_post_multipart(
                "sendPhoto",
                {"chat_id": chat_id,
                 "caption": cap, "parse_mode": "Markdown"},
                {"photo": (fname, img, "image/jpeg")},
            )
            if r:
                log.info("Posted photo (upload) to %s id=%s", chat_id, r.get("message_id"))
                return True, img
        r = tg_post_json("sendPhoto", {
            "chat_id": chat_id,
            "photo": photo_url,
            "caption": cap,
            "parse_mode": "Markdown",
        })
        if r:
            log.info("Posted photo (url) to %s id=%s", chat_id, r.get("message_id"))
            return True, img

    if caption:
        txt = caption[:4096] if len(caption) > 4096 else caption
        r = tg_post_json("sendMessage", {
            "chat_id": chat_id,
            "text": txt,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })
        if r:
            log.info("Posted text to %s id=%s", chat_id, r.get("message_id"))
            return True, None
    return False, None


def run_bot():
    log.info("Bot starting | sources=%s | vi=%s | en=%s | model=%s",
             SOURCE_CHANNELS, TELEGRAM_TARGET_CHATS, TELEGRAM_TARGET_CHATS_EN, GROQ_MODEL)
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

            caption_vi = rewrite_with_groq(text, lang="vi") if text else ""
            caption_en = rewrite_with_groq(text, lang="en") if text else ""

            if not caption_vi and not caption_en and not photo:
                log.warning("Skip #%d no content", mid)
                continue

            img_cache = None
            ok_any = False
            for chat in TELEGRAM_TARGET_CHATS:
                ok, img_cache = post_message(chat, caption_vi, photo, img_cache)
                ok_any = ok_any or ok
                time.sleep(2)
            for chat in TELEGRAM_TARGET_CHATS_EN:
                ok, img_cache = post_message(chat, caption_en, photo, img_cache)
                ok_any = ok_any or ok
                time.sleep(2)

            if ok_any:
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
