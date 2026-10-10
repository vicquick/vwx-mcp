"""Öffnet am ausgewählten Belag (Hardscape) „Oberflächenmodifikatoren bearbeiten".

Vectorworks legt die Modifikatoren-Gruppe eines angepassten Belags erst an, wenn
man diesen Bearbeitungsmodus einmal betritt; per vs.* gibt es dafür keinen
Befehl (EditObjectSpecial 0 = Pfad, 3 = Umformen). Dieses Skript läuft
AUSSERHALB von Vectorworks und klickt das Kontextmenü wie ein Mensch:
Rechtsklick in die Mitte der Zeichenfläche → Ende → eins hoch → Enter
(„Oberflächenmodifikatoren bearbeiten" ist bei einem ANGEPASSTEN Belag der
vorletzte Eintrag, verifiziert VW2026 DE, 10.10.2026). Verfehlt der Klick die
Fläche, erscheint „Plangröße…" — das Skript bricht den Dialog ab (Rückgabe 2).

Ablauf mit der Bridge:
    vwx hardscape_prepare_modifiers {object_id}   # angepasst, auswählen, zoomen
    python tools/belag_modifikatoren_oeffnen.py    # dieses Skript
    vwx hardscape_modifier_info {object_id}       # container: true?
Danach den Modus verlassen (Knopf oben rechts in der Zeichenfläche oder
--verlassen). Die Gruppe bleibt; weitere Gefälle legt hardscape_add_grade ohne
Bearbeitungsmodus hinein.

Nur ctypes, keine Abhängigkeiten. Der Belag muss allein ausgewählt und in der
Mitte der Zeichenfläche sichtbar sein (hardscape_prepare_modifiers sorgt dafür);
bei L-förmigen Belägen kann die Mitte daneben liegen — dann Ansicht anpassen.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    u32.SetProcessDPIAware()

VK_END, VK_UP, VK_RETURN, VK_ESCAPE = 0x23, 0x26, 0x0D, 0x1B
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x02, 0x04
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x08, 0x10


def vw_frame():
    """Größtes sichtbares, nicht-besessenes Fenster eines Vectorworks-Prozesses."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if not u32.IsWindowVisible(h) or u32.GetWindow(h, 4):
            return True
        pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(pid))
        ph = k32.OpenProcess(0x1000, False, pid.value)
        if not ph:
            return True
        try:
            size = wintypes.DWORD(260)
            name = ctypes.create_unicode_buffer(260)
            if not k32.QueryFullProcessImageNameW(ph, 0, name, ctypes.byref(size)):
                return True
            exe = os.path.basename(name.value)
            if not exe.startswith('Vectorworks') or 'Cloud' in exe or 'error' in exe:
                return True
        finally:
            k32.CloseHandle(ph)
        r = wintypes.RECT()
        u32.GetWindowRect(h, ctypes.byref(r))
        found.append(((r.right - r.left) * (r.bottom - r.top), h))
        return True

    u32.EnumWindows(cb, 0)
    return max(found)[1] if found else None


def canvas_rect(frame):
    """Größtes sichtbares Kind im MDIClient = Zeichenfläche des aktiven Dokuments."""
    mdi = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def find_mdi(h, _):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, cls, 64)
        if cls.value == 'MDIClient' and u32.IsWindowVisible(h):
            mdi.append(h)
            return False
        return True

    u32.EnumChildWindows(frame, find_mdi, 0)
    if not mdi:
        return None
    best = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def find_view(h, _):
        if u32.IsWindowVisible(h):
            r = wintypes.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            best.append(((r.right - r.left) * (r.bottom - r.top), (r.left, r.top, r.right, r.bottom)))
        return True

    u32.EnumChildWindows(mdi[0], find_view, 0)
    return max(best)[1] if best else None


def key(vk):
    u32.keybd_event(vk, 0, 0, 0)
    u32.keybd_event(vk, 0, 2, 0)
    time.sleep(0.25)


def click(x, y, right=False):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.1)
    down, up = (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP) if right else (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)
    u32.mouse_event(down, 0, 0, 0, 0)
    u32.mouse_event(up, 0, 0, 0, 0)


def main():
    frame = vw_frame()
    if not frame:
        print('Vectorworks-Fenster nicht gefunden'); return 1
    rc = canvas_rect(frame)
    if not rc:
        print('Zeichenfläche nicht gefunden'); return 1
    # Windows verweigert SetForegroundWindow aus einem Hintergrundprozess;
    # ein kurzer Alt-Tastendruck hebt die Sperre auf (wie pywinauto).
    u32.keybd_event(0x12, 0, 0, 0)
    u32.keybd_event(0x12, 0, 2, 0)
    u32.ShowWindow(frame, 9) if u32.IsIconic(frame) else None
    u32.SetForegroundWindow(frame)
    time.sleep(0.4)
    if u32.GetForegroundWindow() != frame:
        print('Vectorworks ließ sich nicht nach vorn holen'); return 1
    cx, cy = (rc[0] + rc[2]) / 2, (rc[1] + rc[3]) / 2
    if '--verlassen' in sys.argv:
        # Knopf „… verlassen" sitzt oben rechts in der Zeichenfläche
        bx = rc[2] - float(os.environ.get('VWX_EXIT_DX', '340'))
        by = rc[1] + float(os.environ.get('VWX_EXIT_DY', '88'))
        click(bx, by)
        print('verlassen geklickt bei', int(bx), int(by)); return 0
    click(cx, cy, right=True)
    time.sleep(0.9)
    # VW-Kontextmenüs kennen keine Buchstabennavigation („O" schließt das Menü).
    # Bei einem ANGEPASSTEN Belag ist „Oberflächenmodifikatoren bearbeiten" der
    # vorletzte Eintrag (danach „Eigenschaften…"). Bei einem nicht angepassten
    # trifft dieselbe Folge „In Weg umwandeln" (Fehlerdialog, nichts geändert) —
    # deshalb vorher hardscape_prepare_modifiers (prüft/setzt Config=Aligned).
    key(VK_END)
    key(VK_UP)
    key(VK_RETURN)
    time.sleep(1.2)
    # Rechtsklick neben die Belagfläche öffnet das Menü der Zeichenfläche; dessen
    # vorletzter Eintrag ist „Plangröße…" (modal). Einen solchen Dialog abbrechen.
    dlg = modal_dialog(frame)
    if dlg:
        u32.SetForegroundWindow(dlg)
        time.sleep(0.2)
        key(VK_ESCAPE)
        print('Fläche verfehlt (Dialog %r abgebrochen) — Ansicht so legen, dass die '
              'Mitte der Zeichenfläche auf dem Belag liegt' % window_text(dlg))
        return 2
    print('Kontextmenü bedient bei', int(cx), int(cy))
    return 0


def window_text(h):
    n = u32.GetWindowTextLengthW(h)
    buf = ctypes.create_unicode_buffer(n + 1)
    u32.GetWindowTextW(h, buf, n + 1)
    return buf.value


def modal_dialog(frame):
    """Sichtbares Fenster, dessen Besitzer der (dann gesperrte) VW-Rahmen ist."""
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(frame, ctypes.byref(pid))
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(h, _):
        if not u32.IsWindowVisible(h):
            return True
        p = wintypes.DWORD()
        u32.GetWindowThreadProcessId(h, ctypes.byref(p))
        owner = u32.GetWindow(h, 4)
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(h, cls, 64)
        if p.value == pid.value and owner and not u32.IsWindowEnabled(owner) \
                and not cls.value.startswith('BCGP'):
            hits.append(h)
        return True

    u32.EnumWindows(cb, 0)
    return hits[0] if hits else None


if __name__ == '__main__':
    sys.exit(main())
