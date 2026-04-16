# vwx-mcp

**Vectorworks 2026 MCP server — 116 tools, pure Python, no C++ compilation.**

Mirrors the QGIS MCP architecture: FastMCP HTTP bridge → TCP socket → Python plugin inside Vectorworks.

## Architecture

```
Claude Code
    │ streamable-http :8082
    ▼
mcp-server/vw_mcp_server.py     (FastMCP)
    │ TCP :9878, JSON newline-delimited (persistent, multi-message)
    ▼
vwx-plugin/vw_mcp_bridge.py      (runs inside Vectorworks)
    ├── bg thread: socket I/O only (loops reading framed JSON)
    ├── queue:    cmd_queue / result_map / result_events
    └── main thread: vs.* dispatch via RegisterDialogForTimerEvents (100ms)
        ▼
Vectorworks 2026  (vs.* Python API — 3071 functions, 73 categories)
```

Dialog events observed on VW2026 (for reference): setup=12255, teardown=12256, timer=13028, cancel=2.

## Install

1. Copy `vwx-plugin/` contents to `%APPDATA%\Nemetschek\Vectorworks\2026\Plug-ins\VWX-MCP\`
2. Copy `mcp-server/vw_mcp_server.py` to `%USERPROFILE%\.local\share\vwx-mcp\`
3. Copy `bridge/vwx-mcp.bat` to `%USERPROFILE%\bridge\`
4. Ensure `python` on PATH. Install deps: `pip install mcp fastmcp`

## Run

1. Launch Vectorworks 2026
2. In VW: `File → Scripts → Run Script` → pick `vw_mcp_bridge.py` from plugin dir
3. Dialog shows **"Active — TCP :9878"** (keeps VW in modal event loop so main-thread pump works)
4. Outside VW: double-click `bridge\vwx-mcp.bat` → FastMCP listens on `http://127.0.0.1:8082/mcp`
5. Add MCP client config:
   ```json
   {
     "vwx-mcp": {
       "type": "http",
       "url": "http://127.0.0.1:8082/mcp"
     }
   }
   ```

Test with the `ping` tool.

## Known constraint

Bridge dialog is **modal** — blocks VW UI while active. Click **Stop** to reclaim VW. VW Python has no non-modal main-thread timer; this is a platform limitation, not a design choice.

## Tools (116)

Document · Layers · Classes · Object Query · Object Manipulation · 2D Drawing · 3D Drawing · Symbols · Appearance · Records · IFC/BIM · Architectural (walls/spaces) · Landscape/Plants · Site Model · Viewports · Worksheets · Export/Import · View · GIS · Textures · Script Execution (`execute_script` — arbitrary `vs.*` code)

## Escape hatch

`execute_script` runs any Python inside VW on the main thread. Set `__result__` to return a value. Use when no explicit tool exists.

## License

MIT
