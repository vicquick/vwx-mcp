# vwx-mcp

**Vectorworks 2026 MCP server — 150 tools, pure Python, no C++ compilation.**

Drive a live Vectorworks 2026 session from any MCP client (Claude Code, Claude
Desktop, …). FastMCP HTTP server → TCP socket → Python plugin running inside
Vectorworks → the `vs.*` API.

> Building an agent against this server? Read **[AGENTS.md](AGENTS.md)** — it covers
> the three access layers, object addressing, toolset presets, and the VW2026
> API gotchas that will otherwise bite you.

## Architecture

```
MCP client (Claude Code / Desktop)
    │ streamable-http :8082
    ▼
mcp-server/vwx_mcp_server.py     (standalone fastmcp 3.x)
    │ TCP :9878, JSON newline-delimited (persistent, multi-message)
    ▼
vwx-plugin/vwx_mcp_bridge.py     (runs inside Vectorworks)
    ├── bg thread: socket I/O only (loops reading framed JSON)
    ├── queue:    cmd_queue / result_map / result_events
    └── main thread: vs.* dispatch via RegisterDialogForTimerEvents (100ms)
        │  (dispatches to vwx-plugin/commands.py — hot-reloads per call)
        ▼
Vectorworks 2026  (vs.* Python API — 3071 functions, 73 categories)
```

The VW-side bridge is **mandatory** — it is the only path that can run `vs.*`
against the live document. The MCP server is a thin socket proxy in front of it.

## Install

1. Copy `vwx-plugin/` contents to `%APPDATA%\Nemetschek\Vectorworks\2026\Plug-ins\VWX-MCP\`
   (the bridge also accepts the legacy folder name `VW-MCP`).
2. Copy `mcp-server/vwx_mcp_server.py`, `mcp-server/tool_tags.py`, and
   `mcp-server/requirements.txt` to `%USERPROFILE%\.local\share\vwx-mcp\`
3. Copy `bridge/vwx-mcp.bat` to `%USERPROFILE%\bridge\`
4. Ensure `python` is on PATH. **No manual pip install needed** — the launcher
   creates a venv and installs `requirements.txt` (`fastmcp==3.4.2`) on first run.

## Run

1. Launch Vectorworks 2026.
2. In VW: `File → Scripts → Run Script` → pick `vwx_mcp_bridge.py` from the plugin dir.
3. Dialog shows **"Active — TCP :9878"** (keeps VW in a modal event loop so the
   main-thread pump works).
4. Outside VW: double-click `bridge\vwx-mcp.bat`. First run bootstraps the venv,
   then FastMCP listens on `http://127.0.0.1:8082/mcp`.
5. Add the MCP client config:
   ```json
   {
     "vwx-mcp": {
       "type": "http",
       "url": "http://127.0.0.1:8082/mcp"
     }
   }
   ```

Test with the `ping` tool → `{"status":"ok","message":"VW MCP Bridge running"}`.

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

The bridge dialog is **modal** — it blocks the VW UI while active. Click **Stop**
to reclaim VW. VW Python has no non-modal main-thread timer; this is a platform
limitation, not a design choice.

## Docs

- **[AGENTS.md](AGENTS.md)** — agent integration guide + VW2026 API gotchas.
- **[docs/ROADMAP.md](docs/ROADMAP.md)** — API expansion plan (→ ~225 tools).
- **[docs/MIGRATION_fastmcp3.md](docs/MIGRATION_fastmcp3.md)** — bundled→standalone fastmcp migration.

## License

MIT
