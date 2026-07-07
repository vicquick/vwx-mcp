@echo off
REM Deploy the VWX Bridge native plugin (palette). Vectorworks must be CLOSED
REM (the .vlb is DLL-locked while it runs). Self-elevates for the Program Files
REM copy. VW keeps ~5 windowless child processes alive for a few seconds after
REM the window closes — this script WAITS for them instead of aborting.

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator rights...
  powershell -NoProfile -Command "Start-Process '%~f0' -Verb RunAs"
  exit /b
)

set "SRC=C:\Users\victor.budinic\Documents\vwx-mcp-work\vwx-mcp\native\Output\Release"
set "DST=C:\Program Files\Vectorworks 2026\Plug-ins"

echo Waiting for Vectorworks to exit completely (up to 60 s)...
set /a tries=0
:waitloop
tasklist /FI "IMAGENAME eq Vectorworks2026.exe" | find /I "Vectorworks2026.exe" >nul
if %errorlevel% neq 0 goto :vwclosed
set /a tries+=1
if %tries% geq 30 (
  echo.
  echo   Vectorworks is STILL RUNNING after 60 s. Close it completely
  echo   ^(check Task Manager for lingering Vectorworks2026.exe^) and rerun.
  echo.
  pause
  exit /b 1
)
timeout /t 2 /nobreak >nul
goto :waitloop

:vwclosed
echo Vectorworks is closed. Deploying...
echo.
echo Source build:
for %%F in ("%SRC%\VwxBridge.vlb") do echo   %%~tF  %%~zF bytes  %%F

copy /Y "%SRC%\VwxBridge.vlb" "%DST%\" || goto :fail
copy /Y "%SRC%\VwxBridge.vwr" "%DST%\" || goto :fail

REM verify: deployed size must equal source size
for %%F in ("%SRC%\VwxBridge.vlb") do set "SRCSIZE=%%~zF"
for %%F in ("%DST%\VwxBridge.vlb") do set "DSTSIZE=%%~zF"
if not "%SRCSIZE%"=="%DSTSIZE%" (
  echo.
  echo   VERIFY FAILED: deployed size %DSTSIZE% ^!= source size %SRCSIZE%.
  echo.
  pause
  exit /b 1
)

echo.
echo   DEPLOYED + VERIFIED (%DSTSIZE% bytes).
echo   Reopen Vectorworks, then show the VWX Bridge palette
echo   (Extras menu -^> "VWX Bridge Palette anzeigen"). The palette self-pumps:
echo   no watchdog, no hotkey, no focus needed.
echo.
pause
exit /b 0

:fail
echo.
echo   COPY FAILED (error %errorlevel%). Is Vectorworks really closed?
echo   (The .vlb stays DLL-locked until every Vectorworks2026.exe is gone.)
echo.
pause
exit /b 1
