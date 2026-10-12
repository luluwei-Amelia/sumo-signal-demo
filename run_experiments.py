"""Run every (signal strategy x demand level x seed) combination and summarise the results.

Usage:  python run_experiments.py          (add --gui to watch one run in sumo-gui)
Outputs: results/summary.csv, results/webster_plans.csv, results/delay_by_strategy.png
"""
import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
import traci
from scipy import stats
from sumolib import checkBinary

from build_network import build_network
from demand import DEMAND_LEVELS, write_all_routes
from saturation import measure_saturation_flow
from signal_plans import STRATEGIES, write_signal_plans

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output"    # raw per-run files (not committed)
RESULTS_DIR = ROOT / "results"  # summary tables and chart (committed)
SEEDS = range(10)
WARMUP = 600  # s; the network starts empty, so the first 10 minutes are not representative


def run_once(net_file, route_file, tls_file, tls_id, strategy, seed, gui=False):
    tripinfo = OUTPUT_DIR / f"tripinfo_{route_file.stem}_{strategy}_{seed}.xml"
    cmd = [
        checkBinary("sumo-gui" if gui else "sumo"),
        "--net-file", str(net_file),
        "--route-files", str(route_file),
        "--additional-files", str(tls_file),
        "--seed", str(seed),
        "--tripinfo-output", str(tripinfo),
        "--no-step-log", "true",
        "--no-warnings", "true",
    ]
    if gui:
        cmd += ["--delay", "50"]  # slow down so the animation is watchable
    traci.start(cmd)
    traci.trafficlight.setProgram(tls_id, strategy)
    incoming_lanes = set(traci.trafficlight.getControlledLanes(tls_id))

    # Peak number of halting vehicles summed over all approach lanes (not a per-approach queue).
    max_queue = 0
    while traci.simulation.getMinExpectedNumber() > 0:
        traci.simulationStep()
        if traci.simulation.getTime() >= WARMUP:
            queue = sum(traci.lane.getLastStepHaltingNumber(lane) for lane in incoming_lanes)
            max_queue = max(max_queue, queue)
    traci.close()

    # Only count vehicles that entered after the warm-up period.
    trips = [
        t for t in ET.parse(tripinfo).getroot().findall("tripinfo")
        if float(t.get("depart")) >= WARMUP
    ]
    return {
        "vehicles": len(trips),
        "avg_delay_s": sum(float(t.get("timeLoss")) for t in trips) / len(trips),
        "avg_travel_time_s": sum(float(t.get("duration")) for t in trips) / len(trips),
        "max_queue_veh": max_queue,
    }


def summarise(runs):
    """Mean of each metric over seeds, plus a 95% confidence interval for delay."""
    grouped = runs.groupby(["demand", "strategy"], sort=False)
    summary = grouped[["vehicles", "avg_delay_s", "avg_travel_time_s", "max_queue_veh"]].mean()
    n = len(SEEDS)
    t_crit = stats.t.ppf(0.975, n - 1)
    summary["delay_ci95_s"] = t_crit * grouped["avg_delay_s"].std() / math.sqrt(n)
    return summary.round(1).reset_index()


def plot_delay(summary, path):
    colors = ["#2a78d6", "#eb6834", "#1baf7a"]  # fixed categorical order
    levels = list(DEMAND_LEVELS)
    delay = summary.pivot(index="demand", columns="strategy", values="avg_delay_s")
    ci = summary.pivot(index="demand", columns="strategy", values="delay_ci95_s")
    width = 0.26

    fig, ax = plt.subplots(figsize=(7, 4), dpi=150)
    for k, strategy in enumerate(STRATEGIES):
        x = [i + (k - 1) * width for i in range(len(levels))]
        bars = ax.bar(
            x, delay.loc[levels, strategy], width=width - 0.03, color=colors[k], label=strategy,
            yerr=ci.loc[levels, strategy], error_kw={"ecolor": "#52514e", "elinewidth": 1, "capsize": 3},
        )
        ax.bar_label(bars, fmt="%.0f", fontsize=8, color="#52514e", padding=4)

    ax.set_xticks(range(len(levels)), [f"{lvl} demand" for lvl in levels])
    ax.set_ylabel("Average delay per vehicle (s)", color="#52514e")
    ax.set_title(
        f"Signal strategy vs. delay (mean of {len(SEEDS)} seeds, 95% CI)", loc="left", color="#0b0b0b"
    )
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#e6e5e0", linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.tick_params(colors="#898781", length=0)
    fig.tight_layout()
    fig.savefig(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gui", action="store_true", help="watch a single run in sumo-gui")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(exist_ok=True)
    RESULTS_DIR.mkdir(exist_ok=True)

    net_file = build_network()
    route_files = write_all_routes()
    sat_flow = measure_saturation_flow(net_file)
    print(f"Measured saturation flow: {sat_flow:.0f} veh/h/lane")

    signal_files, webster = {}, []
    for level, multiplier in DEMAND_LEVELS.items():
        tls_id, signal_files[level], greens = write_signal_plans(net_file, level, multiplier, sat_flow)
        webster.append({"demand": level, "saturation_flow_veh_h_lane": round(sat_flow),
                        "ns_green_s": greens[0], "ew_green_s": greens[2]})

    if args.gui:
        print(run_once(net_file, route_files["high"], signal_files["high"], tls_id, "equal", 0, gui=True))
        return

    rows = []
    for level, route_file in route_files.items():
        for strategy in STRATEGIES:
            for seed in SEEDS:
                result = run_once(net_file, route_file, signal_files[level], tls_id, strategy, seed)
                rows.append({"demand": level, "strategy": strategy, "seed": seed, **result})
                print(f"{level:6} {strategy:8} seed={seed}  delay={result['avg_delay_s']:.1f}s")

    summary = summarise(pd.DataFrame(rows))
    summary.to_csv(RESULTS_DIR / "summary.csv", index=False)
    pd.DataFrame(webster).to_csv(RESULTS_DIR / "webster_plans.csv", index=False)
    plot_delay(summary, RESULTS_DIR / "delay_by_strategy.png")
    print(pd.DataFrame(webster).to_string(index=False))
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
