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

_base = os.path.join(os.environ.get('APPDATA', ''),
                     'Nemetschek', 'Vectorworks', '2026', 'Plug-ins')
for _name in ('VW-MCP', 'VWX-MCP'):
    _dir = os.path.join(_base, _name)
    if os.path.isdir(_dir):
        if _dir not in sys.path:
            sys.path.insert(0, _dir)
        break

import vwx_pump
importlib.reload(vwx_pump)
# v11 pump has NO module-level auto-run: the entry point must be called
# explicitly. pump_all = full drain incl. document mutation — safe HERE
# because this is VW's own script-plugin execution context (v4-proven,
# months of production incl. the 253-object Winkelstützen build).
vwx_pump.pump_all()
