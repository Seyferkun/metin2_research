from __future__ import annotations

"""Run YOLOv5 train.py with explicit trusted checkpoint loading.

PyTorch 2.6 defaults torch.load(weights_only=True), which breaks old YOLOv5
checkpoints. This wrapper is only for local sandbox checkpoints the user trusts.
"""

import runpy
import sys
from pathlib import Path

import numpy as np
import torch

if not hasattr(np, "trapz") and hasattr(np, "trapezoid"):
    np.trapz = np.trapezoid  # type: ignore[attr-defined]

_original_load = torch.load


def _trusted_load(*args, **kwargs):
    kwargs.setdefault("weights_only", False)
    return _original_load(*args, **kwargs)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: yolov5_train_trusted.py /path/to/yolov5/train.py [train.py args...]")
    train_py = Path(sys.argv[1]).resolve()
    if not train_py.exists():
        raise SystemExit(f"train.py not found: {train_py}")
    repo_root = train_py.parent.parent
    sys.path.insert(0, str(repo_root))
    torch.load = _trusted_load  # type: ignore[assignment]
    sys.argv = [str(train_py), *sys.argv[2:]]
    runpy.run_path(str(train_py), run_name="__main__")


if __name__ == "__main__":
    main()
