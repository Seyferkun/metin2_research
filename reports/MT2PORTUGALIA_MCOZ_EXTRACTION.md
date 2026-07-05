# MT2Portugalia MCOZ pack extraction notes

Date: 2026-06-28
Client: `D:/Games/MT2Portugalia`

## Result

The extensionless MT2Portugalia pack files are extractable without `.eix/.epk` sidecars.

Created extractor:

- `C:/Hermes Unreal/metin2_research/scripts/extract_mt2_mcoz_pack.py`

Extraction outputs:

- `C:/Hermes Unreal/metin2_research/reports/mt2portugalia_pack_extract/root`
- `C:/Hermes Unreal/metin2_research/reports/mt2portugalia_pack_extract/uiscript`
- `C:/Hermes Unreal/metin2_research/reports/mt2portugalia_pack_extract/locale_m2`

Verified extraction counts:

- `root`: 147/147 chunks extracted
- `uiscript`: 192/192 chunks extracted
- `locale_m2`: 677/677 chunks extracted

## Format

Outer chunk format in pack files:

```text
MCOZ
uint32 encrypted_size
uint32 compressed_size
uint32 raw_size
xtea_encrypted_payload[encrypted_size]
padding/alignment
```

Decrypted payload format:

```text
MCOZ
lzo1x_stream[compressed_size]
padding
```

The extracted raw file is:

```python
raw = lzokay.decompress(decrypted_payload[4:4+compressed_size], raw_size)
```

## Key

`pgclient.app` contains decimal key strings near its EterPack code.
The data key appears in the binary as:

```text
592018749
5839472
638120478
38260275
```

For standard EterPack XTEA decrypt, the working word order is reversed:

```python
DATA_KEY = [38260275, 638120478, 5839472, 592018749]
```

This key successfully decrypted all tested `root`, `uiscript`, and `locale_m2` chunks.

## Important extracted scripts

The extractor can guess some filenames from content. Key files:

- `root/0030_0001ae00_system.py`
- `root/0032_0001da00_interfaceModule.py`
- `root/0048_0003dd00_game.py`
- `root/0089_00078800.py` likely minimap UI (`MiniMap` class)
- `root/0090_00079f00_uiCharacter.py`
- `root/0123_000a8000_uiTarget.py`
- `root/0124_000aa500.py` likely taskbar (`TaskBar` class)

## State APIs found in client Python

Useful for the Metin state logger:

```python
import background
import player
import chr
import nonplayer

x, y, z = player.GetMainCharacterPosition()
map_name = str(background.GetCurrentMapName())
hp = player.GetStatus(player.HP)
max_hp = player.GetStatus(player.MAX_HP)
sp = player.GetStatus(player.SP)
max_sp = player.GetStatus(player.MAX_SP)
name = player.GetName()
vid = player.GetTargetVID()
target_name = chr.GetNameByVID2AD(vid)  # observed in uiTarget/game code
target_race = nonplayer.GetRaceNumByVID(vid)  # observed in uiTarget
```

`game.py` has the best periodic hook point:

```python
class GameWindow(ui.ScriptWindow):
    def OnUpdate(self):
        app.UpdateGame()
        ...
```

Extracted location:

- `root/0048_0003dd00_game.py`, `OnUpdate` starts around line 2163.

`game.py` already imports all modules needed for a logger:

- `os`
- `app`
- `background`
- `chr`
- `player`
- `net`
- etc.

## Safe logger strategy

First patch should be low-rate, read-only, and local-file only.

Recommended payload concept inside `GameWindow.OnUpdate`, after `app.UpdateGame()`:

```python
# local/private-server research state dump; read-only
try:
    now = app.GetGlobalTime()
    if not hasattr(self, "_hermes_state_next"):
        self._hermes_state_next = 0
    if now >= self._hermes_state_next:
        self._hermes_state_next = now + 500
        x, y, z = player.GetMainCharacterPosition()
        vid = player.GetTargetVID()
        tname = ""
        try:
            if vid:
                tname = chr.GetNameByVID2AD(vid)
        except:
            tname = ""
        line = "%d\t%s\t%.0f\t%.0f\t%.0f\t%d\t%d\t%d\t%d\t%s\t%s\n" % (
            now,
            str(background.GetCurrentMapName()),
            x, y, z,
            player.GetStatus(player.HP), player.GetStatus(player.MAX_HP),
            vid,
            player.GetStatus(player.SP),
            player.GetName(),
            str(tname),
        )
        f = old_open("hermes_state.tsv", "a")
        f.write(line)
        f.close()
except:
    pass
```

Use `old_open` if available from `system.py` so we bypass the pack-aware `open()` override.

## PackMakerLite outcome

PackMakerLite-RS is useful as a reference, but direct CLI unpack does not work against this install because it expects `.eix/.epk` index/data pairs. MT2Portugalia stores extensionless MCOZ chunks directly.

Observed failures:

```text
packmakerlite-cli --list root
Error: failed to read root.eix

packmakerlite-cli D:/Games/MT2Portugalia/app/pack/root
unsupported auto input: file is not a recognized index archive
```

Renaming `root` to `root.eix/root.epk` and trying custom config failed because the extensionless file is not a normal index/data pair.

## Next implementation step

To modify the client safely:

1. Back up `D:/Games/MT2Portugalia/app/pack/root`.
2. Patch only chunk 0048 (`game.py`) in place.
3. Recompress the edited source with LZO1X.
4. Re-encrypt with XTEA using `DATA_KEY`.
5. Only allow the patch if the encrypted payload still fits before the next chunk boundary, or rewrite/re-align the pack with updated chunk positions.
6. Launch client and verify `D:/Games/MT2Portugalia/app/hermes_state.tsv` appears and updates.

Do not add packet actions or server interaction; keep it read-only state export.
