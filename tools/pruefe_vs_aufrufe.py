"""Jeden vs.*-Aufruf in einer Python-Datei gegen den Funktionsindex halten.

Der Index (vwx-plugin/vs_index.json) kennt fuer alle 3071 vs-Funktionen Name,
Argumentliste und Pflichtstelligkeit. Ein erfundener Name oder eine falsche
Argumentzahl loest in Vectorworks einen Engine-Fehler aus, der die Bruecke
blockiert — das faellt sonst erst im laufenden Betrieb auf, mitten in einer
echten Arbeitsdatei.

Geprueft wird ueber den AST, nicht per regulaerem Ausdruck: nur so werden
Aufrufe in Lambdas, verschachtelte Klammern und mehrzeilige Argumentlisten
richtig gezaehlt.

Aufruf:  python vs_arity.py <index.json> <datei.py> [<datei.py> ...]
"""
import ast
import io
import json
import sys


# Der Index fuehrt die VECTORSCRIPT-Signaturen (Pascal). Die Python-Bindung
# fasst Punkte und Farben zu Tupeln zusammen: Oval(x1,y1,x2,y2) heisst dort
# Oval(p1,p2), SetVPClOvrdFillFore(vp,cls,r,g,b) nimmt (vp,cls,(r,g,b)).
# Eine Stelligkeitsabweichung ist deshalb ein HINWEIS, kein Beweis — ein
# unbekannter FUNKTIONSNAME dagegen ist immer ein Fehler.
_TUPELWOERTER = ("p1", "p2", "p3", "pt", "point", "x1", "y1", "z1", "x2", "y2",
                 "z2", "center", "colorr", "colorg", "colorb", "red", "green",
                 "blue", "rv", "gv", "bv")


def _tupelverdacht(args):
    kleine = [a.lower() for a in args]
    return sum(1 for a in kleine if any(w in a for w in _TUPELWOERTER)) >= 2


def pruefe(quelle, herkunft, idx):
    """Liefert (befunde, geprueft). befund = (zeile, name, gezaehlt, erwartet, art)."""
    try:
        baum = ast.parse(quelle)
    except SyntaxError as e:
        return [(e.lineno or 0, "<Syntaxfehler>", 0, "", str(e))], 0

    befunde, n = [], 0
    for k in ast.walk(baum):
        if not isinstance(k, ast.Call):
            continue
        f = k.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "vs"):
            continue
        n += 1
        name = f.attr
        eintrag = idx.get(name)
        if eintrag is None:
            befunde.append((k.lineno, name, len(k.args), "—",
                            "HART: unbekannte vs-Funktion"))
            continue

        args = eintrag.get("args") or []
        # Ein *args-Aufruf laesst sich nicht statisch zaehlen — nicht melden.
        if any(isinstance(a, ast.Starred) for a in k.args):
            continue
        gezaehlt = len(k.args) + len(k.keywords)
        pflicht = eintrag.get("required")
        if pflicht is None:
            pflicht = len(args)
        if gezaehlt < pflicht or gezaehlt > len(args):
            erwartet = str(pflicht) if pflicht == len(args) else f"{pflicht}–{len(args)}"
            art = ("WEICH: Tupel-Verdacht (Python fasst Punkte/Farben zusammen): "
                   if _tupelverdacht(args) else "PRUEFEN: Stelligkeit passt nicht: ")
            befunde.append((k.lineno, name, gezaehlt, erwartet, art + ", ".join(args)))
    return befunde, n


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    idx = json.load(io.open(sys.argv[1], encoding="utf-8"))
    hart_gesamt = 0
    for pfad in sys.argv[2:]:
        quelle = io.open(pfad, encoding="utf-8", errors="replace").read()
        befunde, n = pruefe(quelle, pfad, idx)
        hart = [b for b in befunde if b[4].startswith("HART")]
        pruef = [b for b in befunde if b[4].startswith("PRUEFEN")]
        weich = [b for b in befunde if b[4].startswith("WEICH")]
        print(f"{'!! ' if hart else 'OK '}{pfad}: {n} vs-Aufrufe · "
              f"{len(hart)} hart, {len(pruef)} zu pruefen, {len(weich)} Tupel-Verdacht")
        for gruppe in (hart, pruef):
            for zeile, name, gezaehlt, erwartet, art in gruppe:
                print(f"      Zeile {zeile}: vs.{name} mit {gezaehlt} Argumenten, "
                      f"erwartet {erwartet} — {art}")
        hart_gesamt += len(hart)
    return 1 if hart_gesamt else 0


if __name__ == "__main__":
    sys.exit(main())
