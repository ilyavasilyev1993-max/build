# -*- coding: utf-8 -*-
import json, time
from html import escape
import config as C
from zapusk import log, read_bot_paths, cleanup_previous_processes, load_bot_token, tg_get_me, start_bot, build_status_message

def do_restart_all() -> str:
    try:
        bot_dirs = read_bot_paths(C.BOT_LIST_FILE)
    except Exception as e:
        return f"<b>Рестарт ботов</b>\n🔴 <code>{escape(str(e))}</code>"

    killed = cleanup_previous_processes(C.PIDS_FILE, bot_dirs)
    if killed:
        log(f"Остановлено ранее запущенных: {len(killed)}")
    time.sleep(0.5)

    statuses_with_dirs = []
    new_pids = {}

    for bot_dir in bot_dirs:
        prefix = f"<b>{escape(bot_dir.name)}</b> | "
        try:
            token = load_bot_token(bot_dir)
        except Exception as e:
            statuses_with_dirs.append((bot_dir, prefix + f"Бот: <i>@unknown</i> — 🔴 <code>{escape(str(e))}</code>"))
            continue

        ok, uname, err = tg_get_me(token)
        user_disp = f"@{escape(uname)}" if uname else "@unknown"
        if not ok:
            statuses_with_dirs.append((bot_dir, prefix + f"Бот: {user_disp} — 🔴 <code>{escape(err or 'getMe failed')}</code>"))
            continue

        proc, start_err = start_bot(bot_dir)
        if start_err:
            statuses_with_dirs.append((bot_dir, prefix + f"Бот: {user_disp} — 🔴 <code>{escape(start_err)}</code>"))
        else:
            statuses_with_dirs.append((bot_dir, prefix + f"Бот: {user_disp} — <b>Включен</b>, работает без ошибок 🟢"))
            new_pids[bot_dir.as_posix()] = proc.pid

    try:
        C.PIDS_FILE.write_text(json.dumps(new_pids, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"Не удалось записать pids.json: {e}")

    ok_cnt  = sum(("🟢" in line or "Включен" in line) for _, line in statuses_with_dirs)
    bad_cnt = len(statuses_with_dirs) - ok_cnt
    header = f"<b>Рестарт ботов</b>  <code>OK:{ok_cnt}</code> | <code>FAIL:{bad_cnt}</code>"
    body = "\n".join(line for _, line in statuses_with_dirs) if statuses_with_dirs else "<i>Список пуст</i>"
    return header + "\n" + body
