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
from metin2_research.win_input import click_xy, hold_key_with_periodic_tap, tap_key
from metin2_research.window_capture import activate_window, find_window

OUT=Path('reports/destroy_current_metin_approach_attack')
OUT.mkdir(parents=True, exist_ok=True)

def capture_state(detector, idx):
    w=find_window('MT2Portugalia'); activate_window(w); time.sleep(0.1)
    img=OUT/f'frame_{idx:03d}.jpg'
    ImageGrab.grab(bbox=w.bbox).save(img)
    report=build_prediction_report(img, detector.detect(img), min_confidence=0.20)
    state=report['state']
    if state.get('target_xy'):
        state['screen_xy']=[float(state['target_xy'][0])+w.bbox[0], float(state['target_xy'][1])+w.bbox[1]]
    save_annotated_preview(img, state, OUT/f'frame_{idx:03d}_preview.jpg')
    return state, w.bbox, img

detector=YoloV5Detector('C:/Users/blade/AppData/Local/Temp/metin2bot/yolov5', 'reports/yolo_easy_retrain_runs/round2_hardneg_10ep_lowlr/weights/best.pt', trust_checkpoint=True)
events=[]
last_xy=None
for i in range(8):
    state,bbox,img=capture_state(detector,i)
    box=state.get('target_box')
    event={'i':i,'conf':state.get('target_confidence'), 'visible':state.get('target_visible'), 'img':str(img)}
    if box:
        # Use body center, and if far/small, double-click to approach then wait.
        sx=int(round(bbox[0]+float(box['x_center']))); sy=int(round(bbox[1]+float(box['y_center'])))
        last_xy=[sx,sy]
        h=float(box['height']); w=float(box['width'])
        event.update({'xy':[sx,sy], 'box_h':h, 'box_w':w})
        tap_key('1')
        click_xy(sx, sy, clicks=2)
        if h >= 120 or w >= 100:
            event['action']='close_enough_attack_space_12s'
            pots=hold_key_with_periodic_tap('space', 12, tap='1', tap_every=5)
            event['potions_during_hold']=pots
        else:
            event['action']='approach_wait'
            time.sleep(3)
    elif last_xy:
        event.update({'xy':last_xy, 'action':'detector_miss_click_last_known_wait'})
        click_xy(*last_xy, clicks=2)
        time.sleep(3)
    else:
        event['action']='no_target_noop'
        time.sleep(1)
    events.append(event)
    print(json.dumps(event), flush=True)

state,bbox,img=capture_state(detector,99)
summary={'events':events,'final_state':state,'final_image':str(img),'final_preview':str(OUT/'frame_099_preview.jpg')}
(OUT/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print(json.dumps(summary, indent=2))
