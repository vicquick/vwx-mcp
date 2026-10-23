# VWX Bridge Start — paste this as the script of a Python MENU COMMAND plugin
# (Plug-in Manager > Eigene Plug-ins > Neu... > Menübefehl, Sprache Python;
# VW2027: select it, then "Code..."), then add it to a menu in the workspace
# editor and assign Ctrl+Shift+B.
#
# v11: THE ONLY SAFE MUTATION EXECUTOR. Vectorworks' own Python-menu-command
# runner wraps script execution in a proper document/undo context — the native
# plugin's raw IPythonScriptEngine::ExecuteScript from DoInterface does NOT
# (document mutation there crashed VW, verified 2026-07-06). This command
# drains the ENTIRE job queue via vwx_pump.pump_all() and returns immediately.
# The native VwxBridge palette triggers it when jobs wait; read-only jobs drain
# in the background without it.
#
# Every run leaves a trace (bridge.log, or %TEMP%\vwx_bridgestart.log when the
# plug-in folder is not found), so "the command did nothing" is never silent.
import os
import sys
import time
import importlib


def _trace(msg, where=None):
    line = '[%s] start: %s\n' % (time.strftime('%H:%M:%S'), msg)
    for p in ((os.path.join(where, 'bridge.log') if where else None),
              os.path.join(os.environ.get('TEMP', ''), 'vwx_bridgestart.log')):
        if not p:
            continue
        try:
            with open(p, 'a', encoding='utf-8') as f:
                f.write(line)
            return
        except Exception:
            pass


_dir = None
try:
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
        # Host year first (embedded Python lives under 'Vectorworks <year>'), so a
        # 2026 and a 2027 running side by side never drain each other's queue.
        import re
        _m = re.search(r'Vectorworks ?(\d{4})',
                       (sys.executable or '') + ' ' + (sys.prefix or ''))
        if _m:
            _versions.insert(0, _m.group(1))

    for _v in _versions:
        for _name in ('VW-MCP', 'VWX-MCP'):
            _cand = os.path.join(_root, _v, 'Plug-ins', _name)
            if os.path.isdir(_cand):
                _dir = _cand
                break
        if _dir:
            break
    _trace('invoked, python %s, dir %s' % (sys.version.split()[0], _dir), _dir)
    if _dir and _dir not in sys.path:
        sys.path.insert(0, _dir)

    import vwx_pump
    # Reload ONLY when the file changed on disk. The mtime marker rides on the
    # module object, which survives in sys.modules between menu-command
    # invocations, so an edited or redeployed vwx_pump.py still hot-reloads.
    try:
        _mt = os.path.getmtime(os.path.join(_dir, 'vwx_pump.py')) if _dir else 0.0
    except Exception:
        _mt = 0.0
    if getattr(vwx_pump, '_vwx_loaded_mtime', None) != _mt:
        importlib.reload(vwx_pump)
        vwx_pump._vwx_loaded_mtime = _mt
    # pump_all = full drain incl. document mutation — safe HERE because this is
    # VW's own script-plugin execution context.
    vwx_pump.pump_all()
except Exception:
    import traceback
    _trace('ERROR ' + traceback.format_exc(), _dir)
    raise
