"""IFC class report for a Vectorworks export: which classes, how many, which
property sets, and which elements ended up as IfcBuildingElementProxy.

Use it before and after fixing IFC assignments in a drawing — the counts
side by side are the proof that the fix reached the export.

usage: python tools/ifc_klassen_bericht.py <file.ifc> [--proxies]
"""
import collections
import sys

import ifcopenshell
import ifcopenshell.util.element as ue


def report(path, show_proxies=False):
    f = ifcopenshell.open(path)
    print(f'{path}\n  schema {f.schema}')
    by_class = collections.Counter()
    names = collections.defaultdict(collections.Counter)
    psets = collections.defaultdict(collections.Counter)
    for e in f.by_type('IfcProduct'):
        if e.is_a('IfcSpatialStructureElement') or e.is_a('IfcAnnotation'):
            continue
        c = e.is_a()
        by_class[c] += 1
        names[c][e.Name or '(leer)'] += 1
        for ps in ue.get_psets(e):
            psets[c][ps] += 1
    for c, n in by_class.most_common():
        top = ', '.join(f'{k} ×{v}' for k, v in names[c].most_common(4))
        ps = ', '.join(f'{k} ×{v}' for k, v in psets[c].most_common(5))
        print(f'  {n:6}  {c:34} Namen: {top}')
        if ps:
            print(f'          {"":34} Psets: {ps}')
    if show_proxies:
        for e in f.by_type('IfcBuildingElementProxy'):
            print(f'  proxy  {e.GlobalId}  {e.Name}')
    return by_class


if __name__ == '__main__':
    report(sys.argv[1], '--proxies' in sys.argv)
