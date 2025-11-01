# main.py
import ctypes
import json
from pathlib import Path

import config as C
from zapusk import (
    log,
    read_bot_paths,
    cleanup_previous_processes,
    load_bot_token,
    tg_get_me,
    start_bot,
    build_start_summary,
)
# берём из пакета ui, не из старого ui.py
from ui import run_status_command_loop, send_html

def main():
    # консоль в UTF-8 (Windows)
    if __import__("os").name == "nt":
        try:
            k32 = ctypes.windll.kernel32
            k32.SetConsoleCP(65001); k32.SetConsoleOutputCP(65001)
        except Exception:
            pass

    bots_file: Path = C.BOT_LIST_FILE
    pids_path: Path = C.PIDS_FILE

    # 1) список ботов
    try:
        bot_dirs = read_bot_paths(bots_file)
    except Exception as e:
        msg = f"<b>Старт ботов</b>\n🔴 <code>{e}</code>"
        log(msg)
        if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
            try: send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, msg)
            except Exception as se: log(f"send_html error: {se}")
        return

    # 2) прибить предыдущие
    killed = cleanup_previous_processes(pids_path, bot_dirs)
    if killed:
        log(f"Остановлены предыдущие процессы: {killed}")

    # 3) запустить
    statuses_with_dirs = []
    pids = {}
    for bot_dir in bot_dirs:
        prefix = f"<b>{bot_dir.name}</b> | "
        try:
            token = load_bot_token(bot_dir)
        except Exception as e:
            line = prefix + f"Бот: <i>@unknown</i> — 🔴 <code>{e}</code>"
            statuses_with_dirs.append((bot_dir, line))
            continue

        ok, uname, err = tg_get_me(token)
        user_disp = f"@{uname}" if uname else "@unknown"
        if not ok:
            line = prefix + f"Бот: {user_disp} — 🔴 <code>{err or 'getMe failed'}</code>"
            statuses_with_dirs.append((bot_dir, line))
            continue

        proc, start_err = start_bot(bot_dir)
        if start_err:
            line = prefix + f"Бот: {user_disp} — 🔴 <code>{start_err}</code>"
        else:
            line = prefix + f"Бот: {user_disp} — <b>Включен</b>, работает без ошибок 🟢"
            pids[bot_dir.as_posix()] = proc.pid
        statuses_with_dirs.append((bot_dir, line))

    # 4) сохранить PID'ы
    try:
        C.PIDS_FILE.write_text(json.dumps(pids, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"Не удалось записать pids.json: {e}")

    # 5) отправить сводку (с @username)
    full = build_start_summary(statuses_with_dirs)
    log(full)
    if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
        try: send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, full)
        except Exception as e: log(f"Не удалось отправить сводку: {e}")

    # 6) запустить UI-лонг-пулл
    run_status_command_loop()

if __name__ == "__main__":
    main()
