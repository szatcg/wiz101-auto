"""Run the bot as a background service controllable from a terminal.

    wiz101-auto start [--supervise]   start in the background
    wiz101-auto stop                  ask it to stop cleanly (unhooks the game)
    wiz101-auto restart               stop + start
    wiz101-auto status                running? what is it doing? last log lines
    wiz101-auto logs [-n 80] [-f]     show / follow the log

A clean stop matters: killing the process leaves the game hooked and forces a
Wizard101 restart. `stop` therefore drops a request file the bot polls, waits
for it to exit, and only force-kills as a last resort.

Files (all in state/):
    bot.pid          PID of the running bot (or supervisor)
    stop.request     created by `stop`; the bot exits when it sees it
    status.json      heartbeat written by the bot every few seconds
    bot.out          stdout/stderr of the background process
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("state")
PID_FILE = STATE / "bot.pid"
STOP_FILE = STATE / "stop.request"
STATUS_FILE = STATE / "status.json"
OUT_FILE = STATE / "bot.out"
WATCH_PID_FILE = STATE / "watch.pid"
LOG_FILE = Path("wiz101-auto.log")

STILL_ACTIVE = 259


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_pid() -> int:
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return 0


def running_pid() -> int:
    pid = read_pid()
    return pid if pid_alive(pid) else 0


def _python() -> str:
    return sys.executable


def start(config: str, supervise: bool) -> int:
    STATE.mkdir(exist_ok=True)
    pid = running_pid()
    if pid:
        print(f"already running (PID {pid}). Use 'stop' or 'restart'.")
        return 1
    STOP_FILE.unlink(missing_ok=True)
    cmd = [_python(), "-m", "wiz101_auto", "supervise" if supervise else "run", "-c", config]
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    out = open(OUT_FILE, "a", encoding="utf-8")
    out.write(f"\n===== start {time.strftime('%Y-%m-%d %H:%M:%S')} {' '.join(cmd)} =====\n")
    out.flush()
    proc = subprocess.Popen(
        cmd,
        stdout=out,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
        cwd=os.getcwd(),
    )
    PID_FILE.write_text(str(proc.pid))
    time.sleep(3)
    if proc.poll() is not None:
        print("bot exited immediately; last output:")
        print(tail(OUT_FILE, 30))
        return 1
    print(
        f"started (PID {proc.pid}{', supervised' if supervise else ''}). Use 'status', 'logs -f' or 'stop'."
    )
    open_watch_window()
    open_dashboard()
    return 0


def open_watch_window() -> None:
    """Show the live activity log (`watch`) in its own console window, unless
    one is already open. It keeps following across bot restarts."""
    if sys.platform != "win32":
        return
    try:
        if pid_alive(int(WATCH_PID_FILE.read_text().strip())):
            return
    except Exception:
        pass
    try:
        proc = subprocess.Popen(
            [_python(), "-m", "wiz101_auto", "watch"],
            creationflags=0x00000010,  # CREATE_NEW_CONSOLE
            cwd=os.getcwd(),
        )
        WATCH_PID_FILE.write_text(str(proc.pid))
        print(f"live log window opened (PID {proc.pid}); close it any time, `watch` reopens it.")
    except OSError as exc:
        print(f"could not open the live log window: {exc}")


def open_dashboard() -> None:
    """Serve the progress dashboard in the background (unless it already is)
    and open it in the browser once."""
    from .dashboard import PORT, is_serving

    if is_serving(PORT):
        return
    try:
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        subprocess.Popen(
            [_python(), "-m", "wiz101_auto", "dashboard"],
            creationflags=flags,
            cwd=os.getcwd(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"progress dashboard: http://127.0.0.1:{PORT}/")
    except OSError as exc:
        print(f"could not start the dashboard: {exc}")


def stop(timeout: float = 30.0) -> int:
    pid = running_pid()
    if not pid:
        print("not running.")
        PID_FILE.unlink(missing_ok=True)
        return 0
    STATE.mkdir(exist_ok=True)
    STOP_FILE.write_text(time.strftime("%Y-%m-%d %H:%M:%S"))
    print(f"asked PID {pid} to stop; waiting for a clean shutdown...", end="", flush=True)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not pid_alive(pid):
            print(" stopped.")
            PID_FILE.unlink(missing_ok=True)
            STOP_FILE.unlink(missing_ok=True)
            return 0
        time.sleep(0.5)
        print(".", end="", flush=True)
    print(" still running; force-killing. Restart Wizard101 before the next run (hooks were left in).")
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        os.kill(pid, 9)
    PID_FILE.unlink(missing_ok=True)
    STOP_FILE.unlink(missing_ok=True)
    return 2


def status() -> int:
    pid = running_pid()
    print(f"running: {'yes, PID ' + str(pid) if pid else 'no'}")
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        age = time.time() - data.get("time", 0)
        print(f"heartbeat: {age:.0f}s ago{'  (STALE)' if pid and age > 30 else ''}")
        for k, v in data.items():
            if k != "time":
                print(f"  {k}: {v}")
    except Exception:
        print("no status yet")
    print("\nlast log lines:")
    print(tail(LOG_FILE, 25))
    return 0 if pid else 1


def tail(path: Path, n: int) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fp:
            return "".join(fp.readlines()[-n:])
    except FileNotFoundError:
        return f"({path} not found)"


def logs(n: int, follow: bool) -> int:
    print(tail(LOG_FILE, n), end="")
    if not follow:
        return 0
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fp:
            fp.seek(0, 2)
            while True:
                line = fp.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        return 0


def _out_size() -> int:
    try:
        return OUT_FILE.stat().st_size
    except OSError:
        return 0


def _bot_output_since(offset: int) -> str:
    """What the last bot run wrote to state/bot.out ('Could not hook'...)."""
    try:
        with OUT_FILE.open("rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def supervise(config: str, max_restarts: int = 10) -> int:
    """Run the bot in a child process and restart it after crashes.

    Clean stops (stop.request, Ctrl+Shift+Q, safety limits) exit with 0 and
    are not restarted; anything else is treated as a crash.
    """
    from . import gamerestart

    STATE.mkdir(exist_ok=True)
    restarts = 0
    last_run_from = _out_size()  # (nothing of an earlier run counts)
    while True:
        STOP_FILE.exists() and STOP_FILE.unlink()
        # A frozen or crashed game (the bot's request, no window, no hook):
        # close it, start it, log in, then the bot (it presses Play).
        why = gamerestart.needs_restart(_bot_output_since(last_run_from))
        if why:
            print(f"supervisor: restarting the game ({why})", flush=True)
            if not gamerestart.restart_game(log=lambda m: print(f"supervisor: {m}", flush=True)):
                print("supervisor: could not restart the game; stopping (the player is needed).")
                return 1
        started = time.monotonic()
        last_run_from = _out_size()
        code = subprocess.call([_python(), "-m", "wiz101_auto", "run", "-c", config])
        if gamerestart.REQUEST.exists() and not STOP_FILE.exists():
            continue  # the bot stopped for a game restart: do it, then carry on
        if code == 0 or STOP_FILE.exists():
            print("supervisor: bot stopped cleanly; not restarting.")
            return 0
        if time.monotonic() - started > 600:
            restarts = 0  # it ran fine for a while; reset the budget
        restarts += 1
        if restarts > max_restarts:
            print(f"supervisor: {max_restarts} crashes in a row; giving up.")
            return 1
        print(f"supervisor: bot exited with {code}; restart {restarts}/{max_restarts} in 10s")
        for _ in range(20):
            if STOP_FILE.exists():
                return 0
            time.sleep(0.5)


def write_status(**fields):
    """Called by the running bot to publish a heartbeat."""
    try:
        STATE.mkdir(exist_ok=True)
        fields["time"] = time.time()
        tmp = STATUS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(fields, indent=1, default=str), encoding="utf-8")
        tmp.replace(STATUS_FILE)
    except Exception:
        pass
