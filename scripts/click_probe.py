from __future__ import annotations
import argparse, ctypes, subprocess, sys, time
from pathlib import Path
PROJECT_ROOT=Path(__file__).resolve().parents[1]
SRC_ROOT=PROJECT_ROOT/'src'
for p in (PROJECT_ROOT,SRC_ROOT):
    if str(p) not in sys.path: sys.path.insert(0,str(p))
from metin2_research.win_input import click_at, move_mouse_to
from scripts.key_macro_control import is_admin

def elevate(argv):
    class SEI(ctypes.Structure):
        _fields_=[('cbSize',ctypes.c_ulong),('fMask',ctypes.c_ulong),('hwnd',ctypes.c_void_p),('lpVerb',ctypes.c_wchar_p),('lpFile',ctypes.c_wchar_p),('lpParameters',ctypes.c_wchar_p),('lpDirectory',ctypes.c_wchar_p),('nShow',ctypes.c_int),('hInstApp',ctypes.c_void_p),('lpIDList',ctypes.c_void_p),('lpClass',ctypes.c_wchar_p),('hkeyClass',ctypes.c_void_p),('dwHotKey',ctypes.c_ulong),('hIcon',ctypes.c_void_p),('hProcess',ctypes.c_void_p)]
    info=SEI(); info.cbSize=ctypes.sizeof(SEI); info.fMask=0x40; info.lpVerb='runas'; info.lpFile=sys.executable; info.lpParameters=subprocess.list2cmdline([str(Path(__file__).resolve()),*argv]); info.lpDirectory=str(PROJECT_ROOT); info.nShow=1
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)): raise ctypes.WinError(ctypes.get_last_error())
    ctypes.windll.kernel32.WaitForSingleObject(info.hProcess,0xffffffff); code=ctypes.c_ulong(); ctypes.windll.kernel32.GetExitCodeProcess(info.hProcess,ctypes.byref(code)); print('elevated_child_exit_code',code.value); return code.value

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--elevate',action='store_true'); ap.add_argument('--x',type=int,required=True); ap.add_argument('--y',type=int,required=True); ap.add_argument('--click',action='store_true'); ap.add_argument('--wait',type=float,default=.5)
    a=ap.parse_args()
    if a.elevate and not is_admin(): return elevate([arg for arg in sys.argv[1:] if arg!='--elevate'])
    if a.click: click_at(a.x,a.y,hold=.05)
    else: move_mouse_to(a.x,a.y)
    time.sleep(a.wait)
    return 0
if __name__=='__main__': raise SystemExit(main())
