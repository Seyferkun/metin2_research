# Metin2 client-side benchmark scope

This project is a private, controlled Metin2 benchmark/training sandbox for Yoshy's local client. The updated rule is:

- All client-side techniques are fair game when used only in Yoshy's controlled benchmark environment.
- Server-side techniques remain forbidden.

## Allowed client-side techniques

Allowed means local-only observation, instrumentation, or control of Yoshy's own running client/lab files. Examples:

1. Screenshots, OCR, crops, image classification, object detection, and UI parsing.
2. Normal OS input: keyboard, mouse, window focus, and other ordinary local desktop automation.
3. Local harness files, configs, reports, screenshots, model weights, client logs, and non-secret local telemetry.
4. Process and window metadata: PID, executable name/path, window title, foreground/focus state, window rectangle, and timing.
5. Read-only local client process memory research in the controlled benchmark, if later approved and implemented with strict safeguards.
6. Local client-side hooks, instrumentation, or injection research in the controlled benchmark, if later approved and implemented with strict safeguards.

## Forbidden server-side techniques

The following remain out of scope even if they might be technically possible:

1. Server database, API, filesystem, admin panel, or server process access.
2. Packet injection, packet replay, MITM, forged network messages, or protocol automation against the server.
3. Direct server-side state mutation, item/currency/stat mutation, spawn manipulation, or privileged game-state reads.
4. Credential, token, cookie, session, private-key, or password extraction.
5. Public-server unattended gameplay, anti-cheat bypass, or evasion-oriented tooling.

## Memory reading boundary

Memory reading is feasible as a client-side signal class, but it is not implemented here yet. A safe implementation must be read-only, target only the local client process, and avoid credentials/session tokens/network buffers. It should start with non-invasive discovery notes and reproducible validation against screenshot/OCR truth before using any memory value for gameplay decisions.

Candidate read-only fields, if later approved:

- local player coordinates that match visible minimap/coordinate text;
- local player HP/MP that match visible UI bars/text;
- selected target name/type/HP that match visible target UI;
- local animation/combat state that only describes the local client view.

Explicitly excluded fields:

- credentials, session material, tokens, account identifiers beyond visible character name;
- network packet buffers or protocol state used for packet injection/replay;
- server-only entity lists that are not represented in the client view;
- any write path to client memory or server state.

## Injection/instrumentation boundary

Injection or hooks may be useful only if screenshots/OCR/process metadata/memory-read adapters cannot provide stable client state. It is not needed as the first adapter. If it is ever used, it must be local lab instrumentation only, with no packet forgery, no credential access, no server-state mutation, and no anti-cheat bypass intent.

## Current recommended adapter order

1. Process/window metadata probe for PID/title/focus/window rectangle.
2. Screenshot/CV/OCR adapter for visible state, target, HP, coordinates, and overlays.
3. Local logs/config adapter for stable harness/client context.
4. Read-only memory feasibility prototype only after visible-state validation defines exact fields.
5. Injection/instrumentation only if the above cannot meet benchmark reliability.

## Current implementation status

Implemented now:

- `src/metin2_research/client_state/schema.py`
- `src/metin2_research/client_state/sources.py`
- `src/metin2_research/client_state/process_probe.py`

Not implemented now:

- memory reading;
- injection/hooking;
- packet tooling;
- server-side access.
