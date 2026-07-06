# Bridge v3 — self-healing setup

The bridge dies whenever a Marionette network executes (VW tears down the
Python script context on frame return) and stalls whenever a modal error
dialog opens. Both are unfixable from inside VW. v3 fixes them from outside:

```
┌───────────────── Vectorworks ─────────────────┐
│ vwx_mcp_bridge.py v3                          │
│  • bridge.hb   heartbeat file (every ~2s)     │
│  • bridge.gen  generation token (new instance │
│    wins; zombie threads release the port)     │
│  • bind-retry on start (no manual cleanup)    │
└──────────────▲────────────────────────────────┘
               │ Ctrl+Shift+B (restart hotkey)
┌──────────────┴────────────────────────────────┐
│ vwx_watchdog.ps1 (outside VW, every 3s)       │
│  • dismisses Marionette/Traceback error       │
│    dialogs (content-matched only)             │
│  • hb stale + port dead → closes zombie       │
│    bridge dialog, sends restart hotkey        │
│  • hb stale + port open → VW busy, no action  │
└───────────────────────────────────────────────┘
```

## One-time setup

1. **Menu command** (enables auto-restart):
   - Extras > Plug-ins > Plug-in-Manager > Benutzerdefiniert > Neu...
   - Typ **Menübefehl**, Sprache **Python**, Name `VWX Bridge Start`
   - Skript bearbeiten → paste `BridgeStart_MenuCommand.py`
   - Extras > Arbeitsumgebungen > Anpassen → drag the command into a menu
     (e.g. Extras) and assign the shortcut **Ctrl+Shift+B**
   - Restart VW once so the workspace change is live
2. **Watchdog**: run `vwx-watchdog.bat` (or add it to shell:startup)

## Behavior

- Marionette error dialogs are auto-dismissed within ~3s, so mass executions
  run through without babysitting.
- After a Marionette execution kills the bridge, the watchdog notices within
  ~15s and restarts it via the hotkey. No zombie-dialog hunting.
- If VW is merely busy (long export, user dialog), the watchdog does nothing.

## Files

| File | Purpose |
|---|---|
| `vwx_watchdog.ps1` | the watchdog loop |
| `BridgeStart_MenuCommand.py` | script body for the VW menu command plugin |
| `%APPDATA%…\VW-MCP\bridge.hb` | heartbeat (epoch seconds) |
| `%APPDATA%…\VW-MCP\bridge.gen` | generation token |
| `%APPDATA%…\VW-MCP\watchdog.log` | watchdog log |
