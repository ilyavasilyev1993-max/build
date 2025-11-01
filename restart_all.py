# restart_all.py
"""
Рестарт всех ботов:
- читает список из bots.txt
- убивает текущие процессы из pids.json (только для этих путей)
- заново запускает ботов (тихо), обновляет pids.json
- отправляет ОДНО HTML-сообщение "Рестарт ботов" в LOG_CHAT_ID (с @username)
- пишет всё в общий logs.txt
"""

import ctypes
import json
import time
from pathlib import Path
import sys
import config as C
from zapusk import (
    log, read_bot_paths, cleanup_previous_processes,
    load_bot_token, tg_get_me, start_bot,
    do_restart_one,
)
from ui import send_html

def build_restart_summary(status_lines_with_dirs, killed):
    ok_cnt  = sum(("🟢" in line or "Включен" in line) for _, line in status_lines_with_dirs)
    bad_cnt = len(status_lines_with_dirs) - ok_cnt
    killed_cnt = len(killed)

    header = f"<b>Рестарт ботов</b>  <code>OK:{ok_cnt}</code> | <code>FAIL:{bad_cnt}</code> | <code>STOP:{killed_cnt}</code>"

    parts = []
    if killed_cnt:
        killed_lines = "\n".join(
            f"⏹ {Path(p).name} <span class=\"tg-spoiler\">(PID {pid})</span>"
            for p, pid in killed
        )
        parts.append(f"<i>Остановлены предыдущие экземпляры:</i>\n{killed_lines}")

    body = "\n".join(line for _, line in status_lines_with_dirs) if status_lines_with_dirs else "<i>Список пуст</i>"
    parts.append(body)
    return header + "\n" + "\n".join(parts)

def main():
    # если передан аргумент --one "<точный путь к папке из bots.txt>"
    if len(sys.argv) >= 3 and sys.argv[1] == "--one":
        target_dir = sys.argv[2]
        line = do_restart_one(target_dir)
        msg = "<b>Рестарт одного бота</b>\n" + line
        log(msg)
        if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
            try: send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, msg)
            except Exception as e: log(f"send_html error: {e}")
        return
    # UTF-8 консоль на Windows
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
        msg = f"<b>Рестарт ботов</b>\n🔴 <code>{e}</code>"
        log(msg)
        if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
            try: send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, msg)
            except Exception as se: log(f"send_html error: {se}")
        return

    log(f"Инициирован рестарт {len(bot_dirs)} ботов")

    # 2) прибить предыдущие
    killed = cleanup_previous_processes(pids_path, bot_dirs)
    if killed:
        log(f"Остановлено ранее запущенных: {len(killed)}")

    # Небольшая пауза, чтобы ОС освободила порты/дескрипторы
    time.sleep(0.5)

    # 3) снова запустить
    statuses_with_dirs = []
    new_pids = {}

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
            new_pids[bot_dir.as_posix()] = proc.pid

        statuses_with_dirs.append((bot_dir, line))

    # 4) сохранить новые PID'ы
    try:
        C.PIDS_FILE.write_text(json.dumps(new_pids, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log(f"Не удалось записать pids.json: {e}")

    # 5) отправить сводку
    full = build_restart_summary(statuses_with_dirs, killed)
    log(full)
    if C.LOG_BOT_TOKEN and C.LOG_CHAT_ID:
        try: send_html(C.LOG_BOT_TOKEN, C.LOG_CHAT_ID, full)
        except Exception as e: log(f"Не удалось отправить сводку: {e}")

if __name__ == "__main__":
    main()
