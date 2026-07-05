from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

from metin2_research.local_vision import build_local_state

from .schema import ClientState, OCRInfo, ScreenshotInfo, VisualInfo


class ScreenshotStateSource:
    """Visible-state adapter from a screenshot plus optional OCR/detector data.

    This source intentionally emits only image, OCR, crop, and detector-derived
    facts. It does not read process memory, inspect packets, or infer hidden
    server state.
    """

    name = "screenshot_state"

    def __init__(
        self,
        image_path: str | Path,
        *,
        detector_state: dict[str, Any] | None = None,
        detections: list[dict[str, Any]] | None = None,
        coordinate_text: str | None = None,
    ):
        self.image_path = Path(image_path)
        self.detector_state = detector_state or {}
        self.detections = detections or []
        self.coordinate_text = coordinate_text

    def read(self) -> ClientState:
        return screenshot_to_client_state(
            self.image_path,
            detector_state=self.detector_state,
            detections=self.detections,
            coordinate_text=self.coordinate_text,
        )


def screenshot_to_client_state(
    image_path: str | Path,
    *,
    detector_state: dict[str, Any] | None = None,
    detections: list[dict[str, Any]] | None = None,
    coordinate_text: str | None = None,
) -> ClientState:
    image_path = Path(image_path)
    detector_state = detector_state or {}
    detections = detections or []
    image = Image.open(image_path).convert("RGB")
    local_state = build_local_state(image_path, coordinate_text=coordinate_text)

    parsed_coord = local_state.get("player_coord")
    ocr = OCRInfo(
        player_name=local_state.get("player_name"),
        player_coord=list(parsed_coord) if parsed_coord is not None else None,
        target_name=detector_state.get("target_name"),
        hp_percent=detector_state.get("hp_percent"),
        confidence=_ocr_confidence(local_state),
    )
    visual = VisualInfo(
        target_visible=detector_state.get("target_visible"),
        target_confirmed=detector_state.get("target_confirmed"),
        target_confidence=detector_state.get("target_confidence"),
        target_xy=detector_state.get("target_xy"),
        target_box=detector_state.get("target_box"),
        recommended_action=detector_state.get("recommended_action"),
        detections=detections,
        regions=local_state.get("regions", {}),
    )

    warnings: list[str] = []
    if ocr.player_coord is None:
        warnings.append("No player coordinate OCR/text was available; coordinate fields left null.")
    if not detector_state:
        warnings.append("No detector state provided; visual target fields left null.")

    return ClientState(
        screenshot=ScreenshotInfo(
            path=str(image_path),
            width=image.width,
            height=image.height,
        ),
        ocr=ocr,
        visual=visual,
        sources={"screenshot_state": "image_metadata_and_visible_state"},
        warnings=warnings,
    )


def _ocr_confidence(local_state: dict[str, Any]) -> float | None:
    if local_state.get("player_coord") is not None:
        return 1.0 if local_state.get("ocr", {}).get("coordinate_source") == "provided_text" else None
    return None
