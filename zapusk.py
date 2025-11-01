# -*- coding: utf-8 -*-
import os, sys, json, time, subprocess, importlib.util
from html import escape
from pathlib import Path
import requests
import re
import config as C
from net import tg_get

# ─────────────────────────────────────────────────────────
# логирование

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(C.LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)

# ─────────────────────────────────────────────────────────
# утилиты для работы с ботами/процессами

def read_bot_paths(path: Path):
    if not path.exists():
        raise FileNotFoundError(f"Не найден файл со списком ботов: {path}")
    return [Path(s.strip()) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]

def load_bot_token(bot_dir: Path) -> str:
    """
    Безопасно достаёт BOT_TOKEN из config.py без exec/import.
    Читает текст как UTF-8-SIG (чтобы снести BOM), ищет строку вида:
        BOT_TOKEN = "123:abc"  или  BOT_TOKEN='123:abc'
    """
    cfg = bot_dir / "config.py"
    if not cfg.exists():
        raise FileNotFoundError(f"Не найден config.py в {bot_dir}")

    try:
        txt = cfg.read_text(encoding="utf-8-sig", errors="replace")
    except Exception as e:
        raise IOError(f"Не удалось прочитать {cfg}: {e}")

    # Ищем присваивание BOT_TOKEN = "..."
    m = re.search(r'(?m)^\s*BOT_TOKEN\s*=\s*([\'"])(?P<val>.+?)\1\s*$', txt)
    if not m:
        raise AttributeError(f"В {cfg} не найдено присваивание BOT_TOKEN")

    token = (m.group("val") or "").strip()

    # Лёгкая валидация телеграм-токена
    if not re.match(r'^\d{6,}:[A-Za-z0-9_-]{20,}$', token):
        raise ValueError(f"BOT_TOKEN в {cfg} имеет неверный формат")

    return token

def tg_get_me(token: str):
    last_err = None
    for attempt in range(1, int(C.RETRIES) + 1):
        try:
            data = tg_get(token, "getMe")
            if not data.get("ok"):
                return False, None, f"ok=false: {data}"
            username = (data.get("result") or {}).get("username")
            return True, username, None
        except requests.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            if code in (400, 401, 403):
                return False, None, f"HTTPError {code}: {e}"
            last_err = f"HTTPError {code or ''}: {e}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
        if attempt < C.RETRIES:
            time.sleep(C.BACKOFF * (2 ** (attempt - 1)))
    return False, None, (last_err or "unknown error")

def build_command(bot_dir: Path):
    if C.LAUNCH_MODE == "direct":
        return [C.PYTHON_EXE, "main.py"], C.CREATE_NO_WINDOW, False
    elif C.LAUNCH_MODE == "cmd":
        cmd = f'"{C.PYTHON_EXE}" "main.py"'
        return ["cmd.exe", "/c", cmd], C.CREATE_NO_WINDOW, False
    elif C.LAUNCH_MODE == "powershell":
        ps_cmd = f"& '{C.PYTHON_EXE}' 'main.py'"
        return ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd], C.CREATE_NO_WINDOW, False
    else:
        raise ValueError(f"Неизвестный LAUNCH_MODE={C.LAUNCH_MODE}")

def start_bot(bot_dir: Path):
    main_py = bot_dir / "main.py"
    if not main_py.exists():
        return None, f"Не найден main.py в {bot_dir}"
    args, flags, use_shell = build_command(bot_dir)
    try:
        logf = open(C.LOG_FILE, "a", encoding="utf-8")
        proc = subprocess.Popen(
            args, cwd=str(bot_dir),
            stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
            creationflags=flags, shell=use_shell, close_fds=(os.name != "nt"),
        )
        try: logf.close()
        except Exception: pass
        time.sleep(C.START_GRACE_SECONDS)
        if proc.poll() is not None:
            return None, f"Процесс завершился сразу с кодом {proc.returncode}"
        return proc, None
    except FileNotFoundError:
        return None, f'Не найден интерпретатор PYTHON_EXE="{C.PYTHON_EXE}"'
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

def is_process_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name != "nt":
            os.kill(pid, 0)
            return True
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        OpenProcess = kernel32.OpenProcess
        OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        OpenProcess.restype = wintypes.HANDLE
        GetExitCodeProcess = kernel32.GetExitCodeProcess
        GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        GetExitCodeProcess.restype = wintypes.BOOL
        CloseHandle = kernel32.CloseHandle
        hProc = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not hProc:
            import subprocess as sp
            try:
                cp = sp.run(
                    ["tasklist", "/FI", f"PID eq {pid}"],
                    stdout=sp.PIPE, stderr=sp.DEVNULL,
                    text=True, creationflags=C.CREATE_NO_WINDOW,
                )
                return str(pid) in cp.stdout
            except Exception:
                return False
        code = wintypes.DWORD()
        ok = GetExitCodeProcess(hProc, ctypes.byref(code))
        CloseHandle(hProc)
        if not ok:
            return False
        return code.value == STILL_ACTIVE
    except Exception:
        return False

def taskkill_pid(pid: int):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=C.CREATE_NO_WINDOW)
        else:
            try:
                os.kill(pid, 15); time.sleep(0.3)
            except ProcessLookupError:
                return
            os.kill(pid, 9)
    except Exception:
        pass

def cleanup_previous_processes(pids_path: Path, current_dirs: list[Path]):
    if not pids_path.exists():
        return []
    killed = []
    try:
        old = json.loads(pids_path.read_text(encoding="utf-8"))
        current_set = {p.as_posix() for p in current_dirs}
        for bot_dir_str, pid in old.items():
            if bot_dir_str in current_set:
                try:
                    pid_int = int(pid)
                except Exception:
                    continue
                taskkill_pid(pid_int)
                log(f"Остановлен прежний PID {pid_int} для {bot_dir_str}")
                killed.append((bot_dir_str, pid_int))
    except Exception:
        pass
    return killed

# ─────────────────────────────────────────────────────────
# сводки/статус — без TG-кнопок

def build_start_summary(bot_results: list[tuple[Path, str]]) -> str:
    ok_cnt  = sum("🟢" in line or "Включен" in line for _, line in bot_results)
    bad_cnt = len(bot_results) - ok_cnt
    header = f"<b>Старт ботов</b>  <code>OK:{ok_cnt}</code> | <code>FAIL:{bad_cnt}</code>"
    body = "\n".join(line for _, line in bot_results) if bot_results else "<i>Список пуст</i>"
    return header + "\n" + body

def build_status_message(bots_file: Path, pids_path: Path) -> str:
    try:
        bot_dirs = read_bot_paths(bots_file)
    except Exception as e:
        return f"<b>Статус ботов</b>\n🔴 <code>{escape(str(e))}</code>"

    pids = {}
    if pids_path.exists():
        try:
            pids = json.loads(pids_path.read_text(encoding="utf-8"))
        except Exception:
            pids = {}

    lines, ok_cnt, bad_cnt = [], 0, 0
    for bot_dir in bot_dirs:
        pid = int(pids.get(bot_dir.as_posix(), 0) or 0)
        alive = is_process_running(pid) if pid else False
        name = escape(bot_dir.name)
        if alive:
            ok_cnt += 1
            lines.append(f"• <b>{name}</b> — 🟢 <span class=\"tg-spoiler\">PID {pid}</span>")
        else:
            bad_cnt += 1
            lines.append(f"• <b>{name}</b> — 🔴 Не запущен")

    header = f"<b>Статус ботов</b>  <code>OK:{ok_cnt}</code> | <code>FAIL:{bad_cnt}</code>"
    return header + "\n" + ("\n".join(lines) if lines else "<i>Список пуст</i>")

# ─────────────────────────────────────────────────────────
# одиночный рестарт бота

def do_restart_one(target_dir: str | Path) -> str:
    """
    Перезапускает одного бота.
    - Останавливает предыдущий процесс по PID из pids.json, если он был запущен.
    - Запускает новый процесс и обновляет запись в pids.json.
    Возвращает HTML-строку статуса для этого бота.
    """
    # всегда используем Path и нормализуем путь
    try:
        target = Path(target_dir).resolve()
    except Exception:
        target = Path(str(target_dir))

    pids_path: Path = C.PIDS_FILE

    # загрузим текущие pids
    try:
        if pids_path.exists():
            pids = json.loads(pids_path.read_text(encoding="utf-8"))
        else:
            pids = {}
    except Exception:
        pids = {}

    # остановим предыдущий процесс для этого бота, если он есть
    old_pid = pids.get(target.as_posix())
    if old_pid:
        try:
            pid_int = int(old_pid)
        except Exception:
            pid_int = None
        if pid_int:
            try:
                taskkill_pid(pid_int)
                log(f"Остановлен прежний PID {pid_int} для {target.as_posix()}")
            except Exception:
                pass
        # удаляем запись о старом pid
        pids.pop(target.as_posix(), None)

    # попытка загрузить токен
    try:
        token = load_bot_token(target)
    except Exception as e:
        # запишем обновлённый pids без записи для этого бота
        try:
            pids_path.write_text(json.dumps(pids, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as werr:
            log(f"Не удалось записать pids.json: {werr}")
        return (f"<b>{escape(target.name)}</b> | Бот: <i>@unknown</i> — 🔴 "
                f"<code>{escape(str(e))}</code>")

    # проверка getMe для токена, чтобы получить username
    ok, uname, err = tg_get_me(token)
    user_disp = f"@{escape(uname)}" if uname else "@unknown"
    if not ok:
        # сохраняем pids без этого бота
        try:
            pids_path.write_text(json.dumps(pids, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as werr:
            log(f"Не удалось записать pids.json: {werr}")
        return (f"<b>{escape(target.name)}</b> | Бот: {user_disp} — 🔴 "
                f"<code>{escape(err or 'getMe failed')}</code>")

    # запускаем бота
    proc, start_err = start_bot(target)
    if start_err:
        # сохраняем pids без этого бота
        try:
            pids_path.write_text(json.dumps(pids, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        except Exception as werr:
            log(f"Не удалось записать pids.json: {werr}")
        return (f"<b>{escape(target.name)}</b> | Бот: {user_disp} — 🔴 "
                f"<code>{escape(start_err)}</code>")

    # успешный запуск: обновляем pids и возвращаем успех
    pids[target.as_posix()] = proc.pid
    try:
        pids_path.write_text(json.dumps(pids, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    except Exception as werr:
        log(f"Не удалось записать pids.json: {werr}")
    return (f"<b>{escape(target.name)}</b> | Бот: {user_disp} — "
            "<b>Включен</b>, работает без ошибок 🟢")
