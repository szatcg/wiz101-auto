"""Restarting a frozen or crashed Wizard101 and logging back in.

The game sometimes hangs on a loading screen, stops responding, or closes.
The bot can't play through that, so:

- the bot notices (a loading screen for FREEZE_SECONDS, the window not
  responding for HUNG_SECONDS), writes state/game_restart.request and stops;
- the supervisor (`start --supervise`) sees the request, or no game window, or
  the bot failing to hook the game, closes the game, starts its client, logs
  in with the saved login and starts the bot again (which presses Play at
  character select).

The login lives in Windows Credential Manager (encrypted for this Windows
user), saved by `wiz101-auto set-login`; never in a file of this project.
At most MAX_PER_HOUR game restarts an hour.
"""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

REQUEST = Path("state") / "game_restart.request"
HISTORY = Path("state") / "game_restarts.txt"  # one time per restart, for the hourly cap
CRED_TARGET = "wiz101-auto/Wizard101"
GAME_EXE = "WizardGraphicalClient.exe"
FREEZE_SECONDS = 300.0  # on a loading screen this long: frozen
HUNG_SECONDS = 120.0  # the window not responding this long: frozen
MAX_PER_HOUR = 3
WINDOW_WAIT = 90.0  # for the game window after starting it
LOGIN_SCREEN_WAIT = 15.0  # from the window to its login screen
AFTER_LOGIN_WAIT = 25.0  # from the login to character select


# --- the login, in Windows Credential Manager ----------------------------------

class _CREDENTIAL(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_char)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2


def save_login(username: str, password: str) -> bool:
    """Store the game login for this Windows user. True if it worked."""
    if sys.platform != "win32":
        return False
    blob = password.encode("utf-16-le")
    cred = _CREDENTIAL()
    cred.Type = _CRED_TYPE_GENERIC
    cred.TargetName = CRED_TARGET
    cred.UserName = username
    cred.CredentialBlobSize = len(blob)
    buf = ctypes.create_string_buffer(blob, len(blob))
    cred.CredentialBlob = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
    cred.Persist = _CRED_PERSIST_LOCAL_MACHINE
    return bool(ctypes.windll.advapi32.CredWriteW(ctypes.byref(cred), 0))


def load_login() -> tuple[str, str] | None:
    """(username, password) saved by `set-login`, or None."""
    if sys.platform != "win32":
        return None
    ptr = ctypes.POINTER(_CREDENTIAL)()
    if not ctypes.windll.advapi32.CredReadW(CRED_TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(ptr)):
        return None
    try:
        cred = ptr.contents
        password = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-16-le")
        return cred.UserName or "", password
    finally:
        ctypes.windll.advapi32.CredFree(ptr)


# --- when ---------------------------------------------------------------------

def recent_restarts(now: float | None = None) -> int:
    now = now or time.time()
    try:
        times = [float(x) for x in HISTORY.read_text(encoding="utf-8").split()]
    except (OSError, ValueError):
        return 0
    return sum(1 for t in times if now - t < 3600)


def _note_restart():
    try:
        HISTORY.parent.mkdir(exist_ok=True)
        with HISTORY.open("a", encoding="utf-8") as f:
            f.write(f"{time.time():.0f}\n")
    except OSError:
        pass


GAME_FILE = Path("state") / "game_client.json"  # the bot's own game: {"pid", "hwnd"}


def all_game_windows() -> list[int]:
    try:
        from wizwalker.utils import get_all_wizard_handles

        return list(get_all_wizard_handles())
    except Exception:
        return []


def window_pid(handle: int) -> int:
    from ctypes import wintypes

    pid = wintypes.DWORD()
    try:
        ctypes.windll.user32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    except Exception:
        return 0
    return pid.value


def bot_game_pid() -> int:
    """The process of the game the bot plays (the player may run another)."""
    try:
        return int(json.loads(GAME_FILE.read_text(encoding="utf-8")).get("pid", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0


def remember_game(pid: int, handle: int = 0):
    try:
        GAME_FILE.parent.mkdir(exist_ok=True)
        GAME_FILE.write_text(json.dumps({"pid": pid, "hwnd": handle}), encoding="utf-8")
    except OSError:
        pass


def game_windows() -> list[int]:
    """The bot's own game window(s): the one it remembers, when that's still
    running; with none remembered, every game window."""
    handles = all_game_windows()
    pid = bot_game_pid()
    if not pid:
        return handles
    return [h for h in handles if window_pid(h) == pid]


def window_hung(handle: int) -> bool:
    """The game window isn't processing messages ("Not Responding")."""
    try:
        return bool(ctypes.windll.user32.IsHungAppWindow(handle))
    except Exception:
        return False


def request(reason: str):
    """Ask the supervisor for a game restart (the bot then stops)."""
    try:
        REQUEST.parent.mkdir(exist_ok=True)
        REQUEST.write_text(reason, encoding="utf-8")
    except OSError:
        pass


def needs_restart(bot_output_tail: str = "") -> str:
    """Why the game should be restarted before the bot starts again ("" if
    it shouldn't): a request from the bot, no game window, or the bot unable
    to hook it."""
    if REQUEST.exists():
        try:
            return REQUEST.read_text(encoding="utf-8").strip() or "the bot asked"
        except OSError:
            return "the bot asked"
    if not game_windows():
        return "no game window (closed or crashed)"
    if "Could not hook into the game" in bot_output_tail or "No Wizard101 window found" in bot_output_tail:
        return "the bot could not hook the game"
    return ""


# --- how ----------------------------------------------------------------------

def restart_game(log=print) -> bool:
    """Close the game, start it, log in. True once a login was sent (the bot
    presses Play at character select itself)."""
    REQUEST.unlink(missing_ok=True)
    if recent_restarts() >= MAX_PER_HOUR:
        log(f"game restart: already {MAX_PER_HOUR} this hour; not again (the player is needed)")
        return False
    login = load_login()
    if login is None:
        log("game restart: no saved login (run `wiz101-auto set-login` once); can't log back in")
        return False
    _note_restart()
    from wizwalker.utils import instance_login, start_instance

    # Only the bot's own game: the player may be playing another copy.
    pid = bot_game_pid()
    others = [h for h in all_game_windows() if window_pid(h) != pid]
    if pid:
        log(f"game restart: closing the bot's Wizard101 (process {pid})")
        subprocess.call(["taskkill", "/PID", str(pid), "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif others:
        log("game restart: several games open and none known as the bot's; not closing any")
        return False
    for _ in range(30):
        if not game_windows() or (pid and not any(window_pid(h) == pid for h in all_game_windows())):
            break
        time.sleep(1.0)
    time.sleep(3.0)
    before = set(all_game_windows())
    log("game restart: starting Wizard101")
    start_instance()
    deadline = time.monotonic() + WINDOW_WAIT
    new: list[int] = []
    while time.monotonic() < deadline and not new:
        time.sleep(2.0)
        new = [h for h in all_game_windows() if h not in before]
    if not new:
        log("game restart: the game window never appeared")
        return False
    remember_game(window_pid(new[0]), new[0])
    time.sleep(LOGIN_SCREEN_WAIT)
    username, password = login
    log(f"game restart: logging in as {username}")
    instance_login(new[0], username, password)
    time.sleep(AFTER_LOGIN_WAIT)
    return True
