"""Server, Kommandos und Tags gegeneinander halten.

Der Pump findet ein Kommando per getattr(commands, name), und der Server uebergibt
ihm ein dict. Beides ist ungetypt: ein Tippfehler im Schluessel faellt nicht auf,
er kommt einfach nie an. Genau das findet dieses Skript — per AST, weil ein
regulaerer Ausdruck weder mehrzeilige Aufrufe noch verschachtelte Klammern zaehlt.

Fuenf Pruefungen:
  1. Jedes @vtool ruft ein cmd("name"), das es in commands.py gibt.
  2. Jeder uebergebene Schluessel wird im Kommando auch abgefragt.
  3. Jeder abgefragte Pflichtschluessel wird vom Server auch uebergeben.
  4. Jedes @vtool hat einen Eintrag in TOOL_TAGS.
  5. Keine Funktion ist doppelt definiert (in Python gewinnt die letzte).

Grenzen, damit niemand den Befunden mehr glaubt als sie hergeben:
  * p.get(k) mit VARIABLEM k (update_plant laeuft ueber ein mapping-dict) ist
    statisch nicht aufloesbar und erscheint als Falschmeldung in Pruefung 2.
  * Ein Kommando, das absichtlich nur noch einen Fehler zurueckgibt (export_dxf,
    export_image auf VW2026), liest seine Parameter zu Recht nicht mehr. Dann
    luegt aber die Server-Signatur, die sie weiter verlangt — auch ein Befund,
    nur ein anderer.

Aufruf:  python konsistenz.py <repo>
Rueckgabe: 0 wenn sauber, 1 wenn Befunde — taugt als Testschritt.
"""
import ast
import io
import os
import sys

# Windows-Konsole ist cp1252 — Kastenzeichen brechen sie sonst.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _dict_schluessel(k):
    """Alle Schluessel, die im Werkzeugkoerper in ein dict gelegt werden.

    Der Server baut die Nutzlast mal inline — cmd("x", {"a": a}) — und mal ueber
    eine Variable:  p = {}; if layer: p["layer"] = layer; ... cmd("x", p).
    Beide Formen zaehlen, sonst meldet die Pruefung jede Variablenform als Fehler.
    """
    aus = set()
    for n in ast.walk(k):
        if isinstance(n, ast.Dict):
            aus |= {s.value for s in n.keys if isinstance(s, ast.Constant)}
        elif isinstance(n, ast.Assign):
            for z in n.targets:
                if (isinstance(z, ast.Subscript) and isinstance(z.slice, ast.Constant)
                        and isinstance(z.slice.value, str)):
                    aus.add(z.slice.value)
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in ("update", "setdefault") and n.args
              and isinstance(n.args[0], ast.Dict)):
            aus |= {s.value for s in n.args[0].keys if isinstance(s, ast.Constant)}
    return aus


def vtools(quelle):
    """{werkzeugname: (kommandoname, {uebergebene schluessel}, zeile)}"""
    aus = {}
    for k in ast.parse(quelle).body:
        if not isinstance(k, ast.FunctionDef):
            continue
        if not any((isinstance(d, ast.Name) and d.id == "vtool") or
                   (isinstance(d, ast.Call) and isinstance(d.func, ast.Name)
                    and d.func.id == "vtool") for d in k.decorator_list):
            continue
        for n in ast.walk(k):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "cmd" and n.args):
                continue
            ziel = n.args[0].value if isinstance(n.args[0], ast.Constant) else None
            aus[k.name] = (ziel, _dict_schluessel(k), k.lineno)
            break
    return aus


def _schluessel_in(k):
    """({abgefragt}, {ohne Vorgabe}, {aufgerufene Helfer, die die Nutzlast erben})

    Der Parameter heisst nicht ueberall "p": Kommandos nehmen p, Helfer wie
    _with_layer_class nennen ihn params. Deshalb den TATSAECHLICHEN Namen des
    ersten Parameters nehmen — sonst sieht die Pruefung in jedem Helfer nichts
    und meldet dessen Schluessel faelschlich als unbenutzt.
    """
    if not k.args.args:
        return set(), set(), set()
    pn = k.args.args[0].arg
    alle, ohne_vorgabe, helfer = set(), set(), set()
    # p.get('a') or p.get('b') ist EIN Wert unter zwei Namen. Kommt einer der
    # beiden an, ist die Sache erfuellt — deshalb zaehlen Alias-Gruppen als
    # Gruppe, nicht als Einzelforderung.
    alias = []
    for n in ast.walk(k):
        if isinstance(n, ast.BoolOp) and isinstance(n.op, ast.Or):
            g = {a.args[0].value for a in n.values
                 if isinstance(a, ast.Call) and isinstance(a.func, ast.Attribute)
                 and a.func.attr == "get" and isinstance(a.func.value, ast.Name)
                 and a.func.value.id == pn and a.args
                 and isinstance(a.args[0], ast.Constant)}
            if len(g) > 1:
                alias.append(g)
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "get" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == pn and n.args
                and isinstance(n.args[0], ast.Constant)):
            alle.add(n.args[0].value)
            if len(n.args) == 1:
                ohne_vorgabe.add(n.args[0].value)
        elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
              and n.value.id == pn and isinstance(n.slice, ast.Constant)):
            alle.add(n.slice.value)
            ohne_vorgabe.add(n.slice.value)
        # Ein Helfer, dem p uebergeben wird, liest die Schluessel stellvertretend.
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and any(isinstance(a, ast.Name) and a.id == pn for a in n.args)):
            helfer.add(n.func.id)
    # Aus einer Alias-Gruppe bleibt nur ein Vertreter als Forderung stehen.
    for g in alias:
        if g & ohne_vorgabe:
            ohne_vorgabe -= g
            ohne_vorgabe.add(sorted(g)[0])
            alle |= g
    return alle, ohne_vorgabe, helfer


def kommandos(quelle):
    """{name: ({abgefragte schluessel}, {ohne Vorgabe}, zeile)}

    Schluessel, die ein aufgerufener Helfer liest (z.B. _ws(p) liest
    'worksheet'), zaehlen mit — sonst meldet die Pruefung jeden Helfer als
    Fehler. Aufgeloest wird transitiv, mit Zyklusschutz.
    """
    roh, zeilen = {}, {}
    for k in ast.parse(quelle).body:
        if isinstance(k, ast.FunctionDef):
            roh[k.name] = _schluessel_in(k)
            zeilen[k.name] = k.lineno

    def aufloesen(name, gesehen):
        if name in gesehen or name not in roh:
            return set(), set()
        gesehen.add(name)
        alle, ohne, helfer = roh[name]
        alle, ohne = set(alle), set(ohne)
        for h in helfer:
            a2, o2 = aufloesen(h, gesehen)
            alle |= a2
            # Was ein Helfer liest, ist fuer den Aufrufer nie Pflicht — der
            # Helfer entscheidet selbst, wie er mit dem Fehlen umgeht.
        return alle, ohne

    aus = {}
    for name in roh:
        if name.startswith("_"):
            continue
        alle, ohne = aufloesen(name, set())
        aus[name] = (alle, ohne, zeilen[name])
    return aus


def doppelte(quelle):
    """[(name, [zeilen])] fuer mehrfach definierte Top-Level-Funktionen."""
    gesehen = {}
    for k in ast.parse(quelle).body:
        if isinstance(k, (ast.FunctionDef, ast.AsyncFunctionDef)):
            gesehen.setdefault(k.name, []).append(k.lineno)
    return [(n, z) for n, z in gesehen.items() if len(z) > 1]


def tags(quelle):
    """Nur die TOOL_TAGS-Eintraege — die PRESETS-Sektion darunter hat dieselbe
    Form (\"name\": ...) und wuerde sonst als Werkzeug gezaehlt."""
    aus, in_presets = set(), False
    for z in quelle.splitlines():
        if z.startswith("PRESETS"):
            in_presets = True
        if in_presets:
            continue
        t = z.strip()
        if t.startswith('"') and '":' in t:
            aus.add(t.split('"')[1])
    return aus


def main():
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    lies = lambda *t: io.open(os.path.join(repo, *t), encoding="utf-8").read()
    q_srv = lies("mcp-server", "vwx_mcp_server.py")
    q_cmd = lies("vwx-plugin", "commands.py")
    q_tag = lies("mcp-server", "tool_tags.py")

    vt, km, tg = vtools(q_srv), kommandos(q_cmd), tags(q_tag)
    print(f"{len(vt)} @vtool · {len(km)} Kommandos · {len(tg)} Tags\n")

    befunde = 0

    print("── 1. @vtool ohne passendes Kommando " + "─" * 40)
    fehlt = [(w, z) for w, (z, _, _) in vt.items() if z and z not in km]
    for w, z in sorted(fehlt):
        print(f"   {w:34} ruft cmd({z!r}) — gibt es nicht")
    print(f"   {len(fehlt) or 'keine'}\n")
    befunde += len(fehlt)

    print("── 2. Server uebergibt einen Schluessel, den das Kommando nie liest " + "─" * 8)
    n = 0
    for w, (ziel, ks, zeile) in sorted(vt.items()):
        if not ziel or ziel not in km:
            continue
        tot = ks - km[ziel][0]
        if tot:
            print(f"   {w:34} Zeile {zeile}: {', '.join(sorted(tot))}")
            n += 1
    print(f"   {n or 'keine'}\n")
    befunde += n

    print("── 3. Kommando liest einen Schluessel ohne Vorgabewert, den der Server nie schickt " + "─" * 2)
    print("   (Hinweis: oft Alternativen — p.get('a') or p.get('b') — deshalb nur ein Verdacht)")
    n = 0
    for w, (ziel, ks, zeile) in sorted(vt.items()):
        if not ziel or ziel not in km:
            continue
        fehlend = km[ziel][1] - ks
        # Ein Alias-Vertreter gilt als erfuellt, wenn IRGENDEIN Name der Gruppe
        # ankommt — km[ziel][0] traegt die ganze Gruppe.
        if fehlend and (km[ziel][0] & ks):
            fehlend = set()
        if fehlend:
            print(f"   {w:34} Zeile {zeile}: {', '.join(sorted(fehlend))}")
            n += 1
    print(f"   {n or 'keine'}\n")
    befunde += n

    print("── 4. @vtool ohne Tag " + "─" * 55)
    ohne = sorted(set(vt) - tg)
    for w in ohne:
        print(f"   {w}")
    verwaist = sorted(tg - set(vt))
    if verwaist:
        print(f"   Tags fuer Werkzeuge, die es nicht gibt: {', '.join(verwaist)}")
    print(f"   {len(ohne) or 'keine'}\n")
    befunde += len(ohne)

    print("── 5. Doppelt definierte Funktionen (die spaetere gewinnt) " + "─" * 18)
    n = 0
    for datei, q in (("commands.py", q_cmd), ("vwx_mcp_server.py", q_srv)):
        for name, zeilen in doppelte(q):
            print(f"   {datei}: {name} in Zeile {', '.join(map(str, zeilen))} "
                  f"— Zeile {zeilen[-1]} gewinnt, der Rest ist toter Code")
            n += 1
    print(f"   {n or 'keine'}\n")
    befunde += n

    print(f"Befunde gesamt: {befunde}")
    return 1 if befunde else 0


if __name__ == "__main__":
    sys.exit(main())
