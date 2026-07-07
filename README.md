# vwx-mcp

**Vectorworks 2026 MCP server — 150 tools, true background control (bridge v12).**

Drive a live Vectorworks 2026 session from any MCP client (Claude Code, Claude
Desktop, …) **while you work in another app**: reads drain invisibly via VW's
OnIdle notification queue, writes reach VW through its own message queue — no
watchdog process, no focus juggling, structurally crash-proof.

> Building an agent against this server? Read **[AGENTS.md](AGENTS.md)** — it covers
> the three access layers, object addressing, toolset presets, and the VW2026
> API gotchas that will otherwise bite you.

## Architecture (Windows, bridge v12)

```
MCP client (Claude Code / Desktop)
    │ streamable-http :8082
    ▼
mcp-server/vwx_mcp_server.py     (standalone fastmcp 3.x)
    │ file IPC: ipc/jobs/*.json → ipc/results/<cid>.json
    ▼
VwxBridge.vlb   (native C++ web palette inside Vectorworks)
    │ palette open = bridge on; 100ms timer:
    │   reads  → OnIdle notification → vwx_pump.pump_readonly()
    │   writes → Ctrl+Shift+B accelerator (posted key when VW backgrounded)
    ▼
"VWX Bridge Start" Python menu command  (VW's script runner —
    │  the ONLY context where document mutation is safe, verified)
    ▼
vwx_pump.pump_all() → commands.py (mtime-gated hot-reload) → vs.*
```

Full context map, crash-test history and lifecycle:
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**. The classic TCP dialog
bridge remains for macOS/remote (`VWX_TRANSPORT=tcp`, [legacy/](legacy/README.md)).

## Install (Windows)

1. Copy `vwx-plugin/` contents to `%APPDATA%\Nemetschek\Vectorworks\2026\Plug-ins\VWX-MCP\`
   (the legacy folder name `VW-MCP` also works).
2. Copy `mcp-server/vwx_mcp_server.py`, `mcp-server/tool_tags.py`, and
   `mcp-server/requirements.txt` to `%USERPROFILE%\.local\share\vwx-mcp\`
3. Copy `bridge/vwx-mcp.bat` to `%USERPROFILE%\bridge\`. `python` on PATH —
   the launcher creates a venv + installs `fastmcp` on first run.
4. **Native palette**: build `native/VwxBridge2026.vcxproj` (VS2022 BuildTools,
   `VWSDK2026` env → SDK root containing `SDKLib`), then copy
   `native/Output/Release/VwxBridge.vlb` + `VwxBridge.vwr` to
   `C:\Program Files\Vectorworks 2026\Plug-ins\` (VW closed, admin).
5. **Executor command (one-time, in VW)**: Plug-in Manager → Eigene Plug-ins →
   Neu… → Menübefehl (Python) named **"VWX Bridge Start"**, code =
   `vwx-plugin/BridgeStart_MenuCommand.py`. Workspace editor: add it to a menu +
   assign **Ctrl+Shift+B**; also add "VWX Bridge Palette anzeigen". Restart VW.

Rebuild the knowledge index after an SDK bump:
`python tools/build_vs_index.py <path-to-SDK>/vs.py` → redeploy `vs_index.json`.

## Parts — a pipeline of three roles

The Windows bridge is **not redundant copies** — the VW2026 execution-context
constraint forces a three-role split (trigger → executor → work; see
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Every file below is required:

| Part (repo → deploy) | Role |
|---|---|
| `native/` → `VwxBridge.vlb`+`.vwr` in `C:\Program Files\Vectorworks 2026\Plug-ins\` | **trigger** — native palette: heartbeat, background keystroke, error-dialog auto-dismiss |
| `vwx-plugin/BridgeStart_MenuCommand.py` → "VWX Bridge Start" menu command (Ctrl+Shift+B) | **executor** — the only VW context where document mutation is safe |
| `vwx-plugin/{vwx_pump,commands}.py` + `vs_index.json` → `…\Plug-ins\VW-MCP\` | **work** — pump drains the queue, commands do the `vs.*`, index gives signatures |
| `mcp-server/` → `~\.local\share\vwx-mcp\`, `bridge/vwx-mcp.bat` → `~\bridge\` | MCP server (writes jobs, reads results) |

**macOS / remote fallback** (`VWX_TRANSPORT=tcp`): `vwx-plugin/vwx_mcp_bridge.py`
(the TCP dialog bridge) + `legacy/vwx_mcp_bridge_dialog.py` (dependency-free
reference). Not used by the Windows file-IPC path.

## Run

1. Launch Vectorworks, open the **VWX Bridge palette** (Extras menu). Palette
   open = bridge on; Pause button or closing the palette stops it.
2. Double-click `bridge\vwx-mcp.bat` → FastMCP on `http://127.0.0.1:8082/mcp`.
3. MCP client config:
   ```json
   {
     "vwx-mcp": {
       "type": "http",
       "url": "http://127.0.0.1:8082/mcp"
     }
   }
   ```

Test with the `ping` tool → `{"status":"ok","message":"VW MCP Bridge running"}`.
`ping` answers even with VW backgrounded; a `draw_rectangle` proves the write
path.

## Toolset presets (tame tool-overload)

150 tools is a lot of context for a client to load. Set `VWX_TOOLSET` in
`bridge/vwx-mcp.bat` to expose only one workflow's tools via the fastmcp
Visibility API (tags live in `mcp-server/tool_tags.py`):

| `VWX_TOOLSET` | tools | for |
|---|---|---|
| `full` (default) | 150 | everything |
| `gis` | 68 | georef / layers / classes / appearance / export |
| `modeling` | 89 | 2D+3D draw / manipulate / BIM / symbols |
| `baumkataster` | 52 | tree register: plants / records / query / IO |
| `minimal` | 24 | document / query / escape hatch |

## Tools (150)

19 tag groups — counts in parentheses:

`document` (6) · `layers` (9) · `classes` (7) · `query` (11) · `manipulate` (13) ·
`draw2d` (12) · `draw3d` (8) · `symbols` (6) · `appearance` (13) · `records` (7) ·
`bim` (16, incl. IFC / walls / spaces / materials / PIOs / components) ·
`landscape` (6, Baumkataster) · `site` (5) · `viewports` (7) · `worksheets` (5) ·
`io` (6, export/import) · `view` (4) · `geo` (2) · `escape` (7)

Three access layers (see [AGENTS.md](AGENTS.md)):
1. **Explicit tools** — the 150 wrappers above.
2. **`vwx(command, params)`** — generic dispatcher reaching every verb in
   `commands.py` (use `list_commands` to discover).
3. **`execute_script`** — arbitrary `vs.*` Python.

## Escape hatch

`execute_script` runs any Python inside VW on the main thread. `print(...)` is
captured into the `output` field; assign **`__result__`** to return a structured
value (`str`/`int`/`float`/`list`/`dict`/`bool`). Use when no explicit tool exists.

```python
# example body
vs.Oval(-1, 1, 1, -1)        # bbox circle (see AGENTS.md — don't use ArcByCenter)
__result__ = vs.GetObjectUuid(vs.LNewObj())
```

## Known constraint

Every `vs.*` call runs on the Vectorworks main thread — the UI is busy only
while a command actually executes. On Windows the transport is the **file-IPC
pump driven by the native palette (bridge v13)**: reads drain in the
background, writes reach VW through its own message queue, error dialogs
self-dismiss. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). The classic TCP
dialog bridge remains for macOS/remote (`VWX_TRANSPORT=tcp`,
[legacy/](legacy/README.md)).

## Docs

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — bridge v13 lifecycle, context map, state files, env knobs.
- **[AGENTS.md](AGENTS.md)** — agent integration guide, VW2026 API gotchas + renames, knowledge index.
- **[docs/TOOL_COVERAGE.md](docs/TOOL_COVERAGE.md)** — full command-sweep coverage report.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — API expansion plan.
- **[docs/MIGRATION_fastmcp3.md](docs/MIGRATION_fastmcp3.md)** — bundled→standalone fastmcp migration.

## License

MIT
