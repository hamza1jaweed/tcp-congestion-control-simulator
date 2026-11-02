"""
TCP Congestion Control Simulator - local web demo
CBIT Computer Networks (22ITC10N) - Case Study 8

Run:   python app.py
Open:  http://localhost:8000   (opens automatically)

Uses only the Python standard library + tcpsim.py (same folder).
"""
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tcpsim import Simulator, summarize, cwnd_series, throughput_series

PORT = 8000
HERE = os.path.dirname(os.path.abspath(__file__))
ALGOS = ["SS", "AIMD", "TAHOE", "RENO", "DLA"]


def run_sim(p):
    cap = float(p.get("bandwidth", 10))
    rtt = float(p.get("rtt", 50))
    loss = float(p.get("loss", 0)) / 100.0
    buf = float(p.get("buffer", 1.0))
    dur = float(p.get("duration", 20))
    seed = int(p.get("seed", 1))
    dynamic = bool(p.get("dynamic", False))
    algos = [a for a in p.get("algos", ALGOS) if a in ALGOS] or ALGOS
    dur = max(5.0, min(dur, 60.0))

    if dynamic:   # capacity drops to 40% at 1/3 of the run, rises to 150% at 2/3
        cap_s = [(0, cap), (dur / 3, cap * 0.4), (2 * dur / 3, cap * 1.5)]
    else:
        cap_s = cap

    out = {"algos": {}, "dt_cwnd": 0.05, "dt_tp": 0.5, "duration": dur}
    for a in algos:
        sim = Simulator(cap_mbps=cap_s, rtt_ms=rtt, loss_p=loss, buffer_bdp=buf,
                        duration=dur, seed=seed)
        f = sim.add_flow(a)
        sim.run()
        s = summarize(sim, f)
        cw = cwnd_series(f, 0.05, dur)
        _, tp = throughput_series(f, 0.5, dur)
        out["bdp"] = round(sim.bdp0, 1)
        out["buffer_pkts"] = sim.buffer_pkts
        out["algos"][a] = {
            "summary": {
                "goodput": round(s["goodput"], 3),
                "util": round(100 * s["goodput"] / cap, 1),
                "mean_cwnd": round(s["mean_cwnd"], 1),
                "loss_pct": round(100 * s["loss_rate"], 2),
                "mean_rtt": round(s["mean_rtt_ms"], 1),
                "p95_rtt": round(s["p95_rtt_ms"], 1),
                "rec_ms": round(s["rec_lat_ms"], 0),
                "timeouts": s["timeouts"],
                "fast_rtx": s["fast_rtx"],
            },
            "cwnd": [round(c, 2) for _, c in cw],
            "tp": [round(x, 3) for x in tp],
        }
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as fh:
                self._send(200, fh.read(), "text/html; charset=utf-8")
        else:
            self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length", 0))
        try:
            params = json.loads(self.rfile.read(n) or b"{}")
            res = run_sim(params)
            self._send(200, json.dumps(res), "application/json")
        except Exception as e:  # show the error in the page
            self._send(400, json.dumps({"error": str(e)}), "application/json")

    def log_message(self, fmt, *args):
        print("[server]", fmt % args)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"TCP simulator demo running at {url}  (Ctrl+C to stop)")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
