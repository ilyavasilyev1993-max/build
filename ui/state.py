# ui/state.py
from __future__ import annotations

import re
from pathlib import Path

import config as C

# глобальные regex
URL_RE   = re.compile(r'(?i)\b((?:https?://)?(?:[a-z0-9-]+\.)+[a-z]{2,}(?:/[^\s<>"\']*)?)')
TOKEN_RE = re.compile(r'\b\d{6,}:[A-Za-z0-9_-]{20,}\b')

# ожидаем ввод нового значения
PENDING_UPDATE: dict[int, dict] = {}

BOT_USERNAME: str | None = None

# BF-состояния
PENDING_BF_SESSION   = set()
BF_SESSION_PATH      = {}
BF_KNOWN_BOTS        = {}
BF_ENTER_USERNAME_WAIT = set()
BF_CHAIN_AFTER_CREATE  = set()

BF_CREATE_NAME_WAIT     = set()
BF_CREATE_USERNAME_WAIT = {}

BF_EDIT_ABOUT_WAIT  = {}
BF_EDIT_DESC_WAIT   = {}
BF_EDIT_BOTPIC_WAIT = {}
BF_MENUBTN_URL_WAIT   = {}
BF_MENUBTN_TITLE_WAIT = {}

BF_MENU_BTN_PREFIX     = getattr(C, "BF_MENU_BTN_PREFIX", "bf_menu_btn:")

BF_TMP_DIR = (C.BASE_DIR / "_bf_tmp")
BF_TMP_DIR.mkdir(exist_ok=True)

BF_SELECT_BOT_PREFIX   = getattr(C, "BF_SELECT_BOT_PREFIX", "bf_select:")
BF_BOTFATHER_CREATE_NEW_CB = getattr(C, "BF_CREATE_NEW_CB", "bf_new")
BF_BACK_TO_LIST_CB     = getattr(C, "BF_BACK_TO_LIST_CB", "bf_back")
BF_EDIT_ABOUT_PREFIX   = getattr(C, "BF_EDIT_ABOUT_PREFIX", "bf_edit_about:")
BF_EDIT_DESC_PREFIX    = getattr(C, "BF_EDIT_DESC_PREFIX", "bf_edit_desc:")
BF_EDIT_BOTPIC_PREFIX  = getattr(C, "BF_EDIT_BOTPIC_PREFIX", "bf_edit_botpic:")
BF_CLOSE_SESSION_CB    = getattr(C, "BF_CLOSE_SESSION_CB", "bf_close")
BF_ENTER_USERNAME_CB   = getattr(C, "BF_ENTER_USERNAME_CB", "bf_enter_uname")
BF_GET_TOKEN_PREFIX    = getattr(C, "BF_GET_TOKEN_PREFIX", "bf_get_token:")
