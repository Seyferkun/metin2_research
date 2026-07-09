#!/usr/bin/env python
from __future__ import annotations

import ctypes
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from metin2_dashboard.control_panel import main


def is_admin() -> bool:
    if not hasattr(ctypes, "windll"):
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_elevated(argv: list[str]) -> int:
    child_args = [str(Path(__file__).resolve()), *[arg for arg in argv if arg != "--elevate"]]
    params = subprocess.list2cmdline(child_args)
    rc = ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        str(PROJECT_ROOT),
        1,
    )
    if rc <= 32:
        raise RuntimeError(f"ShellExecuteW runas failed with code {rc}")
    print("Metin2 Control Panel relaunched as Administrator. Approve the Windows UAC prompt once; per-buff UAC prompts should stop.", flush=True)
    return 0


def entrypoint(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--elevate" in argv and not is_admin():
        return relaunch_elevated(argv)
    sys.argv = [sys.argv[0], *[arg for arg in argv if arg != "--elevate"]]
    return main()


if __name__ == "__main__":
    raise SystemExit(entrypoint())
