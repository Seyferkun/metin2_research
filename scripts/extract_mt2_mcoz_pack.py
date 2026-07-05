#!/usr/bin/env python
"""Extract MT2Portugalia extensionless MCOZ pack chunks.

This is a read-only extractor for the local/private-server research client at
D:/Games/MT2Portugalia. It does not touch network traffic or the running process.

The client stores each file as:
  outer header: MCOZ + encrypted_size + compressed_size + raw_size
  payload: XTEA-encrypted bytes
  decrypted payload: MCOZ + LZO1X stream + padding

The XTEA data key was recovered from pgclient.app string constants and must be
provided in reversed 32-bit word order for the standard EterPack XTEA routine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
from pathlib import Path

import lzokay

# pgclient.app key strings near the EterPack code:
# 592018749, 5839472, 638120478, 38260275
# EterPack XTEA uses them in reverse order here.
DATA_KEY = [38260275, 638120478, 5839472, 592018749]


def xtea_decrypt_block(v0: int, v1: int, key: list[int], rounds: int = 32) -> tuple[int, int]:
    mask = 0xFFFFFFFF
    delta = 0x9E3779B9
    total = (delta * rounds) & mask
    for _ in range(rounds):
        v1 = (v1 - ((((v0 << 4) & mask ^ (v0 >> 5)) + v0) ^ (total + key[(total >> 11) & 3]))) & mask
        total = (total - delta) & mask
        v0 = (v0 - ((((v1 << 4) & mask ^ (v1 >> 5)) + v1) ^ (total + key[total & 3]))) & mask
    return v0, v1


def decrypt_payload(buf: bytes) -> bytes:
    out = bytearray()
    n = len(buf) // 8 * 8
    for j in range(0, n, 8):
        v0, v1 = struct.unpack("<II", buf[j : j + 8])
        a, b = xtea_decrypt_block(v0, v1, DATA_KEY)
        out += struct.pack("<II", a, b)
    out += buf[n:]
    return bytes(out)


def classify(data: bytes) -> str:
    head = data[:4096].lower()
    if data.startswith(b"\x03\xf3\r\n"):
        return "pyc"
    if b"import " in head or b"class " in head or b"def " in head or head.startswith(b"#"):
        return "py"
    if b"scripttype" in head or b"group " in head or b"filename" in head:
        return "txt"
    if b"title subimage" in head or b"version 2.0" in head:
        return "sub"
    if data.startswith(b"\x89PNG"):
        return "png"
    if data.startswith(b"DDS "):
        return "dds"
    return "bin"


def preview(data: bytes, limit: int = 500) -> str:
    text = data[:limit].decode("cp949", "replace").replace("\r", "")
    return "".join(ch if ch >= " " or ch == "\n" else "." for ch in text)


def candidate_name(data: bytes) -> str | None:
    text = data[:8000].decode("cp949", "replace")
    rules = [
        ("class GameWindow", "game.py"),
        ("class Interface", "interfaceModule.py"),
        ("File: localeInfo.py", "localeInfo.py"),
        ("class MainStream", "networkModule.py"),
        ("class MiniMap", "uiMiniMap.py"),
        ("class TargetBoard", "uiTarget.py"),
        ("class CharacterWindow", "uiCharacter.py"),
        ("class TaskBar", "uiTaskBar.py"),
        ("class InventoryWindow", "uiInventory.py"),
        ("def __hybrid_import", "system.py"),
        ("class CMouseController", "mouseModule.py"),
        ("class LoadingWindow", "introLoading.py"),
        ("class LoginWindow", "introLogin.py"),
        ("class SelectCharacterWindow", "introSelect.py"),
        ("class CreateCharacterWindow", "introCreate.py"),
        ("class OptionDialog", "uiOption.py"),
        ("class SystemDialog", "uiSystem.py"),
        ("class ToolTip", "uiToolTip.py"),
    ]
    for needle, name in rules:
        if needle in text:
            return name
    m = re.search(r"#\s*Filename:\s*([A-Za-z0-9_./-]+\.py)", text)
    if m:
        return m.group(1).replace("/", "_")
    return None


def iter_chunks(blob: bytes):
    pos = 0
    while True:
        off = blob.find(b"MCOZ", pos)
        if off < 0:
            break
        if off + 16 <= len(blob):
            enc_size, comp_size, raw_size = struct.unpack_from("<III", blob, off + 4)
            if 0 < enc_size < 100_000_000 and 0 < comp_size <= enc_size and 0 < raw_size < 100_000_000 and off + 16 + enc_size <= len(blob):
                yield off, enc_size, comp_size, raw_size
        pos = off + 1


def extract(pack_path: Path, out_dir: Path) -> list[dict]:
    blob = pack_path.read_bytes()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = []
    for idx, (off, enc_size, comp_size, raw_size) in enumerate(iter_chunks(blob)):
        enc = blob[off + 16 : off + 16 + enc_size]
        dec = decrypt_payload(enc)
        status = "ok"
        data = b""
        error = None
        try:
            if not dec.startswith(b"MCOZ"):
                raise ValueError("decrypted payload missing inner MCOZ magic")
            data = lzokay.decompress(dec[4 : 4 + comp_size], raw_size)
        except Exception as exc:  # keep manifest useful for partial forensics
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        ext = classify(data) if data else "fail"
        guessed = candidate_name(data) if data else None
        stem = f"{idx:04d}_{off:08x}"
        filename = f"{stem}_{guessed}" if guessed else f"{stem}.{ext}"
        # avoid nested paths from guessed names
        filename = filename.replace("/", "_").replace("\\", "_")
        if data:
            (out_dir / filename).write_bytes(data)
        manifest.append(
            {
                "idx": idx,
                "offset": off,
                "enc_size": enc_size,
                "comp_size": comp_size,
                "raw_size": raw_size,
                "status": status,
                "error": error,
                "file": filename if data else None,
                "guessed_name": guessed,
                "sha1": hashlib.sha1(data).hexdigest() if data else None,
                "preview": preview(data) if data else "",
            }
        )
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pack", type=Path)
    ap.add_argument("out_dir", type=Path)
    args = ap.parse_args()
    manifest = extract(args.pack, args.out_dir)
    ok = sum(1 for m in manifest if m["status"] == "ok")
    print(f"Extracted {ok}/{len(manifest)} chunks from {args.pack} to {args.out_dir}")
    guesses = [(m["idx"], m["file"], m["guessed_name"]) for m in manifest if m.get("guessed_name")]
    if guesses:
        print("Guessed key scripts:")
        for idx, file, guessed in guesses:
            print(f"  {idx:04d}: {guessed} -> {file}")
    return 0 if ok == len(manifest) else 1


if __name__ == "__main__":
    raise SystemExit(main())
