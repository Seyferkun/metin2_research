# metin2 local dashboard drop-in

copy `metin2_dashboard/` into the project root `C:/Hermes Unreal/metin2_research`, or keep this folder as a branch patch and run from the project root.

## start locally

```bash
python -m metin2_dashboard.server --host 127.0.0.1 --port 8767 --project-root . --tsv "D:/Games/MT2Portugalia/app/hermes_state.tsv"
```

For access from another device on the same trusted LAN, opt in explicitly:

```bash
python -m metin2_dashboard.server --host 0.0.0.0 --allow-lan --port 8767 --project-root . --tsv "D:/Games/MT2Portugalia/app/hermes_state.tsv"
```

open:

```text
http://127.0.0.1:8767/
```

## safety rails

- the server refuses non-local binds by default. use `--allow-lan` only on a trusted LAN when you need phone/other-device access.
- dry-run buttons are separate from live buttons.
- the UI exposes safe per-script tuning options instead of free-form shell arguments.
- live starts require `confirm_live=true` in the api; the html asks the operator to type `LIVE`.
- duplicate live combat runs are blocked while a prior live combat run is still running.
- stop all only stops subprocesses created by this dashboard registry. it does not scan or kill unrelated python processes.
- stop calls release movement keys defensively through key-up only when `pynput` is available. it does not click or attack.
- logs are written per run under `reports/dashboard_runs/`.

## native Tkinter control panel

The same API also powers the optional native Tkinter control panel:

```bash
python scripts/metin2_control_panel.py --base-url http://127.0.0.1:8767 --project-root .
```

The app keeps settings in native fields, refreshes state/runs without rebuilding the option form, and still uses the dashboard safety rails: dry-run/live separation, live confirmation, allowlisted options, and dashboard-managed stop only. It includes quick buttons for:

- `Open game + login` — starts the local API if needed, then runs `login_mt2_local` in confirmed live mode using the Windows Credential Manager credential, direct-launches the already-patched `app/pgclient.app` client without the launcher/patcher, and presses Começar/Enter.
- `Practice dry-run` — starts a short client-state combat decision dry-run without sending game input.
- `Start practicing LIVE` — after confirmation, starts the bounded private-sandbox combat practice loop.

## api

- `GET /api/state`
- `GET /api/scripts`
- `GET /api/runs`
- `POST /api/start` body: `{"script":"probe_client_state","live":false}`
- `POST /api/start` live body: `{"script":"combat_metin_client_state","live":true,"confirm_live":true}`
- `POST /api/stop` body: `{"run_id":"run-..."}`
- `POST /api/stop_all`

## tests

```bash
PYTHONPATH=. pytest -q
```
