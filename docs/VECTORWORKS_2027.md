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
| Migration Manager | — | does not copy `Plug-ins\VW-MCP`, `VWX Bridge Start.vsm`, or the bridge menu items | `bridge/install_user_plugin.ps1` puts them back |

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
    native\VwxBridge2027.vcxproj -p:Configuration=Release -p:Platform=x64
powershell -NoProfile -ExecutionPolicy Bypass -File bridge\install_user_plugin.ps1
bridge\deploy_native_bridge.bat
```

SDK download: `https://release.vectorworks.net/latest/Vectorworks/2027-NNA-eng-win-SDK.zip`.

`install_user_plugin.ps1` copies `vwx-plugin\*` to `%APPDATA%\...\2027\Plug-ins\VW-MCP`,
carries `VWX Bridge Start.vsm` over from 2026, and adds the three bridge items
back to the Extras menu of every 2027 workspace (backup `*.vww.pre-vwx`).
`deploy_native_bridge.bat` copies `native\Output\2027\Release\VwxBridge.vlb/.vwr`
to `C:\Program Files\Vectorworks 2027\Plug-ins` (UAC). Then start Vectorworks
and open Extras → "VWX Bridge Palette anzeigen".

## Not yet verified live

The bridge contexts (OnIdle-notification reads, background accelerator writes,
error-dialog dismissal) were mapped on 2026. They must be re-checked on 2027
before production use: ping, a read, a write, an error dialog, background mode
with Vectorworks unfocused.
