"""Live: per-call latency of a cheap read, no document switching.  python tests/live_latency.py [port] [n]"""
import os, sys, time
port = sys.argv[1] if len(sys.argv) > 1 else "8082"
n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
sys.argv = [sys.argv[0], port]
src = open(os.path.join(os.path.dirname(__file__), "live_v14.py"), encoding="utf-8").read().split("a, b = Agent(")[0]
ns = {}
exec(src, ns)
a = ns["Agent"]("lat")
for tool in ("get_document_info", "get_layers", "ping"):
    ts = []
    for _ in range(n):
        t = time.time(); a.call(tool); ts.append(time.time() - t)
    ts.sort()
    print("%-18s median %.2fs  min %.2fs  max %.2fs" % (tool, ts[len(ts) // 2], ts[0], ts[-1]))
