# Client-side signal inventory

This inventory lists local client-side signals that can feed a normalized `ClientState` without touching server-side infrastructure.

## Normalized ClientState target

The adapter layer should converge on:

- process metadata: PID, process name, executable path;
- window metadata: title, HWND, focus state, rectangle;
- screenshot metadata: path, size, capture timestamp;
- OCR/UI metadata: player coordinate text, HP, selected target, overlay/menu/death state;
- optional design-only memory feasibility notes;
- source provenance and warnings.

See:

- `src/metin2_research/client_state/schema.py`
- `src/metin2_research/client_state/sources.py`
- `src/metin2_research/client_state/process_probe.py`

## Available signals

### Process/PID/window title/focus

Status: implemented as a read-only OS metadata probe.

Available now:

- visible window title keyword matching (`metin`, `mt2`, `portugalia`, `pgclient`);
- HWND;
- foreground/focus flag;
- window rectangle;
- PID;
- executable path and process name when Windows permits `QueryFullProcessImageNameW`.

Reliability:

- high for focus/title/rectangle if the client window is visible;
- medium for executable path because OS permissions may block process query;
- no game-state semantics beyond identifying the local client window.

### Logs/configs/harness files

Status: available as local files, not yet wrapped as a `ClientStateSource`.

Useful files and directories:

- `configs/local_vision_taxonomy.json` for CV taxonomy;
- `reports/PROJECT_PROGRESS_AUDIT.md` for project status;
- `reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt` for best detector;
- recent live drill folders under `reports/player_drill_*`;
- live harness modules under `src/metin2_research/`.

Reliability:

- high for static configuration and known model paths;
- medium for runtime truth because logs can be stale unless tied to a capture timestamp.

### Screenshot/OCR/crops

Status: screenshot and CV are active; OCR is designed but dependency availability varies.

Available now:

- window/screen capture pipeline;
- YOLO Metin-stone detection;
- minimap and UI crop utilities;
- local state JSON and preview artifacts from live runs;
- CV filtering for UI/minimap/overlay false positives.

Reliability:

- strong for visible Metin candidates when the detector confidence is high and UI filters pass;
- weaker for target name, HP text, and coordinates until OCR is installed and calibrated;
- screenshots remain the safest primary source because they mirror exactly what the player can see.

### Visible target/HP/coordinate text

Status: partially available from visible UI/crops; not yet reliably machine-read everywhere.

Reliable without memory/server access:

- target candidate boxes from CV;
- image dimensions and crop coordinates;
- manually provided or OCR-parsed coordinate text when available;
- visual overlay/menu detection in limited cases.

Needs more work:

- selected target name confirmation (`Metin` text);
- target HP bar and player HP bar parsing;
- coordinate OCR under different scaling/UI skins;
- robust death/stuck/combat state classification.

### Memory-reading feasibility

Status: feasible in principle, design-only now.

Potential read-only client-side fields:

- player coordinates;
- player HP/MP;
- selected target name/type/HP;
- local combat/animation state;
- local camera/player pose if exposed client-side.

Feasibility notes:

- memory reading is client-side if it only opens Yoshy's local client process in the benchmark lab;
- exact fields must be validated against screenshots/OCR and manual truth;
- no value should be trusted until cross-checked with visible state;
- implementation should avoid process writes, DLL injection, credentials/session tokens, packet buffers, and server-only data.

### Injection/instrumentation feasibility

Status: possible but not recommended as the first adapter.

Potential value:

- stable local UI/event state if screenshots/OCR and read-only memory are insufficient;
- controlled benchmark instrumentation for repeatable experiments.

Risks and boundaries:

- must not become packet tooling or anti-cheat bypass tooling;
- must not extract credentials/session material;
- must not write server-affecting state;
- should be postponed until simpler adapters fail with documented evidence.

## What can be read reliably without server-side access

Already reliable or near-reliable:

- active Metin2 window title/focus/rectangle/PID;
- screenshot size/path/timestamp;
- detector candidates and confidence;
- UI/minimap crop coordinates;
- local report/config/model paths and hashes.

Conditionally reliable after calibration:

- visible coordinates via OCR;
- HP from visible UI;
- selected target text;
- target HP from visible UI;
- stuck/death/menu state.

Design-only / future:

- read-only process memory fields validated against visible truth;
- local instrumentation if simpler adapters are insufficient.

Forbidden:

- server DB/API/files;
- packet injection/replay/MITM/forgery;
- credential/session extraction;
- direct server-side mutation.
