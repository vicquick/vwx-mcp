#!/usr/bin/env python3
"""
VWX file-IPC pump — bridge v11: CONTEXT-SPLIT DRAIN (crash-proof by design).

The definitive VW2026 context map (6 live tests):
  - CEF web-palette sync callback : read Python OK, doc mutation CRASHES.
  - OnIdle notification handler    : read Python OK, opening a dialog CRASHES.
  - genuine command dispatch       : full capability (the menu command's
    DoInterface, reached by a real click / accelerator).

Therefore this module exposes TWO entry points and NEVER auto-runs:

  pump_readonly()  -- drains ONLY read-only commands (get_/list_/count_/find_/
                      ping/math). Safe to call from the OnIdle notification
                      context, so reads happen in the true background while
                      Vectorworks is unfocused. Mutation jobs are LEFT QUEUED.

  pump_all()       -- drains EVERY queued job. Called ONLY from the menu
                      command's DoInterface (genuine dispatch), the one context
                      where document mutation is safe.

If a mutation job can never reach DoInterface (e.g. no working background
trigger) it simply stays queued and the MCP call times out visibly — it is
NEVER executed in an unsafe context, so it can never crash Vectorworks.

IPC layout (plugin dir):
  ipc/jobs/<ts>-<cid>.json      written by the MCP server (atomic .tmp+replace)
  ipc/jobs/<...>.working        claimed by the pump (atomic rename)
  ipc/results/<cid>.json        written by the pump, consumed by the server
  ipc/pump.stamp                epoch of the last pump run
"""
import os, sys, json, time, traceback

try:
    _DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:                      # VW runs scripts as <string>
    _base = os.path.join(os.environ.get('APPDATA', ''),
                         'Nemetschek', 'Vectorworks', '2026', 'Plug-ins')
    for _name in ('VW-MCP', 'VWX-MCP'):
        _cand = os.path.join(_base, _name)
        if os.path.isdir(_cand):
            _DIR = _cand
            break
    else:
        _DIR = os.path.join(_base, 'VW-MCP')
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

_IPC     = os.path.join(_DIR, 'ipc')
_JOBS    = os.path.join(_IPC, 'jobs')
_RESULTS = os.path.join(_IPC, 'results')
_STAMP   = os.path.join(_IPC, 'pump.stamp')
_LOG     = os.path.join(_DIR, 'bridge.log')

RESULT_TTL = 3600.0          # orphaned result files are removed after this

# Read-only commands: safe in ANY context (no document mutation, no dialog).
# A job is read-only if its command is in this set OR starts with one of the
# read-only prefixes. Everything else is treated as a mutation and waits for
# genuine dispatch.
_RO_NAMES = frozenset({
    'ping', 'distance', 'distance_3d', 'polygon_centroid',
    'get_document_info', 'get_document_preferences', 'get_georeferencing',
})
_RO_PREFIXES = ('get_', 'list_', 'count_', 'find_')

# Marionette executions may tear down THIS Python context on frame return:
# their ack is written BEFORE dispatch.
_FIRE_AND_FORGET = frozenset({'marionette_recalc'})


def _log(msg):
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write("[%s] pump: %s\n" % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass


def _is_readonly(cmd):
    return cmd in _RO_NAMES or cmd.startswith(_RO_PREFIXES)


def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)      # atomic: the reader never sees a partial file


def _dispatch(cmd, params):
    try:
        import commands, importlib
        importlib.reload(commands)      # hot-reload, same as ever
        fn = getattr(commands, cmd, None)
        if fn is None:
            return {'error': 'Unknown command: %s' % cmd}
        return fn(params)
    except Exception as e:
        return {'error': str(e), 'traceback': traceback.format_exc()}


def _list_jobs():
    try:
        return sorted(fn for fn in os.listdir(_JOBS) if fn.endswith('.json'))
    except Exception:
        return []


def _peek_cmd(fn):
    """Read a job's command name WITHOUT claiming it."""
    try:
        with open(os.path.join(_JOBS, fn), 'r', encoding='utf-8') as f:
            return json.load(f).get('type', '')
    except Exception:
        return None


def _claim_and_run(fn):
    src  = os.path.join(_JOBS, fn)
    work = src + '.working'
    try:
        os.replace(src, work)           # atomic claim
    except Exception:
        return False                    # another invocation grabbed it
    try:
        with open(work, 'r', encoding='utf-8') as f:
            msg = json.load(f)
    except Exception as e:
        _log("bad job %s: %s" % (fn, e))
        try: os.remove(work)
        except Exception: pass
        return False
    try: os.remove(work)                # claim consumed; a crash loses the job
    except Exception: pass              # (visible timeout) instead of re-running
    cid    = str(msg.get('_cid', fn))
    cmd    = msg.get('type', '')
    params = msg.get('params', {}) or {}
    rpath  = os.path.join(_RESULTS, cid + '.json')
    if cmd in _FIRE_AND_FORGET and not params.get('_sync'):
        _write_json(rpath, {'status': 'triggered',
                            'note': 'Marionette execution — ack before dispatch.'})
        _log("fire-and-forget cid=%s cmd=%s" % (cid, cmd))
        _dispatch(cmd, params)
        return True
    t0 = time.time()
    result = _dispatch(cmd, params)
    try:
        _write_json(rpath, result)
    except Exception as e:
        _write_json(rpath, {'error': 'result not serializable: %s' % e})
    _log("cid=%s cmd=%s ms=%d %s"
         % (cid, cmd, (time.time() - t0) * 1000,
            'ERR' if isinstance(result, dict) and result.get('error') else 'ok'))
    return True


def _housekeep():
    for d in (_JOBS, _RESULTS):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
    try:
        with open(_STAMP, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass
    now = time.time()
    try:
        for fn in os.listdir(_RESULTS):
            p = os.path.join(_RESULTS, fn)
            if now - os.path.getmtime(p) > RESULT_TTL:
                os.remove(p)
    except Exception:
        pass


def pump_readonly():
    """Drain read-only jobs only. Safe in the OnIdle / notification context."""
    _housekeep()
    done = 0
    for fn in _list_jobs():
        if _is_readonly(_peek_cmd(fn) or ''):
            if _claim_and_run(fn):
                done += 1
    if done:
        _log("readonly drain: %d job(s)" % done)


def pump_all():
    """Drain EVERY job. Call ONLY from genuine command dispatch (DoInterface)."""
    _housekeep()
    _log("pump_all: genuine dispatch — draining everything")
    done = 0
    while True:
        jobs = _list_jobs()
        if not jobs:
            break
        for fn in jobs:
            if _claim_and_run(fn):
                done += 1
    if done:
        _log("pump_all done: %d job(s)" % done)
