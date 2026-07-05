from __future__ import annotations

from dataclasses import replace
from typing import Protocol, runtime_checkable

from .schema import ClientState


@runtime_checkable
class ClientStateSource(Protocol):
    """Read-only source adapter that contributes normalized ClientState."""

    name: str

    def read(self) -> ClientState:
        """Return the latest state fragment without mutating the game/server."""
        ...


def _prefer_latest(old_value, new_value):
    return new_value if new_value is not None else old_value


def merge_client_states(*states: ClientState) -> ClientState:
    """Merge state fragments, keeping non-null values from later sources."""

    merged = ClientState()
    for state in states:
        if state is None:
            continue
        merged = replace(
            merged,
            process=_prefer_latest(merged.process, state.process),
            window=_prefer_latest(merged.window, state.window),
            screenshot=_prefer_latest(merged.screenshot, state.screenshot),
            game=_prefer_latest(merged.game, state.game),
            ocr=_prefer_latest(merged.ocr, state.ocr),
            visual=_prefer_latest(merged.visual, state.visual),
            memory_feasibility=_prefer_latest(merged.memory_feasibility, state.memory_feasibility),
            sources={**merged.sources, **state.sources},
            warnings=[*merged.warnings, *state.warnings],
        )
    return merged
