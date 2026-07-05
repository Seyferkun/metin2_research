from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class ProcessInfo:
    """Read-only OS process metadata for the local Metin2 client."""

    pid: int | None = None
    name: str | None = None
    executable_path: str | None = None
    command_line: str | None = None


@dataclass
class WindowInfo:
    """Read-only window metadata for the local Metin2 client."""

    hwnd: int | None = None
    title: str | None = None
    focused: bool | None = None
    rect: list[int] | None = None


@dataclass
class ScreenshotInfo:
    path: str | None = None
    width: int | None = None
    height: int | None = None
    captured_at: str | None = None


@dataclass
class OCRInfo:
    player_name: str | None = None
    player_coord: list[int] | None = None
    target_name: str | None = None
    hp_percent: float | None = None
    confidence: float | None = None


@dataclass
class VisualInfo:
    """Visible screenshot/CV-derived state only."""

    target_visible: bool | None = None
    target_confirmed: bool | None = None
    target_confidence: float | None = None
    target_xy: list[float] | None = None
    target_box: dict[str, Any] | None = None
    recommended_action: dict[str, Any] | None = None
    detections: list[dict[str, Any]] = field(default_factory=list)
    regions: dict[str, Any] = field(default_factory=dict)


@dataclass
class GameInfo:
    """Read-only in-game state exported by the local client Python TSV logger."""

    map_name: str | None = None
    player_coord: list[int] | None = None
    z: int | None = None
    hp: int | None = None
    max_hp: int | None = None
    sp: int | None = None
    max_sp: int | None = None
    target_vid: int | None = None
    player_name: str | None = None
    target_name: str | None = None
    target_alive: bool | None = None
    target_type: int | None = None
    target_hp: int | None = None
    target_max_hp: int | None = None
    target_hp_percent: float | None = None
    target_pixel_position: list[float] | None = None
    target_project_position: list[float] | None = None
    target_race_num: int | None = None
    target_liveness_source: str | None = None
    buff_active: bool | None = None
    buff_remaining_seconds: float | None = None
    nearby_entities: list[dict[str, Any]] = field(default_factory=list)
    named_metin_probe: list[dict[str, Any]] = field(default_factory=list)
    buffs: list[dict[str, Any]] = field(default_factory=list)
    skills: list[dict[str, Any]] = field(default_factory=list)
    quickslots: list[dict[str, Any]] = field(default_factory=list)
    player_flags: dict[str, Any] = field(default_factory=dict)
    api_probe: dict[str, Any] = field(default_factory=dict)
    client_timestamp_ms: int | None = None


@dataclass
class MemoryFeasibilityInfo:
    """Design-only notes for possible client-memory adapters.

    This schema records feasibility and boundaries. It is intentionally not a
    memory reader and does not include addresses, signatures, packet fields, or
    credential/session material.
    """

    status: str = "not_implemented_design_only"
    allowed_boundary: str = "local client process only in Yoshy's controlled benchmark"
    forbidden_boundary: str = "no server DB/API/files, packet injection/replay/MITM, credentials, or server-state mutation"
    candidate_fields: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)


@dataclass
class ClientState:
    """Normalized client-side state from safe local adapters."""

    schema_version: int = 1
    observed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    process: ProcessInfo | None = None
    window: WindowInfo | None = None
    screenshot: ScreenshotInfo | None = None
    game: GameInfo | None = None
    ocr: OCRInfo | None = None
    visual: VisualInfo | None = None
    memory_feasibility: MemoryFeasibilityInfo | None = None
    sources: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return {key: value for key, value in data.items() if value not in (None, {}, [])}
