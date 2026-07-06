# VWX Bridge Start — paste this as the script of a Python MENU COMMAND plugin
# (Plug-in Manager > Eigene Plug-ins > Neu... > Menübefehl, Sprache Python),
# then add it to a menu in the workspace editor and assign Ctrl+Shift+B.
#
# v4: running it drains the file-IPC job queue (vwx_pump.py) and returns
# immediately — no dialog, Vectorworks stays responsive. The watchdog fires
# this hotkey automatically whenever the MCP server writes a job.
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
# Reload re-executes the module top-level, which runs pump() once: claim
# job files, execute, write results, return.
importlib.reload(vwx_pump)
