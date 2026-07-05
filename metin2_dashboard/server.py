from __future__ import annotations

import argparse
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .registry import ProcessRegistry
from .state import DEFAULT_TSV_PATH, read_client_state

JSON_STATE_PATH = Path(r"D:/Games/MT2Portugalia/app/hermes_state.json")


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

HTML = r"""<!doctype html>
<html><head><meta charset="utf-8"><title>metin2 local dashboard</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:24px;background:#111;color:#eee}button{margin:3px;padding:8px 10px;border:0;border-radius:6px}button.dry{background:#2f6fed;color:white}button.live{background:#8b1e1e;color:white}button.stop{background:#b7791f;color:white}button.archive{background:#334155;color:white}.card{border:1px solid #333;border-radius:10px;padding:14px;margin:12px 0;background:#181818}pre{background:#060606;padding:10px;border-radius:8px;overflow:auto}.muted{color:#aaa}.bad{color:#ff8a8a}.ok{color:#80d080}</style>
</head><body>
<h1>metin2 local dashboard</h1>
<p class="muted">localhost-only control surface. live starts require typed confirmation.</p>
<div class="card"><h2>client state</h2><pre id="state">loading</pre></div>
<div class="card"><h2>scripts <button onclick="toggleAutoRefresh()" id="refreshToggle">pause auto refresh</button></h2><p class="muted">Auto refresh will not rebuild script settings while you are editing them.</p><div id="scripts"></div></div>
<div class="card"><h2>runs <button class="stop" onclick="stopAll()">emergency stop all managed runs</button><button class="archive" onclick="showArchivedRuns()">show archived</button></h2><div id="runs"></div></div>
<script>
async function api(path, opts={}){let r=await fetch(path, opts); let j=await r.json(); if(!r.ok){throw new Error(j.error||r.status)} return j}
let autoRefresh = true;
let scriptsDirty = false;
function scriptFormHasFocus(){return document.activeElement && document.activeElement.closest && document.activeElement.closest('#scripts')}
async function refresh(){
  document.getElementById('state').textContent=JSON.stringify(await api('/api/state'), null, 2);
  let scripts=await api('/api/scripts');
  if(!scriptsDirty && document.activeElement && !scriptFormHasFocus()) renderScripts(scripts);
  let runs=await api('/api/runs');
  document.getElementById('runs').innerHTML=runs.map(r=>`<div class="card"><b>${r.run_id}</b> ${r.script} ${r.mode} <span class="${r.running?'ok':'bad'}">${r.running?'running':'exited '+r.exit_code}</span><br>pid ${r.pid}<br><button class="stop" onclick="stopRun('${r.run_id}')">stop</button><button class="archive" onclick="archiveRun('${r.run_id}')" ${r.running?'disabled':''}>archive</button><pre>${escapeHtml(r.log_tail||'')}</pre></div>`).join('') || '<span class="muted">no managed runs</span>';
}
function escapeHtml(s){return s.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
function renderScripts(scripts){
  document.getElementById('scripts').innerHTML=scripts.map(s=>`<div><b>${s.name}</b> <span class="muted">${s.description}</span><div>${renderOptions(s)}</div><button class="dry" onclick="start('${s.name}',false)">start dry-run</button><button class="live" onclick="start('${s.name}',true)">start live</button></div>`).join('<hr>');
}
function renderOptions(script){
  if(!script.options || !script.options.length) return '';
  return script.options.map(o=>{
    let id=`opt-${script.name}-${o.name}`;
    let val=o.default===null||o.default===undefined?'':o.default;
    if(o.type==='bool') return `<label title="${escapeHtml(o.description||'')}"><input id="${id}" type="checkbox" data-default="${val?'true':'false'}" oninput="scriptsDirty=true" ${val?'checked':''}> ${o.name}</label> `;
    return `<label title="${escapeHtml(o.description||'')}">${o.name}: <input id="${id}" data-type="${o.type}" data-default="${escapeHtml(String(val))}" value="${escapeHtml(String(val))}" size="10" oninput="scriptsDirty=true"></label> `;
  }).join('<br>');
}
function collectOptions(script){
  let opts={};
  (script.options||[]).forEach(o=>{
    let el=document.getElementById(`opt-${script.name}-${o.name}`);
    if(!el) return;
    if(o.type==='bool') {
      let val = el.checked;
      if(String(val) !== el.dataset.default) opts[o.name]=val;
    }
    else if(el.value!=='' && el.value !== el.dataset.default) opts[o.name]=el.value;
  });
  return opts;
}
async function start(name, live){
  let scripts=await api('/api/scripts');
  let spec=scripts.find(s=>s.name===name);
  let body={script:name, live:live, options:collectOptions(spec)};
  if(live){body.confirm_live = prompt('type LIVE to confirm') === 'LIVE'}
  try{await api('/api/start',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(body)})}catch(e){alert(e.message)}
  scriptsDirty=false;
  refresh()
}
async function stopRun(id){await api('/api/stop',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({run_id:id})}); refresh()}
async function archiveRun(id){await api('/api/archive_run',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({run_id:id})}); refresh()}
async function showArchivedRuns(){let runs=await api('/api/runs?archived=1'); alert(JSON.stringify(runs,null,2))}
async function stopAll(){await api('/api/stop_all',{method:'POST'}); refresh()}
function toggleAutoRefresh(){
  autoRefresh = !autoRefresh;
  document.getElementById('refreshToggle').textContent = autoRefresh ? 'pause auto refresh' : 'resume auto refresh';
}
setInterval(()=>{if(autoRefresh) refresh()}, 1500); refresh();
</script></body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    registry: ProcessRegistry
    tsv_path: Path

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
                self.send_json(read_api_state(self.tsv_path)); return
            if path == "/api/scripts":
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
            if path == "/api/stop":
                self.send_json(self.registry.stop(body["run_id"])); return
            if path == "/api/archive_run":
                self.send_json(self.registry.archive(body["run_id"])); return
            if path == "/api/stop_all":
                self.send_json(self.registry.stop_all()); return
            self.send_json({"error":"not found"}, 404)
        except (PermissionError, FileNotFoundError, RuntimeError, ValueError, KeyError) as e:
            self.send_json({"error": str(e)}, 400)
        except Exception as e:
            self.send_json({"error": type(e).__name__, "detail": str(e)}, 500)


def make_server(host: str, port: int, project_root: Path, tsv_path: Path, *, allow_lan: bool = False):
    if host not in {"127.0.0.1", "localhost"} and not allow_lan:
        raise SystemExit("refusing non-local bind; pass --allow-lan only when you intentionally want LAN access")
    DashboardHandler.registry = ProcessRegistry(project_root)
    DashboardHandler.tsv_path = tsv_path
    return ThreadingHTTPServer((host, port), DashboardHandler)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8767)
    ap.add_argument("--project-root", default=".")
    ap.add_argument("--tsv", default=str(DEFAULT_TSV_PATH))
    ap.add_argument("--allow-lan", action="store_true", help="Allow binding to 0.0.0.0 or a LAN IP. Default refuses non-local binds.")
    ns = ap.parse_args()
    srv = make_server(ns.host, ns.port, Path(ns.project_root).resolve(), Path(ns.tsv), allow_lan=ns.allow_lan)
    print(f"metin2 dashboard listening on http://{ns.host}:{ns.port}")
    srv.serve_forever()

if __name__ == "__main__":
    main()
