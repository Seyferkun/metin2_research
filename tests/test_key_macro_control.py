import json
import subprocess
import sys
from pathlib import Path


def test_key_macro_control_dry_run_logs_f1_without_live_input(tmp_path):
    out = tmp_path / "key_macro.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/key_macro_control.py",
            "--key",
            "f1",
            "--presses",
            "2",
            "--interval-seconds",
            "0.05",
            "--out",
            str(out),
            "--elevate",
        ],
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry_run_key_macro key=f1" in result.stdout
    assert result.stdout.count('"event": "key_macro_press"') == 2
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["key"] == "f1"
    assert data["presses_completed"] == 2
    assert data["live"] is False


def test_key_macro_control_dry_run_accepts_ctrl_g_chord(tmp_path):
    out = tmp_path / "ctrl_g.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/key_macro_control.py",
            "--key",
            "ctrl+g",
            "--presses",
            "1",
            "--out",
            str(out),
        ],
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry_run_key_macro key=ctrl+g" in result.stdout
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["key"] == "ctrl+g"
    assert data["presses_completed"] == 1


def test_key_macro_control_rejects_unknown_key(tmp_path):
    result = subprocess.run(
        [sys.executable, "scripts/key_macro_control.py", "--key", "f9", "--presses", "1", "--out", str(tmp_path / "bad.json")],
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert result.returncode != 0
    assert "unsupported key" in result.stdout + result.stderr
