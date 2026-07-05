from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Iterable, Any

SAFE_KEY_RELEASE_CODE = """
try:
    from pynput.keyboard import Controller, Key
    k = Controller()
    for key in ['w','a','s','d',' ']:
        k.release(key)
except Exception:
    pass
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class OptionSpec:
    name: str
    flag: str
    type: str = "str"
    default: Any = None
    description: str = ""
    live_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "flag": self.flag,
            "type": self.type,
            "default": self.default,
            "description": self.description,
            "live_only": self.live_only,
        }

    def coerce(self, value: Any) -> str:
        if self.type == "bool":
            if isinstance(value, str):
                truthy = value.strip().lower() in {"1", "true", "yes", "on"}
            else:
                truthy = bool(value)
            return "true" if truthy else "false"
        if self.type == "int":
            return str(int(value))
        if self.type == "float":
            return str(float(value))
        return str(value)


@dataclass(frozen=True)
class ScriptSpec:
    name: str
    path: str
    description: str
    default_args: tuple[str, ...] = ()
    live_args: tuple[str, ...] = ("--live",)
    exclusive_live_group: str | None = None
    options: tuple[OptionSpec, ...] = ()
    force_dry_run: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "description": self.description,
            "default_args": list(self.default_args),
            "live_args": list(self.live_args),
            "exclusive_live_group": self.exclusive_live_group,
            "force_dry_run": self.force_dry_run,
            "options": [opt.to_dict() for opt in self.options],
        }


DEFAULT_SCRIPTS: dict[str, ScriptSpec] = {
    "learn_runaround_client_tsv": ScriptSpec(
        name="learn_runaround_client_tsv",
        path="scripts/learn_runaround_client_tsv.py",
        description="navigation learning / runaround script; dry-run uses zero cycles and no focus/input",
        default_args=(
            "--cycles",
            "0",
            "--no-focus",
            "--seconds",
            "0.05",
            "--pause",
            "0.05",
            "--out-jsonl",
            "reports/dashboard_runs/learn_runaround_dryrun.jsonl",
            "--model-out",
            "reports/dashboard_runs/learn_runaround_dryrun_model.json",
        ),
        live_args=(
            "--cycles",
            "1",
            "--seconds",
            "0.2",
            "--pause",
            "0.35",
            "--out-jsonl",
            "reports/dashboard_runs/learn_runaround_live.jsonl",
            "--model-out",
            "reports/dashboard_runs/learn_runaround_live_model.json",
        ),
        options=(
            OptionSpec("cycles", "--cycles", "int", 1, "movement calibration cycles"),
            OptionSpec("seconds", "--seconds", "float", 0.2, "key hold seconds per probe"),
            OptionSpec("pause", "--pause", "float", 0.35, "pause after each probe"),
            OptionSpec("keys", "--keys", "str", "w,a,s,d", "movement keys to probe"),
            OptionSpec("potion_hp_ratio", "--potion-hp-ratio", "float", 0.5, "potion threshold; negative disables"),
        ),
    ),
    "combat_metin_client_state": ScriptSpec(
        name="combat_metin_client_state",
        path="scripts/combat_metin_client_state.py",
        description="combat state script; dry-run by default, live mode is exclusive",
        default_args=("--max-cycles", "1", "--out", "reports/dashboard_runs/combat_metin_dryrun.jsonl"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("max_cycles", "--max-cycles", "int", 60, "decision loop cycles"),
            OptionSpec("burst_seconds", "--burst-seconds", "float", 2.5, "attack burst length"),
            OptionSpec("micro_move_seconds", "--micro-move-seconds", "float", 0.12, "micro-position probe length"),
            OptionSpec("metin_name", "--metin-name", "str", "Metin da Batalha", "target Metin name"),
            OptionSpec("metin_vid", "--metin-vid", "int", None, "known target VID"),
            OptionSpec("metin_x", "--metin-x", "int", None, "known Metin x coord"),
            OptionSpec("metin_y", "--metin-y", "int", None, "known Metin y coord"),
            OptionSpec("metin_coord_source", "--metin-coord-source", "str", None, "coordinate source: live_memory_visible_text or table"),
        ),
    ),
    "find_nearby_metins": ScriptSpec(
        name="find_nearby_metins",
        path="scripts/find_nearby_metins.py",
        description="rank known Metin coordinates around the current character and export JSON/CSV",
        default_args=(),
        live_args=(),
        force_dry_run=True,
        options=(
            OptionSpec("radius", "--radius", "float", 300.0, "search radius in map-coordinate units"),
            OptionSpec("limit", "--limit", "int", 8, "maximum nearby Metins to return"),
            OptionSpec("source", "--source", "str", "hybrid", "hybrid, live-memory, visual, table, or both"),
            OptionSpec("state_json", "--state-json", "str", "D:/Games/MT2Portugalia/app/hermes_state.json", "live client-state JSON path"),
            OptionSpec("coords_csv", "--coords-csv", "str", "data/metin_coordinates.csv", "known Metin coordinate table"),
            OptionSpec("out_json", "--out-json", "str", "reports/nearby_metins/latest_nearby_metins.json", "output JSON artifact"),
            OptionSpec("out_csv", "--out-csv", "str", "reports/nearby_metins/latest_nearby_metins.csv", "output CSV artifact"),
            OptionSpec("include_unknown_map", "--include-unknown-map", "bool", False, "include coordinates whose map is unknown"),
            OptionSpec("display_offset_x", "--display-offset-x", "int", 0, "optional x offset from client/logger coords to user/display coords"),
            OptionSpec("display_offset_y", "--display-offset-y", "int", 0, "optional y offset from client/logger coords to user/display coords"),
        ),
    ),
    "move_to_metin_client_state": ScriptSpec(
        name="move_to_metin_client_state",
        path="scripts/move_to_metin_client_state.py",
        description="movement-only direct approach to a trusted Metin coordinate; live mode is exclusive and never attacks",
        default_args=("--metin-x", "0", "--metin-y", "0", "--metin-coord-source", "dry_run_placeholder", "--max-cycles", "1", "--out", "reports/dashboard_runs/move_to_metin_dryrun.jsonl"),
        exclusive_live_group="combat",
        options=(
            OptionSpec("max_cycles", "--max-cycles", "int", 90, "movement loop cycles"),
            OptionSpec("arrival_radius", "--arrival-radius", "float", 260.0, "stop when within this raw-unit radius"),
            OptionSpec("move_step_seconds", "--move-step-seconds", "float", 0.45, "maximum smooth movement hold per cycle"),
            OptionSpec("metin_name", "--metin-name", "str", "Metin da Batalha", "target Metin name"),
            OptionSpec("metin_x", "--metin-x", "int", None, "trusted Metin x coord"),
            OptionSpec("metin_y", "--metin-y", "int", None, "trusted Metin y coord"),
            OptionSpec("metin_coord_source", "--metin-coord-source", "str", None, "coordinate source: live_memory_visible_text"),
        ),
    ),
    "login_mt2_local": ScriptSpec(
        name="login_mt2_local",
        path="scripts/login_mt2_local.py",
        description="open/restart MT2Portugalia, login with Windows Credential Manager, and enter game",
        default_args=("--help",),
        live_args=("login", "--username", "yoshy", "--restart", "--click-fields", "--enter-game", "--enter-game-count", "3", "--delay", "5"),
        options=(
            OptionSpec("username", "--username", "str", "yoshy", "Credential Manager username"),
            OptionSpec("enter_game_count", "--enter-game-count", "int", 3, "Enter/Começar presses after login"),
            OptionSpec("delay", "--delay", "float", 5.0, "seconds to wait before typing login"),
        ),
    ),
    "patch_mt2_root_state_logger": ScriptSpec(
        name="patch_mt2_root_state_logger",
        path="scripts/patch_mt2_root_state_logger.py",
        description="patch/integrate the read-only Hermes client-state logger into loose game.py and pack/root",
        default_args=("--dry-run",),
        live_args=(),
        options=(
            OptionSpec("skip_loose", "--skip-loose", "bool", False, "skip loose app/game.py patch"),
            OptionSpec("dry_run", "--dry-run", "bool", False, "check patch size without writing"),
        ),
    ),
    "probe_client_state": ScriptSpec(
        name="probe_client_state",
        path="scripts/probe_client_state.py",
        description="client state probe; dry-run skips process/window probing",
        default_args=("--no-process", "--out", "reports/dashboard_runs/probe_client_state_dryrun.json"),
        live_args=("--out", "reports/dashboard_runs/probe_client_state_live.json"),
        options=(
            OptionSpec("no_process", "--no-process", "bool", True, "skip process/window probe"),
            OptionSpec("coordinate_text", "--coordinate-text", "str", None, "manual/OCR coordinate text"),
            OptionSpec("screenshot", "--screenshot", "str", None, "optional screenshot path"),
        ),
    ),
}


@dataclass
class RunRecord:
    run_id: str
    script: str
    mode: str
    command: list[str]
    pid: int
    log_path: str
    started_at: str
    process: subprocess.Popen = field(repr=False)
    stopped_at: str | None = None
    stop_requested: bool = False
    stop_file: str | None = None
    archived_at: str | None = None

    def status(self) -> dict:
        code = self.process.poll()
        return {
            "run_id": self.run_id,
            "script": self.script,
            "mode": self.mode,
            "pid": self.pid,
            "command": self.command,
            "started_at": self.started_at,
            "stopped_at": self.stopped_at,
            "running": code is None,
            "exit_code": code,
            "log_path": self.log_path,
            "stop_requested": self.stop_requested,
            "stop_file": self.stop_file,
            "archived": self.archived_at is not None,
            "archived_at": self.archived_at,
        }


class ProcessRegistry:
    """Tracks only dashboard-started children.

    stop_all never scans global python processes. it only touches Popen handles
    created by this registry. that is the safety rail.
    """

    def __init__(self, project_root: str | Path, reports_dir: str | Path = "reports/dashboard_runs"):
        self.project_root = Path(project_root)
        self.reports_dir = self.project_root / reports_dir
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._runs: dict[str, RunRecord] = {}
        self._lock = RLock()

    def list_scripts(self) -> list[dict]:
        return [spec.to_dict() for spec in DEFAULT_SCRIPTS.values()]

    def build_option_args(self, spec: ScriptSpec, options: dict[str, Any] | None) -> list[str]:
        if not options:
            return []
        by_name = {opt.name: opt for opt in spec.options}
        args: list[str] = []
        for name, value in options.items():
            if value is None or value == "":
                continue
            if name not in by_name:
                raise ValueError(f"unknown option for {spec.name}: {name}")
            opt = by_name[name]
            if opt.type == "bool":
                if opt.coerce(value) == "true":
                    args.append(opt.flag)
                continue
            args.extend([opt.flag, opt.coerce(value)])
        return args

    def start(self, script_name: str, *, live: bool = False, confirm_live: bool = False, extra_args: Iterable[str] = (), options: dict[str, Any] | None = None) -> dict:
        if script_name not in DEFAULT_SCRIPTS:
            raise ValueError(f"unknown script: {script_name}")
        spec = DEFAULT_SCRIPTS[script_name]
        if spec.force_dry_run:
            live = False
            confirm_live = False
        mode = "live" if live else "dry-run"
        if live and not confirm_live:
            raise PermissionError("live start requires confirm_live=true")

        script_path = self.project_root / spec.path
        if not script_path.exists():
            raise FileNotFoundError(f"script not found: {script_path}")

        with self._lock:
            if live and spec.exclusive_live_group:
                for rec in self._runs.values():
                    other = DEFAULT_SCRIPTS.get(rec.script)
                    if other and other.exclusive_live_group == spec.exclusive_live_group and rec.mode == "live" and rec.process.poll() is None:
                        raise RuntimeError(f"duplicate live {spec.exclusive_live_group} run blocked: {rec.run_id}")

            run_id = f"run-{uuid.uuid4().hex[:12]}"
            log_path = self.reports_dir / f"{run_id}-{script_name}.log"
            stop_file = self.reports_dir / f"{run_id}.stop"
            try:
                stop_file.unlink()
            except FileNotFoundError:
                pass
            option_args = self.build_option_args(spec, options)
            if not live and spec.name == "learn_runaround_client_tsv":
                # Dry-run calibration must never be turned into real movement by UI defaults or stale form values.
                option_args = []
            args = list(spec.live_args if live else spec.default_args) + option_args + list(extra_args)
            cmd = [sys.executable, str(script_path), *args]
            log_file = log_path.open("ab", buffering=0)
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            pythonpath_parts = [str(self.project_root / "src"), str(self.project_root)]
            existing_pythonpath = env.get("PYTHONPATH")
            if existing_pythonpath:
                pythonpath_parts.append(existing_pythonpath)
            env["PYTHONPATH"] = os.pathsep.join(pythonpath_parts)
            env["HERMES_RUN_ID"] = run_id
            env["HERMES_STOP_FILE"] = str(stop_file)
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
            rec = RunRecord(run_id, script_name, mode, cmd, proc.pid, str(log_path), utc_now(), proc, stop_file=str(stop_file))
            self._runs[run_id] = rec
            return rec.status()

    def status(self, run_id: str | None = None, *, tail_bytes: int = 4096, include_archived: bool = False) -> list[dict] | dict:
        with self._lock:
            if run_id:
                rec = self._runs[run_id]
                data = rec.status()
                data["log_tail"] = self.tail(rec.log_path, tail_bytes)
                return data
            return [
                self.status(rid, tail_bytes=tail_bytes, include_archived=True)
                for rid, rec in list(self._runs.items())
                if include_archived or rec.archived_at is None
            ]

    def archive(self, run_id: str) -> dict:
        with self._lock:
            rec = self._runs[run_id]
            if rec.process.poll() is None:
                raise RuntimeError("cannot archive running run; stop it first")
            if rec.archived_at is None:
                rec.archived_at = utc_now()
        return self.status(run_id)

    def stop(self, run_id: str, *, timeout: float = 3.0) -> dict:
        with self._lock:
            rec = self._runs[run_id]
            rec.stop_requested = True
        self.release_stuck_keys()
        if rec.stop_file:
            Path(rec.stop_file).parent.mkdir(parents=True, exist_ok=True)
            Path(rec.stop_file).touch()
        proc = rec.process
        if proc.poll() is None:
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=timeout)
        rec.stopped_at = utc_now()
        return self.status(run_id)

    def stop_all(self, *, timeout: float = 3.0) -> list[dict]:
        with self._lock:
            ids = list(self._runs)
        return [self.stop(rid, timeout=timeout) for rid in ids]

    def release_stuck_keys(self) -> None:
        # defensive only. no click or attack action is sent.
        try:
            subprocess.run([sys.executable, "-c", SAFE_KEY_RELEASE_CODE], timeout=1)
        except Exception:
            pass

    @staticmethod
    def tail(path: str | Path, max_bytes: int = 4096) -> str:
        p = Path(path)
        if not p.exists():
            return ""
        with p.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes), os.SEEK_SET)
            return fh.read().decode("utf-8", errors="replace")
