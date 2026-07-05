#!/usr/bin/env python
"""Local MT2Portugalia login helper using Windows Credential Manager.

This script is intentionally local-only. It never prints the stored password.
Use:
  python scripts/login_mt2_local.py setup --username yoshy
  python scripts/login_mt2_local.py login --username yoshy
  python scripts/login_mt2_local.py delete --username yoshy

Login behavior: focuses the MT2Portugalia window, waits a few seconds, then types
username, TAB, password, ENTER using Windows SendInput Unicode events.
"""
from __future__ import annotations

import argparse
import ctypes
import getpass
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

APP_DIR = Path("D:/Games/MT2Portugalia/app")
CLIENT_ROOT = APP_DIR.parent
CLIENT_EXE = APP_DIR / "pgclient.app"
LAUNCHER_EXE = CLIENT_ROOT / "MT2Portugalia.exe"
WINDOW_TITLE = "MT2Portugalia"
DEFAULT_USERNAME_POS = (520, 371)
DEFAULT_PASSWORD_POS = (520, 434)
DEFAULT_LOGIN_POS = (654, 675)

CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2

VK_TAB = 0x09
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
INPUT_KEYBOARD = 1


def normalize_username(username: str) -> str:
    return username.strip()


def parse_pair(value: str) -> tuple[int, int]:
    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError("expected coordinate pair as x,y")
    try:
        return int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ValueError("expected coordinate pair as x,y") from exc


def credential_target(username: str) -> str:
    return f"MT2Portugalia:{normalize_username(username)}"


def build_enter_game_sequence(count: int = 1) -> list[tuple[str, str]]:
    return [("key", "ENTER") for _ in range(max(0, count))]


def build_login_sequence(username: str, password: str, *, enter_game: bool = False, enter_game_count: int = 1) -> list[tuple[str, str]]:
    steps = [("text", normalize_username(username)), ("key", "TAB"), ("text", password), ("key", "ENTER")]
    if enter_game:
        steps.extend(build_enter_game_sequence(enter_game_count))
    return steps


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


PCREDENTIALW = ctypes.POINTER(CREDENTIALW)
advapi32 = ctypes.windll.advapi32 if sys.platform == "win32" else None
user32 = ctypes.windll.user32 if sys.platform == "win32" else None


def write_credential(username: str, password: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Windows Credential Manager is only available on Windows")
    blob = password.encode("utf-16le")
    blob_buf = ctypes.create_string_buffer(blob)
    cred = CREDENTIALW()
    cred.Type = CRED_TYPE_GENERIC
    cred.TargetName = credential_target(username)
    cred.CredentialBlobSize = len(blob)
    cred.CredentialBlob = ctypes.cast(blob_buf, ctypes.POINTER(ctypes.c_byte))
    cred.Persist = CRED_PERSIST_LOCAL_MACHINE
    cred.UserName = username
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def read_credential(username: str) -> str:
    if sys.platform != "win32":
        raise RuntimeError("Windows Credential Manager is only available on Windows")
    pcred = PCREDENTIALW()
    if not advapi32.CredReadW(credential_target(username), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)):
        raise RuntimeError(f"No stored credential for {credential_target(username)}. Run setup first.")
    try:
        cred = pcred.contents
        raw = ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize)
        return raw.decode("utf-16le")
    finally:
        advapi32.CredFree(pcred)


def delete_credential(username: str) -> bool:
    if sys.platform != "win32":
        raise RuntimeError("Windows Credential Manager is only available on Windows")
    return bool(advapi32.CredDeleteW(credential_target(username), CRED_TYPE_GENERIC, 0))


def build_terminate_command() -> str:
    return 'wmic process where "name=\'pgclient.app\'" call terminate'


def terminate_client_processes() -> None:
    subprocess.run(build_terminate_command(), shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def choose_client_launch_executable(client_root: Path = CLIENT_ROOT, app_dir: Path = APP_DIR) -> Path:
    """Open the already-patched game client directly; do not run the launcher/patcher."""
    return app_dir / "pgclient.app"


def is_user_admin() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def quote_windows_arg(value: str) -> str:
    return subprocess.list2cmdline([value])


def relaunch_current_command_elevated() -> bool:
    """Re-run this helper as admin so it can launch/control MT2Portugalia.

    pgclient.app currently has an elevated manifest on Yoshy's machine. Starting it
    from the unelevated dashboard raises WinError 740, and an unelevated helper is
    also unreliable for typing into an elevated game window. The native panel keeps
    its safe local API, while this helper hands the actual login flow to an elevated
    copy after the user approves the UAC prompt.
    """
    if not hasattr(ctypes, "windll"):
        return False
    script = str(Path(__file__).resolve())
    params = " ".join([quote_windows_arg(script), *[quote_windows_arg(arg) for arg in sys.argv[1:]]])
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]
    shell32.ShellExecuteW.restype = wintypes.HINSTANCE
    rc = shell32.ShellExecuteW(None, "runas", sys.executable, params, str(Path.cwd()), 1)
    if int(rc) <= 32:
        raise ctypes.WinError(int(rc))
    return True


def should_self_elevate_for_login(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "self_elevate", True)) and not is_user_admin()


def find_window(title_substring: str) -> int:
    matches: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if title_substring.lower() in buf.value.lower():
            matches.append(int(hwnd))
        return True

    user32.EnumWindows(enum_proc, 0)
    return matches[0] if matches else 0


def is_expected_foreground_window(target_hwnd: int, foreground_hwnd: int) -> bool:
    return bool(target_hwnd) and int(target_hwnd) == int(foreground_hwnd)


def launch_client_if_needed() -> None:
    if find_window(WINDOW_TITLE):
        return
    exe = choose_client_launch_executable()
    subprocess.Popen([str(exe)], cwd=str(exe.parent))


def focus_window(hwnd: int) -> None:
    if not hwnd:
        raise RuntimeError(f"Window not found: {WINDOW_TITLE}")
    user32.ShowWindow(hwnd, 5)  # SW_SHOW
    try:
        fg = user32.GetForegroundWindow()
        current_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        target_thread = user32.GetWindowThreadProcessId(hwnd, None)
        if foreground_thread:
            user32.AttachThreadInput(current_thread, foreground_thread, True)
        if target_thread and target_thread != foreground_thread:
            user32.AttachThreadInput(current_thread, target_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        try:
            if foreground_thread:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if target_thread and target_thread != foreground_thread:
                user32.AttachThreadInput(current_thread, target_thread, False)
        except Exception:
            pass
    time.sleep(0.2)
    if not is_expected_foreground_window(hwnd, user32.GetForegroundWindow()):
        raise RuntimeError("Refusing to type credentials because MT2Portugalia is not the foreground window")


ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("union", INPUT_UNION)]


def send_input(inp: INPUT) -> None:
    sent = user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
    if sent != 1:
        raise ctypes.WinError(ctypes.get_last_error())


def press_vk(vk: int) -> None:
    for flags in (0, KEYEVENTF_KEYUP):
        inp = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(vk, 0, flags, 0, 0)))
        send_input(inp)


def type_text(text: str) -> None:
    for ch in text:
        code = ord(ch)
        down = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)))
        up = INPUT(type=INPUT_KEYBOARD, union=INPUT_UNION(ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)))
        send_input(down)
        send_input(up)
        time.sleep(0.01)


def type_login_sequence(username: str, password: str, *, enter_game: bool = False, enter_game_count: int = 1) -> None:
    for kind, value in build_login_sequence(username, password, enter_game=enter_game, enter_game_count=enter_game_count):
        if kind == "text":
            type_text(value)
        elif value == "TAB":
            press_vk(VK_TAB)
        elif value == "ENTER":
            press_vk(VK_RETURN)
        time.sleep(0.08)


def click_window_relative(hwnd: int, pos: tuple[int, int]) -> None:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    x, y = pos
    user32.SetCursorPos(rect.left + x, rect.top + y)
    time.sleep(0.10)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
    time.sleep(0.20)


def type_login_sequence_by_clicks(
    hwnd: int,
    username: str,
    password: str,
    *,
    username_pos: tuple[int, int] = DEFAULT_USERNAME_POS,
    password_pos: tuple[int, int] = DEFAULT_PASSWORD_POS,
    login_pos: tuple[int, int] = DEFAULT_LOGIN_POS,
    enter_game: bool = False,
    enter_game_count: int = 1,
) -> None:
    click_window_relative(hwnd, username_pos)
    type_text(normalize_username(username))
    click_window_relative(hwnd, password_pos)
    type_text(password)
    click_window_relative(hwnd, login_pos)
    if enter_game:
        time.sleep(4.0)
        for _kind, value in build_enter_game_sequence(enter_game_count):
            if value == "ENTER":
                press_vk(VK_RETURN)
                time.sleep(1.0)


def cmd_setup(args: argparse.Namespace) -> None:
    password = getpass.getpass(f"Password for {args.username} (stored in Windows Credential Manager): ")
    if not password:
        raise SystemExit("Refusing to store an empty password")
    write_credential(args.username, password)
    print(f"Stored credential target: {credential_target(args.username)}")


def cmd_login(args: argparse.Namespace) -> None:
    if should_self_elevate_for_login(args):
        relaunch_current_command_elevated()
        print("MT2Portugalia login helper relaunched as Administrator. Approve the Windows UAC prompt to open/login the game.")
        return
    password = read_credential(args.username)
    if args.restart:
        terminate_client_processes()
        time.sleep(1.0)
        launch_client_if_needed()
    elif args.launch:
        launch_client_if_needed()
    deadline = time.time() + args.window_timeout
    hwnd = 0
    while time.time() < deadline and not hwnd:
        hwnd = find_window(args.window_title)
        if not hwnd:
            time.sleep(0.5)
    focus_window(hwnd)
    print(f"Focused {args.window_title}. Typing login in {args.delay:.1f}s; do not type/click meanwhile.")
    time.sleep(args.delay)
    if args.click_fields:
        type_login_sequence_by_clicks(
            hwnd,
            args.username,
            password,
            username_pos=args.username_pos,
            password_pos=args.password_pos,
            login_pos=args.login_pos,
            enter_game=args.enter_game,
            enter_game_count=args.enter_game_count,
        )
    else:
        type_login_sequence(args.username, password, enter_game=args.enter_game, enter_game_count=args.enter_game_count)
    print("Login sequence sent.")


def cmd_delete(args: argparse.Namespace) -> None:
    if delete_credential(args.username):
        print(f"Deleted credential target: {credential_target(args.username)}")
    else:
        print(f"Credential target was not deleted/found: {credential_target(args.username)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local MT2Portugalia login helper")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="Prompt for password and store it in Windows Credential Manager")
    setup.add_argument("--username", default="yoshy")
    setup.set_defaults(func=cmd_setup)

    login = sub.add_parser("login", help="Read stored credential and type it into MT2Portugalia")
    login.add_argument("--username", default="yoshy")
    login.add_argument("--window-title", default=WINDOW_TITLE)
    login.add_argument("--window-timeout", type=float, default=30.0)
    login.add_argument("--delay", type=float, default=3.0)
    login.add_argument("--launch", action="store_true", help="Launch client if the window is not found")
    login.add_argument("--restart", action="store_true", help="Terminate pgclient.app before launching and logging in")
    login.add_argument("--enter-game", action="store_true", help="Press Enter once more after login to activate Começar/enter game")
    login.add_argument("--enter-game-count", type=int, default=1, help="Number of extra Enter presses after login when --enter-game is set")
    login.add_argument("--click-fields", action="store_true", help="Click static username/password/login positions instead of using Tab navigation")
    login.add_argument("--username-pos", type=parse_pair, default=DEFAULT_USERNAME_POS, help="Username field coordinate as x,y relative to the MT2 window")
    login.add_argument("--password-pos", type=parse_pair, default=DEFAULT_PASSWORD_POS, help="Password field coordinate as x,y relative to the MT2 window")
    login.add_argument("--login-pos", type=parse_pair, default=DEFAULT_LOGIN_POS, help="Login button coordinate as x,y relative to the MT2 window")
    login.add_argument("--no-self-elevate", dest="self_elevate", action="store_false", help="Do not relaunch this helper as Administrator before login")
    login.set_defaults(func=cmd_login, self_elevate=True)

    delete = sub.add_parser("delete", help="Delete stored credential")
    delete.add_argument("--username", default="yoshy")
    delete.set_defaults(func=cmd_delete)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

