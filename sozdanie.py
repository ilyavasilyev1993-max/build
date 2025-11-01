# sozdanie.py
from __future__ import annotations
import re, json, shutil
from pathlib import Path
from html import escape
from typing import Tuple, List, Dict, Optional
from updater import set_config_value_strict, update_config_value_for_bot
import config as C
from zapusk import log, start_bot, is_process_running, load_bot_token, tg_get_me
from net import tg_get

# Что настраиваем сразу (БЕЗ IMAGE_FILE_ID — для него отдельная кнопка)
CREATION_VARS = [
    ("BOT_TOKEN",               "Токен бота"),
    ("ADMIN_ID",                "ADMIN_ID"),
    ("WEBAPP_URL_1",            "Домен Каз"),
    ("WEBAPP_URL_2",            "Домен ТГ"),
    ("PROMOCODE_WEBAPP_URL",    "Домен каз, профиль"),
    ("REFERRAL_NOTIFY_CHAT_ID", "REFERRAL_NOTIFY_CHAT_ID"),
]

# Сессии создания: token -> {...}
#  dir: Path
#  img_bot_token?: str
#  img_offset?: int
#  img_pid?: int
#  ui_chat_id?, ui_msg_id?
CREATION_SESSIONS: Dict[str, Dict[str, object]] = {}

# Ожидание имени каталога: user_id -> {"chat_id": int, "message_id": int}
PENDING_CREATE_NAME: Dict[int, Dict[str, int]] = {}

# Кнопки/префиксы
CREATE_NEW_CB         = getattr(C, "CREATE_NEW_CB", "create_new")
CREATE_SET_PREFIX     = getattr(C, "CREATE_SET_PREFIX", "create_set:")
CREATE_RUN_PREFIX     = getattr(C, "CREATE_RUN_PREFIX", "create_run:")
CREATE_PROMO_PREFIX    = getattr(C, "CREATE_PROMO_PREFIX", "create_promo:")
# Новая кнопка: получить ID IMAGE
CREATE_IMAGE_PREFIX   = getattr(C, "CREATE_IMAGE_PREFIX", "create_img:")
# Автонастройка (новое)
CREATE_AUTOCONF_PREFIX = getattr(C, "CREATE_AUTOCONF_PREFIX", "create_autoconf:")

# Локальные хранилища ожиданий (не в config.py!)
PENDING_AUTOCONF: Dict[int, Dict[str, str]] = {}  # {user_id: {"token": str, "chat_id": str, "message_id": str}}
PENDING_PROMO: Dict[int, Dict[str, str]] = {}  # {user_id: {"token": str, "chat_id": str, "message_id": str}}

# Перечень переменных, которые настраиваем авто-конфигом
AUTOCONF_VARS = ("BOT_TOKEN", "WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL",
                 "WEBAPP_URL_2", "ADMIN_ID", "REFERRAL_NOTIFY_CHAT_ID")

# где лежит путь к шаблону для копирования
_CHIST_PATH = getattr(C, "CLEAN_SOURCE_FILE", C.BASE_DIR / "chist.txt")

# ─────────────────────────────────────────────────────────
# Вспомогательные

def _read_source_dir() -> Path:
    p = Path(_CHIST_PATH)
    if not p.exists():
        raise FileNotFoundError(f"Не найден {_CHIST_PATH}")
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        raise ValueError(f"{_CHIST_PATH} пустой")
    src = Path(raw).expanduser().resolve()
    if not src.exists() or not src.is_dir():
        raise FileNotFoundError(f"Исходная папка из {p} не найдена: {src}")
    return src

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]+")

def _sanitize_folder_name(name: str) -> str:
    name = (name or "").strip()
    name = name.replace(" ", "_")
    name = _SANITIZE_RE.sub("_", name)
    name = name.strip("._-")
    if not name:
        raise ValueError("Имя папки пустое после очистки")
    if len(name) > 64:
        name = name[:64].rstrip("_-")
    return name

def _unique_dest(base: Path) -> Path:
    dst = base
    i = 1
    while dst.exists():
        dst = base.with_name(f"{base.name}_{i}")
        i += 1
    return dst

def _register_session(bot_dir: Path) -> str:
    import secrets
    token = secrets.token_hex(4)  # 8 hex
    CREATION_SESSIONS[token] = {"dir": bot_dir}
    return token

def resolve_token_dir(token: str) -> Optional[Path]:
    sess = CREATION_SESSIONS.get(token) or {}
    return sess.get("dir")

def _append_to_bots_file(bot_dir: Path) -> None:
    path = bot_dir.as_posix()
    lines = []
    if C.BOT_LIST_FILE.exists():
        lines = [s.strip() for s in C.BOT_LIST_FILE.read_text(encoding="utf-8").splitlines()]
    if path not in lines:
        lines.append(path)
        C.BOT_LIST_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")

def _read_pids() -> dict:
    if C.PIDS_FILE.exists():
        try:
            return json.loads(C.PIDS_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def _write_pids(p: dict) -> None:
    C.PIDS_FILE.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")

# ─────────────────────────────────────────────────────────
# Публичный API создания

def request_folder_name(user_id: int, chat_id: int, message_id: int) -> Tuple[str, List[List[dict]]]:
    """
    Включает режим ожидания имени папки для администратора.
    Возвращает (html, keyboard) с подсказкой.
    """
    PENDING_CREATE_NAME[user_id] = {"chat_id": chat_id, "message_id": message_id}
    tip = (
        "<b>Создание нового бота</b>\n"
        "Отправьте <b>имя папки</b> одним сообщением.\n"
        "Допустимы: буквы, цифры, '-', '_'. Пробелы заменяются на '_'."
    )
    kb = [[{"text": "❌ Отмена", "callback_data": getattr(C, "UPDATE_CANCEL_CB", "update_cancel")}]]
    return tip, kb

def handle_folder_name_input(user_id: int, text: str) -> Tuple[str, List[List[dict]]]:
    """
    Обрабатывает введённое имя папки:
    - копирует шаблон в новый каталог BASE_DIR/<name> (с уникализацией при конфликте)
    - возвращает HTML и клавиатуру настройки констант
    """
    # снимаем ожидание
    PENDING_CREATE_NAME.pop(user_id, None)

    src = _read_source_dir()
    clean = _sanitize_folder_name(text)
    base = (C.BASE_DIR / clean).resolve()
    # запретим копирование внутрь исходника
    if str(base).startswith(str(src.resolve())):
        raise ValueError("Имя ведёт внутрь каталога шаблона — выберите другое.")

    dst = _unique_dest(base)
    log(f"[CREATE] Копирую {src} -> {dst}")
    shutil.copytree(src, dst)

    token = _register_session(dst)
    html = (
        f"<b>Создан новый бот</b>\n"
        f"• Каталог: <code>{escape(dst.as_posix())}</code>\n\n"
        f"Теперь задайте значения в <i>config.py</i> для этого бота:"
    )
    kb = build_creation_keyboard(token)
    log(f"[CREATE] Готово. token={token}, dir={dst}")
    return html, kb

# === вспомогательное: найти token сессии по каталогу бота ===
def find_token_by_dir(bot_dir: Path) -> Optional[str]:
    b = Path(bot_dir).resolve()
    for tok, sess in CREATION_SESSIONS.items():
        d = sess.get("dir")
        if not d:
            continue
        if Path(d).resolve() == b:
            return tok
    return None

def build_creation_keyboard(token: str) -> List[List[dict]]:
    rows: List[List[dict]] = []
    rows.append([{"text": "🖼 Добавить IMAGE ID",        "callback_data": f"{CREATE_IMAGE_PREFIX}{token}"}])
    rows.append([{"text": "🛠 Настроить config",         "callback_data": f"{CREATE_AUTOCONF_PREFIX}{token}"}])
    rows.append([{"text": "🔁 Заменить промокод и сумму","callback_data": f"{CREATE_PROMO_PREFIX}{token}"}])  # ← НОВОЕ
    rows.append([{"text": "▶️ Запустить бота",           "callback_data": f"{CREATE_RUN_PREFIX}{token}"}])
    rows.append([{"text": "⬅ Назад",                    "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back_to_status")}])
    return rows

def start_image_capture(token: str) -> tuple[str, List[List[dict]]]:
    """
    Включает режим ловли фото/гиф без запуска main1.py:
    - запоминаем токен нового бота;
    - выставляем img_offset на последний update_id + 1, чтобы ловить ТОЛЬКО новые сообщения;
    - возвращаем инструкцию админу.
    """
    sess = CREATION_SESSIONS.get(token)
    if not sess:
        return ("🔴 <i>Сессия создания не найдена (token устарел).</i>",
                [[{"text": "⬅ Назад", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back_to_status")}]])

    bot_dir: Path = sess["dir"]  # type: ignore
    # читаем токен нового бота
    try:
        bot_token = load_bot_token(bot_dir)
    except Exception as e:
        return (f"🔴 Не удалось прочитать BOT_TOKEN: <code>{escape(str(e))}</code>",
                [[{"text": "⬅ Назад", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back_to_status")}]])

    # узнаем @username чисто для подсказки
    ok, username, err = tg_get_me(bot_token)
    tag = f"@{username}" if username else "<unknown>"

    # НЕ запускаем main1.py — просто начинаем ловить апдейты этим токеном.
    # Заодно «съедим» весь хвост старых апдейтов, чтобы ловить только новые.
    try:
        data = tg_get(bot_token, "getUpdates", None, timeout=5)
        last_id = None
        for it in (data or {}).get("result", []):
            last_id = it.get("update_id", last_id)
        if last_id is not None:
            sess["img_offset"] = int(last_id) + 1
        else:
            sess["img_offset"] = None
    except Exception:
        # если не получилось — начнём с None, тоже ок
        sess["img_offset"] = None

    # сохраняем только токен; никаких PID
    sess["img_bot_token"] = bot_token
    sess.pop("img_pid", None)

    html = (
        "<b>Получение IMAGE_FILE_ID</b>\n"
        f"• Отправьте <b>фото</b> или <b>GIF</b> боту {escape(tag)}.\n"
        "После получения — ID будет автоматически записан в <code>IMAGE_FILE_ID</code>.\n\n"
        "<i>Как пришлёте, подождите пару секунд — всё сделаем сами.</i>"
    )
    kb = build_creation_keyboard(token)
    return html, kb

def _patch_osnovnoe(bot_dir: Path, promo: str, amount: str) -> tuple[int, int]:
    """
    Меняет TESTPROMO -> promo и 111111 -> amount в osnovnoe.py.
    Работает и для вариантов в кавычках, и без кавычек (например, внутри <code>...</code>).
    Возвращает (count_promo, count_amount).
    """
    file_path = bot_dir / "osnovnoe.py"
    if not file_path.exists():
        raise FileNotFoundError(f"Не найден {file_path}")

    text = file_path.read_text(encoding="utf-8")

    # TESTPROMO: либо "TESTPROMO"/'TESTPROMO', либо без кавычек
    promo_pat = r'(?:(["\'])TESTPROMO\1|\bTESTPROMO\b)'
    text, cnt_promo = re.subn(promo_pat, promo, text)

    # 111111: либо "111111"/'111111', либо без кавычек (например, $111111)
    amount_pat = r'(?:(["\'])111111\1|\b111111\b)'
    text, cnt_amount = re.subn(amount_pat, amount, text)

    file_path.write_text(text, encoding="utf-8")
    return cnt_promo, cnt_amount


def _spawn_main1(bot_dir: Path) -> tuple[int | None, str | None]:
    """Запустить main1.py в каталоге нового бота. Вернуть (pid, error)."""
    import subprocess, os
    main1 = bot_dir / "main1.py"
    if not main1.exists():
        return None, f"Не найден {main1}"
    try:
        # в фоновом режиме, вывод в общий лог
        logf = open(C.LOG_FILE, "a", encoding="utf-8")
        args = [C.PYTHON_EXE, "main1.py"]
        proc = subprocess.Popen(
            args, cwd=str(bot_dir),
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            creationflags=getattr(C, "CREATE_NO_WINDOW", 0), shell=False, close_fds=(__import__("os").name != "nt"),
        )
        try: logf.close()
        except Exception: pass
        return proc.pid, None
    except Exception as e:
        return None, str(e)

def request_promo_update(user_id: int, chat_id: int, message_id: int, token: str) -> tuple[str, list[list[dict]]]:
    PENDING_PROMO[user_id] = {"token": token, "chat_id": str(chat_id), "message_id": str(message_id)}
    html = (
        "<b>Замена промокода и суммы</b>\n"
        "Пришлите двумя строками:\n"
        "1) Промокод (например: <code>WIN50</code>)\n"
        "2) Сумма (число, например: <code>50</code>)\n\n"
        "Я заменю в <code>osnovnoe.py</code> все вхождения <code>TESTPROMO</code> и <code>111111</code>\n"
        "на присланные вами значения <b>без кавычек</b>."
    )
    kb = [[{"text": "❌ Отмена", "callback_data": getattr(C, "UPDATE_CANCEL_CB","update_cancel")}]]
    return html, kb

def start_created_bot(token: str) -> str:
    bot_dir = resolve_token_dir(token)
    if not bot_dir:
        return "🔴 <i>Сессия создания не найдена (token устарел).</i>"
    _append_to_bots_file(bot_dir)
    proc, start_err = start_bot(bot_dir)
    if start_err:
        return f"<b>Запуск</b>\n• <b>{escape(bot_dir.name)}</b> — 🔴 <code>{escape(start_err)}</code>"
    pmap = _read_pids(); pmap[bot_dir.as_posix()] = proc.pid; _write_pids(pmap)
    ok = is_process_running(proc.pid)
    mark = "🟢" if ok else "🟡"
    status = "Включен" if ok else "Запущен (проверка статуса не подтверждена)"
    return f"<b>Запуск</b>\n• <b>{escape(bot_dir.name)}</b> — <b>{status}</b> {mark} | <span class=\"tg-spoiler\">PID {proc.pid}</span>"

def build_set_var_prompt(var: str) -> str:
    var2label = {v: lbl for v, lbl in CREATION_VARS}
    label = var2label.get(var, var)
    tip = "Отправьте значение одним сообщением."
    url_vars = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL", "WEBAPP_URL_2"}))
    if var in url_vars: tip = "Отправьте URL (например, https://example.com/)."
    if var == "BOT_TOKEN": tip = "Отправьте токен бота вида <code>1234567:secret</code>."
    return f"<b>Обновление {escape(var)}</b> ({escape(label)})\n{tip}"

def apply_single_value(bot_dir: Path, var: str, value: str) -> str:
    try:
        html = update_config_value_for_bot(value, var, bot_dir.as_posix())
        return html
    except Exception as e:
        return f"• <b>{escape(bot_dir.name)}</b> — 🔴 <code>{escape(str(e))}</code>"

def apply_promo_update(token: str, text: str) -> str:
    bot_dir = resolve_token_dir(token)
    if not bot_dir:
        return "🔴 <i>Сессия создания не найдена (token устарел).</i>"

    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if len(lines) < 2:
        return "🔴 Нужны две строки: промокод и сумма."

    promo = lines[0]
    amount_raw = lines[1]

    # Валидация суммы
    if not re.fullmatch(r"\d+", amount_raw):
        return "🔴 Сумма должна быть числом."
    amount = str(int(amount_raw))  # нормализуем ведущие нули

    try:
        cnt_promo, cnt_amount = _patch_osnovnoe(Path(bot_dir), promo, amount)
    except Exception as e:
        return f"🔴 Ошибка замены: <code>{escape(str(e))}</code>"

    return (
        "<b>Замена промокода и суммы</b>\n"
        f"• TESTPROMO → <code>{escape(promo)}</code> (совпадений: {cnt_promo})\n"
        f"• 111111 → <code>{escape(amount)}</code> (совпадений: {cnt_amount})"
    )

# ——— Автонастройка — запрос ввода ———
def request_autoconfig(user_id: int, chat_id: int, message_id: int, token: str) -> tuple[str, list[list[dict]]]:
    PENDING_AUTOCONF[user_id] = {"token": token, "chat_id": str(chat_id), "message_id": str(message_id)}
    html = (
        "<b>Автонастройка config.py</b>\n"
        "Вставьте одним сообщением данные в любом порядке, по строкам:\n"
        "• BOT_TOKEN\n"
        "• WEBAPP_URL_1 (домен казино)\n"
        "• PROMOCODE_WEBAPP_URL (содержит <code>/profile/bonuses</code>)\n"
        "• ADMIN_ID (число)\n"
        "• WEBAPP_URL_2 (домен вида <code>https://*.pro/<ключ></code>)\n\n"
        "Я распознаю и подставлю всё автоматически. "
        "REFERRAL_NOTIFY_CHAT_ID возьмём таким же как ADMIN_ID."
    )
    kb = [[{"text": "❌ Отмена", "callback_data": getattr(C, "UPDATE_CANCEL_CB","update_cancel")}]]
    return html, kb

# ——— Автонастройка — парсер и применение ———

_TOKEN_RE = re.compile(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b')
_URL_RE   = re.compile(r'(?i)\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>"\']*)?)')
_INT_RE   = re.compile(r'\b\d{6,}\b')

def _norm_url(u: str) -> str:
    s = u.strip()
    if not re.match(r'^(?:https?://)', s, flags=re.I):
        s = 'https://' + s
    if not re.search(r'[/?#]$', s):
        s += '/'
    return s

def _classify_urls(urls: list[str]) -> dict:
    out: dict[str, str] = {}
    for raw in urls:
        u = raw.strip()
        ul = u.lower()
        if "profile/bonuses" in ul and "PROMOCODE_WEBAPP_URL" not in out:
            out["PROMOCODE_WEBAPP_URL"] = _norm_url(u); continue
        # *.pro/<key> — WEBAPP_URL_2
        try:
            from urllib.parse import urlparse
            pu = urlparse(u if u.lower().startswith("http") else "https://"+u)
            host = (pu.hostname or "").lower()
            path = (pu.path or "/")
            if host.endswith(".pro") and len([seg for seg in path.split("/") if seg]) == 1 and "WEBAPP_URL_2" not in out:
                out["WEBAPP_URL_2"] = _norm_url(u); continue
        except Exception:
            pass
        # иначе WEBAPP_URL_1 (первый подходящий)
        if "WEBAPP_URL_1" not in out:
            out["WEBAPP_URL_1"] = _norm_url(u)
    return out

def parse_and_apply_autoconfig(token: str, text: str) -> str:
    """Распознать всё из текста и записать в config.py нового бота."""
    bot_dir = resolve_token_dir(token)
    if not bot_dir:
        return "🔴 <i>Сессия создания не найдена (token устарел).</i>"

    found = {}

    # BOT_TOKEN
    m = _TOKEN_RE.search(text or "")
    if m: found["BOT_TOKEN"] = m.group(0)

    # URLs
    urls = [m.group(1) for m in _URL_RE.finditer(text or "")]
    found.update(_classify_urls(urls))

    # ADMIN_ID / REFERRAL_NOTIFY_CHAT_ID
    # уберём BOT_TOKEN из строки, чтобы colon-числа не мешали
    stripped = (text or "").replace(found.get("BOT_TOKEN",""), " ")
    m2 = _INT_RE.search(stripped)
    if m2:
        admin = m2.group(0)
        found["ADMIN_ID"] = admin
        found["REFERRAL_NOTIFY_CHAT_ID"] = admin

    # применяем
    applied = []
    missing = []
    INT_VARS = set(getattr(C, "INT_VARS", {"ADMIN_ID","REFERRAL_NOTIFY_CHAT_ID"}))

    for var in AUTOCONF_VARS:
        val = found.get(var)
        if not val:
            missing.append(var)
            continue
        try:
            as_int = var in INT_VARS
            msg = set_config_value_strict(bot_dir.as_posix(), var, val, as_int=as_int)
            applied.append(msg.replace("✅ ", ""))  # компактно
        except Exception as e:
            applied.append(f"{var} — 🔴 <code>{escape(str(e))}</code>")

    ok_cnt = sum(1 for v in AUTOCONF_VARS if v in found)
    miss_cnt = len(AUTOCONF_VARS) - ok_cnt
    head = f"<b>Автонастройка</b>  <code>OK:{ok_cnt}</code> | <code>MISS:{miss_cnt}</code>"
    body = ("• " + "\n• ".join(applied)) if applied else "<i>Нечего применять</i>"
    if missing:
        body += "\n\n<i>Не распознано:</i> " + ", ".join(missing)
    return head + "\n" + body