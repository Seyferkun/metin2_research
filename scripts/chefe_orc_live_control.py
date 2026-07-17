#!/usr/bin/env python
"""Gated Chefe Orc live-control loop for Yoshy's private MT2Portugalia sandbox.

Default mode is dry-run: it logs the actions it would take, but sends no input.
Live mode requires --live and should be started only through the dashboard's
confirm_live path or an explicit operator CLI invocation.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_STATE_JSON = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")
DEFAULT_OUT = Path("reports/dashboard_runs/chefe_orc_live_control.jsonl")
LEARNED_CHANNEL_CLICK_POINTS = "0.4990,0.3986;0.4990,0.4326;0.4990,0.4665;0.4990,0.5005;0.4990,0.5344;0.4990,0.5684;0.4990,0.6023;0.4990,0.6363"


def read_state(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {"available": False, "error": "missing_state_json", "_file_mtime": None}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            data = {"raw": data}
    except Exception as exc:
        return {"available": False, "error": f"json_error: {exc}", "_file_mtime": None}
    try:
        data["_file_mtime"] = p.stat().st_mtime
    except Exception:
        data["_file_mtime"] = None
    data.setdefault("available", True)
    return data


def state_age_seconds(state: dict[str, Any]) -> float | None:
    try:
        mtime = state.get("_file_mtime")
        return round(time.time() - float(mtime), 3) if mtime else None
    except Exception:
        return None


def cofre_count(state: dict[str, Any], *, loot_name: str, loot_vnum: int | None) -> int | None:
    name_lc = str(loot_name or "").casefold()
    for item in state.get("inventory") or []:
        if not isinstance(item, dict):
            continue
        vnum_match = loot_vnum is not None and str(item.get("vnum")) == str(loot_vnum)
        name_match = bool(name_lc and name_lc in str(item.get("name") or "").casefold())
        if vnum_match or name_match:
            try:
                return int(item.get("count") or 0)
            except Exception:
                return None
    return None


def target_dict(state: dict[str, Any]) -> dict[str, Any] | None:
    target = state.get("target")
    return target if isinstance(target, dict) and (target.get("vid") or target.get("name")) else None


def target_name(target: dict[str, Any] | None) -> str:
    return str((target or {}).get("name") or "")


def is_boss_target(target: dict[str, Any] | None, boss_name: str) -> bool:
    return bool(target and boss_name.casefold() in target_name(target).casefold())


def target_hp_pct(target: dict[str, Any] | None) -> float | None:
    if not target:
        return None
    raw = target.get("hp_pct")
    if raw is None and target.get("hp") is not None and target.get("max_hp"):
        try:
            raw = 100.0 * float(target["hp"]) / max(1.0, float(target["max_hp"]))
        except Exception:
            raw = None
    try:
        return float(raw) if raw is not None else None
    except Exception:
        return None


def parse_channel_points(spec: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for part in str(spec or "").split(";"):
        part = part.strip()
        if not part:
            continue
        x_s, y_s = part.split(",", 1)
        points.append((float(x_s.strip()), float(y_s.strip())))
    return points


@dataclass
class InputDriver:
    live: bool
    window_query: str
    channel_points: list[tuple[float, float]]
    calls: list[tuple] = field(default_factory=list)

    def _tap(self, key: str, hold: float) -> None:
        if self.live:
            from metin2_research.win_input import tap_key
            tap_key(key, hold)
        else:
            self.calls.append(("dry_tap", key, round(float(hold), 3)))

    def tap(self, key: str, hold: float = 0.06) -> None:
        if self.live:
            self.calls.append(("tap", key, round(float(hold), 3)))
        self._tap(key, hold)

    def hold(self, key: str, duration: float) -> None:
        if self.live:
            self.calls.append(("hold", key, round(float(duration), 3)))
            from metin2_research.win_input import hold_key
            hold_key(key, duration)
        else:
            self.calls.append(("dry_hold", key, round(float(duration), 3)))

    def pickup_spam(self, count: int, interval: float) -> None:
        for _ in range(max(0, int(count))):
            self._tap("z", 0.04)
            if interval > 0:
                time.sleep(float(interval))

    def click_channel(self, index: int) -> None:
        point = self.channel_points[index % len(self.channel_points)]
        if not self.live:
            self.calls.append(("dry_click_channel", index % len(self.channel_points), point))
            return
        self.calls.append(("click_channel", index % len(self.channel_points), point))
        from metin2_research.window_capture import find_window
        from metin2_research.win_input import click_at
        window = find_window(self.window_query)
        x, y = point
        if 0 <= x <= 1 and 0 <= y <= 1:
            sx = int(window.bbox[0] + window.width * x)
            sy = int(window.bbox[1] + window.height * y)
        else:
            sx, sy = int(x), int(y)
        click_at(sx, sy)

    def release_all(self) -> None:
        if not self.live:
            return
        from metin2_research.win_input import key_up
        for key in ("space", "w", "a", "s", "d", "q", "e"):
            try:
                key_up(key)
            except Exception:
                pass


@dataclass
class BossFarmLiveController:
    boss_name: str
    loot_name: str
    loot_vnum: int | None
    channel_rotate: bool
    pickup_spam_count: int
    channel_points: list[tuple[float, float]]
    driver: InputDriver
    out: Path
    max_state_age_seconds: float = 2.0
    attack_burst_seconds: float = 0.6
    pickup_spam_interval: float = 0.08
    channel_menu_delay_seconds: float = 0.35
    channel_switch_wait_seconds: float = 4.0
    confirmed_kills: int = 0
    last_loot_count: int | None = None
    active_boss_vid: int | None = None
    active_boss_start_t: float | None = None
    channel_index: int = 0

    def __post_init__(self) -> None:
        if self.channel_rotate and not self.channel_points:
            raise ValueError("channel click points are required when channel rotation is enabled")
        self.out.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", round(time.time(), 3))
        event.setdefault("confirmed_kills", self.confirmed_kills)
        print(json.dumps(event, ensure_ascii=False), flush=True)
        with self.out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def step(self, state: dict[str, Any]) -> None:
        age = state_age_seconds(state)
        loot = cofre_count(state, loot_name=self.loot_name, loot_vnum=self.loot_vnum)
        if self.last_loot_count is None and loot is not None:
            self.last_loot_count = loot
        if state.get("available") is False or age is None or age > self.max_state_age_seconds:
            self.emit({"action": "WAIT_FRESH_STATE", "state_age_seconds": age, "reason": state.get("error") or "stale_or_missing_state"})
            return

        target = target_dict(state)
        hp = target_hp_pct(target)
        if is_boss_target(target, self.boss_name):
            vid = int(target.get("vid") or 0) if target and target.get("vid") else None
            if vid and self.active_boss_vid != vid:
                self.active_boss_vid = vid
                self.active_boss_start_t = time.time()
            if hp is not None and hp <= 0:
                self._confirm_kill(target, loot, hp)
            else:
                self.emit({"action": "ENGAGE_BOSS", "target": target, "hp_pct": hp, "loot_count": loot})
                self.driver.hold("space", self.attack_burst_seconds)
            return

        if target:
            self.emit({"action": "IGNORE_NON_BOSS_TARGET", "target": target, "loot_count": loot})
            self.driver.tap("tab", 0.06)
            return

        self.emit({"action": "SEARCH_BOSS", "loot_count": loot})
        self.driver.tap("tab", 0.06)

    def _confirm_kill(self, target: dict[str, Any] | None, loot: int | None, hp: float | None) -> None:
        previous = self.last_loot_count
        delta = 0
        if previous is not None and loot is not None and loot > previous:
            delta = loot - previous
        if delta <= 0:
            # HP-zero is strong kill evidence, but pickup/loot may lag a cycle. Still
            # perform pickup once; count only once per active boss VID.
            delta = 1 if self.active_boss_vid and int((target or {}).get("vid") or 0) == self.active_boss_vid else 0
        if delta > 0:
            self.confirmed_kills += delta
        self.last_loot_count = loot if loot is not None else self.last_loot_count
        self.emit({
            "action": "BOSS_KILL_CONFIRMED",
            "target": target,
            "hp_pct": hp,
            "loot_count": loot,
            "previous_loot_count": previous,
            "loot_delta": delta,
            "channel_rotate": self.channel_rotate,
        })
        self.driver.pickup_spam(self.pickup_spam_count, self.pickup_spam_interval)
        if self.channel_rotate:
            self.driver.tap("x", 0.06)
            if self.channel_menu_delay_seconds > 0:
                time.sleep(self.channel_menu_delay_seconds)
            self.driver.click_channel(self.channel_index)
            self.channel_index = (self.channel_index + 1) % max(1, len(self.channel_points))
            if self.channel_switch_wait_seconds > 0:
                time.sleep(self.channel_switch_wait_seconds)
        self.active_boss_vid = None
        self.active_boss_start_t = None


def should_stop(stop_file: Path | None, deadline: float, max_kills: int, confirmed_kills: int) -> bool:
    if stop_file and stop_file.exists():
        return True
    if max_kills > 0 and confirmed_kills >= max_kills:
        return True
    return time.time() >= deadline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gated Chefe Orc select/attack/pickup/channel loop. Dry-run by default; live requires --live.")
    ap.add_argument("--live", action="store_true", help="send bounded inputs; default only logs planned actions")
    ap.add_argument("--state-json", default=str(DEFAULT_STATE_JSON))
    ap.add_argument("--boss-name", default="Chefe Orc")
    ap.add_argument("--loot-name", default="Cofre do Chefe Orc")
    ap.add_argument("--loot-vnum", type=int, default=50070)
    ap.add_argument("--duration", type=float, default=600.0)
    ap.add_argument("--interval", type=float, default=0.25)
    ap.add_argument("--max-kills", type=int, default=0, help="0 means run until duration/stop file")
    ap.add_argument("--attack-burst-seconds", type=float, default=0.6)
    ap.add_argument("--pickup-spam-count", type=int, default=12)
    ap.add_argument("--pickup-spam-interval", type=float, default=0.08)
    ap.add_argument("--channel-rotate", action="store_true", help="after confirmed kill: spam Z, press X, click next channel point")
    ap.add_argument("--channel-click-points", default=LEARNED_CHANNEL_CLICK_POINTS)
    ap.add_argument("--channel-menu-delay-seconds", type=float, default=0.35)
    ap.add_argument("--channel-switch-wait-seconds", type=float, default=4.0)
    ap.add_argument("--window-query", default="MT2Portugalia")
    ap.add_argument("--max-state-age-seconds", type=float, default=2.0)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--stop-file", type=Path, default=None)
    args = ap.parse_args(argv)

    stop_file = args.stop_file or (Path(os.environ["HERMES_STOP_FILE"]) if os.environ.get("HERMES_STOP_FILE") else None)
    if stop_file is None and args.run_id:
        stop_file = Path("reports/dashboard_runs") / f"{args.run_id}.stop"
    points = parse_channel_points(args.channel_click_points)
    driver = InputDriver(live=args.live, window_query=args.window_query, channel_points=points)
    controller = BossFarmLiveController(
        boss_name=args.boss_name,
        loot_name=args.loot_name,
        loot_vnum=args.loot_vnum,
        channel_rotate=bool(args.channel_rotate),
        pickup_spam_count=args.pickup_spam_count,
        channel_points=points,
        driver=driver,
        out=args.out,
        max_state_age_seconds=args.max_state_age_seconds,
        attack_burst_seconds=args.attack_burst_seconds,
        pickup_spam_interval=args.pickup_spam_interval,
        channel_menu_delay_seconds=args.channel_menu_delay_seconds,
        channel_switch_wait_seconds=args.channel_switch_wait_seconds,
    )
    controller.emit({"action": "START", "live": bool(args.live), "run_id": args.run_id, "channel_rotate": bool(args.channel_rotate), "boss_name": args.boss_name})
    deadline = time.time() + max(1.0, float(args.duration))
    try:
        while not should_stop(stop_file, deadline, int(args.max_kills), controller.confirmed_kills):
            controller.step(read_state(args.state_json))
            time.sleep(max(0.05, float(args.interval)))
    finally:
        driver.release_all()
    controller.emit({"action": "STOP", "confirmed_kills": controller.confirmed_kills, "reason": "stop_file" if stop_file and stop_file.exists() else "max_kills_or_duration"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
