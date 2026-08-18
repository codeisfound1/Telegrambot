#!/usr/bin/env python3
import os
import re
import json
import time
import logging
from pathlib import Path

import httpx
from groq import Groq
from groq import RateLimitError, APIStatusError, APIConnectionError

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
GROQ_MODEL           = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Tried in order after GROQ_MODEL. Override via GROQ_FALLBACK_MODELS="model1,model2".
GROQ_FALLBACK_MODELS = [
    m.strip() for m in os.environ.get(
        "GROQ_FALLBACK_MODELS",
        "qwen/qwen3.6-27b,openai/gpt-oss-20b,llama-3.1-8b-instant",
    ).split(",") if m.strip()
]

STATE_FILE   = Path("data/processed_ids.json")
TELEGRAM_API = "https://api.telegram.org/bot" + TELEGRAM_BOT_TOKEN

groq_client = Groq(api_key=GROQ_API_KEY)

SYSTEM_PROMPT_VI = (
    "Ban la mot bien tap vien tin tuc chuyen nghiep nguoi Viet.\n"
    "Nhiem vu: Viet lai tin tuc theo phong cach ro rang, hap dan bang tieng Viet.\n\n"
    "Quy tac:\n"
    "- Giu nguyen thong tin quan trong (so lieu, ten, thoi gian)\n"
    "- Viet lai tu nhien, khong dich may\n"
    "_ Luon viet co dau dung chinh ta tieng Viet\n"
    "- Them emoji phu hop o dau moi doan neu can\n"
    "- Do dai: ngan gon, toi da 100 tu\n"
    "- Ket thuc bang hashtag lien quan (toi da 5 hashtag)\n"
    "- Khong ghi nguon, khong ghi URL, khong them loi dan\n"
    "- KHONG duoc suy nghi thanh tieng, khong giai thich, khong dan nhap\n"
    "- Tra ve DUY NHAT noi dung da viet lai, khong them gi khac truoc/sau"
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
    "- Do not show reasoning, thinking, or any preamble/explanation\n"
    "- Return ONLY the rewritten content, nothing before or after it"
)

# Patterns stripped from raw source text when used as a fallback caption
# (no Groq call succeeded). Removes links, website/channel promo lines, and ads.
_URL_RE       = re.compile(r'https?://\S+|www\.\S+', re.IGNORECASE)
_TG_MENTION_RE = re.compile(r'(?<!\w)@[A-Za-z0-9_]{4,}')
_AD_LINE_RE = re.compile(
    r'(?im)^.*('
    r'website|fanpage|zalo|telegram|kenh|kênh|tham gia|dang ky|đăng ký|'
    r'theo doi|theo dõi|group|nhom|nhóm|link\s|sponsor|quang cao|quảng cáo|'
    r'subscribe|follow us|join (our|us)'
    r').*$'
)
_MULTI_BLANK_RE = re.compile(r'\n{3,}')


def clean_source_text(text):
    """Strip links, mentions, and promo/ad lines from raw source text so it
    can be posted as-is when Groq rewriting is unavailable."""
    if not text:
        return ""
    t = _URL_RE.sub("", text)
    t = _TG_MENTION_RE.sub("", t)
    t = _AD_LINE_RE.sub("", t)
    t = _MULTI_BLANK_RE.sub("\n\n", t)
    t = "\n".join(line.rstrip() for line in t.split("\n"))
    return t.strip()


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


_PREAMBLE_RE = re.compile(
    r'(?im)^\s*(here(\'|\u2019)s|day la|đây là|noi dung|nội dung|ban viet lai|'
    r'bai viet lai|rewritten (version|text|content)|sure[,!]?)\b[^\n]*:?\s*\n+'
)


def _strip_thinking(text):
    """Strip any residual <think>...</think> block (belt-and-suspenders on
    top of reasoning_effort/reasoning_format) and drop a leading preamble
    line if the model added one despite the system prompt."""
    text = re.sub(r'<think>.*?</think>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'```[a-zA-Z]*\n?|```', '', text)  # strip stray code fences
    text = _PREAMBLE_RE.sub('', text, count=1)
    return text.strip()


def rewrite_with_groq(text, lang="vi"):
    """Try GROQ_MODEL then each fallback model in order. Returns the
    rewritten text, or None if every model failed (caller should fall back
    to the raw source text instead of skipping the post)."""
    if not text:
        return ""

    prompt = ("Viet lai tin tuc sau:\n\n" if lang == "vi"
              else "Rewrite the following news:\n\n") + text
    sys_prompt = SYSTEM_PROMPT_VI if lang == "vi" else SYSTEM_PROMPT_EN

    models_to_try = [GROQ_MODEL] + [m for m in GROQ_FALLBACK_MODELS if m != GROQ_MODEL]

    for model in models_to_try:
        for attempt in range(2):  # one retry per model on rate limit
            try:
                kwargs = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                    max_tokens=600,
                    reasoning_format="hidden",  # suppress reasoning field on all reasoning models
                )
                if "qwen" in model:
                    kwargs["reasoning_effort"] = "none"  # qwen3.6: true off-switch for thinking
                resp = groq_client.chat.completions.create(**kwargs)
                out = resp.choices[0].message.content.strip()
                out = _strip_thinking(out)
                if not out:
                    log.warning("Groq empty output model=%s lang=%s", model, lang)
                    break
                log.info("Groq OK model=%s lang=%s (%d chars)", model, lang, len(out))
                return out
            except RateLimitError as exc:
                wait = 5 * (attempt + 1)
                log.warning("Groq rate limit model=%s attempt=%d: %s (retry in %ds)",
                            model, attempt, exc, wait)
                time.sleep(wait)
                continue
            except (APIStatusError, APIConnectionError) as exc:
                log.warning("Groq error model=%s: %s", model, exc)
                break
            except Exception as exc:
                log.error("Groq unexpected error model=%s: %s", model, exc)
                break
        # move to next model in models_to_try

    log.error("All Groq models failed for lang=%s, falling back to raw text", lang)
    return None


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


def build_captions(text):
    """Returns (caption_vi, caption_en). If Groq fully fails, falls back to
    the cleaned raw source text (source channels are Vietnamese, so the
    cleaned raw text is used for the VI post; EN post is skipped since no
    translation is available without Groq)."""
    if not text:
        return "", ""

    caption_vi = rewrite_with_groq(text, lang="vi")
    caption_en = rewrite_with_groq(text, lang="en")

    if caption_vi is None:
        caption_vi = clean_source_text(text)
        log.warning("Using raw source text as VI caption (Groq unavailable)")
    if caption_en is None:
        caption_en = ""  # no translation possible without Groq; skip EN post
        log.warning("Skipping EN caption (Groq unavailable, no translation)")

    return caption_vi, caption_en


def run_bot():
    log.info("Bot starting | sources=%s | vi=%s | en=%s | model=%s | fallbacks=%s",
             SOURCE_CHANNELS, TELEGRAM_TARGET_CHATS, TELEGRAM_TARGET_CHATS_EN,
             GROQ_MODEL, GROQ_FALLBACK_MODELS)
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

            caption_vi, caption_en = build_captions(text) if text else ("", "")

            if not caption_vi and not caption_en and not photo:
                log.warning("Skip #%d no content", mid)
                continue

            img_cache = None
            ok_any = False
            for chat in TELEGRAM_TARGET_CHATS:
                ok, img_cache = post_message(chat, caption_vi, photo, img_cache)
                ok_any = ok_any or ok
                time.sleep(2)
            if caption_en:
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
