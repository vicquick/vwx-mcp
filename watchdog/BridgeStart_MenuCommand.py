# VWX Bridge Start — paste this as the script of a Python MENU COMMAND plugin
# (Plug-in Manager > Benutzerdefiniert > Neu... > Menübefehl, Sprache Python),
# then add it to a menu in the workspace editor and assign Ctrl+Shift+B.
#
# Running it (via menu or hotkey) starts — or restarts — the MCP bridge.
# The bridge's generation token makes restarts safe: threads of a previous
# instance release the port as soon as the new instance starts.
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

import vwx_mcp_bridge
# Reload re-executes the module top-level, which calls start() — a fresh
# bridge instance with a new generation token.
importlib.reload(vwx_mcp_bridge)
