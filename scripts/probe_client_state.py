from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
for _path in (PROJECT_ROOT, SRC_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from metin2_research.client_state.process_probe import ProcessWindowProbe
from metin2_research.client_state.schema import ClientState
from metin2_research.client_state.screenshot_source import ScreenshotStateSource
from metin2_research.client_state.sources import merge_client_states
from metin2_research.client_state.json_state import DEFAULT_JSON_PATH, JsonClientStateSource
from metin2_research.client_state.tsv_state import DEFAULT_TSV_PATH, TsvClientStateSource


DEFAULT_OUT = Path("reports/client_state_probe/latest_client_state.json")


def build_probe_state(
    *,
    screenshot: str | Path | None = None,
    coordinate_text: str | None = None,
    process_state: ClientState | None = None,
    include_process_probe: bool = True,
    tsv_state_path: str | Path | None = DEFAULT_TSV_PATH,
    json_state_path: str | Path | None = DEFAULT_JSON_PATH,
) -> ClientState:
    """Build merged ClientState, preferring client Python JSON over TSV/OCR fallbacks."""

    states: list[ClientState] = []
    if process_state is not None:
        states.append(process_state)
    elif include_process_probe:
        states.append(ProcessWindowProbe().read())

    if screenshot is not None:
        states.append(ScreenshotStateSource(screenshot, coordinate_text=coordinate_text).read())

    if json_state_path is not None:
        json_state = JsonClientStateSource(json_state_path).read()
        if json_state.game is not None:
            states.append(json_state)
        elif tsv_state_path is not None:
            states.append(TsvClientStateSource(tsv_state_path).read())
    elif tsv_state_path is not None:
        states.append(TsvClientStateSource(tsv_state_path).read())

    if not states:
        return ClientState(warnings=["No ClientState sources were requested."], sources={"probe_client_state": "empty"})
    return merge_client_states(*states)


def write_probe_state(state: ClientState, out: str | Path = DEFAULT_OUT) -> Path:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe normalized Metin2 ClientState from safe local client-side sources.")
    parser.add_argument("--screenshot", help="Optional screenshot path to fold into ClientState")
    parser.add_argument("--coordinate-text", help="Optional manually/OCR-provided coordinate text, e.g. 'Yoshypt (607, 1025)'")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="Output JSON artifact path")
    parser.add_argument("--no-process", action="store_true", help="Skip read-only process/window probe")
    parser.add_argument("--json-state", default=str(DEFAULT_JSON_PATH), help="Client Python hermes_state.json path; use '' to disable")
    parser.add_argument("--tsv-state", default=str(DEFAULT_TSV_PATH), help="Client Python hermes_state.tsv fallback path; use '' to disable")
    args = parser.parse_args(argv)

    state = build_probe_state(
        screenshot=args.screenshot,
        coordinate_text=args.coordinate_text,
        include_process_probe=not args.no_process,
        json_state_path=args.json_state or None,
        tsv_state_path=args.tsv_state or None,
    )
    path = write_probe_state(state, args.out)
    print(json.dumps({"out": str(path), "state": state.to_dict()}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
