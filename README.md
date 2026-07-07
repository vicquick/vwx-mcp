# vwx-mcp

**Vectorworks 2026 MCP server — 248 tools + a 3071-function `vs.*` knowledge index, true background control (bridge v13).**

Drive a live Vectorworks 2026 session from any MCP client (Claude Code, Claude
Desktop, …) **while you work in another app**: reads drain invisibly via VW's
OnIdle notification queue, writes reach VW through its own message queue — no
watchdog process, no focus juggling, structurally crash-proof.

> Building an agent against this server? Read **[AGENTS.md](AGENTS.md)** — it covers
> the three access layers, object addressing, toolset presets, and the VW2026
> API gotchas that will otherwise bite you.

## Architecture (Windows, bridge v13)

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
    │   + auto-dismisses VW error dialogs (content-matched) → never blocks
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

248 tools is a lot of context for a client to load. Set `VWX_TOOLSET` in
`bridge/vwx-mcp.bat` to expose only one workflow's tools via the fastmcp
Visibility API (tags live in `mcp-server/tool_tags.py`):

| `VWX_TOOLSET` | tools | for |
|---|---|---|
| `full` (default) | 248 | everything |
| `gis` | 101 | georef / layers / classes / appearance / export |
| `modeling` | 160 | 2D+3D draw / manipulate / BIM / symbols |
| `baumkataster` | 70 | tree register: plants / records / query / IO |
| `minimal` | 40 | document / query / escape hatch |

## Tools (248)

19 tag groups — counts in parentheses:

`bim` (31, incl. IFC deep: psets, bulk classification, walls, roofs) ·
`manipulate` (26, incl. polygon vertex editing) · `worksheets` (23, incl.
criteria-driven report generation) · `query` (21, incl. the criteria engine) ·
`draw3d` (19, incl. loft / shell / path-extrude / NURBS) · `draw2d` (18, incl.
surface booleans) · `appearance` (23, incl. textures + lights) · `layers` (11) ·
`document` (10, incl. doc-default styling) · `escape` (9, incl. `vs_signature`) ·
`geo` (7, incl. lat/lon ⇄ drawing conversion + EPSG georef) · `classes` (7) ·
`records` (7) · `viewports` (7) · `symbols` (6) · `landscape` (6, Baumkataster) ·
`io` (6) · `view` (6) · `site` (5)

Three access layers (see [AGENTS.md](AGENTS.md)):
1. **Explicit tools** — the 248 wrappers above.
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

## Knowledge index — scripts that run right the first time

`vwx-plugin/vs_index.json` holds the exact signature of all **3071** `vs.*`
functions (args, arity, return type, category, doc), built from the SDK stub by
`tools/build_vs_index.py`. The `vs_signature` tool looks them up; `commands.py`
validates arity before calling, turning would-be modal VW engine errors into
clean JSON errors. Rebuild after an SDK update and redeploy next to
`commands.py`.

## Known constraints

The VW UI stays responsive while the bridge idles and during the (typically
millisecond) command execution — reads are invisible, writes hop through VW's
own message queue, and VW error dialogs are auto-dismissed by the palette. What
remains, honestly:

- Every `vs.*` call runs on VW's main thread — a genuinely long operation
  blocks the UI for its duration (a 36-verb batch measures ~300 ms; the one
  known pathological call, `vs.CombineIntoSurface`, measured 215 s and is
  therefore quarantined behind `force:true`).
- Export/import verbs (`export_pdf`, `import_dwg`, …) open VW's own modal
  settings dialogs — the `vs` API has no headless path for them.
- The classic TCP dialog bridge remains for macOS/remote
  (`VWX_TRANSPORT=tcp`, [legacy/](legacy/README.md)).

## Docs

- **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — bridge v13 lifecycle, context map, state files, env knobs.
- **[AGENTS.md](AGENTS.md)** — agent integration guide, VW2026 API gotchas + renames, knowledge index.
- **[docs/TOOL_COVERAGE.md](docs/TOOL_COVERAGE.md)** — full command-sweep coverage report.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — API expansion plan.
- **[docs/MIGRATION_fastmcp3.md](docs/MIGRATION_fastmcp3.md)** — bundled→standalone fastmcp migration.

## License

MIT
