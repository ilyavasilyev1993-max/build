# -*- coding: utf-8 -*-
import time, json, re
from html import escape
from pathlib import Path
from urllib.error import URLError, HTTPError
import math
import config as C
from net import tg_get
from updater import update_webapp_url_all, set_config_value_strict
from zapusk import (
    log,
    read_bot_paths,        # ← добавь
    tg_get_me,
    start_bot,
    build_status_message,
    read_bot_paths,
    do_restart_one,        # ← и это, если ниже п.2 используешь
)
from ui.parsers import bf_validate_username as _bf_validate_username
from ui.keyboards import build_choose_bot_kb as _build_choose_bot_kb, build_status_nav_row

# внешние зависимости (не менялись)
from botfather import (
    bf_list_bots, bf_create_minimal,
    bf_set_about, bf_set_description, bf_set_botpic,
    bf_get_token, bf_set_menu_button_via_ui
)
from sozdanie import (
    request_folder_name, handle_folder_name_input,
    start_created_bot, start_image_capture, build_creation_keyboard,
    request_autoconfig, parse_and_apply_autoconfig,
    request_promo_update, apply_promo_update,
    PENDING_CREATE_NAME, PENDING_AUTOCONF, PENDING_PROMO,
    CREATION_SESSIONS,
)

# из наших ui-модулей:
from .telegram_io import send_html, send_html_with_keyboard, answer_callback, edit_message_html, get_updates
from .parsers import ensure_bot_username, extract_value_by_var, _mask_secret
from .keyboards import (
    _build_main_keyboard, build_update_menu_keyboard, build_restart_one_keyboard,
    render_category_choice, _build_bf_root_kb, _build_bf_bot_menu_kb, _inline_kb_grid
)
from .state import *
from .restart import do_restart_all

def run_status_command_loop():
    """Лонг-пулл: /status + кнопки; URL-переменные по категориям + BotFather мастер + создание бота."""
    if not C.STATUS_BOT_TOKEN or not C.STATUS_CHAT_IDS:
        return

    ensure_bot_username()
    offset = None
    sleep_backoff = 1.0

    url_vars    = set(getattr(C, "URL_VARS", {"WEBAPP_URL_1", "PROMOCODE_WEBAPP_URL"}))
    secret_vars = set(getattr(C, "SECRET_VARS", {"BOT_TOKEN"}))
    CAT_PREFIX  = getattr(C, "UPDATE_CATEGORY_PREFIX", "update_cat:")

    # ——— локальные помощники ———
    def _delete_message(chat_id: int, message_id: int):
        try:
            from net import tg_post
            tg_post(C.STATUS_BOT_TOKEN, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            pass

    def _build_status_page(page_index: int, per_page: int = 5) -> tuple[str, int]:
        full = build_status_message(C.BOT_LIST_FILE, C.PIDS_FILE)
        lines = full.split("\n")
        header = lines[0] if lines else ""
        items = [l for l in lines[1:] if l.strip()]

        total_pages = max(1, math.ceil(len(items) / per_page))
        page_index = max(0, min(page_index, total_pages - 1))

        start = page_index * per_page
        end = start + per_page
        sub_items = items[start:end]

        body = "\n".join(sub_items) if sub_items else "<i>Список пуст</i>"
        header_paged = f"{header} — стр. {page_index + 1}/{total_pages}"

        # Добавьте return, чтобы вернуть HTML и количество страниц:
        return f"{header_paged}\n{body}", total_pages

    def _status_keyboard(is_admin: bool, page_index: int, pages_count: int) -> list[list[dict]]:
        keyboard: list[list[dict]] = []

        # добавляем стрелки перелистывания
        nav_row = build_status_nav_row(page_index, pages_count)
        if nav_row:
            keyboard += nav_row

        if is_admin:
            keyboard += _build_main_keyboard()
        else:
            # только «Обновить», «Создать бота», «Создать через BotFather» на отдельных строках
            keyboard += [
                [{"text": "⟳ Обновить статус", "callback_data": getattr(C, "RELOAD_STATUS_CB", "reload_status")}],
                [{"text": "➕ Создать бота",     "callback_data": getattr(C, "CREATE_NEW_CB",    "create_new")}],
                [{"text": "🤖 Создать через BotFather", "callback_data": getattr(C, "CREATE_BOTFATHER_CB", "create_botfather")}],
            ]
        return keyboard

    def _replace_message(chat_id: int, old_msg_id: int | None, html: str, kb: list[list[dict]] | None):
        if old_msg_id:
            _delete_message(chat_id, old_msg_id)
        try:
            from net import tg_post
            payload = {
                "chat_id": chat_id,
                "text": html,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if kb is not None:
                payload["reply_markup"] = json.dumps({"inline_keyboard": kb}, ensure_ascii=False)
            resp = tg_post(C.STATUS_BOT_TOKEN, "sendMessage", payload)
            return ((resp or {}).get("result") or {}).get("message_id")
        except Exception:
            return None

    def _find_creation_token_by_dir(dir_str: str | None) -> str | None:
        if not dir_str:
            return None
        try:
            d = Path(dir_str).resolve()
        except Exception:
            return None
        for tok, sess in CREATION_SESSIONS.items():
            sdir = sess.get("dir")
            if isinstance(sdir, Path) and sdir.resolve() == d:
                return tok
        return None

    def _poll_image_captures():
        """Ловим фото/гиф у только что созданного бота и пишем IMAGE_FILE_ID в config."""
        for token, sess in list(CREATION_SESSIONS.items()):
            bot_token = sess.get("img_bot_token")
            if not bot_token:
                continue
            params = {}
            if sess.get("img_offset") is not None:
                try:
                    params["offset"] = int(sess["img_offset"])
                except Exception:
                    params = {}
            try:
                data = tg_get(bot_token, "getUpdates", params or None, timeout=5)
            except Exception:
                continue
            for item in data.get("result", []):
                try:
                    sess["img_offset"] = int(item["update_id"]) + 1
                except Exception:
                    pass
                m = item.get("message") or {}
                file_id = None
                if m.get("photo"):
                    last = m["photo"][-1] or {}
                    file_id = last.get("file_id")
                if not file_id and m.get("animation"):
                    anim = m["animation"] or {}
                    file_id = anim.get("file_id")
                if not file_id:
                    continue
                bot_dir = sess.get("dir")
                if not bot_dir:
                    continue
                try:
                    set_config_value_strict(Path(bot_dir).as_posix(), "IMAGE_FILE_ID", file_id, as_int=False)
                    for k in ("img_bot_token", "img_offset", "img_pid"):
                        sess.pop(k, None)
                    ui_chat = sess.get("ui_chat_id")
                    ui_msg  = sess.get("ui_msg_id")
                    tip = (f"✅ IMAGE_FILE_ID обновлён: <code>{escape(file_id)}</code>\n"
                           "Можете продолжить настройку или запустить бота.")
                    kb  = build_creation_keyboard(token)
                    if ui_chat:
                        new_mid = _replace_message(int(ui_chat), int(ui_msg or 0), tip, kb)
                        if new_mid:
                            sess["ui_msg_id"] = new_mid
                except Exception as e:
                    log(f"[CREATE][IMAGE] ошибка записи IMAGE_FILE_ID: {e}")

    # ——— основной цикл ———
    while True:
        try:
            _poll_image_captures()
            updates = get_updates(C.STATUS_BOT_TOKEN, offset)
            sleep_backoff = 1.0
            if not updates.get("ok"):
                time.sleep(0.5); continue

            for upd in updates.get("result", []):
                offset = upd["update_id"] + 1

                # — сообщения —
                msg = upd.get("message") or upd.get("edited_message")
                if msg:
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    chat_type = (chat.get("type") or "")
                    text = (msg.get("text") or "").strip()
                    from_u = msg.get("from") or {}
                    user_id = from_u.get("id")
                    msg_id  = msg.get("message_id")

                    is_admin_dm = (chat_type == "private" and user_id == C.ADMIN_USER_ID)

                    # ===== BotFather: ждём .session (только ЛС) =====
                    if is_admin_dm and user_id in PENDING_BF_SESSION:
                        doc = msg.get("document")
                        if not doc:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "Пришлите .session файлом-документом.")
                            except Exception: pass
                            continue
                        file_id = doc.get("file_id")
                        try:
                            file_resp = tg_get(C.STATUS_BOT_TOKEN, "getFile", {"file_id": file_id})
                            file_path = (file_resp.get("result") or {}).get("file_path")
                            from urllib.request import urlretrieve
                            url = f"https://api.telegram.org/file/bot{C.STATUS_BOT_TOKEN}/{file_path}"
                            local_path = BF_TMP_DIR / f"{user_id}.session"
                            urlretrieve(url, local_path.as_posix())
                        except Exception as e:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, f"Не удалось скачать файл: <code>{escape(str(e))}</code>")
                            except Exception: pass
                            continue

                        PENDING_BF_SESSION.discard(user_id)
                        BF_SESSION_PATH[user_id] = local_path

                        ok, bots, report = bf_list_bots(local_path)
                        BF_KNOWN_BOTS[user_id] = bots if ok else []
                        tip = "<b>Аккаунт подключён.</b>\nВыберите действие ниже."
                        send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id, tip, _build_bf_root_kb(None))
                        continue

                    # ===== BotFather: ручной ввод @username =====
                    if is_admin_dm and user_id in BF_ENTER_USERNAME_WAIT:
                        BF_ENTER_USERNAME_WAIT.discard(user_id)
                        uname_raw = (text or "").strip()
                        problems = _bf_validate_username(uname_raw)
                        if problems:
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                        "⛔ Неверный @username.\n• " + "\n• ".join(problems) +
                                        "\n\nВведите корректный @username.")
                            except Exception: pass
                            BF_ENTER_USERNAME_WAIT.add(user_id)
                            continue

                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена (сначала загрузите .session).")
                            except Exception: pass
                            continue

                        # Проверим владение (и заодно получим токен); если не твой — предупредим, но меню всё равно откроем
                        try:
                            own_ok, token_opt, rep = bf_get_token(spath, uname_raw)
                            if not own_ok:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                        "⚠️ Похоже, этот бот может не принадлежать этой сессии.\n" + rep)
                        except Exception:
                            pass

                        uname_norm = uname_raw if uname_raw.startswith("@") else f"@{uname_raw}"
                        try:
                            send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                f"<b>Меню бота {escape(uname_norm)}</b>", _build_bf_bot_menu_kb(uname_norm))
                        except Exception: pass
                        continue
                            
                    # ===== BotFather: Создание — шаг 1 (Name) =====
                    if is_admin_dm and user_id in BF_CREATE_NAME_WAIT:
                        BF_CREATE_NAME_WAIT.discard(user_id)
                        BF_CREATE_USERNAME_WAIT[user_id] = {"name": text.strip()}
                        try: send_html(C.STATUS_BOT_TOKEN, chat_id, "Теперь введите <b>@username</b> (должен заканчиваться на <code>bot</code>).")
                        except Exception: pass
                        continue

                    # ===== BotFather: Создание — шаг 2 (@username) =====
                    if is_admin_dm and user_id in BF_CREATE_USERNAME_WAIT:
                        ctx = BF_CREATE_USERNAME_WAIT.pop(user_id)
                        uname_raw = text.strip()
                        problems = _bf_validate_username(uname_raw)
                        if problems:
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                          "⛔ Неверный @username.\n• " + "\n• ".join(problems) +
                                          "\n\nВведите другой @username (пример: <code>MyCasinoHelperBot</code>).")
                            except Exception: pass
                            BF_CREATE_USERNAME_WAIT[user_id] = ctx
                            continue

                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена. Нажмите «Создать через BotFather» заново.")
                            except Exception: pass
                            continue

                        name = ctx.get("name") or ""
                        try:
                            ok, token, report = bf_create_minimal(spath, name, uname_raw)
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, report)
                            except Exception: pass

                            # обновим кеш списка, но главное — запустим цепочку правок
                            try:
                                ok2, bots, report2 = bf_list_bots(spath)
                                BF_KNOWN_BOTS[user_id] = bots if ok2 else BF_KNOWN_BOTS.get(user_id, [])
                            except Exception:
                                pass

                            # сразу после создания — начинаем мастер заполнения профиля
                            uname_norm = uname_raw if uname_raw.startswith("@") else f"@{uname_raw}"
                            BF_CHAIN_AFTER_CREATE.add(user_id)
                            BF_EDIT_ABOUT_WAIT[user_id] = uname_norm
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                    f"✅ Бот создан: <code>{escape(uname_norm)}</code>\n"
                                    "Давайте быстро настроим профиль.\n\n"
                                    "Введите <b>About</b> (до 120 символов) или '-' чтобы пропустить.")
                            except Exception:
                                pass
                        except Exception as e:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, f"🔴 Ошибка создания: <code>{escape(str(e))}</code>")
                            except Exception: pass
                        continue

                    # ===== BotFather: Редактирование — ждём About =====
                    if is_admin_dm and user_id in BF_EDIT_ABOUT_WAIT:
                        uname = BF_EDIT_ABOUT_WAIT.pop(user_id)
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена.")
                            except Exception: pass
                            continue

                        about_text = text.strip()
                        if about_text != "-":
                            ok, msg_out = bf_set_about(spath, uname, about_text)
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, msg_out)
                            except Exception: pass

                        # если идём по цепочке сразу после создания — спросим Description
                        if user_id in BF_CHAIN_AFTER_CREATE:
                            BF_EDIT_DESC_WAIT[user_id] = uname
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                        "Теперь введите <b>Description</b> (до ~512 символов) или '-' чтобы пропустить.")
                            except Exception: pass
                        else:
                            try:
                                send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                    f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                            except Exception: pass
                        continue

                    # ===== BotFather: Редактирование — ждём Description =====
                    if is_admin_dm and user_id in BF_EDIT_DESC_WAIT:
                        uname = BF_EDIT_DESC_WAIT.pop(user_id)
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена.")
                            except Exception: pass
                            continue

                        # гарантированно получаем строку и объявляем переменную заранее
                        desc_text = (text or "").strip()

                        if desc_text != "-":
                            ok, msg_out = bf_set_description(spath, uname, desc_text)
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id, msg_out)
                            except Exception:
                                pass

                        if user_id in BF_CHAIN_AFTER_CREATE:
                            BF_EDIT_BOTPIC_WAIT[user_id] = uname
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id,
                                    "И последнее: пришлите <b>фото</b> для аватара (как изображение),\n"
                                    "или отправьте '-' чтобы пропустить.")
                            except Exception:
                                pass
                        else:
                            try:
                                send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                    f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                            except Exception:
                                pass
                        continue

                    # ===== BotFather: Редактирование — ждём фото для Botpic =====
                    if is_admin_dm and user_id in BF_EDIT_BOTPIC_WAIT:
                        uname = BF_EDIT_BOTPIC_WAIT.get(user_id)
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            BF_EDIT_BOTPIC_WAIT.pop(user_id, None)
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена.")
                            except Exception: pass
                            continue

                        local_photo = None
                        photo = msg.get("photo")
                        # поддержка пропуска по '-'
                        if (msg.get("text") or "").strip() == "-":
                            BF_EDIT_BOTPIC_WAIT.pop(user_id, None)
                            if user_id in BF_CHAIN_AFTER_CREATE:
                                BF_CHAIN_AFTER_CREATE.discard(user_id)
                            try:
                                send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                    f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                            except Exception: pass
                            continue
                        if not photo:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, "Пришлите фото как изображение.")
                            except Exception: pass
                            continue
                        try:
                            fid = (photo[-1] or {}).get("file_id")
                            file_resp = tg_get(C.STATUS_BOT_TOKEN, "getFile", {"file_id": fid})
                            fp = (file_resp.get("result") or {}).get("file_path")
                            from urllib.request import urlretrieve
                            local_photo = BF_TMP_DIR / f"{user_id}_botpic.jpg"
                            url = f"https://api.telegram.org/file/bot{C.STATUS_BOT_TOKEN}/{fp}"
                            urlretrieve(url, local_photo.as_posix())
                            ok, msg_out = bf_set_botpic(spath, uname, local_photo)
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, msg_out)
                            except Exception: pass
                        except Exception as e:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, f"🔴 Не удалось скачать/установить фото: <code>{escape(str(e))}</code>")
                            except Exception: pass
                        finally:
                            try:
                                if local_photo and local_photo.exists(): local_photo.unlink()
                            except Exception: pass

                        BF_EDIT_BOTPIC_WAIT.pop(user_id, None)
                        if user_id in BF_CHAIN_AFTER_CREATE:
                            BF_CHAIN_AFTER_CREATE.discard(user_id)
                        try:
                            send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                        except Exception: pass
                        continue

                    # ===== BotFather: Menu Button — ждём URL =====
                    if is_admin_dm and user_id in BF_MENUBTN_URL_WAIT:
                        uname = BF_MENUBTN_URL_WAIT.pop(user_id)
                        url_text = (text or "").strip()
                        if url_text.lower() != "/empty":
                            if not re.match(r'^(?i)(https?://)\S+', url_text):
                                try:
                                    send_html(C.STATUS_BOT_TOKEN, chat_id,
                                              "⛔ Некорректный URL. Пришлите полный адрес (https://...) или <code>/empty</code>.")
                                except Exception:
                                    pass
                                BF_MENUBTN_URL_WAIT[user_id] = uname
                                continue
                        BF_MENUBTN_TITLE_WAIT[user_id] = {"uname": uname, "url": url_text}
                        try:
                            send_html(C.STATUS_BOT_TOKEN, chat_id, "Теперь отправьте <b>Title</b> (или <code>/empty</code>).")
                        except Exception:
                            pass
                        continue

                    # ===== BotFather: Menu Button — ждём Title и применяем =====
                    if is_admin_dm and user_id in BF_MENUBTN_TITLE_WAIT:
                        ctx   = BF_MENUBTN_TITLE_WAIT.pop(user_id)
                        uname = ctx.get("uname")
                        url   = ctx.get("url")
                        title = (text or "").strip()
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id, "🔴 Сессия не найдена.")
                            except Exception:
                                pass
                            continue
                        ok, msg_out = bf_set_menu_button_via_ui(
                            spath,
                            uname,
                            None if (url or "").lower() == "/empty" else url,
                            None if title.lower() == "/empty" else title
                        )
                        try:
                            send_html(C.STATUS_BOT_TOKEN, chat_id, msg_out)
                            send_html_with_keyboard(C.STATUS_BOT_TOKEN, chat_id,
                                f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                        except Exception:
                            pass
                        continue

                    # ===== Остальной твой функционал =====

                    # A) ждём имя папки при создании
                    if user_id == C.ADMIN_USER_ID and user_id in PENDING_CREATE_NAME:
                        try:
                            html, kb = handle_folder_name_input(user_id, text)
                        except Exception as e:
                            html = f"<b>Создание нового бота</b>\n🔴 <code>{escape(str(e))}</code>"
                            kb   = [[{"text": "⬅ Назад", "callback_data": C.BACK_TO_STATUS_CB}]]
                        origin = PENDING_CREATE_NAME.get(user_id, {})
                        new_mid = _replace_message(
                            origin.get("chat_id", chat_id),
                            origin.get("message_id", msg_id),
                            html, kb
                        )
                        if new_mid:
                            for tok, sess in CREATION_SESSIONS.items():
                                if sess.get("ui_msg_id"):
                                    continue
                                sess["ui_chat_id"] = chat_id
                                sess["ui_msg_id"]  = new_mid
                        continue

                    # A2) ждём автонастройку config
                    if user_id == C.ADMIN_USER_ID and user_id in PENDING_AUTOCONF:
                        ctx = PENDING_AUTOCONF.pop(user_id, {})
                        token = ctx.get("token")
                        result_html = parse_and_apply_autoconfig(token, text)
                        kb = build_creation_keyboard(token) if token else None
                        if token in CREATION_SESSIONS:
                            CREATION_SESSIONS[token]["ui_chat_id"] = chat_id
                        new_mid = _replace_message(int(ctx.get("chat_id", chat_id)), int(ctx.get("message_id", msg_id)), result_html, kb)
                        if new_mid and token in CREATION_SESSIONS:
                            CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        continue

                    # A3) ждём промо и сумму
                    if user_id == C.ADMIN_USER_ID and user_id in PENDING_PROMO:
                        ctx = PENDING_PROMO.pop(user_id, {})
                        token = ctx.get("token")
                        result_html = apply_promo_update(token, text)
                        kb = build_creation_keyboard(token) if token else None
                        if token in CREATION_SESSIONS:
                            CREATION_SESSIONS[token]["ui_chat_id"] = chat_id
                        new_mid = _replace_message(int(ctx.get("chat_id", chat_id)), int(ctx.get("message_id", msg_id)), result_html, kb)
                        if new_mid and token in CREATION_SESSIONS:
                            CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        continue

                    # B) ждём ввод значения (общие апдейты)
                    st = PENDING_UPDATE.get(user_id)
                    if st and user_id == C.ADMIN_USER_ID:
                        var         = st.get("var")
                        origin_chat = st.get("chat_id") or chat_id
                        origin_mid  = st.get("message_id") or msg_id
                        target_dir  = st.get("bot_dir")
                        category    = st.get("category")

                        new_val = extract_value_by_var(var, text)
                        if not new_val:
                            tip = ("Отправьте URL вида <code>https://example.com/</code>"
                                   if var in url_vars else "Отправьте корректное значение.")
                            try:
                                send_html(C.STATUS_BOT_TOKEN, chat_id, f"<i>Не нашёл подходящее значение.</i> {tip}")
                            except Exception:
                                pass
                            continue

                        shown = _mask_secret(new_val) if var in secret_vars else new_val
                        try:
                            scope = (f"для категории <b>{escape(category)}</b> " if (category and var in url_vars) else
                                     ("для <b>всех</b> " if (category is None and var in url_vars) else ""))
                            send_html(C.STATUS_BOT_TOKEN, chat_id,
                                      f"✅ Принято. Обновляю <b>{var}</b> {scope}на <code>{escape(shown)}</code> …")
                        except Exception:
                            pass

                        try:
                            if target_dir:
                                as_int = var in getattr(C, "INT_VARS", set())
                                result_html = set_config_value_strict(target_dir, var, new_val, as_int=as_int)
                            else:
                                result_html = update_webapp_url_all(new_val, var_name=var, category=category)
                        except Exception as e:
                            log(f"[UPDATE] error: {e}")
                            result_html = f"<b>Обновление {escape(var)}</b>\n🔴 <code>{escape(str(e))}</code>"

                        creation_token = _find_creation_token_by_dir(target_dir)
                        if creation_token:
                            kb = build_creation_keyboard(creation_token)
                            new_mid = _replace_message(origin_chat, origin_mid, result_html, kb)
                            if new_mid:
                                sess = CREATION_SESSIONS.get(creation_token, {})
                                sess["ui_chat_id"] = origin_chat
                                sess["ui_msg_id"]  = new_mid
                        else:
                            status_text = build_status_message(C.BOT_LIST_FILE, C.PIDS_FILE)
                            full = f"{result_html}\n\n{status_text}"
                            _replace_message(origin_chat, origin_mid, full, _build_main_keyboard())

                        PENDING_UPDATE.pop(user_id, None)
                        continue

                    # /status (в чатах и в ЛС админа)
                    if text.lower().startswith("/status"):
                        # определяем, кто вызвал: администратор или нет
                        is_admin = (user_id == C.ADMIN_USER_ID)
                        page_index = 0
                        html, total_pages = _build_status_page(page_index)
                        keyboard = _status_keyboard(is_admin, page_index, total_pages)
                        _delete_message(chat_id, msg_id)
                        _replace_message(chat_id, None, html, keyboard)
                        continue

                # — коллбэки —
                cb = upd.get("callback_query")
                if cb:
                    cb_id  = cb.get("id")
                    from_u = cb.get("from") or {}
                    user_id = from_u.get("id")
                    data = cb.get("data") or ""  # обязательно присваиваем, чтобы было определено
                    message = cb.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = chat.get("id")
                    message_id = message.get("message_id")

                    # сначала обрабатываем перелистывание страниц
                    if data and data.startswith("status_page:"):
                        try:
                            page_index = int(data.split(":", 1)[1])
                        except Exception:
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Некорректная страница", show_alert=True)
                            continue
                        is_admin = (user_id == C.ADMIN_USER_ID)
                        html, pages_count = _build_status_page(page_index)
                        kb = _status_keyboard(is_admin, page_index, pages_count)
                        _replace_message(chat_id, message_id, html, kb)
                        continue

                    # ещё до проверки callback на "BotFather" и другие префиксы
                    prefix = getattr(C, "RESTART_ONE_PREFIX", "restart_one:")
                    if data.startswith(prefix):
                        try:
                            idx_str = data.split(":", 1)[1]
                            idx = int(idx_str)
                            bot_dirs = read_bot_paths(C.BOT_LIST_FILE)
                            if idx < 0 or idx >= len(bot_dirs):
                                raise IndexError("некорректный индекс")
                            target_dir = bot_dirs[idx].as_posix()
                            line = do_restart_one(target_dir)
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Готово ✅")
                            html = f"<b>Рестарт одного бота</b>\n{line}"
                            _replace_message(chat_id, message_id, html, _build_main_keyboard())
                        except Exception as e:
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                        continue
                    
                    # ==== BotFather: старт мастера ====
                    if data == getattr(C, "CREATE_BOTFATHER_CB", "create_botfather"):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        PENDING_BF_SESSION.add(user_id)
                        tip = (
                            "<b>BotFather мастер</b>\n"
                            "Отправьте <b>.session</b> файл <u>в личку</u> этому боту.\n\n"
                            "Файл используется только для текущей сессии и будет удалён по завершении."
                        )
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Жду .session в личке")
                        except Exception: pass
                        try: edit_message_html(C.STATUS_BOT_TOKEN, chat_id, message_id, tip, None)
                        except Exception: pass
                        continue

                    # ==== BotFather: закрыть сессию ====
                    if data == BF_CLOSE_SESSION_CB:
                        if user_id in BF_SESSION_PATH:
                            p = BF_SESSION_PATH.pop(user_id)
                            try:
                                if p and p.exists(): p.unlink()
                            except Exception: pass
                        BF_KNOWN_BOTS.pop(user_id, None)
                        BF_CREATE_NAME_WAIT.discard(user_id)
                        BF_CREATE_USERNAME_WAIT.pop(user_id, None)
                        BF_EDIT_ABOUT_WAIT.pop(user_id, None)
                        BF_EDIT_DESC_WAIT.pop(user_id, None)
                        BF_EDIT_BOTPIC_WAIT.pop(user_id, None)
                        BF_MENUBTN_URL_WAIT.pop(user_id, None)
                        BF_MENUBTN_TITLE_WAIT.pop(user_id, None)
                        BF_ENTER_USERNAME_WAIT.discard(user_id)
                        BF_CHAIN_AFTER_CREATE.discard(user_id)
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Сессия закрыта.")
                        except Exception: pass
                        # Возвращаемся к пагинированному статусу (стр. 1)
                        is_admin = (user_id == C.ADMIN_USER_ID)
                        page_index = 0
                        html, total_pages = _build_status_page(page_index)
                        kb = _status_keyboard(is_admin, page_index, total_pages)
                        _replace_message(chat_id, message_id, html, kb)
                        continue

                    if data and data.startswith(BF_MENU_BTN_PREFIX):
                        uname = data.split(":", 1)[1]
                        BF_MENUBTN_URL_WAIT[user_id] = uname
                        try:
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Введите URL в личке")
                            send_html(C.STATUS_BOT_TOKEN, user_id,
                                    f"Введите <b>URL</b> для Menu Button {escape(uname)}.\n"
                                    "Или пришлите <code>/empty</code> для дефолтного поведения.")
                        except Exception: pass
                        continue

                    # ==== BotFather: ручной ввод @username ====
                    if data == BF_ENTER_USERNAME_CB:
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        BF_ENTER_USERNAME_WAIT.add(user_id)
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Введите @username в ЛС")
                        except Exception: pass
                        try: send_html(C.STATUS_BOT_TOKEN, user_id, "Введите <b>@username</b> бота (пример: <code>@onewin_appbot</code>).")
                        except Exception: pass
                        continue

                    if data and data.startswith(BF_GET_TOKEN_PREFIX):
                        uname = data.split(":", 1)[1]
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Сессия не найдена", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            ok, token, msg_out = bf_get_token(spath, uname)
                            # короткое уведомление без лога
                            send_html(C.STATUS_BOT_TOKEN, chat_id, msg_out)
                        except Exception as e:
                            try: send_html(C.STATUS_BOT_TOKEN, chat_id, f"🔴 Ошибка: <code>{escape(str(e))}</code>")
                            except Exception: pass
                        # остаёмся в меню бота
                        _replace_message(chat_id, message_id, f"<b>Меню бота {escape(uname)}</b>", _build_bf_bot_menu_kb(uname))
                        continue    
                    # ==== BotFather: выбор бота из списка ====
                    if data and data.startswith(BF_SELECT_BOT_PREFIX):
                        uname = data.split(":", 1)[1]
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Открываю меню {uname}")
                        except Exception: pass
                        _replace_message(chat_id, message_id,
                                         f"<b>Меню бота {escape(uname)}</b>",
                                         _build_bf_bot_menu_kb(uname))
                        continue

                    # ==== BotFather: назад к списку ====
                    if data == BF_BACK_TO_LIST_CB:
                        spath = BF_SESSION_PATH.get(user_id)
                        if not spath or not spath.exists():
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Сессия не найдена", show_alert=True)
                            except Exception: pass
                            continue
                        ok, bots, report = bf_list_bots(spath)
                        BF_KNOWN_BOTS[user_id] = bots if ok else BF_KNOWN_BOTS.get(user_id, [])
                        tip = "<b>Боты аккаунта</b>\nВыберите действие."
                        if report: tip += "\n\n" + report
                        _replace_message(chat_id, message_id, tip, _build_bf_root_kb(BF_KNOWN_BOTS[user_id]))
                        continue

                    # ==== BotFather: создать нового (запросить Name) ====
                    if data == BF_BOTFATHER_CREATE_NEW_CB:
                        BF_CREATE_NAME_WAIT.add(user_id)
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Введите Name в личке")
                        except Exception: pass
                        try: send_html(C.STATUS_BOT_TOKEN, user_id, "Введите <b>Name</b> для нового бота.")
                        except Exception: pass
                        continue

                    # ==== BotFather: редактировать About ====
                    if data and data.startswith(BF_EDIT_ABOUT_PREFIX):
                        uname = data.split(":", 1)[1]
                        BF_EDIT_ABOUT_WAIT[user_id] = uname
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Введите About (до 120 симв.) в личке")
                        except Exception: pass
                        try: send_html(C.STATUS_BOT_TOKEN, user_id, f"Введите новый <b>About</b> для {escape(uname)} (до 120 символов).")
                        except Exception: pass
                        continue

                    # ==== BotFather: редактировать Description ====
                    if data and data.startswith(BF_EDIT_DESC_PREFIX):
                        uname = data.split(":", 1)[1]
                        BF_EDIT_DESC_WAIT[user_id] = uname
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Введите Description в личке")
                        except Exception: pass
                        try: send_html(C.STATUS_BOT_TOKEN, user_id, f"Введите новый <b>Description</b> для {escape(uname)} (до ~512 символов).")
                        except Exception: pass
                        continue

                    # ==== BotFather: редактировать Botpic ====
                    if data and data.startswith(BF_EDIT_BOTPIC_PREFIX):
                        uname = data.split(":", 1)[1]
                        BF_EDIT_BOTPIC_WAIT[user_id] = uname
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Пришлите фото в личке")
                        except Exception: pass
                        try: send_html(C.STATUS_BOT_TOKEN, user_id, f"Пришлите <b>фото</b> для аватара {escape(uname)} (как изображение).")
                        except Exception: pass
                        continue

                    # ==== Остальные твои кнопки ====

                    if data == C.RELOAD_STATUS_CB:
                        try:
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"✅ Статус обновлён — {time.strftime('%H:%M:%S')}")
                        except Exception:
                            pass
                        # Переотрисуем статус с пагинацией (стр. 1)
                        is_admin = (user_id == C.ADMIN_USER_ID)
                        page_index = 0
                        html, total_pages = _build_status_page(page_index)
                        kb = _status_keyboard(is_admin, page_index, total_pages)
                        _replace_message(chat_id, message_id, html, kb)
                        continue

                    if data == C.RESTART_ALL_CB:
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "🔄 Рестарт запущен…")
                        except Exception: pass
                        summary = do_restart_all()
                        _replace_message(chat_id, message_id, summary, _build_main_keyboard())
                        try:
                            if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
                                send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, summary)
                        except Exception as e:
                            log(f"send_html LOG error: {e}")
                        continue

                    if data == C.RESTART_ONE_CB:
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            bot_dirs = read_bot_paths(C.BOT_LIST_FILE)
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                            continue
                        kb = build_restart_one_keyboard(bot_dirs)
                        _replace_message(chat_id, message_id, "<b>Выберите бота для рестарта</b>", kb)
                        continue

                    if data and data.startswith(getattr(C, "CREATE_PROMO_PREFIX", "create_promo:")):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            token = data.split(":", 1)[1]
                            if token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_chat_id"] = chat_id
                                CREATION_SESSIONS[token]["ui_msg_id"]  = message_id
                            html, kb = request_promo_update(user_id, chat_id, message_id, token)
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "✏️ Введите промокод и сумму, 2 строки")
                            new_mid = _replace_message(chat_id, message_id, html, kb)
                            if new_mid and token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                        continue

                    if data == C.BACK_TO_STATUS_CB:
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⬅ Возврат к статусу")
                        except Exception: pass
                        status_text = build_status_message(C.BOT_LIST_FILE, C.PIDS_FILE)
                        _replace_message(chat_id, message_id, status_text, _build_main_keyboard())
                        continue

                    if data == C.UPDATE_DOMAINS_CB:
                        if user_id != C.ADMIN_USER_ID:
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            continue
                        kb = build_update_menu_keyboard()
                        _replace_message(chat_id, message_id,
                            "<b>Что обновляем?</b>\nURL‑переменные меняются по категориям (или для всех).\n"
                            "BOT_TOKEN / IMAGE_FILE_ID — у выбранного бота.",
                            kb
                        )
                        continue
                    if data in (C.UPDATE_VAR_WEBAPP1_CB, C.UPDATE_VAR_PROMO_CB, C.UPDATE_VAR_BOT_TOKEN_CB, C.UPDATE_VAR_IMAGE_CB):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        var_map = {
                            C.UPDATE_VAR_WEBAPP1_CB:   "WEBAPP_URL_1",
                            C.UPDATE_VAR_PROMO_CB:     "PROMOCODE_WEBAPP_URL",
                            C.UPDATE_VAR_BOT_TOKEN_CB: "BOT_TOKEN",
                            C.UPDATE_VAR_IMAGE_CB:     "IMAGE_FILE_ID",
                        }
                        var = var_map.get(data, "WEBAPP_URL_1")
                        if var in url_vars:
                            html, kb = render_category_choice(var)
                            _replace_message(chat_id, message_id, html, kb)
                            continue
                        try:
                            bot_dirs = read_bot_paths(C.BOT_LIST_FILE)
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                            continue
                        kb = _build_choose_bot_kb(var, bot_dirs)
                        _replace_message(
                            chat_id, message_id,
                            f"<b>Обновление {var}</b>\nВыберите бота, у которого нужно поменять значение.",
                            kb
                        )
                        continue

                    if data and data.startswith(CAT_PREFIX):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            _, tail = data.split(":", 1)
                            cat_str, var = tail.split(":", 1)
                            category = None if cat_str == "ALL" else cat_str
                        except Exception:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Некорректный выбор категории", show_alert=True)
                            except Exception: pass
                            continue
                        PENDING_UPDATE[user_id] = {
                            "var": var, "category": category,
                            "chat_id": chat_id, "message_id": message_id, "ts": time.time(),
                        }
                        tip = "Отправьте новый URL (пример: https://example.com/)"
                        _replace_message(
                            chat_id, message_id,
                            f"<b>Обновление {var}</b>\nКатегория: <code>{escape(category) if category else 'Все'}</code>\n{tip}",
                            [[{"text": "❌ Отмена", "callback_data": C.UPDATE_CANCEL_CB}]]
                        )
                        continue

                    if data == C.UPDATE_CANCEL_CB:
                        if user_id in PENDING_UPDATE:
                            PENDING_UPDATE.pop(user_id, None)
                        try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "Отменено")
                        except Exception: pass
                        status_text = build_status_message(C.BOT_LIST_FILE, C.PIDS_FILE)
                        _replace_message(chat_id, message_id, status_text, _build_main_keyboard())
                        continue

                    if data == getattr(C, "CREATE_NEW_CB", "create_new"):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            tip, kb = request_folder_name(user_id, chat_id, message_id)
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "✏️ Введите имя папки")
                            _replace_message(chat_id, message_id, tip, kb)
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                        continue

                    if data and data.startswith(getattr(C, "CREATE_AUTOCONF_PREFIX", "create_autoconf:")):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            token = data.split(":", 1)[1]
                            if token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_chat_id"] = chat_id
                                CREATION_SESSIONS[token]["ui_msg_id"]  = message_id
                            html, kb = request_autoconfig(user_id, chat_id, message_id, token)
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "✏️ Вставьте строки одним сообщением")
                            new_mid = _replace_message(chat_id, message_id, html, kb)
                            if new_mid and token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                        continue

                    if data and data.startswith("create_img:"):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            token = data.split(":", 1)[1]
                            if token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_chat_id"] = chat_id
                                CREATION_SESSIONS[token]["ui_msg_id"]  = message_id
                            html, kb = start_image_capture(token)
                            answer_callback(C.STATUS_BOT_TOKEN, cb_id, "✅ Ждём фото/гиф у нового бота")
                            new_mid = _replace_message(chat_id, message_id, html, kb)
                            if new_mid and token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                        continue

                    if data and data.startswith(getattr(C, "CREATE_RUN_PREFIX", "create_run:")):
                        if user_id != C.ADMIN_USER_ID:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, "⛔ Недостаточно прав", show_alert=True)
                            except Exception: pass
                            continue
                        try:
                            token = data.split(":", 1)[1]
                            result_html = start_created_bot(token)
                            status_text  = build_status_message(C.BOT_LIST_FILE, C.PIDS_FILE)
                            full = f"{result_html}\n\n{status_text}"
                            new_mid = _replace_message(chat_id, message_id, full, _build_main_keyboard())
                            if new_mid and token in CREATION_SESSIONS:
                                CREATION_SESSIONS[token]["ui_msg_id"] = new_mid
                        except Exception as e:
                            try: answer_callback(C.STATUS_BOT_TOKEN, cb_id, f"Ошибка: {e}", show_alert=True)
                            except Exception: pass
                        continue

            time.sleep(0.1)

        except HTTPError as e:
            log(f"getUpdates HTTPError: {e}")
            time.sleep(min(sleep_backoff, 60.0))
            sleep_backoff = min(sleep_backoff * 2.0, 60.0)
        except URLError as e:
            reason = getattr(e, "reason", e)
            log(f"getUpdates URLError: {reason}")
            import random
            jitter = random.uniform(0.1, 0.5)
            time.sleep(min(sleep_backoff + jitter, 60.0))
            sleep_backoff = min(sleep_backoff * 2.0, 60.0)
        except Exception as e:
            log(f"getUpdates loop error: {e}")
            time.sleep(min(sleep_backoff, 60.0))
            sleep_backoff = min(sleep_backoff * 2.0, 60.0)
