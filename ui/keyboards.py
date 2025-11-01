# -*- coding: utf-8 -*-
from html import escape
from pathlib import Path
import config as C
from .state import (
    BF_SELECT_BOT_PREFIX, BF_BOTFATHER_CREATE_NEW_CB, BF_ENTER_USERNAME_CB,
    BF_CLOSE_SESSION_CB, BF_MENU_BTN_PREFIX
)
from .state import BOT_USERNAME  

def _inline_kb_grid(items: list[tuple[str, str]], cols: int = 3) -> list[list[dict]]:
    rows, row = [], []
    cols = max(1, int(cols or 1))
    for title, cb in items:
        row.append({"text": title, "callback_data": cb})
        if len(row) >= cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    return rows

def _categories() -> list[str]:
    rules = list(getattr(C, "CATEGORY_RULES", [("BotKazino","BotKazino"),("GGBET","GGBET"),("1WIN","1WIN")]))
    seen, cats = set(), []
    for _, cat in rules:
        cs = str(cat)
        if cs not in seen:
            seen.add(cs); cats.append(cs)
    return cats

def _build_main_keyboard():
    return [
        [
            {"text": "⟳ Обновить статус", "callback_data": getattr(C, "RELOAD_STATUS_CB", "reload_status")},
            {"text": "🔁 Рестарт всех",   "callback_data": getattr(C, "RESTART_ALL_CB", "restart_all")},
        ],
        [{"text": "🎯 Рестарт бота", "callback_data": getattr(C, "RESTART_ONE_CB", "restart_one")}],
        [{"text": "✏️ Обновить домены", "callback_data": getattr(C, "UPDATE_DOMAINS_CB", "update_domains")}],
        [{"text": "🤖 Создать через BotFather", "callback_data": getattr(C, "CREATE_BOTFATHER_CB", "create_botfather")}],
        [{"text": "➕ Создать бота", "callback_data": getattr(C, "CREATE_NEW_CB", "create_new")}],
    ]

def build_update_menu_keyboard() -> list[list[dict]]:
    kb = [
        [
            {"text": "WEBAPP_URL_1",         "callback_data": getattr(C, "UPDATE_VAR_WEBAPP1_CB", "upd_var:webapp1")},
            {"text": "PROMOCODE_WEBAPP_URL", "callback_data": getattr(C, "UPDATE_VAR_PROMO_CB",   "upd_var:promo")},
        ],
        [
            {"text": "BOT_TOKEN",     "callback_data": getattr(C, "UPDATE_VAR_BOT_TOKEN_CB", "upd_var:token")},
            {"text": "IMAGE_FILE_ID", "callback_data": getattr(C, "UPDATE_VAR_IMAGE_CB",     "upd_var:image")},
        ],
    ]
    if BOT_USERNAME:
        kb.append([{"text": "🔒 Отправить значение в ЛС", "url": f"https://t.me/{BOT_USERNAME}"}])
    kb.append([{"text": "⬅ Назад", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back")}])
    return kb

def _build_bf_root_kb(bots: list[str] | None) -> list[list[dict]]:
    rows: list[list[dict]] = []
    rows.append([{"text": "➕ Создать нового бота", "callback_data": BF_BOTFATHER_CREATE_NEW_CB}])
    bot_items = [(u, f"{BF_SELECT_BOT_PREFIX}{u}") for u in (bots or [])[:24]]
    rows += _inline_kb_grid(bot_items, cols=1)
    rows.append([{"text": "✍ Ввести @username вручную", "callback_data": BF_ENTER_USERNAME_CB}])
    rows.append([
        {"text": "⬅ Назад", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back")},
        {"text": "❌ Закрыть сессию", "callback_data": getattr(C, "BF_CLOSE_SESSION_CB", "bf_close")},
    ])
    return rows

def _build_bf_bot_menu_kb(username: str) -> list[list[dict]]:
    u = username if str(username).startswith("@") else f"@{username}"
    return [
        [
            {"text": "✏️ About",      "callback_data": f"{getattr(C,'BF_EDIT_ABOUT_PREFIX','bf_edit_about:')}{u}"},
            {"text": "📝 Description", "callback_data": f"{getattr(C,'BF_EDIT_DESC_PREFIX','bf_edit_desc:')}{u}"},
        ],
        [
            {"text": "🖼 Botpic",      "callback_data": f"{getattr(C,'BF_EDIT_BOTPIC_PREFIX','bf_edit_botpic:')}{u}"},
        ],
        [{"text": "🍔 Menu Button",  "callback_data": f"{BF_MENU_BTN_PREFIX}{u}"}],
        [{"text": "⬅ К корню мастера", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back")}],
    ]

def _build_category_keyboard(for_var: str) -> list[list[dict]]:
    CAT_PREFIX = getattr(C, "UPDATE_CATEGORY_PREFIX", "update_cat:")
    cols       = int(getattr(C, "RESTART_ONE_COLS", 3))
    items = [("🌐 Все", f"{CAT_PREFIX}ALL:{for_var}")]
    items += [(cat, f"{CAT_PREFIX}{cat}:{for_var}") for cat in _categories()]
    rows = _inline_kb_grid(items, cols=cols)
    rows.append([{"text": "⬅ Назад", "callback_data": getattr(C, "UPDATE_DOMAINS_CB", "update_domains")}])
    return rows

def build_restart_one_keyboard(bot_dirs: list[Path]) -> list[list[dict]]:
    max_list = int(getattr(C, "RESTART_ONE_MAX", 30))
    cols     = int(getattr(C, "RESTART_ONE_COLS", 3))
    prefix   = getattr(C, "RESTART_ONE_PREFIX", "restart_one:")
    bot_dirs = (bot_dirs or [])[:max_list]
    items = []
    for idx, p in enumerate(bot_dirs):
        title = (p.name or "")[:32]
        items.append((title, f"{prefix}{idx}"))
    rows = _inline_kb_grid(items, cols=cols)
    rows.append([{"text": "⬅ Назад", "callback_data": getattr(C, "BACK_TO_STATUS_CB", "back")}])
    return rows

def render_category_choice(var: str) -> tuple[str, list[list[dict]]]:
    html = f"<b>Обновление {escape(var)}</b>\nВыберите категорию, для которой нужно задать URL."
    kb = _build_category_keyboard(var)
    return html, kb

def build_choose_bot_kb(var: str, bot_dirs: list[Path]) -> list[list[dict]]:
    """
    Сетка выбора конкретного бота для апдейта переменной var.
    """
    cols     = int(getattr(C, "RESTART_ONE_COLS", 3))
    max_list = int(getattr(C, "RESTART_ONE_MAX", 30))
    prefix   = getattr(C, "UPDATE_ONE_PREFIX", "update_one:")

    bot_dirs = (bot_dirs or [])[:max_list]
    rows, row = [], []
    for idx, p in enumerate(bot_dirs):
        title = (p.name or "")[:32]
        row.append({"text": title, "callback_data": f"{prefix}{idx}:{var}"})
        if len(row) >= cols:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([{"text": "⬅ Назад", "callback_data": getattr(C, "UPDATE_DOMAINS_CB", "update_domains")}])
    return rows

def build_status_nav_row(page_index: int, total_pages: int) -> list[list[dict]]:
    """
    Строит строку клавиатуры со стрелками для перелистывания страниц в меню /status.
    Возвращает список из одной строки (или пустой, если страниц 1).
    """
    nav = []
    if page_index > 0:
        nav.append({"text": "⬅", "callback_data": f"status_page:{page_index - 1}"})
    if page_index < total_pages - 1:
        nav.append({"text": "➡", "callback_data": f"status_page:{page_index + 1}"})
    return [nav] if nav else []