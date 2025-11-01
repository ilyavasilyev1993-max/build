# ui/telegram_io.py
import json
import config as C
from net import tg_post, tg_get

def send_html(token: str, chat_id: int, text: str):
    payload = {
        "chat_id": chat_id,
        "text": text or "",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    return tg_post(token, "sendMessage", payload)

def send_html_with_keyboard(token: str, chat_id: int, text: str, keyboard: list[list[dict]]):
    payload = {
        "chat_id": chat_id,
        "text": text or "",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": json.dumps({"inline_keyboard": keyboard or []}, ensure_ascii=False),
    }
    return tg_post(token, "sendMessage", payload)

def answer_callback(token: str, callback_query_id: str, text: str | None = None, show_alert: bool = False):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if show_alert:
        payload["show_alert"] = True
    return tg_post(token, "answerCallbackQuery", payload)

def edit_message_html(token: str, chat_id: int, message_id: int, text: str, keyboard: list[list[dict]] | None = None):
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text or "",
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if keyboard is not None:
        payload["reply_markup"] = json.dumps({"inline_keyboard": keyboard}, ensure_ascii=False)
    return tg_post(token, "editMessageText", payload)

def get_updates(token: str, offset: int | None):
    timeout = int(getattr(C, "TG_LONGPOLL_TIMEOUT", 25))
    params = {"timeout": timeout}
    if isinstance(offset, int):
        params["offset"] = offset
    return tg_get(token, "getUpdates", params, timeout=timeout + 5)
