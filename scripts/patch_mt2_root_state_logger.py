#!/usr/bin/env python
"""Patch MT2Portugalia root pack chunk 0048/game.py with a read-only state logger.

Safety properties:
- Creates a timestamped backup before touching the pack.
- Patches only one existing chunk in place.
- Keeps encrypted chunk size unchanged; refuses if compressed payload no longer fits.
- Logger writes local TSV and JSON only; it does not send packets or modify game state.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import shutil
import struct
from pathlib import Path

import lzokay

DATA_KEY = [38260275, 638120478, 5839472, 592018749]
GAME_CHUNK_INDEX = 48
MARKER = "# HERMES_STATE_LOGGER_BEGIN"

LOGGER_BLOCK = """\
		# HERMES_STATE_LOGGER_BEGIN
		try:
			now=app.GetGlobalTime()
			if not hasattr(self,"_hermes_state_next"): self._hermes_state_next=0
			if now>=self._hermes_state_next:
				self._hermes_state_next=now+200
				x=y=z=0
				try: x,y,z=player.GetMainCharacterPosition()
				except: pass
				hp=player.GetStatus(player.HP); mhp=player.GetStatus(player.MAX_HP); sp=player.GetStatus(player.SP); msp=player.GetStatus(player.MAX_SP)
				m=""; pn=""; vid=0; tn=""; ta=-1; tt=-1; rn=-1; pix=""; proj=""; nprobe=""; nearby=[]; ep="not_called"; cp=""
				try: m=background.GetCurrentMapName()
				except: pass
				try: pn=player.GetName()
				except: pass
				try:
					vid=player.GetTargetVID()
					if vid:
						try: ta=1 if chr.HasInstance(vid) else 0
						except: ta=-1
						try: tt=chr.GetInstanceType(vid)
						except: tt=-1
						try: tn=chr.GetNameByVID2AD(vid)
						except: tn=""
						try: rn=nonplayer.GetRaceNumByVID(vid)
						except: rn=-1
						try:
							chr.SelectInstance(vid)
							_pp=chr.GetPixelPosition(); px=_pp[0]; py=_pp[1]
							pix='['+str(px)+','+str(py)+']'
						except: pix=""
						try:
							try:
								_pr=chr.GetProjectPosition(vid); qx=_pr[0]; qy=_pr[1]; qz=_pr[2]
							except:
								chr.SelectInstance(vid)
								_pr=chr.GetProjectPosition(); qx=_pr[0]; qy=_pr[1]; qz=_pr[2]
							proj='['+str(qx)+','+str(qy)+','+str(qz)+']'
						except: proj=""
				except: vid=0
				try:
					_np=[]
					for mn in ("Metin da Batalha","Metin do Combate","Metin Negra","Metin da Sombra","Metin da Dureza","Metin da Alma","Metin do Ciume","Metin da Escuridao","Metin da Morte","Metin da Queda","Metin Pung-Ma","Metin Tu-Young"):
						try: nv=chr.GetVIDByName(mn)
						except: nv=0
						try: nv=int(nv)
						except: nv=0
						if nv and nv>0:
							na=1 if chr.HasInstance(nv) else 0
							nt=-1
							try: nt=chr.GetInstanceType(nv)
							except: nt=-1
							npix=""
							try:
								chr.SelectInstance(nv)
								_np2=chr.GetPixelPosition(); px=_np2[0]; py=_np2[1]
								npix='['+str(px)+','+str(py)+']'
							except: npix=""
							_ni='{"name":"'+mn+'","vid":'+str(nv)+',"alive":'+('true' if na else 'false')+',"type":'+str(nt)
							if npix: _ni+=',"pixel_position":'+npix
							_ni+='}'
							_np.append(_ni)
					nprobe=','.join(_np)
				except: nprobe=""
				try:
					for cn in dir(chr):
						try:
							if ("VID" in cn) or ("Vid" in cn) or ("Instance" in cn) or ("Near" in cn) or ("Target" in cn) or ("Name" in cn) or ("Position" in cn): cp+=str(cn)+";"
						except: pass
				except: cp="dir_fail"
				try:
					vl=chr.GetNearInstanceList(1500)
					ep="ok_empty"
					if vl:
						ep="ok_nonempty"
						for ev in vl:
							en=""
							try: en=chr.GetNameByVID(ev)
							except: pass
							nearby.append('{"vid":'+str(ev)+',"name":"'+str(en)+'"}')
				except: ep="fail"
				try:
					out='{"timestamp_ms":'+str(now)+',"map":"'+str(m)+'","player":{"name":"'+str(pn)+'","x":'+str(x)+',"y":'+str(y)+',"z":'+str(z)+',"hp":'+str(hp)+',"max_hp":'+str(mhp)+',"sp":'+str(sp)+',"max_sp":'+str(msp)+'},'
					if vid:
						out+='"target":{"vid":'+str(vid)+',"name":"'+str(tn)+'"'
						if ta!=-1: out+=',"alive":'+('true' if ta else 'false')+',"alive_source":"chr.HasInstance"'
						if tt!=-1: out+=',"type":'+str(tt)
						if rn!=-1: out+=',"race_num":'+str(rn)
						if pix: out+=',"pixel_position":'+pix
						if proj: out+=',"project_position":'+proj
						out+='},'
					else: out+='"target":null,'
					out+='"nearby_entities":['+','.join(nearby)+'],"named_metin_probe":['+str(nprobe)+'],"entity_probe":"'+str(ep)+'","chr_probe":"'+str(cp)+'","buffs":[],"skills":[],"quickslots":[]}'
					_hf=old_open("hermes_state.json","w"); _hf.write(out); _hf.close()
				except: pass
				try:
					line="%d\t%s\t%.0f\t%.0f\t%.0f\t%d\t%d\t%d\t%d\t%d\t%s\t%s\\n"%(now,str(m),x,y,z,hp,mhp,sp,msp,vid,str(pn),str(tn))
					f=old_open("hermes_state.tsv","a"); f.write(line); f.close()
				except: pass
		except: pass
		# HERMES_STATE_LOGGER_END
"""

def xtea_decrypt_block(v0: int, v1: int, key: list[int], rounds: int = 32) -> tuple[int, int]:
    mask = 0xFFFFFFFF
    delta = 0x9E3779B9
    total = (delta * rounds) & mask
    for _ in range(rounds):
        v1 = (v1 - ((((v0 << 4) & mask ^ (v0 >> 5)) + v0) ^ (total + key[(total >> 11) & 3]))) & mask
        total = (total - delta) & mask
        v0 = (v0 - ((((v1 << 4) & mask ^ (v1 >> 5)) + v1) ^ (total + key[total & 3]))) & mask
    return v0, v1


def xtea_encrypt_block(v0: int, v1: int, key: list[int], rounds: int = 32) -> tuple[int, int]:
    mask = 0xFFFFFFFF
    delta = 0x9E3779B9
    total = 0
    for _ in range(rounds):
        v0 = (v0 + ((((v1 << 4) & mask ^ (v1 >> 5)) + v1) ^ (total + key[total & 3]))) & mask
        total = (total + delta) & mask
        v1 = (v1 + ((((v0 << 4) & mask ^ (v0 >> 5)) + v0) ^ (total + key[(total >> 11) & 3]))) & mask
    return v0, v1


def crypt_payload(buf: bytes, encrypt: bool) -> bytes:
    if len(buf) % 8:
        raise ValueError("payload length must be 8-byte aligned")
    out = bytearray()
    fn = xtea_encrypt_block if encrypt else xtea_decrypt_block
    for j in range(0, len(buf), 8):
        v0, v1 = struct.unpack("<II", buf[j : j + 8])
        a, b = fn(v0, v1, DATA_KEY)
        out += struct.pack("<II", a, b)
    return bytes(out)


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


def strip_comment_only_lines(src: bytes) -> bytes:
    """Remove comment-only and blank lines to make room without changing code tokens."""
    out = []
    for line in src.splitlines(True):
        stripped = line.strip()
        if not stripped or stripped.startswith(b"#"):
            continue
        out.append(line)
    return b"".join(out)


def patch_source(src: bytes, strip_comments: bool = False) -> bytes:
    # Patch as bytes to preserve the client's original mixed/legacy encoding exactly.
    if strip_comments:
        src = strip_comment_only_lines(src)
    newline = b"\r\n" if b"\r\n" in src else b"\n"
    block = LOGGER_BLOCK.encode("ascii").replace(b"\n", newline)
    marker = MARKER.encode("ascii")
    end_marker = b"# HERMES_STATE_LOGGER_END"

    def insert_block_after_update_game(source: bytes) -> bytes:
        onupdate_markers = [b"\tdef OnUpdate(self):", b"    def OnUpdate(self):"]
        onupdate = -1
        for candidate in onupdate_markers:
            onupdate = source.find(candidate)
            if onupdate >= 0:
                break
        if onupdate < 0:
            raise RuntimeError("Could not find GameWindow.OnUpdate hook")
        update = source.find(b"app.UpdateGame()", onupdate)
        if update < 0:
            raise RuntimeError("Could not find GameWindow.OnUpdate/app.UpdateGame hook")
        line_end = source.find(newline, update)
        line_end = len(source) if line_end < 0 else line_end + len(newline)
        return source[:line_end] + block + source[line_end:]

    def remove_legacy_body_after_logger(patched: bytes, start_at: int) -> bytes:
        legacy = newline + b"\t\ttry:" + newline + b"\t\t\tnow = app.GetGlobalTime()"
        idx = patched.find(legacy, start_at)
        if idx < 0:
            return patched
        resume = patched.find(newline + b"\t\tif self.mapNameShower.IsShow():", idx)
        if resume < 0:
            return patched
        return patched[:idx] + patched[resume:]

    if marker in src:
        begin = src.find(marker)
        line_begin = src.rfind(newline, 0, begin) + len(newline)
        end = src.find(end_marker, begin)
        if end < 0:
            raise RuntimeError("Found logger begin marker without end marker")
        line_end = src.find(newline, end)
        line_end = len(src) if line_end < 0 else line_end + len(newline)
        if b"hermes_state.json" in src[line_begin:line_end]:
            print("Refreshing existing Hermes JSON logger block.")
        else:
            print("Replacing existing Hermes TSV logger with TSV+JSON logger.")
        patched = src[:line_begin] + block + src[line_end:]
        return remove_legacy_body_after_logger(patched, line_begin)
    return insert_block_after_update_game(src)


def pad_python_to_size(src: bytes, target_size: int) -> bytes:
    """Pad Python source back to the original raw size using a trailing comment."""
    if len(src) > target_size:
        raise RuntimeError(f"patched source is too large to pad: {len(src)} > {target_size}")
    need = target_size - len(src)
    if need == 0:
        return src
    if need == 1:
        return src + b"\n"
    prefix = b"" if src.endswith((b"\n", b"\r")) else b"\n"
    need_after_prefix = need - len(prefix)
    if need_after_prefix < 1:
        return src + (b"\n" * need)
    pad = prefix + b"#" + (b"H" * (need_after_prefix - 1))
    assert len(pad) == need
    return src + pad


def decode_chunk(blob: bytes | bytearray, chunk: tuple[int, int, int, int]) -> bytes:
    off, enc_size, comp_size, raw_size = chunk
    enc = bytes(blob[off + 16 : off + 16 + enc_size])
    dec = crypt_payload(enc, encrypt=False)
    if not dec.startswith(b"MCOZ"):
        raise RuntimeError("chunk did not decrypt to inner MCOZ")
    return lzokay.decompress(dec[4 : 4 + comp_size], raw_size)


def find_game_chunk(blob: bytes | bytearray, chunks: list[tuple[int, int, int, int]]) -> tuple[int, tuple[int, int, int, int], bytes]:
    for idx, chunk in enumerate(chunks):
        try:
            src = decode_chunk(blob, chunk)
        except Exception:
            continue
        head = src[:200000]
        if b"class GameWindow" in head and b"def OnUpdate" in head and b"app.UpdateGame()" in head:
            return idx, chunk, src
    raise RuntimeError("Could not locate game.py chunk by GameWindow/OnUpdate markers")


def patch_pack(root_pack: Path, dry_run: bool = False) -> Path | None:
    blob = bytearray(root_pack.read_bytes())
    chunks = list(iter_chunks(blob))
    if not chunks:
        raise RuntimeError("No MCOZ chunks found in root pack")
    chunk_index, (off, enc_size, comp_size, raw_size), src = find_game_chunk(blob, chunks)
    patched = patch_source(src)
    comp = lzokay.compress(patched)

    if 4 + len(comp) > enc_size:
        print(
            f"Initial patch too large ({4 + len(comp)} > {enc_size}); "
            "retrying after removing comment-only lines from game.py."
        )
        patched = patch_source(src, strip_comments=True)
        if len(patched) < raw_size:
            patched = pad_python_to_size(patched, raw_size)
        comp = lzokay.compress(patched)
    if 4 + len(comp) > enc_size:
        raise RuntimeError(
            f"Patched compressed payload too large for in-place patch: {4 + len(comp)} > {enc_size}. "
            f"Original comp_size={comp_size}, raw_size={raw_size}, new_comp_size={len(comp)}, new_raw_size={len(patched)}"
        )
    inner = b"MCOZ" + comp + (b"\x00" * (enc_size - 4 - len(comp)))
    new_enc = crypt_payload(inner, encrypt=True)
    assert len(new_enc) == enc_size
    print(
        f"chunk {chunk_index} @ {off}: raw {raw_size}->{len(patched)}, "
        f"comp {comp_size}->{len(comp)}, enc stays {enc_size}"
    )
    if dry_run:
        return None
    backup_dir = Path("C:/Hermes Unreal/metin2_research/backups/mt2portugalia_pack_root")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (root_pack.name + ".hermes_backup_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(root_pack, backup)
    struct.pack_into("<III", blob, off + 4, enc_size, len(comp), len(patched))
    blob[off + 16 : off + 16 + enc_size] = new_enc
    root_pack.write_bytes(blob)
    print(f"backup: {backup}")
    print(f"patched: {root_pack}")
    return backup


def patch_loose_game(game_py: Path, *, backup_dir: Path | None = None, dry_run: bool = False) -> Path | None:
    """Patch a loose app/game.py override, which MT2Portugalia loads before pack/root."""
    if not game_py.exists():
        return None
    src = game_py.read_bytes()
    patched = patch_source(src)
    if patched == src:
        print(f"loose game.py already patched: {game_py}")
        return None
    if dry_run:
        print(f"would patch loose game.py: {game_py}")
        return None
    backup_dir = backup_dir or Path("C:/Hermes Unreal/metin2_research/backups/mt2portugalia_loose_game")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / (game_py.name + ".hermes_backup_" + _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(game_py, backup)
    game_py.write_bytes(patched)
    print(f"loose backup: {backup}")
    print(f"loose patched: {game_py}")
    return backup


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("root_pack", type=Path, nargs="?", default=Path("D:/Games/MT2Portugalia/app/pack/root"))
    ap.add_argument("--loose-game", type=Path, default=Path("D:/Games/MT2Portugalia/app/game.py"))
    ap.add_argument("--skip-loose", action="store_true", help="Do not patch loose app/game.py override")
    ap.add_argument("--pack-too", action="store_true", help="Also patch pack/root when a loose app/game.py override exists")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    loose_touched = False
    if not args.skip_loose:
        loose_touched = args.loose_game.exists()
        patch_loose_game(args.loose_game, dry_run=args.dry_run)
    if loose_touched and not args.pack_too:
        print(f"loose app/game.py override exists; skipping pack/root. Use --pack-too to patch pack/root as well.")
        return 0
    patch_pack(args.root_pack, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



