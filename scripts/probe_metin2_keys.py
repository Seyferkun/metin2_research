from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from PIL import Image, ImageChops, ImageGrab, ImageStat

sys.path.insert(0, 'src')
from metin2_research.win_input import hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window

OUT = Path('reports/key_probe')
OUT.mkdir(parents=True, exist_ok=True)


def capture(name: str):
    w = find_window('MT2Portugalia')
    activate_window(w)
    time.sleep(0.15)
    path = OUT / f'{name}.jpg'
    ImageGrab.grab(bbox=w.bbox).save(path)
    return path, w.bbox


def diff_score(a: Path, b: Path) -> float:
    im1 = Image.open(a).convert('L').resize((320, 180))
    im2 = Image.open(b).convert('L').resize((320, 180))
    diff = ImageChops.difference(im1, im2)
    return float(ImageStat.Stat(diff).mean[0])

# close big map if open, then probe movement/rotation keys.
tap_key('m')
time.sleep(0.3)
base, bbox = capture('00_before')
results = []
for key in ['q', 'e', 'a', 'd', 'space']:
    hold_key(key, 0.8 if key != 'space' else 1.5)
    time.sleep(0.35)
    img, _ = capture(f'after_{key}')
    results.append({'key': key, 'capture': str(img), 'diff_from_before': round(diff_score(base, img), 3)})
    # short pause between probes
    time.sleep(0.4)
summary = {'bbox': bbox, 'baseline': str(base), 'results': results}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
