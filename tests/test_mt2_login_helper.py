from scripts.login_mt2_local import INPUT, KEYBDINPUT, build_enter_game_sequence, build_login_sequence, build_terminate_command, choose_client_launch_executable, credential_target, is_expected_foreground_window, parse_pair, quote_windows_arg, should_self_elevate_for_login


def test_windows_sendinput_struct_has_expected_keyboard_layout():
    assert KEYBDINPUT.dwExtraInfo.size in (4, 8)
    assert INPUT.type.offset == 0
    assert INPUT.union.offset >= INPUT.type.size
    assert INPUT.union.size >= KEYBDINPUT.dwExtraInfo.offset + KEYBDINPUT.dwExtraInfo.size


def test_credential_target_includes_username_without_secret():
    assert credential_target("yoshy") == "MT2Portugalia:yoshy"


def test_build_login_sequence_uses_username_tab_password_enter():
    steps = build_login_sequence("yoshy", "secret")

    assert steps == [
        ("text", "yoshy"),
        ("key", "TAB"),
        ("text", "secret"),
        ("key", "ENTER"),
    ]


def test_build_enter_game_sequence_presses_enter():
    assert build_enter_game_sequence() == [("key", "ENTER")]


def test_build_login_sequence_can_append_enter_game():
    assert build_login_sequence("u", "p", enter_game=True)[-2:] == [("key", "ENTER"), ("key", "ENTER")]


def test_login_parser_accepts_enter_game_flag():
    from scripts.login_mt2_local import build_parser
    args = build_parser().parse_args(["login", "--enter-game"])
    assert args.enter_game is True


def test_login_parser_accepts_restart_flag_and_command_targets_pgclient():
    from scripts.login_mt2_local import build_parser
    args = build_parser().parse_args(["login", "--restart"])
    assert args.restart is True
    assert "pgclient.app" in build_terminate_command()


def test_credential_target_normalizes_username_whitespace():
    assert credential_target(" yoshy ") == "MT2Portugalia:yoshy"


def test_build_enter_game_sequence_supports_multiple_enters():
    assert build_enter_game_sequence(count=3) == [("key", "ENTER"), ("key", "ENTER"), ("key", "ENTER")]


def test_login_parser_accepts_enter_game_count_and_sequence_uses_it():
    from scripts.login_mt2_local import build_parser
    args = build_parser().parse_args(["login", "--enter-game", "--enter-game-count", "2"])
    assert args.enter_game_count == 2
    assert build_login_sequence("u", "p", enter_game=True, enter_game_count=2)[-3:] == [("key", "ENTER"), ("key", "ENTER"), ("key", "ENTER")]


def test_expected_foreground_window_requires_exact_hwnd_match():
    assert is_expected_foreground_window(123, 123) is True
    assert is_expected_foreground_window(123, 456) is False
    assert is_expected_foreground_window(0, 0) is False


def test_login_parser_accepts_click_field_coordinates():
    from scripts.login_mt2_local import build_parser
    args = build_parser().parse_args([
        "login",
        "--click-fields",
        "--username-pos", "520,371",
        "--password-pos", "520,434",
        "--login-pos", "654,675",
    ])
    assert args.click_fields is True
    assert args.username_pos == (520, 371)
    assert args.password_pos == (520, 434)
    assert args.login_pos == (654, 675)


def test_parse_pair_rejects_invalid_coordinate_pair():
    assert parse_pair("1,2") == (1, 2)
    try:
        parse_pair("1")
    except ValueError as exc:
        assert "x,y" in str(exc)
    else:
        raise AssertionError("parse_pair accepted invalid coordinate")


def test_choose_client_launch_executable_uses_direct_client_even_when_launcher_exists(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    launcher = tmp_path / "MT2Portugalia.exe"
    direct_client = app_dir / "pgclient.app"
    launcher.write_bytes(b"launcher")
    direct_client.write_bytes(b"client")

    assert choose_client_launch_executable(tmp_path, app_dir) == direct_client


def test_choose_client_launch_executable_returns_direct_client_path(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    direct_client = app_dir / "pgclient.app"
    direct_client.write_bytes(b"client")

    assert choose_client_launch_executable(tmp_path, app_dir) == direct_client


def test_login_parser_self_elevates_by_default_and_can_disable():
    from scripts.login_mt2_local import build_parser

    assert build_parser().parse_args(["login"]).self_elevate is True
    assert build_parser().parse_args(["login", "--no-self-elevate"]).self_elevate is False


def test_should_self_elevate_only_when_enabled_and_not_admin(monkeypatch):
    from scripts import login_mt2_local

    parser = login_mt2_local.build_parser()
    monkeypatch.setattr(login_mt2_local, "is_user_admin", lambda: False)
    assert should_self_elevate_for_login(parser.parse_args(["login"])) is True
    assert should_self_elevate_for_login(parser.parse_args(["login", "--no-self-elevate"])) is False
    monkeypatch.setattr(login_mt2_local, "is_user_admin", lambda: True)
    assert should_self_elevate_for_login(parser.parse_args(["login"])) is False


def test_quote_windows_arg_preserves_backslashes_and_quotes_spaces():
    assert quote_windows_arg(r"C:\Hermes Unreal\metin2_research\scripts\login_mt2_local.py") == r'"C:\Hermes Unreal\metin2_research\scripts\login_mt2_local.py"'
