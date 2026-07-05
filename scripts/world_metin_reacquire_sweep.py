from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from PIL import ImageGrab

sys.path.insert(0, 'src')
from metin2_research.detector import YoloV5Detector
from metin2_research.predict import build_prediction_report
from metin2_research.screenshot_state import save_annotated_preview
from metin2_research.win_input import hold_key, tap_key
from metin2_research.window_capture import activate_window, find_window

OUT = Path('reports/world_metin_reacquire_sweep')
OUT.mkdir(parents=True, exist_ok=True)
detector = YoloV5Detector('C:/Users/blade/AppData/Local/Temp/metin2bot/yolov5', 'reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt', trust_checkpoint=True)

def capture_detect(i: int):
    w = find_window('MT2Portugalia')
    activate_window(w)
    time.sleep(0.1)
    img = OUT / f'frame_{i:02d}.jpg'
    ImageGrab.grab(bbox=w.bbox).save(img)
    report = build_prediction_report(img, detector.detect(img), min_confidence=0.20)
    st = report['state']
    world = []
    for b in st.get('boxes', []):
        x = float(b['x_center']); y = float(b['y_center']); bw=float(b['width']); bh=float(b['height'])
        # Reject minimap/top-right, hotbar, left character panel region, and titlebar-edge false positives.
        if x > st['image_width'] * 0.78 and y < st['image_height'] * 0.35:
            continue
        if y > st['image_height'] * 0.86:
            continue
        if x < 360 and y < 820:
            continue
        if y < 70:
            continue
        if bw < 35 or bh < 50:
            continue
        world.append(b)
    st['boxes'] = world
    st['box_count'] = len(world)
    st['target_visible'] = bool(world)
    st['target_box'] = world[0] if world else None
    st['target_confidence'] = float(world[0]['confidence']) if world else 0.0
    if world:
        st['screen_xy'] = [w.bbox[0] + float(world[0]['x_center']), w.bbox[1] + float(world[0]['y_center'])]
    save_annotated_preview(img, st, OUT / f'frame_{i:02d}_preview.jpg')
    return st, str(img), str(OUT / f'frame_{i:02d}_preview.jpg')

# Close character panel if open, then sweep.
tap_key('1')
events=[]
for i in range(24):
    st,img,prev = capture_detect(i)
    events.append({'i':i,'visible':st['target_visible'],'conf':st['target_confidence'],'screen_xy':st.get('screen_xy'),'preview':prev})
    print(json.dumps(events[-1]), flush=True)
    if st['target_visible']:
        break
    hold_key('e', 0.35)
    time.sleep(0.2)
summary={'events':events,'latest_preview':events[-1]['preview']}
(OUT/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
print(json.dumps(summary, indent=2))
