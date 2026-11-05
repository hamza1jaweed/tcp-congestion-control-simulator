"""
Reproduce the results table and figures in the README.

Run from the repository root:
    python results/run_experiments.py

Needs Python 3.8+ and matplotlib (pip install matplotlib) for the figures.
The simulator itself (demo/tcpsim.py) uses only the standard library.
Writes results/results.csv, results/results.md and the PNGs in docs/.
"""
import csv
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "demo"))

from tcpsim import Simulator, summarize, cwnd_series, throughput_series  # noqa: E402

ALGOS = ["SS", "AIMD", "TAHOE", "RENO", "DLA"]
SEEDS = [1, 2, 3, 4, 5]
DURATION = 30.0

# Same five presets as the buttons in demo/index.html.
SCENARIOS = [
    ("Clean wired", dict(cap=10, rtt=50, loss=0.0, buf=1.0)),
    ("Lossy Wi-Fi (1%)", dict(cap=10, rtt=50, loss=0.01, buf=1.0)),
    ("Satellite (300 ms)", dict(cap=10, rtt=300, loss=0.001, buf=1.0)),
    ("Bufferbloat (4 BDP)", dict(cap=10, rtt=50, loss=0.0, buf=4.0)),
    ("Fast + lossy (50 Mbps)", dict(cap=50, rtt=50, loss=0.001, buf=1.0)),
]


def run_one(algo, sc, seed, cap_schedule=None):
    sim = Simulator(cap_mbps=cap_schedule or sc["cap"], rtt_ms=sc["rtt"], loss_p=sc["loss"],
                    buffer_bdp=sc["buf"], duration=DURATION, seed=seed)
    f = sim.add_flow(algo)
    sim.run()
    return sim, f, summarize(sim, f)


def main():
    rows = []
    for name, sc in SCENARIOS:
        for algo in ALGOS:
            runs = [run_one(algo, sc, s)[2] for s in SEEDS]
            gp = [r["goodput"] for r in runs]
            rows.append({
                "scenario": name,
                "algorithm": algo,
                "goodput_mbps": statistics.mean(gp),
                "goodput_sd": statistics.stdev(gp),
                "utilisation_pct": 100 * statistics.mean(gp) / sc["cap"],
                "mean_rtt_ms": statistics.mean(r["mean_rtt_ms"] for r in runs),
                "p95_rtt_ms": statistics.mean(r["p95_rtt_ms"] for r in runs),
                "loss_pct": 100 * statistics.mean(r["loss_rate"] for r in runs),
                "timeouts": statistics.mean(r["timeouts"] for r in runs),
            })
            print(f"{name:24s} {algo:6s} goodput {rows[-1]['goodput_mbps']:6.2f} Mbps")

    with open(os.path.join(ROOT, "results", "results.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()})

    # Markdown table: utilisation per scenario and algorithm.
    lines = ["| Scenario | " + " | ".join(ALGOS) + " |", "|---|" + "---|" * len(ALGOS)]
    for name, _ in SCENARIOS:
        cells = []
        best = max(r["utilisation_pct"] for r in rows if r["scenario"] == name)
        for algo in ALGOS:
            r = next(x for x in rows if x["scenario"] == name and x["algorithm"] == algo)
            v = f"{r['utilisation_pct']:.0f}%"
            cells.append(f"**{v}**" if abs(r["utilisation_pct"] - best) < 1e-9 else v)
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    lines += ["", "| Scenario | Algorithm | Goodput (Mbps) | Mean RTT (ms) | p95 RTT (ms) | Timeouts |", "|---|---|---|---|---|---|"]
    for name in ("Bufferbloat (4 BDP)", "Lossy Wi-Fi (1%)"):
        for algo in ALGOS:
            r = next(x for x in rows if x["scenario"] == name and x["algorithm"] == algo)
            lines.append(f"| {name} | {algo} | {r['goodput_mbps']:.2f} ± {r['goodput_sd']:.2f} | "
                         f"{r['mean_rtt_ms']:.0f} | {r['p95_rtt_ms']:.0f} | {r['timeouts']:.1f} |")
    with open(os.path.join(ROOT, "results", "results.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib not installed: skipped figures")
        return

    colours = {"SS": "#9ca3af", "AIMD": "#f59e0b", "TAHOE": "#3b82f6", "RENO": "#8b5cf6", "DLA": "#10b981"}
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)

    # Figure 1: utilisation by scenario.
    fig, ax = plt.subplots(figsize=(10, 4.2), dpi=150)
    width = 0.16
    for i, algo in enumerate(ALGOS):
        vals = [next(r for r in rows if r["scenario"] == n and r["algorithm"] == algo)["utilisation_pct"] for n, _ in SCENARIOS]
        ax.bar([x + (i - 2) * width for x in range(len(SCENARIOS))], vals, width, label=algo, color=colours[algo])
    ax.set_xticks(range(len(SCENARIOS)))
    ax.set_xticklabels([n for n, _ in SCENARIOS], fontsize=9)
    ax.set_ylabel("Link utilisation (%)")
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.set_title(f"Mean link utilisation, single flow, {DURATION:.0f} s runs, {len(SEEDS)} seeds")
    ax.legend(ncol=5, fontsize=8, frameon=False, loc="upper center")
    ax.grid(axis="y", alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(docs, "utilisation.png"))
    plt.close(fig)

    # Figure 2: congestion window over time on the lossy link.
    sc = dict(SCENARIOS)["Lossy Wi-Fi (1%)"]
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=150)
    for algo in ALGOS:
        _, f, _ = run_one(algo, sc, 1)
        series = cwnd_series(f, 0.05, DURATION)
        ax.plot([t for t, _ in series], [c for _, c in series], label=algo, color=colours[algo],
                lw=1.8 if algo == "DLA" else 1.1)
    sim = Simulator(cap_mbps=10, rtt_ms=50, duration=1)
    ax.axhline(sim.bdp0, color="#111827", ls="--", lw=0.8)
    ax.text(DURATION * 0.995, sim.bdp0 + 1, f"BDP ≈ {sim.bdp0:.0f} packets", ha="right", fontsize=8)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("cwnd (packets)")
    ax.set_title("Congestion window, 10 Mbps, 50 ms RTT, 1% random loss (seed 1)")
    ax.legend(ncol=5, fontsize=8, frameon=False, loc="upper left")
    ax.grid(alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(docs, "cwnd_lossy.png"))
    plt.close(fig)

    # Figure 3: throughput when capacity changes (10 -> 4 -> 15 Mbps).
    cap_s = [(0, 10.0), (DURATION / 3, 4.0), (2 * DURATION / 3, 15.0)]
    base = dict(cap=10, rtt=50, loss=0.0, buf=1.0)
    fig, ax = plt.subplots(figsize=(10, 3.8), dpi=150)
    for algo in ALGOS:
        _, f, _ = run_one(algo, base, 1, cap_schedule=cap_s)
        t, tp = throughput_series(f, 0.5, DURATION)
        ax.plot(t, tp, label=algo, color=colours[algo], lw=1.8 if algo == "DLA" else 1.1)
    ax.step([0, DURATION / 3, 2 * DURATION / 3, DURATION], [10, 4, 15, 15], where="post",
            color="#111827", ls="--", lw=0.8, label="capacity")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Goodput (Mbps)")
    ax.set_title("Changing network: capacity 10 → 4 → 15 Mbps, 50 ms RTT (seed 1)")
    ax.legend(ncol=6, fontsize=8, frameon=False, loc="upper left")
    ax.grid(alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(os.path.join(docs, "changing_network.png"))
    plt.close(fig)
    print("figures written to docs/")


if __name__ == "__main__":
    main()
