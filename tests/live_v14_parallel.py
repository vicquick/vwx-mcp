"""Live: two agents fire read calls concurrently; every answer must come from
the agent's own document. Read-only.   python tests/live_v14_parallel.py [port]"""
import sys, threading, time
sys.argv += [] if len(sys.argv) > 1 else ["8082"]
import importlib.util, os
spec = importlib.util.spec_from_file_location("lv", os.path.join(os.path.dirname(__file__), "live_v14.py"))
src = open(spec.origin, encoding="utf-8").read().split("a, b = Agent(")[0]   # reuse the Agent class only
ns = {}
exec(src, ns)
Agent = ns["Agent"]

a, b = Agent("par-A"), Agent("par-B")
docs = a.call("list_documents")
front = docs["aktiv"]
other = next(d["datei"] for d in docs["dokumente"] if d["datei"] and d["datei"] != front)
b.call("use_document", document=other, priority="batch")
res = {"A": [], "B": []}


def run(agent, key, n=8):
    for _ in range(n):
        r = agent.call("get_document_info")
        res[key].append(r.get("name") if isinstance(r, dict) else r)


t0 = time.time()
ta = threading.Thread(target=run, args=(a, "A")); tb = threading.Thread(target=run, args=(b, "B"))
ta.start(); tb.start(); ta.join(); tb.join()
dt = time.time() - t0
for _ in range(20):  # restore comes after the idle delay (VWX_RESTORE_DELAY_S)
    if a.call("list_documents")["aktiv"] == front: break
    time.sleep(0.5)
fin = a.call("list_documents")["aktiv"]
okA = all(x == front for x in res["A"]); okB = all(x == other for x in res["B"])
print("A answers:", set(res["A"])); print("B answers:", set(res["B"]))
print("16 calls in %.1fs, front at end: %s" % (dt, fin))
print("RESULT:", "PASS" if okA and okB and fin == front else "FAIL")
