from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from PIL import ImageGrab

sys.path.insert(0, 'src')
from metin2_research.win_input import hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window

OUT = Path('reports/qe_reacquire_metin')
OUT.mkdir(parents=True, exist_ok=True)

def capture(name: str):
    w = find_window('MT2Portugalia')
    activate_window(w)
    time.sleep(0.1)
    path = OUT / f'{name}.jpg'
    ImageGrab.grab(bbox=w.bbox).save(path)
    return str(path)

w = find_window('MT2Portugalia')
activate_window(w)
time.sleep(0.2)
tap_key('1')
events = [{'step': 'start', 'capture': capture('00_start')}]
# Rotate left/right in small increments to learn camera speed and reacquire the Metin visually.
seq = [('q', 0.45), ('q', 0.45), ('e', 0.45), ('e', 0.45), ('e', 0.45), ('q', 0.30)]
for idx, (key, seconds) in enumerate(seq, start=1):
    hold_key(key, seconds)
    time.sleep(0.25)
    events.append({'step': idx, 'key': key, 'seconds': seconds, 'capture': capture(f'{idx:02d}_after_{key}_{seconds:.2f}s')})
summary = {'events': events, 'note': 'Q/E short rotations only; no movement clicks'}
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
