"""Live stress test of bridge v16 document routing (SDK switching).

    python tests/live_v16_stress.py WRITE_DOC READ_DOC [n] [port]

Three MCP sessions run at the same time:
  W  bound to WRITE_DOC: every call creates one named rectangle there
     (use a throwaway copy!) and reports which document it ran in
  R  bound to READ_DOC: read-only calls
  F  unbound: read-only calls in whatever document the user has in front
Every call checks vs.GetFName() against the document it was routed to.
At the end: number of rectangles in WRITE_DOC must equal n, none may exist
in the other documents, and the user's front document must be back.
"""
import json, sys, threading, time, urllib.request

WRITE_DOC, READ_DOC = sys.argv[1], sys.argv[2]
N = int(sys.argv[3]) if len(sys.argv) > 3 else 15
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 8082
URL = "http://127.0.0.1:%d/mcp" % PORT
TAG = "V16_STRESS_%d" % int(time.time())


class Agent:
    def __init__(self, name):
        self.name, self.sid, self.i = name, None, 10
        h, _ = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                      "clientInfo": {"name": name, "version": "1"}}})
        self.sid = h.get("Mcp-Session-Id") or h.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _post(self, payload):
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={
            "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-06-18"})
        if self.sid:
            req.add_header("Mcp-Session-Id", self.sid)
        r = urllib.request.urlopen(req, timeout=600)
        return r.headers, r.read().decode("utf-8", "replace")

    def call(self, tool, **args):
        self.i += 1
        _, body = self._post({"jsonrpc": "2.0", "id": self.i, "method": "tools/call",
                              "params": {"name": tool, "arguments": args}})
        for line in body.splitlines():
            if line.startswith("data:"):
                m = json.loads(line[5:])
                if "result" in m:
                    txt = m["result"]["content"][0]["text"]
                    try:
                        return json.loads(txt)
                    except Exception:
                        return {"raw": txt}
                if "error" in m:
                    return {"error": m["error"]}
        return {"error": "no data", "body": body[:300]}


def key(s):
    return (s or "").replace("\\", "/").rsplit("/", 1)[-1].strip(" *").lower()


results = {"W": [], "R": [], "F": []}
errors = []


def run(agent, label, expect, write):
    for k in range(N):
        code = ("vs.Rect((%d,0),(%d,100))\nvs.SetName(vs.LNewObj(), '%s_%d')\n" % (k * 200, k * 200 + 100, TAG, k)
                if write else "")
        code += "__result__ = vs.GetFName()"
        t0 = time.time()
        r = agent.call("execute_script", code=code)
        dt = time.time() - t0
        got = (r or {}).get("result")
        ok = isinstance(got, str) and (expect is None or key(got) == key(expect))
        results[label].append((ok, dt, got))
        if not ok:
            errors.append((label, k, r))


front0 = Agent("probe").call("list_documents")
front = front0.get("aktiv")
print("front at start:", front, "| source:", front0.get("quelle"))
if front0.get("quelle") != "sdk":
    sys.exit("native v16 not active — refusing to run")

W, R, F = Agent("stress-W"), Agent("stress-R"), Agent("stress-F")
print("bind W:", W.call("use_document", document=WRITE_DOC).get("status"))
print("bind R:", R.call("use_document", document=READ_DOC, priority="batch").get("status"))

t0 = time.time()
th = [threading.Thread(target=run, args=(W, "W", WRITE_DOC, True)),
      threading.Thread(target=run, args=(R, "R", READ_DOC, False)),
      threading.Thread(target=run, args=(F, "F", front, False))]
for t in th: t.start()
for t in th: t.join()
total = time.time() - t0

for lab in "WRF":
    rs = results[lab]
    ok = sum(1 for o, _, _ in rs if o)
    lat = sorted(d for _, d, _ in rs)
    print("%s: %d/%d ok  median %.2fs  max %.2fs" % (lab, ok, len(rs), lat[len(lat) // 2], lat[-1]))
print("total %.1fs, %d calls" % (total, sum(len(v) for v in results.values())))

crit = "(N='%s_*')" % TAG
cnt_w = W.call("execute_script", code="__result__ = vs.Count(\"%s\")" % crit).get("result")
cnt_r = R.call("execute_script", code="__result__ = vs.Count(\"%s\")" % crit).get("result")
W.call("use_document"); R.call("use_document")
time.sleep(4)
cnt_f = F.call("execute_script", code="__result__ = [vs.GetFName(), vs.Count(\"%s\")]" % crit).get("result")
fin = F.call("list_documents").get("aktiv")
print("rects in WRITE_DOC: %s (want %d) | in READ_DOC: %s | in front doc: %s" % (cnt_w, N, cnt_r, cnt_f))
print("front at end:", fin, "(was %s)" % front)
fail = errors or cnt_w != N or cnt_r or (cnt_f and cnt_f[1]) or key(fin) != key(front)
for e in errors[:10]:
    print("ERR", e)
print("RESULT:", "FAIL" if fail else "PASS")
