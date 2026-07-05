"""Normalized client-side state adapters for the Metin2 research sandbox."""

from .schema import ClientState, ProcessInfo, WindowInfo, ScreenshotInfo, OCRInfo, VisualInfo, MemoryFeasibilityInfo
from .sources import ClientStateSource, merge_client_states

__all__ = [
    "ClientState",
    "ProcessInfo",
    "WindowInfo",
    "ScreenshotInfo",
    "OCRInfo",
    "VisualInfo",
    "MemoryFeasibilityInfo",
    "ClientStateSource",
    "merge_client_states",
]
