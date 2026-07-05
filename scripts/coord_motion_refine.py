#!/usr/bin/env python
"""Motion-differential coordinate source finder for pgclient.app.

This is read-only memory probing plus normal keyboard input done elsewhere.
It snapshots compact numeric samples from readable private/mapped memory and
compares snapshots after controlled movement. The goal is to find same-address
numeric fields that change with movement and reverse when the opposite movement
key is pressed.

Typical workflow:
  python scripts/coord_motion_refine.py snapshot --label p0 --out reports/coord_p0.json
  python scripts/send_game_key.py s --seconds 2
  python scripts/coord_motion_refine.py snapshot --label s1 --out reports/coord_s1.json
  python scripts/send_game_key.py w --seconds 2
  python scripts/coord_motion_refine.py snapshot --label w1 --out reports/coord_w1.json
  python scripts/coord_motion_refine.py score --p0 reports/coord_p0.json --p1 reports/coord_s1.json --p2 reports/coord_w1.json --out reports/coord_motion_score.json
"""

from __future__ import annotations

import argparse
import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from refine_value import close_handle, enumerate_regions, find_pid, open_process, read_memory


@dataclass(frozen=True)
class Sample:
    address: int
    typ: str
    value: float
    region_type: str | None


TYPES = ("i16", "u16", "i32", "u32", "f32")


def decode_at(raw: bytes, off: int, typ: str) -> float | None:
    try:
        if typ == "i16":
            return float(struct.unpack_from("<h", raw, off)[0])
        if typ == "u16":
            return float(struct.unpack_from("<H", raw, off)[0])
        if typ == "i32":
            return float(struct.unpack_from("<i", raw, off)[0])
        if typ == "u32":
            return float(struct.unpack_from("<I", raw, off)[0])
        if typ == "f32":
            v = struct.unpack_from("<f", raw, off)[0]
            if not math.isfinite(v):
                return None
            return float(v)
    except struct.error:
        return None
    return None


def plausible_value(v: float, typ: str, *, center_x: float, center_y: float, radius: float) -> bool:
    # Keep the sample set compact by focusing near likely coordinate scales.
    if typ == "f32":
        if abs(v) > 1_000_000:
            return False
    else:
        if abs(v) > 10_000_000:
            return False
    centers = [center_x, center_y]
    scales = [1, 10, 100, 1000]
    for c in centers:
        for s in scales:
            if abs(v - c * s) <= radius * s:
                return True
            if abs(v + c * s) <= radius * s:
                return True
    # Also include small local/world offsets that often pair with map coords after transform.
    return -5000 <= v <= 5000


def snapshot(process: str, label: str, out: Path, center_x: float, center_y: float, radius: float, stride: int, max_region_mb: int, max_samples: int) -> int:
    pid = find_pid(process)
    if pid is None:
        print(f"Process not found: {process}")
        return 1
    handle = open_process(pid)
    if not handle:
        print(f"OpenProcess failed for pid={pid}")
        return 1
    samples: list[Sample] = []
    regions_meta: list[dict[str, Any]] = []
    try:
        regions = enumerate_regions(handle, max_region_mb=max_region_mb)
        for region in regions:
            if region.get("type") == "image":
                continue
            base = int(region["base"])
            size = min(int(region["size"]), max_region_mb * 1024 * 1024)
            regions_meta.append({"base": base, "size": size, "type": region.get("type")})
            # Read in chunks; sample every stride bytes. Include unaligned because UI/game structs can be packed.
            chunk_size = 1024 * 1024
            for chunk_off in range(0, size, chunk_size):
                raw = read_memory(handle, base + chunk_off, min(chunk_size, size - chunk_off))
                if not raw:
                    continue
                limit = max(0, len(raw) - 4)
                for off in range(0, limit, stride):
                    addr = base + chunk_off + off
                    for typ in TYPES:
                        v = decode_at(raw, off, typ)
                        if v is None:
                            continue
                        if plausible_value(v, typ, center_x=center_x, center_y=center_y, radius=radius):
                            samples.append(Sample(addr, typ, v, region.get("type")))
                            if len(samples) >= max_samples:
                                break
                    if len(samples) >= max_samples:
                        break
                if len(samples) >= max_samples:
                    break
            if len(samples) >= max_samples:
                break
    finally:
        close_handle(handle)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process": process,
        "pid": pid,
        "label": label,
        "center_x": center_x,
        "center_y": center_y,
        "radius": radius,
        "stride": stride,
        "samples": [asdict(s) for s in samples],
        "regions": regions_meta,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"snapshot label={label} pid={pid} samples={len(samples)} saved {out}")
    return 0 if samples else 2


def load_samples(path: Path) -> dict[tuple[int, str], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {(int(s["address"]), s["typ"]): s for s in data.get("samples", [])}


def score(p0: Path, p1: Path, p2: Path | None, out: Path, show: int, min_delta: float, max_delta: float) -> int:
    a = load_samples(p0)
    b = load_samples(p1)
    c = load_samples(p2) if p2 else {}
    rows: list[dict[str, Any]] = []
    for key, s0 in a.items():
        if key not in b:
            continue
        v0 = float(s0["value"])
        v1 = float(b[key]["value"])
        d1 = v1 - v0
        if not (min_delta <= abs(d1) <= max_delta):
            continue
        row: dict[str, Any] = {
            "address": key[0],
            "typ": key[1],
            "region_type": s0.get("region_type"),
            "p0": v0,
            "p1": v1,
            "delta1": d1,
            "score": 1.0,
        }
        if c and key in c:
            v2 = float(c[key]["value"])
            d2 = v2 - v1
            return_error = abs(v2 - v0)
            row.update({"p2": v2, "delta2": d2, "return_error": return_error})
            # Strong candidate: second movement reverses the first and returns close to baseline.
            if d1 * d2 < 0:
                row["score"] += 5.0
            row["score"] += max(0.0, 5.0 - min(return_error, 5.0))
            if abs(d2) >= min_delta:
                row["score"] += 1.0
        # Prefer private memory and coordinate-sized values.
        if s0.get("region_type") == "private":
            row["score"] += 1.0
        if key[1] == "f32":
            row["score"] += 0.5
        rows.append(row)
    rows.sort(key=lambda r: (-float(r["score"]), float(r.get("return_error", 999999)), abs(float(r["delta1"])), int(r["address"])))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"p0": str(p0), "p1": str(p1), "p2": str(p2) if p2 else None, "rows": rows}, indent=2), encoding="utf-8")
    print(f"scored candidates={len(rows)} saved {out}")
    for r in rows[:show]:
        p2s = "" if "p2" not in r else f" p2={r['p2']:.3f} d2={r['delta2']:.3f} ret={r['return_error']:.3f}"
        print(f"score={r['score']:.2f} 0x{r['address']:08x}/{r['typ']} [{r['region_type']}] p0={r['p0']:.3f} p1={r['p1']:.3f} d1={r['delta1']:.3f}{p2s}")
    return 0 if rows else 2


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("snapshot")
    sp.add_argument("--process", default="pgclient.app")
    sp.add_argument("--label", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--center-x", type=float, default=830)
    sp.add_argument("--center-y", type=float, default=485)
    sp.add_argument("--radius", type=float, default=250)
    sp.add_argument("--stride", type=int, default=2)
    sp.add_argument("--max-region-mb", type=int, default=64)
    sp.add_argument("--max-samples", type=int, default=250000)

    cp = sub.add_parser("score")
    cp.add_argument("--p0", required=True)
    cp.add_argument("--p1", required=True)
    cp.add_argument("--p2")
    cp.add_argument("--out", required=True)
    cp.add_argument("--show", type=int, default=80)
    cp.add_argument("--min-delta", type=float, default=0.01)
    cp.add_argument("--max-delta", type=float, default=5000)
    args = ap.parse_args()
    if args.cmd == "snapshot":
        return snapshot(args.process, args.label, Path(args.out), args.center_x, args.center_y, args.radius, args.stride, args.max_region_mb, args.max_samples)
    return score(Path(args.p0), Path(args.p1), Path(args.p2) if args.p2 else None, Path(args.out), args.show, args.min_delta, args.max_delta)


if __name__ == "__main__":
    raise SystemExit(main())
