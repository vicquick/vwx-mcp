@echo off
rem Starts the bridge watchdog HIDDEN (no console window). It logs to
rem %APPDATA%\Nemetschek\Vectorworks\2026\Plug-ins\VW-MCP\watchdog.log.
rem A second start is a no-op (single-instance mutex).
rem For a visible console use vwx-watchdog-visible.bat.
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0vwx_watchdog.ps1"
