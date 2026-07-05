from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


class SafetyGateError(RuntimeError):
    """Raised when an actuator command is not allowed by the sandbox safety gate."""


def _target_xy(state: dict[str, Any]) -> list[int] | None:
    value = (
        state.get("screen_xy")
        or state.get("recommended_action", {}).get("screen_xy")
        or state.get("target_xy")
        or state.get("recommended_action", {}).get("target_xy")
    )
    if not value or len(value) != 2:
        return None
    return [int(round(float(value[0]))), int(round(float(value[1])))]


def plan_private_server_action(
    state: dict[str, Any],
    *,
    execute: bool = False,
    private_server_confirmed: bool = False,
    click_button: str = "left",
) -> dict[str, Any]:
    """Plan or execute a basic private-sandbox action from an advisory state.

    The default is dry-run. Execution is allowed only after the caller explicitly
    confirms this is a private server/sandbox they own or have permission to use.
    This uses normal OS-level input only; no anti-cheat bypass or memory access.
    """
    if not private_server_confirmed:
        raise SafetyGateError("Actuator requires explicit private server/sandbox confirmation")

    recommended = state.get("recommended_action", {})
    action = recommended.get("action")
    xy = _target_xy(state)
    intended = "NO_OP"
    steps: list[dict[str, Any]] = []

    if action == "INVESTIGATE_TARGET" and xy:
        intended = "MOVE_CURSOR_TO_TARGET"
        steps = [{"op": "moveTo", "x": xy[0], "y": xy[1], "duration": 0.15}]
    elif action == "APPROACH_OR_ATTACK" and xy:
        intended = "CLICK_TARGET"
        steps = [
            {"op": "moveTo", "x": xy[0], "y": xy[1], "duration": 0.10},
            {"op": "click", "button": click_button},
        ]
    else:
        steps = [{"op": "noop", "reason": f"No actuator mapping for {action}"}]

    plan = {
        "mode": "execute" if execute else "dry_run",
        "recommended_action": action,
        "intended_action": intended,
        "screen_xy": xy,
        "steps": steps,
        "executed": False,
        "safety": "private_server_confirmed; standard OS input only; no bypass",
    }

    if execute and steps:
        try:
            import pyautogui

            pyautogui.FAILSAFE = True
            for step in steps:
                if step["op"] == "moveTo":
                    pyautogui.moveTo(step["x"], step["y"], duration=step.get("duration", 0.0))
                elif step["op"] == "click":
                    pyautogui.click(button=step.get("button", click_button))
            plan["backend"] = "pyautogui"
        except ModuleNotFoundError:
            import ctypes
            import time

            user32 = ctypes.windll.user32
            for step in steps:
                if step["op"] == "moveTo":
                    user32.SetCursorPos(int(step["x"]), int(step["y"]))
                    time.sleep(float(step.get("duration", 0.0)))
                elif step["op"] == "click":
                    if step.get("button", click_button) != "left":
                        raise RuntimeError("ctypes fallback currently supports only left click")
                    user32.mouse_event(0x0002, 0, 0, 0, 0)
                    time.sleep(0.05)
                    user32.mouse_event(0x0004, 0, 0, 0, 0)
            plan["backend"] = "ctypes.user32"
        plan["executed"] = True
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safety-gated private-server Metin2 actuator. Dry-run by default.")
    parser.add_argument("state_json", help="State JSON from live_try or predict")
    parser.add_argument("--private-server-confirmed", action="store_true", help="Required safety gate")
    parser.add_argument("--execute", action="store_true", help="Actually send normal OS mouse input")
    parser.add_argument("--out", help="Write action plan JSON")
    args = parser.parse_args(argv)

    state = json.loads(Path(args.state_json).read_text(encoding="utf-8"))
    plan = plan_private_server_action(
        state,
        execute=args.execute,
        private_server_confirmed=args.private_server_confirmed,
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
