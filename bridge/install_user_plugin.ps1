<#
Install the user-side half of the VWX bridge for one Vectorworks year:

  1. vwx-plugin\*  ->  %APPDATA%\Nemetschek\Vectorworks\<year>\Plug-ins\VW-MCP\
  2. "VWX Bridge Start.vsm" (the Python menu command, Ctrl+Shift+B) carried over
     from an older year if the target has none. A new major version's
     Migration Manager does not copy it, nor the VW-MCP folder.
  3. Workspaces: the three bridge menu items put back into the Extras menu
     ("VWX Bridge Palette anzeigen", "VWX Bridge Start" with Ctrl+Shift+B,
     "VWX Pump"). Migration drops them, because at migration time the native
     plug-in and the .vsm are not installed yet.

The native half (VwxBridge.vlb/.vwr into Program Files) is
deploy_native_bridge.bat. Run this script with Vectorworks CLOSED: Vectorworks
rewrites its workspace on quit, so a patch made while it runs is lost.

Usage:  powershell -NoProfile -ExecutionPolicy Bypass -File bridge\install_user_plugin.ps1 [-Year 2027]
#>
param([string]$Year = $env:VWX_VW_VERSION)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$vwRoot = Join-Path $env:APPDATA 'Nemetschek\Vectorworks'

if (-not $Year) {
    $Year = Get-ChildItem 'C:\Program Files' -Directory -Filter 'Vectorworks *' |
        ForEach-Object { ($_.Name -split ' ')[1] } |
        Where-Object { $_ -match '^\d{4}$' } | Sort-Object -Descending | Select-Object -First 1
}
if (-not $Year) { throw 'No Vectorworks install found; pass -Year.' }
Write-Host "Target: Vectorworks $Year"

if (Get-Process -Name "Vectorworks$Year" -ErrorAction SilentlyContinue) {
    throw "Vectorworks $Year is running. Close it (it rewrites the workspace on quit) and rerun."
}

$plugins = Join-Path $vwRoot "$Year\Plug-ins"
if (-not (Test-Path $plugins)) { throw "$plugins does not exist. Start Vectorworks $Year once first." }

# 1. Python side
$dst = Join-Path $plugins 'VW-MCP'
New-Item -ItemType Directory -Force $dst | Out-Null
foreach ($f in 'commands.py', 'vwx_pump.py', 'vwx_mcp_bridge.py', 'vs_index.json', 'BridgeStart_MenuCommand.py') {
    Copy-Item (Join-Path $repo "vwx-plugin\$f") $dst -Force
    Write-Host "  VW-MCP\$f"
}

# 2. Python menu command
$vsm = Join-Path $plugins 'VWX Bridge Start.vsm'
if (Test-Path $vsm) {
    Write-Host '  VWX Bridge Start.vsm already present'
} else {
    $src = Get-ChildItem $vwRoot -Directory | Where-Object { $_.Name -match '^\d{4}$' -and $_.Name -lt $Year } |
        Sort-Object Name -Descending |
        ForEach-Object { Join-Path $_.FullName 'Plug-ins\VWX Bridge Start.vsm' } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($src) {
        Copy-Item $src $vsm
        Write-Host "  VWX Bridge Start.vsm  <- $src"
    } else {
        Write-Warning ('No older "VWX Bridge Start.vsm" found. Create it in the Plug-in Manager: ' +
            'new Python menu command "VWX Bridge Start", script = vwx-plugin\BridgeStart_MenuCommand.py.')
    }
}

# 3. Workspaces
$item = 'net.vectorworks.workspaceitem.menuitem.'
$anchor = 'BluebeamConnect'
$names = 'ExtMenuShowVwxBridge', 'VWX_x20Bridge_x20Start', 'ExtMenuVwxPump'
$wsDirs = 'Arbeitsumgebungen', 'Workspaces' | ForEach-Object { Join-Path $vwRoot "$Year\$_" } | Where-Object { Test-Path $_ }
foreach ($ws in Get-ChildItem $wsDirs -Filter *.vww | Where-Object { $_.Name -notlike '*_backup*' }) {
    $text = [IO.File]::ReadAllText($ws.FullName)
    if ($text.Contains("$item$($names[0])")) {
        Write-Host "  $($ws.Name): bridge menu items already present"
        continue
    }
    $ref = "<$item$anchor/>"
    $def = "</$item$anchor>"
    if (-not ($text.Contains($ref) -and $text.Contains($def))) {
        Write-Warning "$($ws.Name): no '$anchor' anchor in Extras; add the three items with the Workspace Editor."
        continue
    }
    $nl = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
    $refIndent = ([regex]::Match($text, "([ \t]*)" + [regex]::Escape($ref))).Groups[1].Value
    $defIndent = ([regex]::Match($text, "([ \t]*)" + [regex]::Escape($def))).Groups[1].Value
    $refs = ($names | ForEach-Object { "$nl$refIndent<$item$_/>" }) -join ''
    $defs = ($names | ForEach-Object {
        $shortcut = if ($_ -eq 'VWX_x20Bridge_x20Start') { "$nl$defIndent  <Shortcut Key=""B"" Modifiers=""3""/>" } else { '' }
        "$nl$defIndent<$item$_>$nl$defIndent  <ResourceManagerID>0</ResourceManagerID>" +
        "$nl$defIndent  <UniversalName>$_</UniversalName>$shortcut$nl$defIndent</$item$_>"
    }) -join ''
    $i = $text.IndexOf($ref) + $ref.Length
    $text = $text.Insert($i, $refs)
    $j = $text.IndexOf($def) + $def.Length
    $text = $text.Insert($j, $defs)
    Copy-Item $ws.FullName "$($ws.FullName).pre-vwx" -Force
    [IO.File]::WriteAllText($ws.FullName, $text, (New-Object Text.UTF8Encoding($false)))
    Write-Host "  $($ws.Name): bridge menu items added to Extras (backup .pre-vwx)"
}

Write-Host ''
Write-Host "Done. Next: bridge\deploy_native_bridge.bat (native palette, UAC), then start Vectorworks $Year"
Write-Host 'and open Extras -> "VWX Bridge Palette anzeigen".'
