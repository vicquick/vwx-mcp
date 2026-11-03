"""Offline check: every vs.* call in vwx-plugin/*.py exists in vs_index.json and
is called with an argument count the index allows (no Vectorworks needed).

    python tests/test_vs_arity.py

A wrong arity is not a Python TypeError inside Vectorworks: VW shows a modal
"Script Errors" window that blocks the bridge until someone dismisses it
(import_dwg called the zero-argument vs.ImportDXFDWG with two arguments).
Existence check idea: williamjvest/vwx-mcp 3eb78b6.
"""
import ast, glob, json, os, sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX = os.path.join(REPO, 'vwx-plugin', 'vs_index.json')

# Calls that are guarded at runtime (hasattr / try) on purpose.
ALLOW_MISSING = {'GetPolyClosed'}
# Index entries whose arity is known to be wrong or variadic: name -> (min, max).
ARITY_OVERRIDE = {}
# Mismatches found when this test was added (2026-11-03) that still need a live
# check on VW2027 — the stub may be incomplete, or the call may be a real bug.
# Remove a name once it is verified or fixed. New mismatches still fail.
PENDING_LIVE_CHECK = {
    'ConvertToPolyline', 'SetTextureRef', 'DTM6_RiseToSurface', 'MakeModifierClass',
    'SetObjBeginningMarker', 'SetObjEndMarker', 'CircularDim', 'CreateChainDimension',
    'CreateStaticHatchFromObject', 'CombinePolygons', 'CreateVP',
    'SetVPClOvrdFillFore', 'SetVPClOvrdFillBack', 'SetVPClOvrdPenFore',
    'SetVPClOvrdPenBack', 'SetVPClOvrdFillOpty', 'SetVPClOvrdPenOpty',
    'SetVPClOvrdFillStyle', 'SetVPLrOvrdFillFore', 'SetVPLrOvrdPenFore',
    'SetVPLrOvrdOpty',
}


import re

# The Python vs module accepts a POINT either as one tuple or spread out as
# x, y (or x, y, z), and an RGB triple either as three numbers or one tuple.
_POINT_ARG = re.compile(r'^(p\d?|pt\d?|.*Pt\d?|.*Point\d?|box\d|.*[Cc]enter|origin)$')
_RGB = re.compile(r'(R|G|B)V?$|(red|green|blue)$', re.I)


def arg_range(args):
    """(min, max) positional count a call may use for these index arg names."""
    lo = hi = 0
    i = 0
    while i < len(args):
        a = args[i]
        trip = args[i:i + 3]
        if (len(trip) == 3 and all(_RGB.search(x) for x in trip)
                and [_RGB.search(x).group(0)[0].upper() for x in trip] == ['R', 'G', 'B']):
            lo += 1; hi += 3; i += 3
            continue
        if _POINT_ARG.match(a):
            lo += 1; hi += 3
        else:
            lo += 1; hi += 1
        i += 1
    return lo, hi


def load_index():
    with open(INDEX, encoding='utf-8') as f:
        d = json.load(f)
    return d.get('functions', d)


def vs_calls(path):
    with open(path, encoding='utf-8') as f:
        tree = ast.parse(f.read(), path)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == 'vs'):
            starred = any(isinstance(a, ast.Starred) for a in node.args) or node.keywords
            yield node.func.attr, len(node.args), bool(starred), node.lineno


def check():
    idx = load_index()
    problems = []
    files = sorted(glob.glob(os.path.join(REPO, 'vwx-plugin', '*.py')))
    for path in files:
        rel = os.path.relpath(path, REPO)
        for name, n, starred, line in vs_calls(path):
            fn = idx.get(name)
            if fn is None:
                if name not in ALLOW_MISSING:
                    problems.append(f'{rel}:{line}: vs.{name} is not in vs_index.json')
                continue
            if starred or name in PENDING_LIVE_CHECK:
                continue
            if name in ARITY_OVERRIDE:
                lo, hi = ARITY_OVERRIDE[name]
            else:
                lo, hi = arg_range(fn['args'])
                lo -= fn['arity'] - fn.get('required', fn['arity'])   # optional args
            if not lo <= n <= hi:
                want = str(hi) if lo == hi else f'{lo}..{hi}'
                problems.append(f'{rel}:{line}: vs.{name} called with {n} args, '
                                f'index says {want} ({", ".join(fn["args"])})')
    return problems


def test_vs_calls_match_index():
    problems = check()
    assert not problems, '\n' + '\n'.join(problems)


if __name__ == '__main__':
    p = check()
    print('\n'.join(p) if p else 'OK: all vs.* calls match vs_index.json')
    sys.exit(1 if p else 0)
