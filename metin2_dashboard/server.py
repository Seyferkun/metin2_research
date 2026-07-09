from __future__ import annotations

import argparse
import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import read_buff_config, read_combat_config, read_login_config, read_reroll_config, write_buff_config, write_combat_config, write_login_config, write_reroll_config
from .registry import ProcessRegistry
from .state import DEFAULT_TSV_PATH, read_client_state
from .state_bridge import state_bridge_report

JSON_STATE_PATH = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")
BUFFER_JSON_STATE_PATH = Path(r"D:/Games/MT2PortugaliaBuffer/app/hermes_state.json")
BUFFER_TSV_STATE_PATH = Path(r"D:/Games/MT2PortugaliaBuffer/app/hermes_state.tsv")



def read_api_state(tsv_path: str | Path, *, json_path: str | Path = JSON_STATE_PATH) -> dict:
    json_file = Path(json_path)
    if json_file.exists():
        try:
            data = json.loads(json_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                data["_file_mtime"] = json_file.stat().st_mtime
                data["available"] = True
                return data
        except Exception:
            pass
    return read_client_state(tsv_path)


def equipment_from_state(state: dict) -> dict:
    inventory = state.get("inventory") if isinstance(state.get("inventory"), list) else []
    weapon = state.get("equipped_weapon") if isinstance(state.get("equipped_weapon"), dict) else None
    if weapon is None:
        weapon = next((item for item in inventory if isinstance(item, dict) and item.get("attrs")), None)
    state_mtime = state.get("_file_mtime")
    try:
        state_age_seconds = round(time.time() - float(state_mtime), 3) if state_mtime else None
    except (TypeError, ValueError):
        state_age_seconds = None
    stale = bool(state_age_seconds is not None and state_age_seconds > 2.0)
    return {
        "available": bool(inventory or weapon),
        "source": "/api/state.inventory",
        "state_mtime": state_mtime,
        "state_age_seconds": state_age_seconds,
        "stale": stale,
        "warning": "client state file is stale; equipment attrs may not reflect live rerolls" if stale else None,
        "equipped_weapon": weapon,
        "inventory_with_attrs": [item for item in inventory if isinstance(item, dict) and item.get("attrs")],
        "inventory_count": len(inventory),
    }

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>metin2 local dashboard</title>
<style>
:root{--bg:#101112;--panel:#181a1d;--line:#30343a;--green:#2e8b57;--amber:#d4a017;--red:#b22222;--grey:#4b5563;--text:#f5f7fb;--muted:#a8b0bd}*{box-sizing:border-box}body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;background:var(--bg);color:var(--text)}button{margin:3px;padding:8px 10px;border:0;border-radius:6px;font-weight:600}button.dry{background:#2f6fed;color:white}button.live{background:#8b1e1e;color:white}button.live.locked,button:disabled{background:var(--grey);color:#ddd;cursor:not-allowed}button.stop{background:#b7791f;color:white}button.archive{background:#334155;color:white}.card{border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0;background:var(--panel)}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}.ribbon{display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;position:sticky;top:0;z-index:2;background:#0c0d0e;border:1px solid var(--line);border-radius:12px;padding:12px;margin-bottom:12px}.truth{border-left:5px solid #666}.truth.ok{border-left-color:var(--green)}.truth.warn{border-left-color:var(--amber)}.truth.bad{border-left-color:var(--red)}.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#333;margin:2px;font-size:.8rem;font-weight:700}.badge.ok{background:var(--green)}.badge.warn{background:var(--amber);color:#111}.badge.bad{background:var(--red)}.badge.grey{background:var(--grey)}.bar{height:12px;background:#333;border-radius:8px;overflow:hidden;margin:8px 0}.bar>span{display:block;height:100%;background:var(--green)}pre{background:#060606;padding:10px;border-radius:8px;overflow:auto;max-height:240px}.muted{color:var(--muted)}.bad{color:#ff8a8a}.warn{color:#ffd27d}.ok{color:#80d080}.copyrow{display:flex;gap:8px;flex-wrap:wrap}.small{font-size:.9rem}.gate{font-size:1.05rem;border:1px dashed var(--amber);padding:10px;border-radius:8px;margin-top:8px}.kv{line-height:1.55}.audit{max-height:180px}.table{width:100%;border-collapse:collapse}.table td,.table th{border-bottom:1px solid var(--line);padding:4px;text-align:left}</style>
</head><body>
<h1>metin2 local dashboard</h1>
<p class="muted">localhost-only control surface. live starts require typed LIVE confirmation. Modes: dry-run, live, use buff config, attack nearby mobs.</p>
<div id="statusRibbon" class="ribbon"><div>connection loading</div><div>mode loading</div><div>safety loading</div></div>
<div class="card"><h2>automation truth dashboard</h2><div id="truthCards" class="grid">loading</div><p class="muted">Honest status: pixel x/y and HP can be proven while project_position/screen/z and buff automation remain unproven until evidence exists. named_metin_probe is never treated as selected-target evidence.</p></div>
<div class="card"><h2>diagnostics + blocked-reason audit</h2><div class="copyrow"><button class="archive" onclick="copyPanel('state')">copy /api/state</button><button class="archive" onclick="copyPanel('bridgeTrust')">copy /api/state_bridge</button><button class="archive" onclick="copyPanel('runsSummary')">copy /api/runs summary</button><button class="archive" onclick="copyText(latestReasonText())">copy latest reason</button></div><p id="diagLine" class="muted">waiting for first refresh</p><div id="capabilityMatrix" class="grid"></div><h3>runs newest-first compact table</h3><div id="runsTable">loading</div><h3>last 20 failure/block reasons</h3><pre id="auditStrip" class="audit">loading</pre><pre id="runsSummary">loading</pre></div>
<div class="card"><h2>client state</h2><pre id="state">loading</pre></div>
<div class="card"><h2>state bridge trust</h2><pre id="bridgeTrust">loading</pre></div>
<div class="card"><h2>direct F1/F2 key test + timed macro</h2>
<p class="muted">LIVE key tests focus MT2Portugalia and press only the selected key. presses=0 repeats until stopped from runs. Key sender relaunches elevated; approve UAC if Windows asks.</p>
<button class="live" onclick="startKeyMacro('f1',1)">Press F1 once LIVE</button>
<button class="live" onclick="startKeyMacro('f2',1)">Press F2 once LIVE</button><br>
<label>interval seconds <input id="keyMacroInterval" value="35" size="6"></label>
<label>presses (0 = until stopped) <input id="keyMacroPresses" value="0" size="6"></label>
<label>hold <input id="keyMacroHold" value="0.06" size="6"></label><br>
<button class="live" onclick="startKeyMacro('f1',document.getElementById('keyMacroPresses').value)">Start F1 timed macro LIVE</button>
<button class="live" onclick="startKeyMacro('f2',document.getElementById('keyMacroPresses').value)">Start F2 timed macro LIVE</button>
</div>
<div class="card"><h2>scripts <button onclick="toggleAutoRefresh()" id="refreshToggle">pause auto refresh</button></h2><p class="muted">Auto refresh will not rebuild script settings while you are editing them. attack nearby mobs remains DRY-RUN ONLY unless separately approved.</p><div id="scripts"></div></div>
<div class="card"><h2>runs <button class="stop" onclick="stopAll()">emergency stop all managed runs</button><button class="archive" onclick="archiveAll()">archive all completed</button><button class="archive" onclick="showArchivedRuns()">show archived</button></h2><div id="runs"></div></div>
<script>
async function api(path, opts={}){let r=await fetch(path, opts); let j=await r.json(); if(!r.ok){throw new Error(j.error||r.status)} return j}
let autoRefresh = true;
let scriptsDirty = false;
let lastState={}, lastBridge={}, lastRuns=[];
let eventAudit=[];
function scriptFormHasFocus(){return document.activeElement && document.activeElement.closest && document.activeElement.closest('#scripts')}
async function refresh(){
  try{
    let started = Date.now();
    let state = await api('/api/state');
    let bridge = await api('/api/state_bridge');
    let runs = await api('/api/runs');
    let combat = await api('/api/combat');
    let buffs = await api('/api/buffs');
    lastState=state; lastBridge=bridge; lastRuns=runs;
    document.getElementById('state').textContent=JSON.stringify(state, null, 2);
    document.getElementById('bridgeTrust').textContent=JSON.stringify(bridge, null, 2);
    renderRibbon(state, bridge);
    renderTruthCards(state, bridge, combat, buffs, runs);
    renderDiagnostics(state, bridge, runs, Date.now()-started);
    renderCapabilityMatrix(state, bridge, runs);
    let scripts=await api('/api/scripts');
    if(!scriptsDirty && document.activeElement && !scriptFormHasFocus()) renderScripts(scripts);
    document.getElementById('runs').innerHTML=runs.map(r=>`<div class="card"><b>${escapeHtml(r.run_id)}</b> ${escapeHtml(r.script)} ${escapeHtml(r.mode)} <span class="${r.running?'ok':'bad'}">${r.running?'running':'exited '+r.exit_code}</span><br>pid ${escapeHtml(String(r.pid))}<br><button class="stop" onclick="stopRun('${r.run_id}')">stop</button><button class="archive" onclick="archiveRun('${r.run_id}')" ${r.running?'disabled':''}>archive</button><pre>${escapeHtml(r.log_tail||'')}</pre></div>`).join('') || '<span class="muted">no managed runs</span>';
  }catch(e){document.getElementById('statusRibbon').innerHTML=`<div class="bad">API ERROR</div><div>${escapeHtml(String(e.message||e))}</div><div><span class="badge bad">ERROR</span></div>`}
}
function escapeHtml(s){return String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function fmtCoord(v){return Array.isArray(v)&&v.length>=2 ? `(${Math.round(Number(v[0]))}, ${Math.round(Number(v[1]))})` : 'Not proven'}
function fmtPct(t){let p=t&&(t.hp_pct??t.target_hp_pct??t.hp_percent??t.target_hp_percent); if((p===undefined||p===null)&&t&&t.hp&&t.max_hp){p=Number(t.hp)/Number(t.max_hp)*100} return Number.isFinite(Number(p)) ? Number(p).toFixed(1)+'%' : 'Not proven'}
function stateAge(state, bridge){let a=bridge.age_seconds; if(Number.isFinite(Number(a))) return Number(a); if(state._file_mtime) return Math.max(0, Date.now()/1000-Number(state._file_mtime)); return null}
function modeFor(state, bridge){if(bridge.dry_run_action==='DRY_RUN_IDLE') return 'DRY-RUN'; if(bridge.has_trusted_target) return 'TARGETING'; if(state.target) return 'TARGETING'; return 'OBSERVE'}
function badge(text, cls){return `<span class="badge ${cls}">${escapeHtml(text)}</span>`}
function cap(label,state,detail){let cls=state==='VERIFIED'?'ok':state==='BLOCKED'?'bad':'warn'; return `<div class="card truth ${cls}"><b>${escapeHtml(label)}</b><br>${badge(state,cls)}<br><span class="small">${escapeHtml(detail)}</span></div>`}
function latestReasonText(){return String((lastBridge&&lastBridge.reason)||'no latest reason')}
function copyText(txt){navigator.clipboard?.writeText(String(txt||''));}
function renderRibbon(state, bridge){let player=state.player||{}; let age=stateAge(state, bridge); let stale=age!==null && age>2; let mode=modeFor(state, bridge); document.getElementById('statusRibbon').innerHTML=`<div><b>Connection</b><br>${badge(stale?'STALE':'PASS',stale?'bad':'ok')} API age ${age===null?'unknown':age.toFixed(1)+'s'}<br>${escapeHtml(player.name||state.player_name||'unknown')} / ${escapeHtml(state.map||state.map_name||'unknown map')}</div><div><b>Mode</b><br>${badge(mode,mode==='DRY-RUN'?'warn':mode==='OBSERVE'?'grey':'ok')} current_action ${escapeHtml(String(bridge.current_action||'IDLE'))}</div><div><b>Safety</b><br>${badge('LIVE ATTACK ENABLED','warn')}<br><span class="small">Live attack UI enabled; LIVE confirmation and script gates still required. No position-perfect targeting.</span></div>`}

function renderCapabilityMatrix(state, bridge, runs){
  let t=state.target||{};
  let verifiedTarget=!!bridge.has_trusted_target;
  let verifiedFields=!!(t.vid&&t.name&&t.alive===true&&Array.isArray(t.pixel_position)&&(t.hp_pct!==undefined||t.target_hp_pct!==undefined));
  document.getElementById('capabilityMatrix').innerHTML = [
    cap('Target acquisition', verifiedTarget?'VERIFIED':'UNVERIFIED', verifiedTarget?'Telemetry proven in this build':'No fresh selected-target proof'),
    cap('Target VID/name/alive/type/x-y/hp', verifiedFields?'VERIFIED':'UNVERIFIED', verifiedFields?'Telemetry proven in this build':'Not yet proven; behavior must not be treated as completed'),
    cap('Bridge trust HIGH_EXACT', bridge.has_trusted_target?'VERIFIED':'UNVERIFIED', bridge.trust_level||'NONE'),
    cap('project_position/screen-space/z', 'UNVERIFIED', 'Not yet proven; behavior must not be treated as completed'),
    cap('Keep-buff automation', 'UNVERIFIED', 'Not yet proven; manual buffs do not count'),
    cap('Attack live / engage / kill / auto-run', 'BLOCKED', 'Action disabled by current safety policy')
  ].join('');
}

function renderTruthCards(state, bridge, combat={}, buffs={}, runs=[]){
  let t = state.target || {}; let bt = bridge.target || {}; let hp = fmtPct(t); let hpNum=parseFloat(hp)||0;
  let pixel = fmtCoord(t.pixel_position); let project = fmtCoord(t.project_position); let z = (t.z??bt.z); z = (z===null||z===undefined||z==='') ? 'Not proven' : z;
  let trusted = !!bridge.has_trusted_target; let reason = escapeHtml(String(bridge.reason||'no gate reason available')); let posPerfect = project!=='Not proven' && z!=='Not proven';
  let enabledBuffs=(buffs.buffs||[]).filter(b=>b&&b.enabled).map(b=>String(b.key||'?').toUpperCase()); let buffRun=(runs||[]).find(r=>r.script==='combat_metin_client_state'&&r.running&&String(r.command||'').includes('buff'));
  let attackRun=(runs||[]).find(r=>r.script==='combat_metin_client_state'&&r.running&&String(r.command||'').includes('attack-nearby'));
  document.getElementById('truthCards').innerHTML = `
    <div class="card truth ${trusted?'ok':'warn'}"><h3>Target Evidence ${trusted?badge('PASS','ok'):badge('BLOCKED','warn')}</h3><div class="kv"><b>${escapeHtml(t.name||bt.name||'none')}</b><br>VID ${escapeHtml(String(t.vid||bt.vid||0))} type ${escapeHtml(String(t.type||'unknown'))} is_metin ${escapeHtml(String(bt.is_metin??String(t.name||'').toLowerCase().includes('metin')))}<br>alive ${escapeHtml(String(t.alive??bt.is_alive??'unknown'))} source ${escapeHtml(String(t.alive_source||'unknown'))}<br>trust ${badge(String(bridge.trust_level||'NONE'),trusted?'ok':'grey')} age ${escapeHtml(String(bridge.age_seconds??'unknown'))}s<br>${trusted?'Selected target identity proven':'No fresh selected-target identity proof'}<br><span class="warn">${posPerfect?'Position fields present':'Position-perfect targeting not proven'}</span><div class="bar"><span style="width:${hpNum}%"></span></div>HP ${hp}<br>pixel_position ${pixel}<br>project_position/screen ${project}<br>z ${z}</div></div>
    <div class="card truth warn"><h3>Safety / Gate ${badge('LOCKED','warn')}</h3>current ${escapeHtml(String(bridge.current_action||'IDLE'))}<br>dry_run_action ${escapeHtml(String(bridge.dry_run_action||'NEED_METIN_TARGET'))}<br>live_action ${escapeHtml(String(bridge.live_action||'NEED_METIN_TARGET'))}<div class="gate">${reason.includes('dry-run')?'Dry-run blocks engagement':reason}</div><button class="live" title="Operator enabled: requires LIVE confirmation and script safety gates">Live engage enabled: confirm LIVE in controls</button></div>
    <div class="card truth warn"><h3>Buff Automation ${buffRun?badge('ATTEMPTING','warn'):badge('UNVERIFIED','warn')}</h3>toggle/use_config ${buffs.use_buff_config?'enabled':'disabled'}<br>backend route/readback: ${buffs?'available':'unknown'}<br>timer heartbeat/last tick: Not proven<br>detector result: Not proven<br>dispatch attempt/result: ${buffRun?'managed run active; visual proof still required':'Not proven'}<br><span class="warn">Buff automation not proven / UNVERIFIED — manual buffs do not count. Disabled: NO_BUFFS_PROVEN</span><br>enabled keys ${enabledBuffs.join(',')||'none'}</div>
    <div class="card truth warn"><h3>Attack Mobs ${badge('LIVE ENABLED','warn')}</h3>Status: LIVE ENABLED WITH EXPLICIT CONFIRMATION<br>target_class predicate: Metin-only HIGH_EXACT selected target<br>generic hostile/generic mob: blocked until separate predicate exists<br>gate trace: candidate -> ${t.vid?'selected target':'none'} -> confidence ${bridge.trust_level||'NONE'} -> coordinate ${pixel} / hp ${hp} -> would_action ${bridge.dry_run_action||'NEED_METIN_TARGET'}<br>${attackRun?'<span class="bad">attack-nearby run detected; verify mode/stop if unintended</span>':'live attack UI enabled; requires LIVE confirmation and current script gate proof'}</div>`;
}
function renderDiagnostics(state, bridge, runs, latencyMs){let age=stateAge(state, bridge); let reason=bridge.reason||'none'; let summary={last_refresh:new Date().toISOString(), latency_ms:latencyMs, api_age_seconds:age, latest_reason:reason, runs:(runs||[]).map(r=>({run_id:r.run_id,script:r.script,mode:r.mode,running:r.running,exit_code:r.exit_code}))}; if(reason&&eventAudit[0]!==reason){eventAudit.unshift(reason); eventAudit=eventAudit.slice(0,20)} document.getElementById('diagLine').textContent=`Last refresh ${summary.last_refresh}; API age ${age===null?'unknown':age.toFixed(1)+'s'}; latest reason: ${reason}`; document.getElementById('runsSummary').textContent=JSON.stringify(summary,null,2); document.getElementById('auditStrip').textContent=eventAudit.map((r,i)=>`${i+1}. ${r}`).join('\n')||'no failure/block reasons yet'; let rows=[...(runs||[])].reverse().map(r=>`<tr><td>${escapeHtml(r.run_id)}</td><td>${escapeHtml(r.script)}</td><td>${escapeHtml(r.mode)}</td><td>${r.running?badge('RUNNING','ok'):badge('EXIT '+r.exit_code,'grey')}</td></tr>`).join(''); document.getElementById('runsTable').innerHTML=`<table class="table"><tr><th>run_id</th><th>script</th><th>mode</th><th>status</th></tr>${rows||'<tr><td colspan=4>no managed runs</td></tr>'}</table>`}
function copyPanel(id){let el=document.getElementById(id); navigator.clipboard?.writeText(el.textContent||'');}
function liveButtonFor(s){if(s.name==='combat_metin_client_state') return `<button class="live" onclick="start('${s.name}',true)" title="Operator enabled: requires LIVE confirmation and current script safety gates">start live attack</button>`; if(s.name==='move_to_metin_client_state') return `<button class="live locked" disabled title="Disabled: movement live remains locked; enable separately if needed">Disabled: MOVEMENT_LIVE_LOCKED</button>`; return `<button class="live" onclick="start('${s.name}',true)">start live</button>`}
function renderScripts(scripts){document.getElementById('scripts').innerHTML=scripts.map(s=>`<div><b>${escapeHtml(s.name)}</b> <span class="muted">${escapeHtml(s.description||'')}</span><div>${renderOptions(s)}</div><button class="dry" onclick="start('${s.name}',false)">start dry-run</button>${liveButtonFor(s)}</div>`).join('<hr>');}
function renderOptions(script){if(!script.options || !script.options.length) return ''; return script.options.map(o=>{let id=`opt-${script.name}-${o.name}`; let val=o.default===null||o.default===undefined?'':o.default; if(o.type==='bool') return `<label title="${escapeHtml(o.description||'')}"><input id="${id}" type="checkbox" data-default="${val?'true':'false'}" oninput="scriptsDirty=true" ${val?'checked':''}> ${escapeHtml(o.name)}</label> `; return `<label title="${escapeHtml(o.description||'')}">${escapeHtml(o.name)}: <input id="${id}" data-type="${escapeHtml(o.type||'str')}" data-default="${escapeHtml(String(val))}" value="${escapeHtml(String(val))}" size="10" oninput="scriptsDirty=true"></label> `;}).join('<br>')}
function collectOptions(script){let opts={}; (script.options||[]).forEach(o=>{let el=document.getElementById(`opt-${script.name}-${o.name}`); if(!el) return; if(o.type==='bool') {let val = el.checked; if(String(val) !== el.dataset.default) opts[o.name]=val;} else if(el.value!=='' && el.value !== el.dataset.default) opts[o.name]=el.value;}); return opts;}
async function start(name, live){let scripts=await api('/api/scripts'); let spec=scripts.find(s=>s.name===name); let body={script:name, live:live, options:collectOptions(spec)}; if(live){body.confirm_live = prompt('type LIVE to confirm') === 'LIVE'} try{await api('/api/start',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})}catch(e){alert(e.message)} scriptsDirty=false; refresh()}
async function startKeyMacro(key, presses){let body={script:'key_macro_control', live:true, confirm_live:false, options:{key:key, interval_seconds:document.getElementById('keyMacroInterval').value, presses:String(presses), hold_seconds:document.getElementById('keyMacroHold').value, window_query:'MT2Portugalia', elevate:true}}; body.confirm_live = prompt('type LIVE to confirm '+key.toUpperCase()+' key macro') === 'LIVE'; try{await api('/api/start',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})}catch(e){alert(e.message)} refresh();}
async function stopRun(id){await api('/api/stop',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({run_id:id})}); refresh()}
async function archiveRun(id){await api('/api/archive_run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({run_id:id})}); refresh()}
async function archiveAll(){await api('/api/archive_all',{method:'POST'}); refresh()}
async function showArchivedRuns(){let runs=await api('/api/runs?archived=1'); alert(JSON.stringify(runs,null,2))}
async function stopAll(){await api('/api/stop_all',{method:'POST'}); refresh()}
function toggleAutoRefresh(){autoRefresh = !autoRefresh; document.getElementById('refreshToggle').textContent = autoRefresh ? 'pause auto refresh' : 'resume auto refresh';}
setInterval(()=>{if(autoRefresh) refresh()}, 1500); refresh();
</script></body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    registry: ProcessRegistry
    tsv_path: Path
    json_path: Path
    project_root: Path
    login_profile: str

    def log_message(self, fmt, *args):
        # keep logs minimal and secret-free.
        try:
            sys.stderr.write(f"dashboard {self.address_string()} {fmt % args}\n")
            sys.stderr.flush()
        except Exception:
            # If the native panel's parent console/stream disappears, letting a
            # broken inherited stream exception escape here aborts the request
            # before headers are sent (RemoteDisconnected in the panel).
            pass

    def send_json(self, data, status=200):
        raw = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json; charset=utf-8")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_body(self):
        n = int(self.headers.get("content-length", "0") or "0")
        if not n:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                raw = HTML.encode("utf-8")
                self.send_response(200)
                self.send_header("content-type", "text/html; charset=utf-8")
                self.send_header("content-length", str(len(raw)))
                self.end_headers(); self.wfile.write(raw); return
            if path == "/api/state":
                self.send_json(read_api_state(self.tsv_path, json_path=self.json_path)); return
            if path == "/api/state_bridge":
                self.send_json(state_bridge_report(read_api_state(self.tsv_path, json_path=self.json_path))); return
            if path == "/api/equipment":
                self.send_json(equipment_from_state(read_api_state(self.tsv_path, json_path=self.json_path))); return
            if path == "/api/client":
                self.send_json({"json_state": str(self.json_path), "tsv_state": str(self.tsv_path), "project_root": str(self.project_root)}); return
            if path == "/api/login_config":
                self.send_json(read_login_config(self.project_root)); return
            if path == "/api/buffs":
                self.send_json(read_buff_config(self.project_root)); return
            if path == "/api/combat":
                self.send_json(read_combat_config(self.project_root)); return
            if path == "/api/reroll":
                self.send_json(read_reroll_config(self.project_root)); return
            if path == "/api/scripts":
                login_cfg = read_login_config(self.project_root)
                profile = self.login_profile if self.login_profile in login_cfg.get("profiles", {}) else login_cfg.get("active_profile", "main")
                row = login_cfg.get("profiles", {}).get(profile, {})
                self.registry.default_login_username = row.get("username")
                self.registry.default_login_app_dir = row.get("app_dir")
                self.send_json(self.registry.list_scripts()); return
            if path == "/api/runs":
                query = parse_qs(urlparse(self.path).query)
                include_archived = str(query.get("archived", [""])[0]).lower() in {"1", "true", "yes", "all"}
                self.send_json(self.registry.status(include_archived=include_archived)); return
            self.send_json({"error":"not found"}, 404)
        except Exception as e:
            self.send_json({"error": type(e).__name__, "detail": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self.read_body()
            if path == "/api/start":
                out = self.registry.start(
                    body["script"],
                    live=bool(body.get("live")),
                    confirm_live=bool(body.get("confirm_live")),
                    extra_args=body.get("extra_args", []),
                    options=body.get("options", {}),
                )
                self.send_json(out); return
            if path == "/api/client":
                self.send_json({"json_state": str(self.json_path), "tsv_state": str(self.tsv_path), "project_root": str(self.project_root)}); return
            if path == "/api/login_config":
                self.send_json(write_login_config(self.project_root, body)); return
            if path == "/api/buffs":
                self.send_json(write_buff_config(self.project_root, body)); return
            if path == "/api/combat":
                self.send_json(write_combat_config(self.project_root, body)); return
            if path == "/api/reroll":
                self.send_json(write_reroll_config(self.project_root, body)); return
            if path == "/api/stop":
                self.send_json(self.registry.stop(body["run_id"])); return
            if path == "/api/archive_run":
                self.send_json(self.registry.archive(body["run_id"])); return
            if path == "/api/archive_all":
                self.send_json(self.registry.archive_all()); return
            if path == "/api/stop_all":
                self.send_json(self.registry.stop_all()); return
            self.send_json({"error":"not found"}, 404)
        except (PermissionError, FileNotFoundError, RuntimeError, ValueError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": type(e).__name__, "detail": str(e)}, 500)


def make_server(host: str, port: int, project_root: Path, tsv_path: Path, *, json_path: Path = JSON_STATE_PATH, login_profile: str = "main", allow_lan: bool = False):
    if host not in {"127.0.0.1", "localhost"} and not allow_lan:
        raise SystemExit("refusing non-local bind; pass --allow-lan only when you intentionally want LAN access")
    login_cfg = read_login_config(project_root)
    login_row = login_cfg.get("profiles", {}).get(login_profile, {})
    DashboardHandler.registry = ProcessRegistry(project_root, default_state_json=json_path, default_login_username=login_row.get("username"), default_login_app_dir=login_row.get("app_dir"))
    DashboardHandler.tsv_path = tsv_path
    DashboardHandler.json_path = json_path
    DashboardHandler.project_root = project_root
    DashboardHandler.login_profile = login_profile
    return ThreadingHTTPServer((host, port), DashboardHandler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--tsv", default=str(DEFAULT_TSV_PATH))
    ap.add_argument("--json-state", default=str(JSON_STATE_PATH), help="client-state JSON path for /api/state and managed script defaults")
    ap.add_argument("--client-profile", choices=["main", "buffer"], default=None, help="shortcut state paths: main=MT2Portugalia/app, buffer=MT2PortugaliaBuffer/app")
    ap.add_argument("--allow-lan", action="store_true", help="Allow binding to 0.0.0.0 or a LAN IP. Default refuses non-local binds.")
    ns = ap.parse_args()
    json_state = Path(ns.json_state)
    tsv_state = Path(ns.tsv)
    if ns.client_profile == "buffer":
        json_state = BUFFER_JSON_STATE_PATH
        tsv_state = BUFFER_TSV_STATE_PATH
    login_profile = ns.client_profile or "main"
    srv = make_server(ns.host, ns.port, Path(ns.project_root).resolve(), tsv_state, json_path=json_state, login_profile=login_profile, allow_lan=ns.allow_lan)
    print(f"metin2 dashboard listening on http://{ns.host}:{ns.port} json_state={json_state} tsv={tsv_state}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
