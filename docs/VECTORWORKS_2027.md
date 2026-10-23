# Vectorworks 2027 port

The last Vectorworks 2026 state is archived as branch `vw2026` and tag
`vw2026-final`. This branch (`vw2027`) targets Vectorworks 2027.

## What changed in 2027

| Area | 2026 | 2027 | Effect on the bridge |
|---|---|---|---|
| SDK | SDKVW(832364) | SDKVW(882699), `SDK_VERSION` 3200 | rebuild only; no source change needed to compile |
| Embedded Python | 3.9 | 3.14.5 | all plug-in files compile under 3.14 with `-W error`; no removed stdlib modules used |
| `vs.*` API | 3071 functions | 3098 functions | +28, −1, **no signature changes** among shared names |
| Build props | MFC implicit | `UseOfMfc=false`, `/D_DISABLE_STRING_ANNOTATION /D_DISABLE_VECTOR_ANNOTATION` | picked up through `NNA_PluginBuild_RELEASE.props` |
| Migration Manager | — | does not copy `Plug-ins\VW-MCP` or the bridge menu items; a `VWX Start, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit. Start.vsm` copied from 2026 appears in the menu but **never executes** | `install_user_plugin.ps1` copies VW-MCP; recreate the menu command and the menu entries by hand (the script prints the steps) |
| Palette docking | floating or docked | docked palettes fold into side tabs that report hidden on every fold | hiding no longer stops the bridge; use Pause |

Added functions: `CC_GetCableTypeData`, `CC_GetCircuitDest`, `CC_GetCircuitSource`,
`CC_GetConnectorData`, `CC_GetDevice`, `CC_GetEquipmentItem`, `CC_GetSignalData`,
`ExcludeFromDataVis`, `GetCableBreakData`, `GetCableBreaksCnt`, `GetCablePartsCnt`,
`GetCableSectionData`, `GetCableSectionsCnt`, `GetLBItemByClass`, `GetModifierFlags`,
`IFC_CustPsetFromRec`, `OLDDeleteLoad`, `OLDGetCountLoads`, `Plant_LocateStyleMgr`,
`Prot_GetAppMode`, `PtPerpLine3D`, `RefreshResManager`, `RemoveTrussAssoc`,
`SetCableBreakData`, `SetCableSectionData`, `SetLBItemByClass`, `SetModeButtonTextN`,
`ShowLBItemByClassOpt`. Removed: `Prot_GetLicenseType` (unused here).

The MSVC toolset the SDK pins (14.39 in `Toolset.props`, 14.42 in Vectorworks'
2027 developer notes) is not required: the project does not import
`Toolset.props`, and 14.44 (VS 2022 17.14) builds and links cleanly. v143
toolsets are binary compatible.

## Two years side by side

Every host-side component now tries **its own** year first: the native palette
from `SDK_VERSION`, the Python side from the embedded interpreter's path
(`...\Vectorworks <year>\Python314`). Without this, a 2026 and a 2027 running
together would both drain the newest year's queue. The MCP server still talks to
the newest year that has a `VW-MCP` folder; set `VWX_VW_VERSION=2026` on the
server to drive 2026 instead.

## Install

Vectorworks 2027 closed:

```powershell
$env:VWSDK2027 = "$HOME\Documents\vwx-sdk\2027\sdk\SDK\SDKVW(882699)"
& "${env:ProgramFiles(x86)}\Microsoft Visual Studio\2022\BuildTools\MSBuild\Current\Bin\MSBuild.exe" `
    native\VwxStart, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit.2027.vcxproj -p:Configuration=Release -p:Platform=x64
powershell -NoProfile -ExecutionPolicy Bypass -File bridge\install_user_plugin.ps1
bridge\deploy_native_bridge.bat
```

SDK download: `https://release.vectorworks.net/latest/Vectorworks/2027-NNA-eng-win-SDK.zip`.

`install_user_plugin.ps1` copies `vwx-plugin\*` to `%APPDATA%\...\2027\Plug-ins\VW-MCP`
and prints the manual steps: create the `VWX Start, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit. Start` Python menu command in the
2027 Plug-in Manager, and add the three bridge items to Extras with the Workspace Editor.
Both were automated first (copying the 2026 `.vsm`, writing `.vww` entries) and both
failed silently: the copied command never ran, the written entries were not linked.
`deploy_native_bridge.bat` copies `native\Output\2027\Release\VwxStart, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit..vlb/.vwr`
to `C:\Program Files\Vectorworks 2027\Plug-ins` (UAC). Then start Vectorworks
and open Extras → "VWX Start, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit. Palette anzeigen".

## Verified live (2026-10-23, VW2027 32.1.0)

Read jobs through the OnIdle notification; write jobs (`execute_script`,
`draw_rectangle`, `delete_object`) through the recreated menu command;
modal-dialog detection (Plug-in Manager, Script Editor). Still open: writes
with Vectorworks in the background.

## Start, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit. v15 trigger

Writes used to be triggered by pressing Ctrl+Shift+B: `keybd_event` when Vectorworks
was in front (global input, which hit an open menu and left a flyout stuck over the
drawing), a posted key with an edited key state when it was not. Neither reached
`VWX Start, 0)`
from a message-only window: the timer posts a private message, Vectorworks'
own message loop dispatches it, and the menu command runs by name, independent of
focus, shortcut and menu position. The menu command must exist under exactly that
name. `VWX_TRIGGER=keys` (set before Vectorworks starts) restores the keystrokes.
Each change of the `DoMenuName` return code is logged to `bridge.log`.

A docked palette folded into a 2027 side tab reports itself hidden; the bridge now
keeps running until Pause or quit. Start` on 2027. v15 calls `ISDK::DoMenuName(VWX
