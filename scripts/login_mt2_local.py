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


def credential_exists(username: str) -> bool:
    if sys.platform != "win32":
        return False
    pcred = PCREDENTIALW()
    ok = bool(advapi32.CredReadW(credential_target(username), CRED_TYPE_GENERIC, 0, ctypes.byref(pcred)))
    if ok:
        advapi32.CredFree(pcred)
    return ok


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
    return 'taskkill /F /IM pgclient.app /T'


def terminate_client_processes(app_dir: Path | None = None) -> None:
    # With a configured app_dir, terminate only pgclient processes launched from that folder so the buffer client does not kill the main client.
    if app_dir is not None and sys.platform == "win32":
        try:
            out = subprocess.check_output(
                'wmic process where "name=\'pgclient.app\'" get ProcessId,ExecutablePath /format:csv',
                shell=True, text=True, stderr=subprocess.STDOUT, timeout=10,
            )
            root = str(Path(app_dir)).replace("\\", "/").lower().rstrip("/") + "/"
            for line in out.splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 3 and parts[-1].isdigit():
                    exe = ",".join(parts[1:-1]).replace("\\", "/").lower()
                    if exe.startswith(root):
                        subprocess.run(f"taskkill /F /PID {parts[-1]} /T", shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            return
        except Exception:
            pass
    # taskkill returns non-zero when no client is running; that is OK for restart.
    subprocess.run(build_terminate_command(), shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for name in ("MT2Portugalia.exe",):
        subprocess.run(f'taskkill /F /IM {name} /T', shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def choose_client_launch_executable(client_root: Path = CLIENT_ROOT, app_dir: Path = APP_DIR) -> Path:
    """Open the already-patched game client directly; do not run the launcher/patcher."""
    return app_dir / "pgclient.app"


def materialize_loose_game_from_pack_if_missing(app_dir: Path = APP_DIR) -> Path | None:
    """Direct pgclient launches can fail at game phase unless app/game.py exists.

    MT2Portugalia's launcher normally resolves packed root assets, but our direct
    login flow launches pgclient.app after local pack patching. On this client,
    direct game phase import raises `IOError: game.py` unless a loose app/game.py
    override exists. Materialize the patched game chunk from app/pack/root when
    missing so Open game + login reaches the map.
    """
    loose = app_dir / "game.py"
    if loose.exists():
        return None
    root_pack = app_dir / "pack" / "root"
    if not root_pack.exists():
        return None
    project_root = Path(__file__).resolve().parents[1]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from scripts.patch_mt2_root_state_logger import find_game_chunk, iter_chunks

    blob = bytearray(root_pack.read_bytes())
    _idx, _chunk, src = find_game_chunk(blob, list(iter_chunks(blob)))
    loose.write_bytes(src)
    return loose


def is_user_admin() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def quote_windows_arg(value: str) -> str:
    return subprocess.list2cmdline([value])


def relaunch_current_command_elevated(elevated_log: Path | None = None) -> int:
    """Re-run this helper as admin, wait, and surface the elevated child's result.

    The dashboard parent is normally medium integrity while pgclient.app is
    elevated. A fire-and-forget UAC launch made the control panel report success
    even when the elevated child failed or never reached the login window. Use
    ShellExecuteExW + SEE_MASK_NOCLOSEPROCESS so the managed run waits and logs
    the elevated child exit code, matching the proven key_macro_control pattern.
    """
    if not hasattr(ctypes, "windll"):
        return 1

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("fMask", ctypes.c_ulong),
            ("hwnd", ctypes.c_void_p),
            ("lpVerb", ctypes.c_wchar_p),
            ("lpFile", ctypes.c_wchar_p),
            ("lpParameters", ctypes.c_wchar_p),
            ("lpDirectory", ctypes.c_wchar_p),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", ctypes.c_wchar_p),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]

    relaunch_args = list(sys.argv[1:])
    if "--elevated-log" not in relaunch_args:
        elevated_log = elevated_log or Path("reports/dashboard_runs/login_mt2_local.elevated.log")
        relaunch_args.extend(["--elevated-log", str(elevated_log)])
    relaunch_args.append("--no-self-elevate")
    params = subprocess.list2cmdline([str(Path(__file__).resolve()), *relaunch_args])
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = sys.executable
    info.lpParameters = params
    info.lpDirectory = str(Path.cwd())
    info.nShow = 1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)):
        err = ctypes.get_last_error()
        raise RuntimeError(f"ShellExecuteExW runas failed with WinError {err}")
    print("MT2Portugalia login helper relaunched as Administrator. Approve the Windows UAC prompt; waiting for elevated child.", flush=True)
    if info.hProcess:
        ctypes.windll.kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        exit_code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(exit_code))
        ctypes.windll.kernel32.CloseHandle(info.hProcess)
        print(f"elevated_login_child_exit_code {exit_code.value}", flush=True)
        return int(exit_code.value)
    return 0


def should_self_elevate_for_login(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "self_elevate", True)) and not is_user_admin()


def _process_image_path(pid: int) -> str:
    if sys.platform != "win32":
        return ""
    kernel32 = ctypes.windll.kernel32
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, wintypes.DWORD(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        query = kernel32.QueryFullProcessImageNameW
        query.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        query.restype = wintypes.BOOL
        if query(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _process_image_name(pid: int) -> str:
    path = _process_image_path(pid)
    return Path(path).name.lower() if path else ""


def _is_game_window_process(pid: int) -> bool:
    name = _process_image_name(pid)
    return name in {"pgclient.app", "pgclient.exe", "mt2portugalia.exe"}


def _pid_matches_app_dir(pid: int, app_dir: Path | None) -> bool:
    if app_dir is None:
        return True
    image = _process_image_path(pid).replace("\\", "/").lower()
    root = str(Path(app_dir)).replace("\\", "/").lower().rstrip("/") + "/"
    return bool(image) and image.startswith(root)


def find_window(title_substring: str, app_dir: Path | None = None) -> int:
    matches: list[tuple[int, int, str, str]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def enum_proc(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if title_substring.lower() in title.lower():
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            proc_path = _process_image_path(int(pid.value))
            # Some elevated/game windows deny PROCESS_QUERY_LIMITED_INFORMATION
            # to the medium-integrity dashboard helper, so the image path can be
            # blank even for the exact MT2Portugalia top-level window.  Accept
            # exact title matches with an unknown path; still reject non-exact
            # folder/browser windows unless they pass the app_dir filter below.
            if _pid_matches_app_dir(int(pid.value), app_dir) or (not proc_path and title.strip().lower() == title_substring.strip().lower()):
                matches.append((int(hwnd), int(pid.value), title, _process_image_name(int(pid.value))))
        return True

    user32.EnumWindows(enum_proc, 0)
    exact_title = title_substring.strip().lower()
    for hwnd, _pid, title, _proc in matches:
        if title.strip().lower() == exact_title:
            return hwnd
    for hwnd, pid, _title, _proc in matches:
        if _is_game_window_process(pid):
            return hwnd

    # folder/title text as the game. Returning 0 lets launch_client_if_needed()
    # start pgclient.app instead of typing credentials into the wrong app.
    return 0


def is_expected_foreground_window(target_hwnd: int, foreground_hwnd: int) -> bool:
    return bool(target_hwnd) and int(target_hwnd) == int(foreground_hwnd)


def launch_client_if_needed(app_dir: Path = APP_DIR, window_title: str = WINDOW_TITLE) -> None:
    if find_window(window_title, app_dir=app_dir):
        return
    materialized = materialize_loose_game_from_pack_if_missing(app_dir)
    if materialized:
        print(f"materialized_loose_game_py {materialized}", flush=True)
    exe = choose_client_launch_executable(app_dir.parent, app_dir)
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
        code = relaunch_current_command_elevated(getattr(args, "elevated_log", None))
        raise SystemExit(code)
    if getattr(args, "elevated_log", None):
        log_path = Path(args.elevated_log)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("a", encoding="utf-8", buffering=1)
        sys.stdout = log
        sys.stderr = log
        print(f"elevated_login_child_started admin={is_user_admin()} argv={sys.argv[1:]}", flush=True)
    password = read_credential(args.username)
    app_dir = Path(args.app_dir)
    if args.restart:
        terminate_client_processes(app_dir)
        time.sleep(1.0)
        launch_client_if_needed(app_dir, args.window_title)
    elif args.launch:
        launch_client_if_needed(app_dir, args.window_title)
    deadline = time.time() + args.window_timeout
    hwnd = 0
    while time.time() < deadline and not hwnd:
        hwnd = find_window(args.window_title, app_dir=app_dir)
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
    login.add_argument("--app-dir", type=Path, default=APP_DIR, help="Client app directory containing pgclient.app")
    login.add_argument("--window-title", default=WINDOW_TITLE)
    login.add_argument("--window-timeout", type=float, default=90.0)
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
    login.add_argument("--elevated-log", type=Path, default=None, help="Path where elevated child writes detailed login diagnostics")
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

