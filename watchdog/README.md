# Bridge v4 — file-IPC pump setup (Windows)

Full design: [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
Cross-platform/macOS classic bridge: [`legacy/README.md`](../legacy/README.md).

**v4 = no resident bridge.** The MCP server writes job files, this watchdog
fires the pump hotkey, `vwx_pump.py` executes and returns. Vectorworks stays
responsive for the user whenever no command is executing.

## One-time setup

1. **Menu command**:
   - Extras > Plug-ins > Plug-in-Manager > Eigene Plug-ins > Neu...
   - Typ **Menübefehl**, Sprache **Python**, Name `VWX Bridge Start`
   - **Code...** → paste `BridgeStart_MenuCommand.py` (imports `vwx_pump`)
   - Extras > Arbeitsumgebungen > Anpassen → drag the command into a menu,
     shortcut **Ctrl+Shift+B** → restart VW once
2. **Watchdog**: run `vwx-watchdog.bat` — starts HIDDEN, logs to
   `%APPDATA%…\VW-MCP\watchdog.log`; `vwx-watchdog-visible.bat` for a console.
   Single-instance mutex — double starts are no-ops.
   Autostart at logon (no admin needed):
   ```
   schtasks /Create /F /SC ONLOGON /TN VwxBridgeWatchdog /TR "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Users\<you>\bridge\vwx_watchdog.ps1"
   ```
   (remove with `schtasks /Delete /TN VwxBridgeWatchdog /F`)
3. Server: `bridge/vwx-mcp.bat` as before (`VWX_TRANSPORT=file` is the
   Windows default).

## Behavior

- Tool call → job file → hotkey → pump → result. Overhead ≈ 300–500 ms.
- VW UI is **usable while the system idles armed** — no bridge dialog.
- Marionette error dialogs auto-dismissed within ~250 ms; Marionette context
  teardowns are harmless (each pump is its own context).
- If a user-opened modal dialog is up, triggers are held (jobs wait) — close
  the dialog and the queue drains.
- The hotkey briefly focuses Vectorworks if it wasn't the foreground window.

## Files

| File | Purpose |
|---|---|
| `vwx_watchdog.ps1` | trigger + error-dialog janitor |
| `BridgeStart_MenuCommand.py` | script body for the VW menu command |
| `vwx-watchdog.bat` / `-visible.bat` | hidden / console launcher |
