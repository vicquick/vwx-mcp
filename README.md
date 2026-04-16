# vw-mcp

**Vectorworks 2026 MCP server — 116 tools, pure Python, no C++ compilation.**

Mirrors the QGIS MCP architecture: FastMCP HTTP bridge → TCP socket → Python plugin inside VW.

## Architecture

```
Claude Code
    │ streamable-http :8082
    ▼
mcp-server/vw_mcp_server.py   (FastMCP)
    │ TCP :9878, JSON newline-delimited
    ▼
vw-plugin/vw_mcp_bridge.py    (runs inside Vectorworks)
    ├── bg thread: socket I/O only
    └── main thread: vs.* dispatch via RegisterDialogForTimerEvents (100ms)
        ▼
Vectorworks 2026  (vs.* Python API — 3071 functions, 73 categories)
```

## Quick Start

1. Copy `vw-plugin/` to `%APPDATA%\Nemetschek\Vectorworks\2026\Plug-ins\VW-MCP\`
2. In VW: Scripts menu → Run Script → `vw_mcp_bridge.py`
3. Dialog shows "Active on :9878"
4. Run `bridge\vw-mcp.bat`
5. Add to metamcp: `http://localhost:8082/mcp`

## Tools (116)

Document, Layers, Classes, Object Query, Object Manipulation,
2D Drawing, 3D Drawing, Symbols, Appearance, Records, IFC/BIM,
Landscape/Plants, Viewports, Worksheets, Export/Import, View,
Script Execution (`execute_script` — arbitrary vs.* code)
