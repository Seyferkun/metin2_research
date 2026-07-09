import ctypes

from metin2_research.win_input import KEYBDINPUT, MOUSEINPUT, SCANCODES, SmoothMover


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


def test_sendinput_extra_info_fields_are_ulong_ptr_not_pointers():
    # Windows SendInput rejects the old POINTER(c_ulong) struct layout with
    # WinError 0 / ERROR_INVALID_PARAMETER on this host. dwExtraInfo must be the
    # ULONG_PTR-sized integer field used by the Win32 INPUT ABI.
    key_fields = dict(KEYBDINPUT._fields_)
    mouse_fields = dict(MOUSEINPUT._fields_)
    assert not issubclass(key_fields["dwExtraInfo"], ctypes._Pointer)
    assert not issubclass(mouse_fields["dwExtraInfo"], ctypes._Pointer)
    assert ctypes.sizeof(key_fields["dwExtraInfo"]) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(mouse_fields["dwExtraInfo"]) == ctypes.sizeof(ctypes.c_void_p)


def test_quickslot_function_keys_include_f1_to_f4():
    assert SCANCODES["f1"] == 0x3B
    assert SCANCODES["f2"] == 0x3C
    assert SCANCODES["f3"] == 0x3D
    assert SCANCODES["f4"] == 0x3E


def test_metin_workflow_keys_include_pickup_and_channel_menu():
    assert SCANCODES["z"] == 0x2C
    assert SCANCODES["x"] == 0x2D
    assert SCANCODES["i"] == 0x17
    assert SCANCODES["c"] == 0x2E
    assert SCANCODES["escape"] == SCANCODES["esc"] == 0x01


def test_tap_key_accepts_f2_quickslot(monkeypatch):
    from metin2_research import win_input
    sent = []
    monkeypatch.setattr(win_input, "_send_input", lambda inp: sent.append(inp))
    monkeypatch.setattr(win_input.time, "sleep", lambda seconds: None)

    win_input.tap_key("f2", 0.01)

    assert len(sent) == 2


def test_click_at_moves_and_sends_left_click(monkeypatch):
    from metin2_research import win_input
    sent = []
    moved = []
    class FakeUser32:
        def SetCursorPos(self, x, y):
            moved.append((x, y))
            return 1
    monkeypatch.setattr(win_input.ctypes, "windll", type("Windll", (), {"user32": FakeUser32()})())
    monkeypatch.setattr(win_input, "_send_input", lambda inp: sent.append(inp))
    monkeypatch.setattr(win_input.time, "sleep", lambda seconds: None)

    win_input.click_at(510, 330)

    assert moved == [(510, 330)]
    assert len(sent) == 2
