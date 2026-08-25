# VWX Bridge Start — paste this as the script of a Python MENU COMMAND plugin
# (Plug-in Manager > Eigene Plug-ins > Neu... > Menübefehl, Sprache Python),
# then add it to a menu in the workspace editor and assign Ctrl+Shift+B.
#
# v11: THE ONLY SAFE MUTATION EXECUTOR. Vectorworks' own Python-menu-command
# runner wraps script execution in a proper document/undo context — the native
# plugin's raw IPythonScriptEngine::ExecuteScript from DoInterface does NOT
# (document mutation there crashed VW, verified 2026-07-06). This command
# drains the ENTIRE job queue via vwx_pump.pump_all() and returns immediately.
# The native VwxBridge palette triggers it with a Ctrl+Shift+B keystroke when
# jobs wait and VW is the foreground app; read-only jobs drain in the
# background without it.
import os
import sys
import importlib

_root = os.path.join(os.environ.get('APPDATA', ''), 'Nemetschek', 'Vectorworks')
_forced = os.environ.get('VWX_VW_VERSION')
if _forced:
    _versions = [_forced]
else:
    try:
        _versions = sorted((d for d in os.listdir(_root)
                            if len(d) == 4 and d.isdigit()), reverse=True)
    except Exception:
        _versions = []

_dir = None
for _v in _versions:
    for _name in ('VW-MCP', 'VWX-MCP'):
        _cand = os.path.join(_root, _v, 'Plug-ins', _name)
        if os.path.isdir(_cand):
            _dir = _cand
            break
    if _dir:
        break
if _dir and _dir not in sys.path:
    sys.path.insert(0, _dir)

import vwx_pump
# Reload ONLY when the file changed on disk. This used to reload
# unconditionally on every trigger — a disk read, compile and exec of the whole
# module on each drain cycle, repeating for as long as the queue was non-empty,
# and it threw away the pump's warm state (peek cache, sweep timer, read-only
# manifest) every single time. The mtime marker rides on the module object,
# which survives in sys.modules between menu-command invocations, so an edited
# or redeployed vwx_pump.py still hot-reloads at once.
try:
    _mt = os.path.getmtime(os.path.join(_dir, 'vwx_pump.py')) if _dir else 0.0
except Exception:
    _mt = 0.0
if getattr(vwx_pump, '_vwx_loaded_mtime', None) != _mt:
    importlib.reload(vwx_pump)
    vwx_pump._vwx_loaded_mtime = _mt
# v11 pump has NO module-level auto-run: the entry point must be called
# explicitly. pump_all = full drain incl. document mutation — safe HERE
# because this is VW's own script-plugin execution context (v4-proven,
# months of production incl. the 253-object Winkelstützen build).
vwx_pump.pump_all()
