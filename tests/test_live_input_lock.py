import json
import time
import threading
from pathlib import Path

import metin2_research.live_input_lock as live_lock
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


def test_live_input_lease_acquire_is_atomic_when_readers_race(monkeypatch, tmp_path):
    lock_path = tmp_path / "live_input.lock"
    real_read = live_lock.read_live_input_lock
    barrier = threading.Barrier(2)
    first_reads = 0
    first_reads_lock = threading.Lock()

    def racing_read(path):
        nonlocal first_reads
        if Path(path) == lock_path:
            with first_reads_lock:
                first_reads += 1
                call_no = first_reads
            if call_no <= 2:
                barrier.wait(timeout=2)
                return None
        return real_read(path)

    monkeypatch.setattr(live_lock, "read_live_input_lock", racing_read)
    results = []

    def contender(owner):
        lease = acquire_live_input_lease(lock_path, owner=owner, run_id=owner, action="input", ttl_seconds=30, timeout_seconds=0)
        try:
            lease.__enter__()
        except TimeoutError:
            results.append((owner, "blocked"))
        else:
            results.append((owner, "acquired"))
            time.sleep(0.1)
            lease.__exit__(None, None, None)

    threads = [threading.Thread(target=contender, args=(f"owner-{idx}",)) for idx in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert sorted(status for _owner, status in results) == ["acquired", "blocked"]


def test_expired_lock_cleanup_does_not_delete_new_active_replacement(monkeypatch, tmp_path):
    lock_path = tmp_path / "live_input.lock"
    expired = {"owner": "old", "run_id": "stale", "action": "channel_switch", "expires_at": time.time() - 10}
    replacement = {"owner": "sweep", "run_id": "new", "action": "pickup_channel_transition", "expires_at": time.time() + 30}
    lock_path.write_text(json.dumps(replacement), encoding="utf-8")
    real_read = live_lock.read_live_input_lock
    reads = 0

    def stale_then_real(path):
        nonlocal reads
        if Path(path) == lock_path:
            reads += 1
            if reads == 1:
                return dict(expired)
        return real_read(path)

    monkeypatch.setattr(live_lock, "read_live_input_lock", stale_then_real)

    try:
        acquire_live_input_lease(lock_path, owner="buff", run_id="buff", action="f1", ttl_seconds=30, timeout_seconds=0).__enter__()
    except TimeoutError as exc:
        assert "held by sweep" in str(exc)
    else:
        raise AssertionError("stale read must not let buff delete and replace a fresh active lock")

    assert read_live_input_lock(lock_path)["owner"] == "sweep"
