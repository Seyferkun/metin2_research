import json
import time
from pathlib import Path

from metin2_research.live_input_lock import LiveInputLease, acquire_live_input_lease, read_live_input_lock


def test_live_input_lease_blocks_other_owner_until_released(tmp_path):
    lock_path = tmp_path / "live_input.lock"

    with acquire_live_input_lease(lock_path, owner="sweep", run_id="run-a", action="channel_switch", ttl_seconds=30, timeout_seconds=0) as lease:
        assert isinstance(lease, LiveInputLease)
        data = read_live_input_lock(lock_path)
        assert data["owner"] == "sweep"
        assert data["action"] == "channel_switch"

        try:
            acquire_live_input_lease(lock_path, owner="buff", run_id="run-b", action="f1", ttl_seconds=30, timeout_seconds=0).__enter__()
        except TimeoutError as exc:
            assert "held by sweep" in str(exc)
        else:
            raise AssertionError("buff lease should not acquire while sweep holds lock")

    assert read_live_input_lock(lock_path) is None


def test_live_input_lease_allows_takeover_of_expired_lock(tmp_path):
    lock_path = tmp_path / "live_input.lock"
    lock_path.write_text(
        json.dumps({"owner": "old", "run_id": "stale", "action": "channel_switch", "expires_at": time.time() - 10}),
        encoding="utf-8",
    )

    with acquire_live_input_lease(lock_path, owner="buff", run_id="run-b", action="f1", ttl_seconds=30, timeout_seconds=0):
        data = read_live_input_lock(lock_path)
        assert data["owner"] == "buff"
        assert data["run_id"] == "run-b"
