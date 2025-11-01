# botfather.py
from __future__ import annotations
import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple, List, Callable
from html import escape
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError, RpcCallFailError
from telethon.tl.custom.conversation import Conversation
from telethon.tl.types import KeyboardButtonCallback, KeyboardButtonUrl, KeyboardButton, ReplyInlineMarkup, MessageEntityTextUrl, MessageEntityMention

import config as C

BOTFATHER = "BotFather"  # @BotFather
TOKEN_RE  = re.compile(r'\b(\d+:[A-Za-z0-9_-]{20,})\b')

@dataclass
class BotProfile:
    name: str
    username: str   # без @ или с @ — всё равно
    about: Optional[str] = None
    description: Optional[str] = None
    botpic_path: Optional[Path] = None

# ─────────────────────────────────────────────────────────
# ====== общие утилиты ======

_USERNAME_RE = re.compile(r'@([A-Za-z0-9_]{5,32})\b')

def _norm(s: str) -> str:
    return (s or "").strip().lower()

async def _click_button_by_predicate(
    conv: Conversation,
    msg,
    predicate: Callable[[str], bool]
) -> Optional[str]:
    """
    Клик по первой inline-кнопке, удовлетворяющей predicate(text)->bool.
    Возвращает текст ответа (str) или None, если не нашли/не кликнулось.
    """
    try:
        markup = getattr(msg, "reply_markup", None)
        if not markup or not getattr(markup, "rows", None):
            return None
        for row in markup.rows:
            for btn in getattr(row, "buttons", []) or []:
                t = getattr(btn, "text", None)
                if t and predicate(t):
                    try:
                        await msg.click(text=t)
                    except Exception:
                        data = getattr(btn, "data", None)
                        if data:
                            await msg.click(data=data)
                        else:
                            return None
                    resp = await conv.get_response(timeout=15.0)
                    return (getattr(resp, "message", "") or "").strip()
    except Exception:
        return None
    return None

def _text_matches_any(text: str, candidates: list[str]) -> bool:
    t = _norm(text)
    return any(c in t for c in candidates)

# Ключевые фразы (EN/RU) для разных локалей BotFather
_BOT_SETTINGS_KEYS = ["bot settings", "настройки бота"]
_MENU_BUTTON_KEYS  = ["menu button", "кнопка меню", "меню"]
# Варианты выбора типа Menu Button → Web App (для новых диалогов BotFather)
_WEBAPP_KEYS       = ["web app", "webapp", "веб-приложение", "веб приложение", "вебапп"]

def _is_nav_button(text: str) -> bool:
    t = (text or "").strip().lower()
    nav = ("next", "далее", "вперёд", "вперед", "›", "»", ">>", "⏭", "⏩", "previous", "назад", "‹", "«", "<<", "⏮", "⏪")
    return any(k in t for k in nav)

async def _click_button_by_text(conv: Conversation, msg, text: str) -> Optional[str]:
    """Клик по кнопке с заданным текстом. Возвращает текст ответа или None."""
    try:
        await msg.click(text=text)
        resp = await conv.get_response(timeout=12.0)
        return (getattr(resp, "message", "") or "").strip()
    except Exception:
        return None

async def _collect_menu_page_buttons(conv: Conversation, log: list) -> tuple[list[str], list[str], object]:
    """
    Возвращает (btn_texts, nav_texts, msg) для текущей страницы меню.
    btn_texts — «карточки ботов», nav_texts — стрелки/сервисные.
    """
    try:
        menu_msg = await conv.get_response(timeout=5.0)
    except asyncio.TimeoutError:
        return [], [], None
    txt = (getattr(menu_msg, "message", "") or "").strip()
    log.append(("bf", txt))
    btn_texts_all = _btn_texts_from_markup(getattr(menu_msg, "reply_markup", None))
    if not btn_texts_all:
        return [], [], menu_msg
    cards, navs = [], []
    for t in btn_texts_all:
        if _is_service_button(t) or _is_nav_button(t):
            navs.append(t)
        else:
            cards.append(t)
    return cards, navs, menu_msg

def _parse_usernames_from_text(text: str) -> list[str]:
    if not text:
        return []
    found = []
    # 1) явные @mentions
    for m in _USERNAME_RE.finditer(text):
        u = "@" + m.group(1)
        if u not in found:
            found.append(u)
    # 2) ссылки вида t.me/<name>
    for m in re.finditer(r't\.me/(@?[A-Za-z0-9_]{5,32})', text, flags=re.I):
        u = m.group(1)
        if not u.startswith("@"):
            u = "@" + u
        if u not in found:
            found.append(u)
    return found

def _btn_texts_from_markup(markup) -> list[str]:
    texts = []
    if hasattr(markup, "rows") and markup.rows:
        for row in markup.rows:
            for btn in getattr(row, "buttons", []) or []:
                t = getattr(btn, "text", None)
                if t:
                    texts.append(t)
    return texts

def _is_service_button(text: str) -> bool:
    t = (text or "").strip().lower()
    # локализации "создать нового", "назад", "закрыть сессию" и англ. варианты
    svc = (
        "создать нового", "создать нового бота", "назад", "закрыть сессию",
        "create a new bot", "back", "close session", "cancel"
    )
    return any(k in t for k in svc)

def _bf_sleep():
    return asyncio.sleep(0.6)

async def _start_and_cancel(conv: Conversation, log: list[str]|None=None):
    await conv.send_message("/start");   await _bf_sleep()
    try: resp = await conv.get_response()
    except asyncio.TimeoutError: resp = None
    if log is not None: log.append(("bf", (resp.message if resp else "").strip()))
    await conv.send_message("/cancel");  await _bf_sleep()
    try: resp2 = await conv.get_response()
    except asyncio.TimeoutError: resp2 = None
    if log is not None: log.append(("bf", (resp2.message if resp2 else "").strip()))

async def _safe_step(conv: Conversation, text: str, log: list) -> str:
    """Отправить текст и дождаться ответа c повтором 1 раз при таймауте (с ресетом)."""
    for attempt in (1, 2):
        await conv.send_message(text)
        log.append(("you", text))
        await _bf_sleep()
        try:
            resp = await conv.get_response()
            msg = (resp.message or "").strip()
            log.append(("bf", msg))
            return msg
        except asyncio.TimeoutError:
            if attempt == 1:
                # ресет сессии и повтор
                await _start_and_cancel(conv, log)
                continue
            raise
    return ""  # недостижимо

async def _safe_send_file(conv: Conversation, path: Path, log: list) -> str:
    for attempt in (1, 2):
        await conv.send_file(path.as_posix())
        log.append(("you", f"<file:{path.name}>"))
        await _bf_sleep()
        try:
            resp = await conv.get_response()
            msg = (resp.message or "").strip()
            log.append(("bf", msg))
            return msg
        except asyncio.TimeoutError:
            if attempt == 1:
                await _start_and_cancel(conv, log)
                continue
            raise
    return ""

def _bf_validate_username(uname_raw: str) -> List[str]:
    uname = uname_raw.lstrip("@")
    problems = []
    if not uname.lower().endswith("bot"):
        problems.append("должен оканчиваться на <code>bot</code>")
    if not (5 <= len(uname) <= 32):
        problems.append("длина 5–32 символа")
    if not re.match(r'^[A-Za-z][A-Za-z0-9_]*$', uname):
        problems.append("только латинские буквы, цифры и подчёркивания; первый символ — буква")
    return problems

def _ensure_api() -> Tuple[int, str]:
    api_id  = getattr(C, "TELETHON_API_ID", None)
    api_hash= getattr(C, "TELETHON_API_HASH", None)
    if not api_id or not api_hash:
        raise RuntimeError("В config.py не заданы TELETHON_API_ID / TELETHON_API_HASH.")
    return api_id, api_hash

def _fmt_uname(u: str) -> str:
    return u if u.startswith("@") else f"@{u}"

def _hint_from_reply(text: str, username: str) -> str:
    t = (text or "").lower()
    if "is already taken" in t or "already taken" in t:
        base = username[:-3] if username.lower().endswith("bot") else username
        alts = [f"{base}AppBot", f"{base}HelperBot", f"{base}OfficialBot", f"{base}XBot", f"{base}123Bot"]
        return ("Похоже, юзернейм занят. Попробуйте варианты:\n• @" + "\n• @".join(alts))
    if "username is invalid" in t or "invalid" in t:
        return ("Юзернейм некорректен. Разрешены латинские буквы/цифры/подчёркивания, "
                "длина 5–32, последний суффикс — <code>bot</code>, первый символ — буква.")
    if "too long" in t and "about" in t:
        return "Поле About слишком длинное. Ограничение ~120 символов."
    if "too long" in t and "description" in t:
        return "Поле Description слишком длинное. Ограничение ~512 символов."
    return ""

def _log_step(log: list[tuple[str, str]], text: str):
    # "sys" — наши ручные шаги (человеческий лог)
    log.append(("sys", text))

def _format_log(log: List[tuple[str, str]], last_n: int = 10) -> str:
    out = ["<b>Диалог с @BotFather</b> (последние сообщения):"]
    for role, line in log[-last_n:]:
        if role == "you":
            out.append("Вы: <code>%s</code>" % escape(line))
        elif role == "bf":
            out.append("BF: <code>%s</code>" % escape(line))
        else:  # 'sys'
            out.append("• %s" % escape(line))
    return "\n".join(out)

async def _send(conv: Conversation, text: str, log: list, timeout: float = 40.0) -> str:
    await conv.send_message(text)
    log.append(("you", text))
    resp = await conv.get_response(timeout=timeout)
    msg = (getattr(resp, "message", "") or "").strip()
    log.append(("bf", msg))
    return msg

async def _send_file(conv: Conversation, path: Path, log: list, timeout: float = 60.0) -> str:
    await conv.send_file(path.as_posix())
    log.append(("you", f"<file:{path.name}>"))
    resp = await conv.get_response(timeout=timeout)
    msg = (getattr(resp, "message", "") or "").strip()
    log.append(("bf", msg))
    return msg

def _extract_inline_usernames(reply) -> List[str]:
    """Парсим кнопку-меню /mybots: собираем @usernames из inline-кнопок."""
    res: List[str] = []
    markup = getattr(reply, "reply_markup", None)
    if not isinstance(markup, ReplyInlineMarkup):
        return res
    for row in markup.rows or []:
        for btn in row.buttons or []:
            # кнопки могут быть callback или url; текст у них — это имя/юзернейм бота
            if isinstance(btn, (KeyboardButton, KeyboardButtonCallback, KeyboardButtonUrl)):
                text = getattr(btn, "text", None)
                if text and text.startswith("@"):
                    res.append(text)
    # Уникализируем, сохраняя порядок
    seen = set()
    uniq = []
    for u in res:
        if u not in seen:
            uniq.append(u); seen.add(u)
    return uniq

async def _connect_from_session(session_path: Path) -> TelegramClient:
    if not session_path.exists():
        raise RuntimeError("Не найден файл сессии.")
    api_id, api_hash = _ensure_api()
    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError("Сессия не авторизована. Выполните вход этим аккаунтом заранее.")
    return client

# ─────────────────────────────────────────────────────────
# 1) СПИСОК БОТОВ АККАУНТА

def bf_list_bots(session_path: Path) -> Tuple[bool, List[str], str]:
    """
    Возвращает (ok, usernames_list, report_html).
    Снимает список с /mybots, обходит все страницы (стрелки),
    кликает карточки и вытаскивает @username из карточки.
    """
    log: List[tuple[str,str]] = []

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                usernames: list[str] = []
                seen_cards: set[str] = set()

                async with client.conversation(BOTFATHER, timeout=180) as conv:
                    # мягкий ресет, чтобы не залипало
                    await _start_and_cancel(conv, log)

                    # открываем меню
                    _ = await _safe_step(conv, "/mybots", log)

                    # собираем со всех страниц
                    page_guard = 0
                    while page_guard < 12:  # до 12 страниц — хватит с запасом
                        page_guard += 1
                        cards, navs, menu_msg = await _collect_menu_page_buttons(conv, log)
                        if menu_msg is None:
                            break

                        # если карт нет — возможно пустой список (только «Создать нового/Назад»)
                        if not cards:
                            # Попробуем извлечь из самого текста
                            fallback = _parse_usernames_from_text((getattr(menu_msg, "message", "") or ""))
                            for u in fallback:
                                if u not in usernames:
                                    usernames.append(u)
                            break

                        # кликаем по каждой карточке
                        for card_text in cards:
                            if card_text in seen_cards:
                                continue
                            seen_cards.add(card_text)

                            # каждый раз заново открываем /mybots, чтобы иметь актуальный msg для клика
                            _ = await _safe_step(conv, "/mybots", log)
                            cards2, _, menu_msg2 = await _collect_menu_page_buttons(conv, log)
                            if not menu_msg2:
                                continue

                            # найдём точный текст кнопки из актуального сообщения
                            if card_text not in cards2:
                                # если текст чуть отличается (локализация/обрезка), пропустим
                                continue

                            # кликаем карточку
                            card_reply_text = await _click_button_by_text(conv, menu_msg2, card_text)
                            if card_reply_text is None:
                                # ресет и продолжим
                                await _start_and_cancel(conv, log)
                                continue
                            log.append(("bf", card_reply_text))

                            # вытаскиваем usernames из текста карточки и её кнопок
                            found = _parse_usernames_from_text(card_reply_text)
                            card_reply = await conv.get_response(timeout=2.0) if False else None  # нет второго ответа — ок
                            if not found and hasattr(menu_msg2, "reply_markup"):
                                for t in _btn_texts_from_markup(getattr(menu_msg2, "reply_markup", None)):
                                    found.extend(_parse_usernames_from_text(t))

                            for u in found:
                                if u not in usernames:
                                    usernames.append(u)
                                    break  # по одной на карточку достаточно

                            # вернёмся обратно в /mybots (на случай, если BF остался в карточке)
                            await _safe_step(conv, "/mybots", log)

                        # если есть кнопка «Next/Далее», листаем, иначе выходим
                        next_btn = None
                        for n in navs:
                            if _is_nav_button(n) and ("next" in n.lower() or "дале" in n.lower() or "›" in n or "»" in n or "⏩" in n):
                                next_btn = n
                                break
                        if next_btn:
                            # листаем на следующую страницу
                            await _click_button_by_text(conv, menu_msg, next_btn)
                            continue
                        break  # нет next — список исчерпан

                return True, usernames, _format_log(log, 12)
            finally:
                await client.disconnect()
        except FloodWaitError as e:
            return False, [], f"🔴 Flood wait: подождите {getattr(e, 'seconds', 'несколько')} сек."
        except asyncio.TimeoutError:
            return False, [], "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, [], f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())


# ─────────────────────────────────────────────────────────
# 2) СОЗДАНИЕ БОТА (ТОЛЬКО NAME + USERNAME)

def bf_create_minimal(session_path: Path, name: str, username: str) -> Tuple[bool, Optional[str], str]:
    """
    Создаёт бота: /newbot → Name → @username.
    Возвращает (ok, token_or_none, report_html).
    """
    log: List[tuple[str,str]] = []
    uname_at = _fmt_uname(username)

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                token: Optional[str] = None
                async with client.conversation(BOTFATHER, timeout=150) as conv:
                    # ресетим диалог, чтобы не «залипало»
                    await _start_and_cancel(conv, log)

                    # 1) /newbot → 2) Name → 3) @username (с авто-повтором при таймауте)
                    msg = await _safe_step(conv, "/newbot", log)
                    msg = await _safe_step(conv, name,     log)
                    msg = await _safe_step(conv, uname_at, log)

                    low = msg.lower()
                    if "sorry" in low or "invalid" in low:
                        hint = _hint_from_reply(msg, username)
                        report = _format_log(log, 12) + "\n\n" + (f"🔴 Ошибка при установке username.\n{hint}" if hint else "🔴 Ошибка при установке username.")
                        return False, None, report

                    # иногда токен уже в этом ответе
                    m = TOKEN_RE.search(msg)
                    if m:
                        token = m.group(1)

                    # если токена нет — /token → @username
                    if token is None:
                        msg = await _safe_step(conv, "/token",  log)
                        msg = await _safe_step(conv, uname_at,  log)
                        m = TOKEN_RE.search(msg)
                        if m:
                            token = m.group(1)

                    if token:
                        report = (
                            "<b>Бот создан</b> 🎉\n"
                            f"• Name: <code>{name}</code>\n"
                            f"• Username: <code>{uname_at}</code>\n"
                            f"• Token: <code>{token}</code>"
                        )
                        return True, token, report

                hint = _hint_from_reply(log[-1][1] if log else "", username)
                report = _format_log(log, 12) + "\n\n" + (f"🔴 BotFather не выдал токен.\n{hint}" if hint else "🔴 BotFather не выдал токен.")
                return False, None, report

            finally:
                await client.disconnect()
        except FloodWaitError as e:
            return False, None, f"🔴 Flood wait: подождите {getattr(e, 'seconds', 'несколько')} сек."
        except asyncio.TimeoutError:
            return False, None, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, None, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

# ─────────────────────────────────────────────────────────
# 3) РЕДАКТИРОВАНИЕ СУЩЕСТВУЮЩЕГО БОТА

def bf_set_about(session_path: Path, username: str, about: str) -> Tuple[bool, str]:
    """ /setabouttext → @username → about (<=120) """
    log: List[tuple[str,str]] = []
    uname_at = _fmt_uname(username)
    about = (about or "")[:120]

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                async with client.conversation(BOTFATHER, timeout=150) as conv:
                    await _start_and_cancel(conv, log)
                    msg = await _safe_step(conv, "/setabouttext", log)
                    msg = await _safe_step(conv, uname_at,       log)
                    msg = await _safe_step(conv, about,          log)
                ok = ("success" in msg.lower()) or ("updated" in msg.lower()) or ("about" in msg.lower())
                return ok, ("✅ About обновлён." if ok else "🔴 Не удалось обновить About.")
            finally:
                await client.disconnect()
        except asyncio.TimeoutError:
            return False, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

def bf_set_description(session_path: Path, username: str, description: str) -> Tuple[bool, str]:
    """ /setdescription → @username → description (<=512) """
    log: List[tuple[str,str]] = []
    uname_at = _fmt_uname(username)
    description = (description or "")[:512]

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                async with client.conversation(BOTFATHER, timeout=150) as conv:
                    await _start_and_cancel(conv, log)
                    msg = await _safe_step(conv, "/setdescription", log)
                    msg = await _safe_step(conv, uname_at,        log)
                    msg = await _safe_step(conv, description,     log)
                ok = ("success" in msg.lower()) or ("updated" in msg.lower()) or ("description" in msg.lower())
                return ok, ("✅ Description обновлён." if ok else "🔴 Не удалось обновить Description.")
            finally:
                await client.disconnect()
        except asyncio.TimeoutError:
            return False, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

def bf_set_botpic(session_path: Path, username: str, photo_path: Path) -> Tuple[bool, str]:
    """ /setuserpic → @username → <file> """
    log: List[tuple[str,str]] = []
    uname_at = _fmt_uname(username)

    if not photo_path or not photo_path.exists():
        return False, "🔴 Файл картинки не найден."

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                async with client.conversation(BOTFATHER, timeout=180) as conv:
                    await _start_and_cancel(conv, log)
                    msg = await _safe_step(conv, "/setuserpic", log)
                    msg = await _safe_step(conv, uname_at,      log)
                    msg = await _safe_send_file(conv, photo_path, log)
                ok = ("success" in msg.lower()) or ("updated" in msg.lower()) or ("profile photo" in msg.lower())
                return ok, ("✅ Аватар установлен." if ok else "🔴 Не удалось установить аватар.")
            finally:
                await client.disconnect()
        except asyncio.TimeoutError:
            return False, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

# ─────────────────────────────────────────────────────────
# 5) ПАКЕТНОЕ РЕДАКТИРОВАНИЕ ПРОФИЛЯ БОТА (about/description/botpic)

def bf_apply_profile(session_path: Path, profile: BotProfile) -> Tuple[bool, str]:
    """
    Применяет к существующему боту (по profile.username) поля:
    - about (если задан)
    - description (если задан)
    - botpic_path (если задан и существует)
    Всё — в одной конверсии с авто-ретраями.
    Возвращает (ok, report_html).
    """
    log: List[tuple[str,str]] = []
    uname_at = _fmt_uname(profile.username)
    overall_ok = True

    async def _runner():
        nonlocal overall_ok
        try:
            client = await _connect_from_session(session_path)
            try:
                async with client.conversation(BOTFATHER, timeout=240) as conv:
                    await _start_and_cancel(conv, log)

                    # ABOUT
                    if profile.about is not None:
                        about = (profile.about or "")[:120]
                        try:
                            _ = await _safe_step(conv, "/setabouttext", log)
                            _ = await _safe_step(conv, uname_at,       log)
                            msg = await _safe_step(conv, about,        log)
                            if not any(k in msg.lower() for k in ("success", "updated", "about")):
                                overall_ok = False
                        except Exception as e:
                            log.append(("bf", f"<about error: {e}>"))
                            overall_ok = False

                    # DESCRIPTION
                    if profile.description is not None:
                        desc = (profile.description or "")[:512]
                        try:
                            _ = await _safe_step(conv, "/setdescription", log)
                            _ = await _safe_step(conv, uname_at,         log)
                            msg = await _safe_step(conv, desc,            log)
                            if not any(k in msg.lower() for k in ("success", "updated", "description")):
                                overall_ok = False
                        except Exception as e:
                            log.append(("bf", f"<description error: {e}>"))
                            overall_ok = False

                    # BOTPIC
                    if profile.botpic_path and Path(profile.botpic_path).exists():
                        try:
                            _ = await _safe_step(conv, "/setuserpic",   log)
                            _ = await _safe_step(conv, uname_at,        log)
                            msg = await _safe_send_file(conv, Path(profile.botpic_path), log)
                            if not any(k in msg.lower() for k in ("success", "updated", "profile photo")):
                                overall_ok = False
                        except Exception as e:
                            log.append(("bf", f"<botpic error: {e}>"))
                            overall_ok = False

                title = "✅ Профиль обновлён" if overall_ok else "⚠️ Профиль обновлён частично"
                return overall_ok, f"<b>{title}</b>\n" + _format_log(log, 12)
            finally:
                await client.disconnect()
        except asyncio.TimeoutError:
            return False, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

def bf_get_token(session_path: Path, username: str) -> Tuple[bool, Optional[str], str]:
    """
    Возвращает (ok, token_or_none, message_html_без_диалога).
    Делает: /token -> @username -> парсит токен.
    """
    log: List[tuple[str, str]] = []
    uname_at = _fmt_uname(username)

    async def _runner():
        try:
            client = await _connect_from_session(session_path)
            try:
                token: Optional[str] = None
                async with client.conversation(BOTFATHER, timeout=120) as conv:
                    await _start_and_cancel(conv, log)
                    _ = await _safe_step(conv, "/token",  log)
                    msg = await _safe_step(conv, uname_at, log)
                    m = TOKEN_RE.search(msg)
                    if m:
                        token = m.group(1)
                if token:
                    return True, token, f"🔑 Token для <code>{escape(uname_at)}</code>:\n<code>{token}</code>"
                return False, None, "🔴 BotFather не выдал токен."
            finally:
                await client.disconnect()
        except asyncio.TimeoutError:
            return False, None, "🔴 Таймаут ожидания ответа от @BotFather."
        except Exception as e:
            return False, None, f"🔴 Ошибка: {e}"

    return asyncio.get_event_loop().run_until_complete(_runner())

# ─────────────────────────────────────────────────────────
# Menu Button via /mybots → [@bot] → Bot Settings → Menu Button

def _text_matches(t: str, variants: list[str]) -> bool:
    t = (t or "").strip().lower()
    return any(t == v.lower() or v.lower() in t for v in variants)

async def _open_bot_settings_menu(conv: Conversation, uname_at: str, log: list) -> bool:
    """
    Открывает: /mybots → клик по @username → клик 'Bot Settings'.
    Возвращает True/False, получилось ли дойти до раздела настроек бота.
    """
    # /mybots
    _ = await _safe_step(conv, "/mybots", log)
    # получить сообщение со списком
    try:
        menu_msg = await conv.get_response(timeout=8.0)
        log.append(("bf", (getattr(menu_msg, "message", "") or "").strip()))
    except asyncio.TimeoutError:
        return False

    # найти кнопку с @username
    btns = _btn_texts_from_markup(getattr(menu_msg, "reply_markup", None))
    target_text = None
    for b in btns:
        if uname_at.lower() in (b or "").lower():
            target_text = b; break
    if not target_text:
        # иногда BotFather печатает имя без @ — попробуем по «хвосту»
        uname_noat = uname_at.lstrip("@").lower()
        for b in btns:
            if uname_noat in (b or "").lower():
                target_text = b; break
    if not target_text:
        return False

    # клик по карточке бота
    try:
        await menu_msg.click(text=target_text)
        card = await conv.get_response(timeout=12.0)
        log.append(("bf", (getattr(card, "message", "") or "").strip()))
    except Exception:
        return False

    # клик "Bot Settings"
    settings_labels = ["Bot Settings", "Настройки бота"]
    btns2 = _btn_texts_from_markup(getattr(card, "reply_markup", None))
    settings_btn = None
    for b in btns2:
        if _text_matches(b, settings_labels):
            settings_btn = b; break
    if not settings_btn:
        return False

    try:
        await card.click(text=settings_btn)
        settings_msg = await conv.get_response(timeout=12.0)
        log.append(("bf", (getattr(settings_msg, "message", "") or "").strip()))
        return True
    except Exception:
        return False

async def _bf_open_bot_card(conv: Conversation, log: list, target_uname: str):
    """
    Открывает карточку нужного бота из /mybots (на одной странице).
    Возвращает объект сообщения карточки или None.
    """
    uname = target_uname if target_uname.startswith("@") else f"@{target_uname}"
    uname_core = uname.lstrip("@").lower()

    # Открыть список
    await conv.send_message("/mybots"); log.append(("you","/mybots"))
    try:
        menu_msg = await conv.get_response(timeout=15.0)
    except asyncio.TimeoutError:
        return None
    log.append(("bf", (menu_msg.message or "").strip()))

    # Собрать только карточки (без «Создать нового», «Назад», стрелок)
    btn_texts = _btn_texts_from_markup(getattr(menu_msg, "reply_markup", None)) or []
    card_btns = [t for t in btn_texts if not _is_service_button(t) and not _is_nav_button(t)]
    if not card_btns:
        return None

    # Перебирать карточки: открыть → проверить наличие @username в тексте/кнопках → вернуть
    for t in card_btns:
        try:
            await menu_msg.click(text=t)
            reply = await conv.get_response(timeout=12.0)
            txt = (reply.message or "").strip()
            log.append(("bf", txt))
        except Exception:
            # если клик сорвался — вернуться в список и продолжить
            await conv.send_message("/mybots"); log.append(("you","/mybots"))
            try:
                menu_msg = await conv.get_response(timeout=15.0)
            except asyncio.TimeoutError:
                return None
            log.append(("bf", (menu_msg.message or "").strip()))
            continue

        found = (uname_core in txt.lower())
        if not found:
            for bt in _btn_texts_from_markup(getattr(reply, "reply_markup", None)) or []:
                if uname_core in (bt or "").lower() or ("@" + uname_core) in (bt or "").lower():
                    found = True
                    break

        if found:
            return reply  # нужная карточка открыта

        # не тот бот → вернуться в список и пробовать следующую карточку
        await conv.send_message("/mybots"); log.append(("you","/mybots"))
        try:
            menu_msg = await conv.get_response(timeout=15.0)
        except asyncio.TimeoutError:
            return None
        log.append(("bf", (menu_msg.message or "").strip()))

    return None


def bf_set_menu_button_via_ui(session_path: Path, username: str,
                              url: Optional[str], title: Optional[str]) -> Tuple[bool, str]:
    log: list[tuple[str,str]] = []

    async def _runner():
        try:
            _log_step(log, "Подключаюсь к сессии")
            client = await _connect_from_session(session_path)
            try:
                async with client.conversation(BOTFATHER, timeout=240) as conv:
                    _log_step(log, "Перехожу в @BotFather")
                    await _start_and_cancel(conv, log)

                    # 1) Открыть карточку бота «в лоб» (брутфорс по всем кнопкам/страницам)
                    _log_step(log, f"Ищу карточку {username} через /mybots")
                    card_msg = await _open_bot_card_bruteforce(conv, log, username)
                    if not card_msg:
                        return False, "🔴 Не удалось найти карточку бота. Проверьте @username.\n" + _format_log(log, 30)

                    # 2) Клик «Bot Settings»
                    def _is_bot_settings(txt: str) -> bool:
                        return _text_matches_any(txt, _BOT_SETTINGS_KEYS)
                    _log_step(log, "Открываю Bot Settings")
                    reply = await _click_button_by_predicate(conv, card_msg, _is_bot_settings)
                    if reply is None:
                        return False, "🔴 Не удалось открыть Bot Settings.\n" + _format_log(log, 30)

                    try:
                        settings_msg = await conv.get_response(timeout=10.0)
                    except asyncio.TimeoutError:
                        settings_msg = card_msg  # fallback
                    log.append(("bf", (getattr(settings_msg, "message", "") or "").strip() if settings_msg else ""))

                    # 3) Клик «Menu Button»
                    def _is_menu_button(txt: str) -> bool:
                        return _text_matches_any(txt, _MENU_BUTTON_KEYS)
                    _log_step(log, "Открываю Menu Button")
                    reply2 = await _click_button_by_predicate(conv, settings_msg, _is_menu_button)
                    if reply2 is None:
                        return False, "🔴 В Bot Settings не нашёл пункт «Menu Button».\n" + _format_log(log, 30)

                    # 4) Отправляем URL → ждём запрос Title
                    to_send = "/empty" if (url is None or str(url).strip() == "") else str(url).strip()
                    _log_step(log, f"Отправляю URL: {to_send}")
                    await conv.send_message(to_send); log.append(("you", to_send))
                    try:
                        ask_title = await conv.get_response(timeout=20.0)
                    except asyncio.TimeoutError:
                        return False, "🔴 BotFather не запросил Title после URL.\n" + _format_log(log, 30)
                    log.append(("bf", (ask_title.message or "").strip()))

                    # 5) Отправляем Title → финальный ответ
                    to_title = "/empty" if (title is None or str(title).strip() == "") else str(title).strip()
                    _log_step(log, f"Отправляю Title: {to_title}")
                    await conv.send_message(to_title); log.append(("you", to_title))
                    try:
                        final = await conv.get_response(timeout=20.0)
                    except asyncio.TimeoutError:
                        return False, "🔴 Не дождался подтверждения от BotFather после Title.\n" + _format_log(log, 30)

                    final_text = (final.message or "").strip()
                    log.append(("bf", final_text))
                    ok = ("success" in final_text.lower()) or ("updated" in final_text.lower())

                    human = "✅ Menu Button обновлён." if ok else f"⚠️ Ответ BotFather: <code>{escape(final_text)}</code>"
                    # Вернём расширенный лог, чтобы видеть каждый шаг
                    report = _format_log(log, 40)
                    return (True, f"{human}\n{report}") if ok else (False, f"{human}\n{report}")

            finally:
                await client.disconnect()
        except Exception as e:
            return False, f"🔴 Ошибка: {escape(str(e))}\n{_format_log(log, 40)}"

    return asyncio.get_event_loop().run_until_complete(_runner())

async def _open_bot_card_bruteforce(conv: Conversation, log: list, target_uname: str, max_pages: int = 20):
    """
    Открывает карточку нужного бота через /mybots, обходя все страницы и кликая все карточки.
    Успех: возвращает message карточки (объект ответа). Неудача: None.
    """
    uname = target_uname if target_uname.startswith("@") else f"@{target_uname}"
    uname_core = uname.lstrip("@").lower()

    _log_step(log, "Подключаюсь к BotFather меню: /mybots")
    await conv.send_message("/mybots"); log.append(("you","/mybots"))
    try:
        menu_msg = await conv.get_response(timeout=15.0)
    except asyncio.TimeoutError:
        _log_step(log, "Таймаут после /mybots")
        return None
    log.append(("bf", (menu_msg.message or "").strip()))

    page_idx = 0
    visited_cards: set[str] = set()

    while page_idx < max_pages:
        page_idx += 1
        _log_step(log, f"Страница /mybots №{page_idx}")
        btn_texts = _btn_texts_from_markup(getattr(menu_msg, "reply_markup", None)) or []

        # Разделим на карточки и навигацию
        cards, navs = [], []
        for t in btn_texts:
            if _is_service_button(t) or _is_nav_button(t):
                navs.append(t)
            else:
                cards.append(t)
        _log_step(log, f"Найдено карточек: {len(cards)}, навигационных: {len(navs)}")

        # Если карточек нет — возможно список текстовый → попробуем сразу из текста
        if not cards:
            txt = (menu_msg.message or "").strip()
            found_unames = _parse_usernames_from_text(txt)
            _log_step(log, f"В тексте страницы /mybots найдены: {', '.join(found_unames) or '—'}")
            # Карточки нет для клика, уходим
            break

        # Кликаем каждую карточку на странице
        for card_text in cards:
            if card_text in visited_cards:
                continue
            visited_cards.add(card_text)

            _log_step(log, f"Кликаю карточку: «{card_text}»")
            try:
                await menu_msg.click(text=card_text)
                reply = await conv.get_response(timeout=15.0)
            except Exception as e:
                _log_step(log, f"Клик по «{card_text}» не удался: {e}. Возвращаюсь в /mybots")
                await conv.send_message("/mybots"); log.append(("you","/mybots"))
                try:
                    menu_msg = await conv.get_response(timeout=15.0)
                    log.append(("bf", (menu_msg.message or "").strip()))
                except asyncio.TimeoutError:
                    _log_step(log, "Таймаут при возврате в /mybots")
                    return None
                continue

            txt = (reply.message or "").strip()
            log.append(("bf", txt))

            # Есть ли нужный @username в тексте или кнопках карточки?
            hit = (uname_core in txt.lower())
            if not hit:
                for bt in _btn_texts_from_markup(getattr(reply, "reply_markup", None)) or []:
                    t_low = (bt or "").lower()
                    if uname_core in t_low or ("@" + uname_core) in t_low:
                        hit = True; break

            if hit:
                _log_step(log, f"Найдена карточка нужного бота: {uname}")
                return reply  # карточка открыта

            # Не тот бот → возвращаемся в /mybots
            _log_step(log, "Это карточка другого бота — возвращаюсь в /mybots")
            await conv.send_message("/mybots"); log.append(("you","/mybots"))
            try:
                menu_msg = await conv.get_response(timeout=15.0)
                log.append(("bf", (menu_msg.message or "").strip()))
            except asyncio.TimeoutError:
                _log_step(log, "Таймаут при возврате в /mybots")
                return None

        # Листаем «Далее», если есть
        next_btn = None
        for n in navs:
            if _is_nav_button(n) and ("next" in n.lower() or "дале" in n.lower() or "›" in n or "»" in n or "⏩" in n):
                next_btn = n; break
        if next_btn:
            _log_step(log, f"Листаю на следующую страницу: «{next_btn}»")
            try:
                await menu_msg.click(text=next_btn)
                menu_msg = await conv.get_response(timeout=15.0)
                log.append(("bf", (menu_msg.message or "").strip()))
            except Exception as e:
                _log_step(log, f"Не удалось перейти на следующую страницу: {e}")
                return None
            continue

        # страниц больше нет
        _log_step(log, "Дальше страниц нет — нужную карточку не нашли")
        break

    return None
