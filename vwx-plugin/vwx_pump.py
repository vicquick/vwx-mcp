#!/usr/bin/env python3
"""
VWX file-IPC pump — bridge v4. Runs inside Vectorworks as a MENU COMMAND
(hotkey Ctrl+Shift+B), drains the job queue and RETURNS immediately.

No dialog, no threads, no persistent context:
  - Vectorworks stays fully responsive for the user whenever no command is
    actually executing (the modal pump dialog of the TCP bridge is gone).
  - Marionette executions may tear down the Python context on frame return —
    irrelevant here, every pump invocation is its own context anyway.

IPC layout (plugin dir):
  ipc/jobs/<ts>-<cid>.json      written by the MCP server (atomic .tmp+replace)
  ipc/jobs/<...>.working        claimed by the pump (atomic rename)
  ipc/results/<cid>.json        written by the pump, consumed by the server
  ipc/pump.stamp                epoch of the last pump run (watchdog telemetry)

The trigger chain: server writes a job -> the watchdog's FileSystemWatcher
fires the menu-command hotkey -> this script runs. See watchdog/README.md.
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

def _log(msg):
    try:
        with open(_LOG, 'a', encoding='utf-8') as f:
            f.write("[%s] pump: %s\n" % (time.strftime('%H:%M:%S'), msg))
    except Exception:
        pass

# Commands that execute a Marionette network: VW may tear down THIS context
# when the dispatch frame returns, so their result (an ack) is written BEFORE
# dispatching. Everything after the dispatch may never run — that's fine.
_FIRE_AND_FORGET = frozenset({'marionette_recalc'})

def _write_json(path, obj):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)      # atomic: the reader never sees a partial file

def _dispatch(cmd, params):
    try:
        import commands, importlib
        importlib.reload(commands)      # hot-reload, same as the TCP bridge
        fn = getattr(commands, cmd, None)
        if fn is None:
            return {'error': 'Unknown command: %s' % cmd}
        return fn(params)
    except Exception as e:
        return {'error': str(e), 'traceback': traceback.format_exc()}

def pump():
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
    # housekeeping: drop orphaned results (server crashed / timed out long ago)
    now = time.time()
    try:
        for fn in os.listdir(_RESULTS):
            p = os.path.join(_RESULTS, fn)
            if now - os.path.getmtime(p) > RESULT_TTL:
                os.remove(p)
    except Exception:
        pass

    done = 0
    while True:
        try:
            jobs = sorted(fn for fn in os.listdir(_JOBS) if fn.endswith('.json'))
        except Exception:
            jobs = []
        if not jobs:
            break
        for fn in jobs:
            src = os.path.join(_JOBS, fn)
            work = src + '.working'
            try:
                os.replace(src, work)   # atomic claim
            except Exception:
                continue                # another pump invocation grabbed it
            try:
                with open(work, 'r', encoding='utf-8') as f:
                    msg = json.load(f)
            except Exception as e:
                _log("bad job %s: %s" % (fn, e))
                try: os.remove(work)
                except Exception: pass
                continue
            cid    = str(msg.get('_cid', fn))
            cmd    = msg.get('type', '')
            params = msg.get('params', {}) or {}
            rpath  = os.path.join(_RESULTS, cid + '.json')
            try: os.remove(work)        # claim consumed; a crash loses the job
            except Exception: pass      # (visible timeout) instead of re-running it
            if cmd in _FIRE_AND_FORGET and not params.get('_sync'):
                _write_json(rpath, {'status': 'triggered',
                                    'note': 'Marionette execution — ack written '
                                            'before dispatch (context may reset).'})
                _log("fire-and-forget cid=%s cmd=%s" % (cid, cmd))
                _dispatch(cmd, params)
                done += 1
                continue                # may never be reached — fine
            t0 = time.time()
            result = _dispatch(cmd, params)
            try:
                _write_json(rpath, result)
            except Exception as e:
                _write_json(rpath, {'error': 'result not serializable: %s' % e})
            _log("cid=%s cmd=%s ms=%d %s"
                 % (cid, cmd, (time.time() - t0) * 1000,
                    'ERR' if isinstance(result, dict) and result.get('error') else 'ok'))
            done += 1
    if done:
        _log("pump done: %d job(s)" % done)

pump()
