# vwx_watchdog.ps1 — external self-healing watchdog for the VWX MCP bridge.
#
# The bridge (vwx_mcp_bridge.py) runs inside Vectorworks as a dialog-timer
# pump. Two failure modes are unfixable from inside VW:
#   1. Marionette execution tears down the Python script context on frame
#      return -> bridge threads + timer die, port goes dead.
#   2. A modal error dialog (e.g. "Marionette" execution errors) suspends the
#      dialog timer -> pump stalls, requests time out.
# This watchdog fixes both from the outside:
#   - dismisses known Marionette/script error dialogs (content-matched, so a
#     dialog the user is actually working in is never touched)
#   - when the bridge context died (heartbeat stale AND port dead), closes the
#     zombie "VW MCP Bridge" dialog and sends the restart hotkey to VW.
#
# ONE-TIME VW SETUP (required for auto-restart):
#   1. Extras > Plug-ins > Plug-in-Manager > Benutzerdefiniert > Neu...
#      -> Menübefehl (Python), Name: "VWX Bridge Start"
#   2. Skript bearbeiten -> paste plugin/BridgeStart_MenuCommand.py
#   3. Extras > Arbeitsumgebungen > Anpassen -> add the command to a menu
#      and assign the hotkey Ctrl+Shift+B.
# Without the hotkey the watchdog still dismisses error dialogs; it just
# cannot restart the bridge (it will log "restart needed").
#
# Run:  powershell -ExecutionPolicy Bypass -File vwx_watchdog.ps1
# Stop: Ctrl+C (or close the window)

param(
    [int]$Port = 9878,
    [string]$PluginDir = "$env:APPDATA\Nemetschek\Vectorworks\2026\Plug-ins\VW-MCP",
    [int]$StaleSeconds = 15,
    [int]$PollSeconds = 3,
    [string]$ProcessName = "Vectorworks2026"
)

$HbFile  = Join-Path $PluginDir 'bridge.hb'
$LogFile = Join-Path $PluginDir 'watchdog.log'

Add-Type @"
using System;
using System.Text;
using System.Runtime.InteropServices;
using System.Collections.Generic;
public class VwWd {
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, EnumProc cb, IntPtr lp);
    [DllImport("user32.dll")] static extern int GetWindowText(IntPtr h, StringBuilder sb, int max);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, IntPtr lp);
    [DllImport("user32.dll", CharSet=CharSet.Auto)] static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr wp, StringBuilder lp);
    [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder sb, int max);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    delegate bool EnumProc(IntPtr h, IntPtr lp);

    const uint WM_GETTEXT = 0x000D;
    const uint WM_CLOSE   = 0x0010;
    const uint BM_CLICK   = 0x00F5;
    const uint KEYUP      = 0x0002;

    public static List<IntPtr> Dialogs(uint pid) {
        var res = new List<IntPtr>();
        EnumWindows((h, lp) => {
            uint p; GetWindowThreadProcessId(h, out p);
            if (p != pid || !IsWindowVisible(h)) return true;
            var cls = new StringBuilder(64); GetClassName(h, cls, 64);
            if (cls.ToString() == "#32770") res.Add(h);
            return true;
        }, IntPtr.Zero);
        return res;
    }
    public static string Title(IntPtr h) {
        var t = new StringBuilder(256); GetWindowText(h, t, 256); return t.ToString();
    }
    public static string AllKidText(IntPtr dlg) {
        var sb = new StringBuilder();
        EnumChildWindows(dlg, (c, lp) => {
            var txt = new StringBuilder(2048);
            SendMessage(c, WM_GETTEXT, (IntPtr)2048, txt);
            if (txt.Length > 0) sb.AppendLine(txt.ToString());
            return true;
        }, IntPtr.Zero);
        return sb.ToString();
    }
    public static bool ClickByText(IntPtr dlg, string text) {
        IntPtr found = IntPtr.Zero;
        EnumChildWindows(dlg, (c, lp) => {
            var t = new StringBuilder(256); GetWindowText(c, t, 256);
            if (t.ToString() == text) { found = c; return false; }
            return true;
        }, IntPtr.Zero);
        if (found == IntPtr.Zero) return false;
        SendMessage(found, BM_CLICK, IntPtr.Zero, IntPtr.Zero);
        return true;
    }
    public static void CloseWindow(IntPtr h) {
        SendMessage(h, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
    }
    public static IntPtr MainWindow(uint pid) {
        IntPtr best = IntPtr.Zero; int bestLen = -1;
        EnumWindows((h, lp) => {
            uint p; GetWindowThreadProcessId(h, out p);
            if (p != pid || !IsWindowVisible(h)) return true;
            var t = new StringBuilder(256); GetWindowText(h, t, 256);
            var cls = new StringBuilder(64); GetClassName(h, cls, 64);
            if (cls.ToString() != "#32770" && t.Length > bestLen) { best = h; bestLen = t.Length; }
            return true;
        }, IntPtr.Zero);
        return best;
    }
    // Ctrl+Shift+B — must match the hotkey assigned to "VWX Bridge Start"
    public static void SendRestartHotkey(IntPtr mainWin) {
        SetForegroundWindow(mainWin);
        System.Threading.Thread.Sleep(400);
        keybd_event(0x11, 0, 0, UIntPtr.Zero);        // Ctrl down
        keybd_event(0x10, 0, 0, UIntPtr.Zero);        // Shift down
        keybd_event(0x42, 0, 0, UIntPtr.Zero);        // B down
        System.Threading.Thread.Sleep(60);
        keybd_event(0x42, 0, KEYUP, UIntPtr.Zero);
        keybd_event(0x10, 0, KEYUP, UIntPtr.Zero);
        keybd_event(0x11, 0, KEYUP, UIntPtr.Zero);
    }
}
"@

function Write-WdLog([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    try {
        if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) {
            Set-Content -Path $LogFile -Value $line -Encoding utf8
        } else {
            Add-Content -Path $LogFile -Value $line -Encoding utf8
        }
    } catch {}
}

function Test-BridgePort {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        if ($iar.AsyncWaitHandle.WaitOne(500)) { $client.EndConnect($iar); return $true }
        return $false
    } catch { return $false } finally { $client.Close() }
}

function Get-HbAge {
    try {
        $epoch = [double](Get-Content $HbFile -TotalCount 1)
        $now = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        return $now - $epoch
    } catch { return 99999 }
}

# Only dialogs whose CONTENT matches these are auto-dismissed. Never touch a
# dialog the user is working in.
$BadContent = 'Marionette|Traceback|Python Script Error'
$DismissButtons = @('Schließen', 'OK', 'Close')

Write-WdLog "watchdog start: port=$Port stale=${StaleSeconds}s poll=${PollSeconds}s hb=$HbFile"
$lastRestart = [DateTime]::MinValue

while ($true) {
    Start-Sleep -Seconds $PollSeconds
    $vw = Get-Process $ProcessName -ErrorAction SilentlyContinue |
          Where-Object { $_.MainWindowTitle -ne '' } | Select-Object -First 1
    if (-not $vw) { continue }
    $vwpid = [uint32]$vw.Id

    # 1. Dismiss known error dialogs (content-matched)
    foreach ($dlg in [VwWd]::Dialogs($vwpid)) {
        $title = [VwWd]::Title($dlg)
        if ($title -eq 'VW MCP Bridge') { continue }
        $content = [VwWd]::AllKidText($dlg)
        if ($content -match $BadContent) {
            foreach ($btn in $DismissButtons) {
                if ([VwWd]::ClickByText($dlg, $btn)) {
                    Write-WdLog "dismissed error dialog '$title' via '$btn'"
                    break
                }
            }
        }
    }

    # 2. Health check
    $age = Get-HbAge
    if ($age -lt $StaleSeconds) { continue }
    $portUp = Test-BridgePort

    if ($portUp) {
        # Main thread blocked (long op or a dialog we don't recognize) — the
        # dismiss pass above already handled known ones. Log only.
        if ($age -gt 120) { Write-WdLog "heartbeat stale ${age}s but port open — VW busy (no action)" }
        continue
    }

    # 3. Context died — restart (rate-limited to one attempt / 30s)
    if (((Get-Date) - $lastRestart).TotalSeconds -lt 30) { continue }
    $lastRestart = Get-Date
    Write-WdLog "bridge DEAD (hb ${age}s, port closed) — restarting"
    foreach ($dlg in [VwWd]::Dialogs($vwpid)) {
        if ([VwWd]::Title($dlg) -eq 'VW MCP Bridge') {
            Write-WdLog 'closing zombie bridge dialog'
            [VwWd]::CloseWindow($dlg)
            Start-Sleep -Seconds 2
        }
    }
    $main = [VwWd]::MainWindow($vwpid)
    if ($main -ne [IntPtr]::Zero) {
        [VwWd]::SendRestartHotkey($main)
        Write-WdLog 'sent Ctrl+Shift+B (VWX Bridge Start)'
        Start-Sleep -Seconds 8
        if (Test-BridgePort) { Write-WdLog 'bridge back UP' }
        else { Write-WdLog 'restart NOT confirmed — is the hotkey assigned to "VWX Bridge Start"?' }
    }
}
