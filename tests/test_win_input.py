from metin2_research.win_input import SmoothMover


def test_smooth_mover_holds_keys_continuously_when_direction_unchanged(monkeypatch):
    calls = []
    monkeypatch.setattr("metin2_research.win_input.key_down", lambda key: calls.append(("down", key)))
    monkeypatch.setattr("metin2_research.win_input.key_up", lambda key: calls.append(("up", key)))
    monkeypatch.setattr("metin2_research.win_input.time.sleep", lambda seconds: calls.append(("sleep", seconds)))

    mover = SmoothMover()
    mover.move({"w", "d"}, 0.35)
    mover.move({"w", "d"}, 0.35)

    assert sorted(calls[:2]) == [("down", "d"), ("down", "w")]
    assert calls[2:] == [("sleep", 0.35), ("sleep", 0.35)]
    assert mover.held == {"w", "d"}


def test_smooth_mover_releases_only_changed_keys_on_direction_change(monkeypatch):
    calls = []
    monkeypatch.setattr("metin2_research.win_input.key_down", lambda key: calls.append(("down", key)))
    monkeypatch.setattr("metin2_research.win_input.key_up", lambda key: calls.append(("up", key)))
    monkeypatch.setattr("metin2_research.win_input.time.sleep", lambda seconds: calls.append(("sleep", seconds)))

    mover = SmoothMover()
    mover.move({"w", "d"}, 0.35)
    calls.clear()
    mover.move({"w", "a"}, 0.2)

    assert ("up", "d") in calls
    assert ("down", "a") in calls
    assert ("up", "w") not in calls
    assert ("down", "w") not in calls
    assert calls[-1] == ("sleep", 0.2)
    assert mover.held == {"w", "a"}


def test_smooth_mover_release_all_clears_held_set(monkeypatch):
    calls = []
    monkeypatch.setattr("metin2_research.win_input.key_down", lambda key: calls.append(("down", key)))
    monkeypatch.setattr("metin2_research.win_input.key_up", lambda key: calls.append(("up", key)))
    monkeypatch.setattr("metin2_research.win_input.time.sleep", lambda seconds: None)

    mover = SmoothMover()
    mover.move({"s", "d"}, 0.1)
    calls.clear()
    mover.release_all()

    assert sorted(calls) == [("up", "d"), ("up", "s")]
    assert mover.held == set()
