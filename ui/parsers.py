# ui/parsers.py
from __future__ import annotations

from html import escape
from urllib.parse import urlsplit, urlunsplit
import re

import config as C
from .state import BOT_USERNAME, URL_RE, TOKEN_RE
from net import tg_get
from zapusk import log

def ensure_bot_username():
    from .state import BOT_USERNAME as _BU  # локально
    global BOT_USERNAME
    if BOT_USERNAME is not None:
        return
    try:
        me = tg_get(C.STATUS_BOT_TOKEN, "getMe")
        BOT_USERNAME = (me.get("result") or {}).get("username")
        if BOT_USERNAME:
            log(f"[BOOT] bot username = @{BOT_USERNAME}")
        else:
            log("[BOOT] getMe ok, но username отсутствует")
    except Exception as e:
        log(f"[BOOT] getMe failed: {e}")
        BOT_USERNAME = None

def _mask_secret(s: str) -> str:
    if not s:
        return "******"
    s = str(s).strip()
    if len(s) <= 8:
        return "****"
    return f"{s[:6]}…{s[-4:]}"

def extract_value_by_var(var: str, text: str) -> str | None:
    text = (text or "").strip()
    if not text:
        return None

    url_vars = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL"}))
    if var in url_vars:
        m = URL_RE.search(text)
        if not m:
            return None
        s = m.group(1).strip()
        if not re.match(r'^(?i)https?://', s):
            s = "https://" + s
        parts = urlsplit(s)
        if not parts.netloc:
            return None
        path = parts.path or ""
        if not path and not parts.query and not parts.fragment:
            path = "/"
        return urlunsplit((parts.scheme.lower(), parts.netloc, path, parts.query, parts.fragment))

    if var == "BOT_TOKEN":
        m = TOKEN_RE.search(text)
        return m.group(0) if m else None

    if var == "IMAGE_FILE_ID":
        return text.split()[0]

    return text

def bf_validate_username(uname: str) -> list[str]:
    """
    Проверяет корректность @username для бота.
    Возвращает список проблем (пустой список = всё ок).
    """
    import re
    u = (uname or "").lstrip("@")
    problems: list[str] = []
    if not u.lower().endswith("bot"):
        problems.append("должен оканчиваться на <code>bot</code>")
    if not (5 <= len(u) <= 32):
        problems.append("длина 5–32 символа")
    if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', u):
        problems.append("только латинские буквы, цифры и подчёркивания; первый символ — буква")
    return problems