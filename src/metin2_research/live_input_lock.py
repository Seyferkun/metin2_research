from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def read_live_input_lock(path: Path) -> dict[str, Any] | None:
    path = Path(path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"owner": "unknown", "action": "unknown", "run_id": "unknown", "expires_at": 0.0, "corrupt": True}
    return data if isinstance(data, dict) else None


def _lock_is_active(data: dict[str, Any] | None, *, now: float | None = None) -> bool:
    if not data:
        return False
    now = time.time() if now is None else now
    try:
        return float(data.get("expires_at", 0.0)) > now
    except (TypeError, ValueError):
        return False


def _write_lock_exclusive(path: Path, payload: dict[str, Any]) -> bool:
    data = json.dumps(payload, sort_keys=True).encode("utf-8")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        return False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
    except Exception:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise
    return True


def _read_lock_file_direct(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {"owner": "unknown", "action": "unknown", "run_id": "unknown", "expires_at": 0.0, "corrupt": True}
    return data if isinstance(data, dict) else None


def _remove_lock_if_matches(path: Path, expected: dict[str, Any]) -> bool:
    current = _read_lock_file_direct(path)
    if current != expected:
        return False
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


@dataclass
class LiveInputLease:
    path: Path
    owner: str
    run_id: str | None
    action: str
    ttl_seconds: float = 30.0
    timeout_seconds: float = 10.0
    poll_seconds: float = 0.1
    acquired: bool = False

    def __enter__(self) -> "LiveInputLease":
        deadline = time.time() + max(0.0, float(self.timeout_seconds))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            now = time.time()
            current = read_live_input_lock(self.path)
            if not _lock_is_active(current, now=now):
                if current is not None:
                    _remove_lock_if_matches(self.path, current)
                payload = {
                    "owner": self.owner,
                    "run_id": self.run_id,
                    "action": self.action,
                    "pid": os.getpid(),
                    "acquired_at": now,
                    "expires_at": now + max(0.1, float(self.ttl_seconds)),
                }
                if _write_lock_exclusive(self.path, payload):
                    self.acquired = True
                    return self
                current = read_live_input_lock(self.path)
            if now >= deadline:
                owner = current.get("owner") if isinstance(current, dict) else "unknown"
                action = current.get("action") if isinstance(current, dict) else "unknown"
                raise TimeoutError(f"live input lock held by {owner} action={action}")
            time.sleep(max(0.01, min(float(self.poll_seconds), deadline - now)))

    def refresh(self) -> None:
        if not self.acquired:
            return
        data = read_live_input_lock(self.path) or {}
        if data.get("owner") == self.owner and data.get("run_id") == self.run_id and data.get("action") == self.action:
            data["expires_at"] = time.time() + max(0.1, float(self.ttl_seconds))
            tmp = self.path.with_name(self.path.name + f".{os.getpid()}.tmp")
            tmp.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.acquired:
            return
        data = read_live_input_lock(self.path)
        if data and data.get("owner") == self.owner and data.get("run_id") == self.run_id and data.get("action") == self.action:
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self.acquired = False


def acquire_live_input_lease(
    path: Path,
    *,
    owner: str,
    run_id: str | None,
    action: str,
    ttl_seconds: float = 30.0,
    timeout_seconds: float = 10.0,
    poll_seconds: float = 0.1,
) -> LiveInputLease:
    return LiveInputLease(
        path=Path(path),
        owner=str(owner),
        run_id=str(run_id) if run_id is not None else None,
        action=str(action),
        ttl_seconds=ttl_seconds,
        timeout_seconds=timeout_seconds,
        poll_seconds=poll_seconds,
    )
