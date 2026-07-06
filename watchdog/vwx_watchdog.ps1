# vwx_watchdog.ps1 — trigger + janitor for the VWX file-IPC pump (bridge v4).
#
# Since v4 there is NO persistent bridge inside Vectorworks: the MCP server
# writes job files, this watchdog fires the 'VWX Bridge Start' menu-command
# hotkey (Ctrl+Shift+B), and vwx_pump.py drains the queue in a short-lived
# script context. Vectorworks stays responsive for the user except while a
# command actually executes.
#
# Duties:
#   1. jobs dir non-empty  -> send the pump hotkey to VW (rate-limited)
#   2. dismiss Marionette/Traceback error dialogs (content-matched, so a
#      dialog the user is actually working in is never touched)
#   3. hold triggers while a foreign modal dialog is open (the keystroke
#      would land in the dialog and the pump could not run anyway)
#
# ONE-TIME VW SETUP:
#   1. Extras > Plug-ins > Plug-in-Manager > Eigene Plug-ins > Neu...
#      -> Menübefehl (Python), Name: "VWX Bridge Start"
#   2. Code... -> paste watchdog/BridgeStart_MenuCommand.py (runs vwx_pump)
#   3. Extras > Arbeitsumgebungen > Anpassen -> add to a menu, hotkey
#      Ctrl+Shift+B. Restart VW once.
#
# Run hidden: vwx-watchdog.bat   (logs to <PluginDir>\watchdog.log)
# Stop:       close via Task-Manager or schtasks; single-instance mutex.

param(
    [string]$PluginDir = "$env:APPDATA\Nemetschek\Vectorworks\2026\Plug-ins\VW-MCP",
    [int]$PollMs = 250,
    [string]$ProcessName = "Vectorworks2026"
)

$JobsDir  = Join-Path $PluginDir 'ipc\jobs'
$LogFile  = Join-Path $PluginDir 'watchdog.log'
New-Item -ItemType Directory -Force -Path $JobsDir | Out-Null

# Single instance — a second watchdog would double-fire hotkeys.
$script:WdMutex = New-Object System.Threading.Mutex($false, 'Global\VwxBridgeWatchdog')
if (-not $script:WdMutex.WaitOne(0)) {
    Write-Host 'vwx watchdog already running — exiting.'
    exit 0
}

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
    [DllImport("user32.dll")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    delegate bool EnumProc(IntPtr h, IntPtr lp);

    const uint WM_GETTEXT = 0x000D;
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
    public static bool IsForeground(IntPtr h) { return GetForegroundWindow() == h; }
    // Ctrl+Shift+B — must match the hotkey assigned to "VWX Bridge Start"
    public static void SendPumpHotkey(IntPtr mainWin) {
        if (!IsForeground(mainWin)) {
            SetForegroundWindow(mainWin);
            System.Threading.Thread.Sleep(150);
        }
        keybd_event(0x11, 0, 0, UIntPtr.Zero);        // Ctrl down
        keybd_event(0x10, 0, 0, UIntPtr.Zero);        // Shift down
        keybd_event(0x42, 0, 0, UIntPtr.Zero);        // B down
        System.Threading.Thread.Sleep(40);
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

# Only dialogs whose CONTENT matches these are auto-dismissed.
$BadContent = 'Marionette|Traceback|Python Script Error'
$DismissButtons = @('Schließen', 'OK', 'Close')

Write-WdLog "watchdog v4 start: jobs=$JobsDir poll=${PollMs}ms"
$lastTrigger = [DateTime]::MinValue

while ($true) {
    Start-Sleep -Milliseconds $PollMs
    $vw = Get-Process $ProcessName -ErrorAction SilentlyContinue |
          Where-Object { $_.MainWindowTitle -ne '' } | Select-Object -First 1
    if (-not $vw) { continue }
    $vwpid = [uint32]$vw.Id

    # 1. Dismiss known error dialogs; note whether a FOREIGN dialog is open.
    $foreignDialog = $false
    foreach ($dlg in [VwWd]::Dialogs($vwpid)) {
        $content = [VwWd]::AllKidText($dlg)
        if ($content -match $BadContent) {
            foreach ($btn in $DismissButtons) {
                if ([VwWd]::ClickByText($dlg, $btn)) {
                    Write-WdLog "dismissed error dialog '$([VwWd]::Title($dlg))' via '$btn'"
                    break
                }
            }
        } else {
            $foreignDialog = $true      # user dialog — don't type into it
        }
    }

    # 2. Jobs waiting -> fire the pump hotkey (unless a user dialog is open).
    #    Skipped while the NATIVE palette pump is alive (pump.stamp fresh):
    #    the palette drains jobs itself; the keystroke is only the fallback.
    $jobs = @(Get-ChildItem $JobsDir -Filter '*.json' -ErrorAction SilentlyContinue)
    if ($jobs.Count -eq 0) { continue }
    if ($foreignDialog) { continue }    # pump can't run now; server will wait
    $alive = Join-Path $PluginDir 'ipc\native.alive'
    try {
        $age = [double][DateTimeOffset]::UtcNow.ToUnixTimeSeconds() - [double](Get-Content $alive -ErrorAction Stop)
        if ($age -lt 15) { continue }   # native palette active — no keystroke needed
    } catch {}
    if (((Get-Date) - $lastTrigger).TotalMilliseconds -lt 400) { continue }
    $main = [VwWd]::MainWindow($vwpid)
    if ($main -eq [IntPtr]::Zero) { continue }
    [VwWd]::SendPumpHotkey($main)
    $lastTrigger = Get-Date
}
