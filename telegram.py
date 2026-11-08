"""Отправка постов через Telegram Bot API."""
import os

import requests


def _api(method, **kw):
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", timeout=60, **kw)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def send_photo(chat_id, photo: bytes, caption_html: str):
    return _api("sendPhoto",
                data={"chat_id": chat_id, "caption": caption_html, "parse_mode": "HTML"},
                files={"photo": ("post.jpg", photo, "image/jpeg")})


def send_with_preview(chat_id, text_html: str, url: str):
    """Сообщение с большим превью ссылки над текстом (для трейлеров — плеер YouTube прямо в посте)."""
    import json
    return _api("sendMessage", data={
        "chat_id": chat_id, "text": text_html, "parse_mode": "HTML",
        "link_preview_options": json.dumps({"url": url, "prefer_large_media": True, "show_above_text": True}),
    })


def send_text(chat_id, text_html: str):
    return _api("sendMessage", data={
        "chat_id": chat_id, "text": text_html, "parse_mode": "HTML",
        "link_preview_options": '{"is_disabled": true}',
    })
