from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
TOKEN_QUERY = 0x0008
TokenElevation = 20
TokenIntegrityLevel = 25

class SID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [('Sid', wintypes.LPVOID), ('Attributes', wintypes.DWORD)]

class TOKEN_MANDATORY_LABEL(ctypes.Structure):
    _fields_ = [('Label', SID_AND_ATTRIBUTES)]

advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
advapi32.OpenProcessToken.restype = wintypes.BOOL
advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
advapi32.GetTokenInformation.restype = wintypes.BOOL
advapi32.GetSidSubAuthorityCount.argtypes = [wintypes.LPVOID]
advapi32.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
advapi32.GetSidSubAuthority.argtypes = [wintypes.LPVOID, wintypes.DWORD]
advapi32.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetCurrentProcess.restype = wintypes.HANDLE


def winerr() -> int:
    return ctypes.get_last_error()


def close(h):
    if h:
        kernel32.CloseHandle(h)


def open_process(pid: int, mask: int):
    ctypes.set_last_error(0)
    h = kernel32.OpenProcess(mask, False, pid)
    return int(h or 0), winerr()


def integrity_name(rid: int | None) -> str | None:
    if rid is None:
        return None
    if rid >= 0x4000:
        return 'System'
    if rid >= 0x3000:
        return 'High'
    if rid >= 0x2000:
        return 'Medium'
    if rid >= 0x1000:
        return 'Low'
    return f'Unknown({rid})'


def token_info_from_handle(process_handle: int) -> dict:
    out = {}
    token = wintypes.HANDLE()
    ctypes.set_last_error(0)
    if not advapi32.OpenProcessToken(wintypes.HANDLE(process_handle), TOKEN_QUERY, ctypes.byref(token)):
        out['token_error'] = winerr()
        return out
    try:
        # Elevation
        elev = wintypes.DWORD(0)
        ret_len = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        if advapi32.GetTokenInformation(token, TokenElevation, ctypes.byref(elev), ctypes.sizeof(elev), ctypes.byref(ret_len)):
            out['elevated'] = bool(elev.value)
        else:
            out['elevation_error'] = winerr()
        # Integrity
        needed = wintypes.DWORD(0)
        ctypes.set_last_error(0)
        advapi32.GetTokenInformation(token, TokenIntegrityLevel, None, 0, ctypes.byref(needed))
        buf = ctypes.create_string_buffer(needed.value)
        ctypes.set_last_error(0)
        if advapi32.GetTokenInformation(token, TokenIntegrityLevel, buf, needed, ctypes.byref(needed)):
            tml = ctypes.cast(buf, ctypes.POINTER(TOKEN_MANDATORY_LABEL)).contents
            sid = tml.Label.Sid
            count = advapi32.GetSidSubAuthorityCount(sid).contents.value
            rid = advapi32.GetSidSubAuthority(sid, count - 1).contents.value
            out['integrity_rid'] = int(rid)
            out['integrity'] = integrity_name(int(rid))
        else:
            out['integrity_error'] = winerr()
        return out
    finally:
        close(token)


def inspect_pid(pid: int, name: str = '') -> dict:
    item = {'pid': pid, 'name': name}
    for label, mask in [
        ('query_limited', PROCESS_QUERY_LIMITED_INFORMATION),
        ('query_info_vm_read', PROCESS_QUERY_INFORMATION | PROCESS_VM_READ),
    ]:
        h, err = open_process(pid, mask)
        item[label] = {'handle': h, 'error': err}
        if h:
            close(h)
    h, err = open_process(pid, PROCESS_QUERY_LIMITED_INFORMATION)
    if h:
        item.update(token_info_from_handle(h))
        close(h)
    else:
        item['token_error'] = f'cannot open process for token; OpenProcess error {err}'
    return item


def discover_processes() -> list[dict]:
    cmd = "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pgclient.app' -or $_.CommandLine -like '*metin2_dashboard.server*' -or $_.CommandLine -like '*metin2_control_panel.py*' } | Select-Object ProcessId,Name,CommandLine | ConvertTo-Json -Depth 4"
    ps = subprocess.run(['powershell.exe', '-NoProfile', '-Command', cmd], capture_output=True, text=True, timeout=20)
    raw = ps.stdout.strip()
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return [{'pid': int(p['ProcessId']), 'name': p.get('Name') or '', 'command': p.get('CommandLine') or ''} for p in data]


def main() -> int:
    processes = discover_processes()
    targets = []
    seen = set()
    # current process first
    targets.append({'pid': os.getpid(), 'name': 'checker_current_process', 'command': ' '.join(sys.argv)})
    seen.add(os.getpid())
    for p in processes:
        if p['pid'] not in seen:
            targets.append(p)
            seen.add(p['pid'])
    result = {
        'checker_pid': os.getpid(),
        'checker': inspect_pid(os.getpid(), 'checker_current_process'),
        'processes': [],
    }
    for p in targets:
        info = inspect_pid(p['pid'], p.get('name', ''))
        info['command'] = p.get('command', '')
        result['processes'].append(info)
    print(json.dumps(result, indent=2))
    out = os.environ.get('INTEGRITY_CHECK_OUT')
    if len(sys.argv) > 1 and sys.argv[1].lower() != '--no-out':
        out = sys.argv[1]
    if out:
        Path(out).write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
