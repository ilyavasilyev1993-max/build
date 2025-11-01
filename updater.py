# updater.py
"""
Массовое обновление значения константы в config.py у всех ботов.
Поддерживает, например: WEBAPP_URL_1 и PROMOCODE_WEBAPP_URL.
"""

from __future__ import annotations
import re
import json
import shutil
from datetime import datetime
from pathlib import Path
from html import escape

import ssl
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler
from urllib.parse import urlencode

import config as C

# ─────────────────────────────────────────────────────────
# HTTP opener без прокси (чтобы не было циклических импортов)
_SSL_CTX  = ssl.create_default_context()
_NO_PROXY = build_opener(ProxyHandler({}), HTTPSHandler(context=_SSL_CTX))

# Устойчивый паттерн присваивания:
#   VAR_NAME   =   "value"      # коммент
#   VAR_NAME='value'
_ASSIGN_RE_TMPL = r"""
^
(?P<indent>\s*)
{var}
\s*=\s*
(?:
  (?P<quote>['"])
  (?P<valq>.*?)
  (?P=quote)
 |
  (?P<valn>[^#\r\n]+?)
)
\s*
(?:\#.*)?      # необязательный комментарий
$
"""


# ─────────────────────────────────────────────────────────
# logging
_INT_ONLY_RE = re.compile(r'^\s*\d+\s*$')

def set_config_value_strict(bot_dir: str, var: str, value: str, as_int: bool = False) -> str:
    """
    Строго правит ТОЛЬКО строку ^VAR\s*= в <bot_dir>/config.py. Если строки нет — добавляет новую.
    Гейт типов:
      - если as_int=True — пишем без кавычек; валидируем, что value = число
      - если var в URL_VARS — не позволяем писать «числа», нормализуем протокол и хвост '/'
      - иначе — строка в двойных кавычках (с экранированием).
    Возвращает короткий HTML-отчёт.
    """
    cfg = Path(bot_dir) / "config.py"
    if not cfg.exists():
        raise FileNotFoundError(f"Не найден config.py в {cfg.parent}")

    src = cfg.read_text(encoding="utf-8")

    URL_VARS = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1","PROMOCODE_WEBAPP_URL","WEBAPP_URL_2"}))
    INT_VARS = set(getattr(C, "INT_VARS", {"ADMIN_ID","REFERRAL_NOTIFY_CHAT_ID"}))

    # Типобезопасность
    if as_int or (var in INT_VARS):
        if not _INT_ONLY_RE.match(str(value)):
            raise ValueError(f"{var} должен быть числом без кавычек")
        rhs = str(int(value))
    else:
        # URL – нормализуем и запрещаем «число» как значение
        v = str(value).strip()
        if var in URL_VARS:
            if _INT_ONLY_RE.match(v):
                raise ValueError(f"{var} — URL, нельзя подставлять число")
            if not re.match(r'^(?:https?://)', v, flags=re.I):
                v = 'https://' + v
            if not re.search(r'[/?#]$', v):
                v += '/'
        safe = v.replace("\\", "\\\\").replace('"', '\\"')
        rhs = f"\"{safe}\""

    new_line = f'{var} = {rhs}'

    pat = re.compile(rf'^(?P<prefix>\s*){re.escape(var)}\s*=.*$', flags=re.M)
    if pat.search(src):
        def _repl(m):
            pref = m.group('prefix') or ''
            return pref + new_line
        out = pat.sub(_repl, src, count=1)
        cfg.write_text(out, encoding="utf-8")
        return f'✅ {escape(Path(bot_dir).name)}: обновлено ({var} → {escape(str(value))})'
    else:
        sep = "" if src.endswith("\n") else "\n"
        out = src + sep + new_line + "\n"
        cfg.write_text(out, encoding="utf-8")
        return f'✅ {escape(Path(bot_dir).name)}: добавлено ({var} → {escape(str(value))})'


def update_config_value_for_bot(new_value: str, var_name: str, bot_dir_str: str) -> str:
    """
    Обновляет ТОЛЬКО у одного бота (bot_dir_str) переменную var_name на new_value.
    Возвращает HTML-резюме по одному боту.

    Использует ту же логику нормализации URL (для URL_VARS),
    делает .bak, пишет красивый лог.
    """
    from html import escape as _esc
    from pathlib import Path as _P
    import re as _re

    var_name = (var_name or "").strip()
    if not var_name:
        msg = "<b>Обновление</b>\n🔴 <code>Имя переменной пусто</code>"
        log("⛔ Имя переменной пусто.")
        return msg

    # Нормализация для URL-переменных (как в update_webapp_url_all)
    url_vars = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL"}))
    val = (new_value or "").strip()
    if not val:
        msg = f"<b>Обновление { _esc(var_name) }</b>\n🔴 <code>Значение пусто</code>"
        log("⛔ Пустое значение.")
        return msg
    if var_name in url_vars:
        # если не указан протокол — добавим https://
        if not _re.match(r'^(?:https?://)', val, flags=_re.I):
            val = 'https://' + val
        # завершим слэшем (если нет query/hash в конце)
        if not _re.search(r'[/?#]$', val):
            val += '/'

    # Один бот
    bot_dir = _P(bot_dir_str)
    cfg = bot_dir / "config.py"
    if not cfg.exists():
        html = f"<b>Обновление { _esc(var_name) }</b>\n" \
               f"• <b>{ _esc(bot_dir.name) }</b> — 🔴 <i>нет config.py</i>"
        log(f"❌ {bot_dir.name}: нет config.py")
        return html

    # Внутренний апдейтер уже есть — используем его
    status, info = _update_one_config(cfg, var_name, val)  # ← использует бэкап и запись

    if status == "updated":
        line = f"• <b>{ _esc(bot_dir.name) }</b> — 🟢 обновлено → <code>{ _esc(val) }</code>"
        log(f"✅ {bot_dir.name}: обновлено ({info})")
        header = f"<b>Обновление { _esc(var_name) } (1 бот)</b>  <code>UPDATED:1</code> | <code>ADDED:0</code> | <code>SAME:0</code> | <code>FAIL:0</code>"
    elif status == "added":
        line = f"• <b>{ _esc(bot_dir.name) }</b> — 🟢 добавлено → <code>{ _esc(val) }</code>"
        log(f"✅ {bot_dir.name}: добавлено ({info})")
        header = f"<b>Обновление { _esc(var_name) } (1 бот)</b>  <code>UPDATED:0</code> | <code>ADDED:1</code> | <code>SAME:0</code> | <code>FAIL:0</code>"
    elif status == "same":
        line = f"• <b>{ _esc(bot_dir.name) }</b> — 🟡 без изменений"
        log(f"ℹ️ {bot_dir.name}: без изменений")
        header = f"<b>Обновление { _esc(var_name) } (1 бот)</b>  <code>UPDATED:0</code> | <code>ADDED:0</code> | <code>SAME:1</code> | <code>FAIL:0</code>"
    else:
        line = f"• <b>{ _esc(bot_dir.name) }</b> — 🔴 { _esc(info) }"
        log(f"❌ {bot_dir.name}: {info}")
        header = f"<b>Обновление { _esc(var_name) } (1 бот)</b>  <code>UPDATED:0</code> | <code>ADDED:0</code> | <code>SAME:0</code> | <code>FAIL:1</code>"

    html = header + "\n" + line

    # По желанию — в лог-чат
    try:
        if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
            send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, html)
    except Exception as e:
        log(f"✉️  send_html error: {e}")

    return html

def _mask_secret(val: str) -> str:
    if not val:
        return "******"
    if len(val) <= 12:
        return "******"
    return f"{val[:6]}…{val[-4:]}"

def _ts() -> str:
    from time import strftime
    return strftime("%Y-%m-%d %H:%M:%S")

def log(msg: str) -> None:
    line = f"[{_ts()}] {msg}"
    try:
        with open(C.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)

def _hr():
    log("─" * 72)

# ─────────────────────────────────────────────────────────
# utils

def read_bot_paths(path: Path) -> list[Path]:
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл со списком ботов: {path}")
    return [Path(s.strip()) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]

def send_html(token: str, chat_id: int, text: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = Request(url, data=data, method="POST")
    with _NO_PROXY.open(req, timeout=getattr(C, "TELEGRAM_TIMEOUT", 15.0)) as resp:
        import json as _json
        return _json.loads(resp.read().decode("utf-8"))

def _ensure_url_norm(url: str) -> str:
    """Нормализуем мягко: добавляем https:// если нет схемы. НИЧЕГО больше не правим."""
    u = (url or "").strip()
    if not u:
        return u
    if not re.match(r'^(?:https?://)', u, flags=re.I):
        u = "https://" + u
    return u

def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)

def _backup_file(path: Path) -> Path | None:
    """Сделать простой .bak с таймстампом; ошибка не фатальна."""
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        bak = path.with_suffix(path.suffix + f".{ts}.bak")
        shutil.copy2(path, bak)
        return bak
    except Exception:
        return None

def _update_one_config(cfg_path: Path, var_name: str, new_value: str) -> tuple[str, str]:
    """
    Возвращает: (status, message)
      status ∈ {"updated", "added", "same", "error"}
    Пишет без кавычек для переменных из C.INT_VARS.
    """
    try:
        original = cfg_path.read_text(encoding="utf-8")
    except Exception as e:
        return "error", f"не прочитан: {e}"

    pattern = re.compile(_ASSIGN_RE_TMPL.format(var=re.escape(var_name)),
                         re.M | re.U | re.X)

    INT_VARS = set(getattr(C, "INT_VARS", set()))

    def _render_line(indent: str) -> str:
        if var_name in INT_VARS:
            return f'{indent}{var_name} = {new_value}'
        else:
            return f'{indent}{var_name} = "{new_value}"'

    m = pattern.search(original)
    if m:
        old_value = (m.group("valq") if m.group("valq") is not None else (m.group("valn") or "")).strip()

        # если для числовых переменных ранее было в кавычках — это ок, сравниваем по строке
        if old_value == new_value:
            return "same", "без изменений"

        indent = m.group("indent") or ""
        replaced_line = _render_line(indent)

        new_content = pattern.sub(replaced_line, original, count=1)
        _backup_file(cfg_path)
        try:
            _atomic_write(cfg_path, new_content)
        except Exception as e:
            return "error", f"не записан: {e}"
        return "updated", f'{old_value} → {new_value}'
    else:
        # добавить в конец
        sep = "" if original.endswith("\n") else "\n"
        added_line = _render_line("")
        new_content = original + sep + added_line + "\n"
        _backup_file(cfg_path)
        try:
            _atomic_write(cfg_path, new_content)
        except Exception as e:
            return "error", f"не записан: {e}"
        return "added", f"добавлено → {new_value}"

# ─────────────────────────────────────────────────────────
# main API

def update_webapp_url_all(new_value: str, var_name: str = "WEBAPP_URL_1", category: str | None = None) -> str:
    """
    Обновляет значение var_name у всех ботов ИЛИ только у ботов выбранной категории.
    Категория определяется по имени папки (вхождение подстрок, без учёта регистра):
      - содержит "BotKazino" → категория "BotKazino"
      - содержит "GGBET"     → категория "GGBET"
      - содержит "1WIN"      → категория "1WIN"
    Правила можно переопределить в config.py:
      CATEGORY_RULES = [("BotKazino", "BotKazino"), ("GGBET", "GGBET"), ("1WIN", "1WIN")]

    :param new_value: новое значение (для URL-переменных — мягко нормализуем схему https://, без завершающего '/')
    :param var_name: имя переменной (например, 'WEBAPP_URL_1' или 'PROMOCODE_WEBAPP_URL')
    :param category: имя категории (строго одно из ключей правил), либо None — обновить всех
    :return: HTML-резюме
    """
    # --- локальные помощники (без внешних зависимостей) ---
    def _hr():
        log("─" * 72)

    def _mask_secret(val: str) -> str:
        if not val:
            return "******"
        return val if len(val) < 12 else f"{val[:6]}…{val[-4:]}"

    def _detect_category(folder_name: str) -> str | None:
        """
        Возвращает имя категории по правилам.
        Правила берутся из C.CATEGORY_RULES (если есть), иначе используются дефолтные:
            [("BotKazino","BotKazino"), ("GGBET","GGBET"), ("1WIN","1WIN")]
        Каждый кортеж: (подстрока_для_поиска, имя_категории).
        Совпадение — по вхождению подстроки (lower()).
        Первое совпадение побеждает.
        """
        rules = list(getattr(C, "CATEGORY_RULES", [("BotKazino", "BotKazino"),
                                                   ("GGBET",     "GGBET"),
                                                   ("1WIN",      "1WIN")]))
        s = folder_name.lower()
        for needle, cat in rules:
            if str(needle).lower() in s:
                return str(cat)
        return None

    URL_VARS    = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL"}))
    SECRET_VARS = set(getattr(C, "SECRET_VARS", {"BOT_TOKEN"}))

    var_name  = (var_name or "").strip()
    new_value = (new_value or "").strip()
    category  = (category or "").strip() or None  # пустые строки → None

    # нормализация по типу переменной
    if var_name in URL_VARS:
        new_value = _ensure_url_norm(new_value)

    shown_value = _mask_secret(new_value) if var_name in SECRET_VARS else new_value

    _hr()
    log(f"🚀 Обновление переменной: {var_name}" + (f" (категория: {category})" if category else " (все категории)"))
    log(f"🔧 Новое значение: {shown_value if shown_value else '<пусто>'}")
    _hr()

    if not var_name:
        html = "<b>Обновление переменной</b>\n🔴 <code>Имя переменной пусто</code>"
        log("⛔ Имя переменной пусто.")
        return html
    if not new_value:
        html = f"<b>Обновление {escape(var_name)}</b>\n🔴 <code>Значение пусто</code>"
        log("⛔ Значение пусто.")
        return html

    # читаем список ботов
    try:
        bot_dirs = read_bot_paths(C.BOT_LIST_FILE)
    except Exception as e:
        html = f"<b>Обновление {escape(var_name)}</b>\n🔴 <code>{escape(str(e))}</code>"
        log(f"⛔ Ошибка чтения списка ботов: {e}")
        return html

    # если задана категория — отфильтруем
    if category:
        before = len(bot_dirs)
        bot_dirs = [p for p in bot_dirs if _detect_category(p.name) == category]
        log(f"Фильтр категории '{category}': {len(bot_dirs)} из {before}")

        if not bot_dirs:
            html = (f"<b>Обновление {escape(var_name)}</b> (категория: <code>{escape(category)}</code>)\n"
                    f"🟡 <i>Подходящих ботов не найдено.</i>")
            log("ℹ️ Подходящих ботов не найдено по выбранной категории.")
            return html

    # подготовим шаблон для поиска текущего значения (до записи)
    pat = re.compile(_ASSIGN_RE_TMPL.format(var=re.escape(var_name)), re.M | re.U)

    lines: list[str] = []
    ok_updated = ok_added = same = errors = 0

    for bot_dir in bot_dirs:
        cfg = bot_dir / "config.py"
        name = bot_dir.name

        if not cfg.exists():
            lines.append(f"• <b>{escape(name)}</b> — 🔴 <i>нет config.py</i>")
            log(f"❌ {name}: нет config.py")
            errors += 1
            continue

        # Выясним текущее значение, чтобы уметь считать SAME и не переписывать файл зря
        try:
            txt = cfg.read_text(encoding="utf-8")
        except Exception as e:
            lines.append(f"• <b>{escape(name)}</b> — 🔴 не читается config.py: {escape(str(e))}")
            log(f"❌ {name}: не читается config.py — {e}")
            errors += 1
            continue

        cur_val_norm: str | None = None
        m = pat.search(txt)
        if m:
            cur_val = (m.group("val") or "").strip()
            cur_val_norm = _ensure_url_norm(cur_val) if var_name in URL_VARS else cur_val

        # если уже совпадает — пропускаем
        if cur_val_norm is not None and cur_val_norm == new_value:
            same += 1
            lines.append(f"• <b>{escape(name)}</b> — 🟡 без изменений")
            log(f"ℹ️ {name}: без изменений")
            continue

        # Пишем новое значение через стандартный апдейтер
        try:
            status, info = _update_one_config(cfg, var_name, new_value)
        except Exception as e:
            status, info = "error", f"ошибка записи: {e}"

        if status == "updated":
            ok_updated += 1
            lines.append(f"• <b>{escape(name)}</b> — 🟢 обновлено → <code>{escape(shown_value)}</code>")
            log(f"✅ {name}: обновлено")
        elif status == "added":
            ok_added += 1
            lines.append(f"• <b>{escape(name)}</b> — 🟢 добавлено → <code>{escape(shown_value)}</code>")
            log(f"✅ {name}: добавлено")
        elif status == "same":
            same += 1
            lines.append(f"• <b>{escape(name)}</b> — 🟡 без изменений")
            log(f"ℹ️ {name}: без изменений")
        else:
            errors += 1
            lines.append(f"• <b>{escape(name)}</b> — 🔴 {escape(info)}")
            log(f"❌ {name}: {info}")

    # шапка + тело
    header = (
        f"<b>Обновление {escape(var_name)}</b>"
        + (f"  <i>(категория: {escape(category)})</i>" if category else "")
        + f"  <code>UPDATED:{ok_updated}</code> | "
          f"<code>ADDED:{ok_added}</code> | "
          f"<code>SAME:{same}</code> | "
          f"<code>FAIL:{errors}</code>"
    )
    body = "\n".join(lines) if lines else "<i>Список пуст</i>"
    html = header + "\n" + body

    _hr()
    log("🏁 Готово.")
    _hr()

    # по желанию — отправляем в лог-чат
    try:
        if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
            send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, html)
    except Exception as e:
        log(f"✉️ send_html error: {e}")

    return html


# CLI: python updater.py https://new-domain.tld/ [VAR_NAME]
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python updater.py <domain_or_url> [VAR_NAME]")
        sys.exit(1)
    url = sys.argv[1]
    var = sys.argv[2] if len(sys.argv) >= 3 else "WEBAPP_URL_1"
    print(update_webapp_url_all(url, var))
