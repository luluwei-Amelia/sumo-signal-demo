"""Grid search over driver parameters against one day's detector counts.

Each parameter set and seed runs the whole morning (06:45-10:15). Simulated 15-min counts
are compared with the TD counts using GEH on hourly rates; the 06:45-07:00 bin is warm-up.

    GEH = sqrt(2 (M - C)^2 / (M + C)),  M = simulated, C = observed, veh/h

Target (UK DMRB / TfL, meant for hourly flows): GEH < 5 for at least 85% of comparisons.
Demand is fitted to the same counts (demand.py), so a good fit mostly means the model doesn't
build queues the real road doesn't have.

Usage: python calibrate.py          (cars only)
       python calibrate.py census   (TD census vehicle mix)
"""
import itertools
import json
import subprocess
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from sumolib import checkBinary

from build_network import NET_FILE
from counts import BIN_MIN, END, START, load_counts, to_seconds
from detectors import locate_detectors

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
RUN_DIR = ROOT / "runs"          # raw simulation output (not committed)
RESULTS_DIR = ROOT / "results"
CALIBRATION_DAY = "20260922"
WARMUP_BINS = 1
SIM_END = to_seconds(END) - to_seconds(START) + 900  # let the last vehicles finish
SEEDS = range(5)

# SUMO defaults: tau 1.0 s, minGap 2.5 m, lcKeepRight 1
GRID = {
    "tau": [1.0, 0.8, 0.6, 0.5],     # Krauss model: desired minimum time headway (s); >= step length
    "minGap": [2.5, 2.0, 1.5, 1.0],  # gap to the leader when stopped (m)
    # Pull back to the kerbside lane (lane 0, "right" in SUMO terms). Detectors show heavy
    # offside-lane use; 0 beat 1 on lane shares in all 16 (tau, minGap) pairs of an earlier
    # grid (results/calibration_grid_stage2a_aid01222_3lanes.csv), so it's fixed at 0.
    "lcKeepRight": [0.0],
}
DEFAULT_PARAMS = {"tau": 1.0, "minGap": 2.5, "lcKeepRight": 1.0}

# name: (share, SUMO vClass, extra attributes)
# census = TD Annual Traffic Census 2024, core station 1004 (Aberdeen Tunnel), weekdays
# 07:00-10:00, mean of the three hours. Nearest classified station; Aberdeen Praya Road
# itself isn't classified and probably has more buses. The model can't carry this mix
# westbound (see README), so it's only a sensitivity run.
FLEETS = {
    "cars": {"car": (1.0, "passenger", {})},
    "census": {
        "car": (0.693, "passenger", {}),                 # private car 49.8, taxi 16.1, motor cycle 3.4
        "light_goods": (0.159, "delivery", {}),          # light goods vehicle 15.9
        "bus": (0.078, "bus", {}),                       # franchised double-deck 4.2, non-franchised 3.5
        "light_bus": (0.041, "bus", {"length": "7"}),    # private 2.2 and public 1.9 light bus
        "heavy_goods": (0.029, "truck", {"length": "10"}),  # medium and heavy goods vehicle 2.9
    },
}
LANE_NAMES = {"Slow Lane": 0, "Middle Lane": 1, "Fast Lane": 2}  # TD label -> SUMO lane index (3 lanes)
# Step must not exceed tau: with 1 s steps, tau < 1 gave thousands of collisions/teleports
# while the counts still looked fine.
STEP_LENGTH = 0.5
MAX_TELEPORTS = 50  # out of ~17,000 vehicles
TIE_PP = 2.0        # GEH pass rates within this many points of the best are treated as tied


def geh(m, c):
    return np.sqrt(2 * (m - c) ** 2 / np.maximum(m + c, 1e-9))


def vtype_xml(params, fleet="cars"):
    """Replace SUMO's default vehicle type by the fleet, all with the given parameters.

    Routes from routeSampler carry no type, so every vehicle draws its type from this
    distribution. Lengths, acceleration and speed limits come from SUMO's vClass defaults.
    """
    common = " ".join(f'{k}="{v}"' for k, v in params.items())
    lines = ['    <vTypeDistribution id="DEFAULT_VEHTYPE">']
    for name, (share, vclass, extra) in FLEETS[fleet].items():
        attrs = " ".join(f'{k}="{v}"' for k, v in extra.items())
        lines.append(f'        <vType id="{name}" vClass="{vclass}" probability="{share}" {common} {attrs} speedDev="0.1"/>')
    lines.append("    </vTypeDistribution>")
    return "\n".join(lines)


def run_sim(day, params, name, seed, routes=None, fleet="cars"):
    """Run one simulation; return simulated 15-minute counts and speeds per detector, lane shares,
    and SUMO's statistics. `routes` overrides the day's route file (a file name in network/)."""
    RUN_DIR.mkdir(exist_ok=True)
    loops_out = RUN_DIR / f"loops_{name}.xml"
    located = locate_detectors(NET_FILE)
    add = ['<?xml version="1.0" encoding="UTF-8"?>', "<additional>", vtype_xml(params, fleet)]
    for r in located.itertuples():
        for lane in range(r.lanes):
            add.append(f'    <inductionLoop id="{r.detector}_{lane}" lane="{r.edge}_{lane}" pos="{r.pos_m}" '
                       f'period="{BIN_MIN * 60}" file="{loops_out}"/>')
    add.append("</additional>")
    add_file = RUN_DIR / f"add_{name}.xml"
    add_file.write_text("\n".join(add) + "\n", encoding="utf-8")

    log = subprocess.run(
        [checkBinary("sumo"), "--net-file", str(NET_FILE),
         "--route-files", str(NET_DIR / (routes or f"routes_{day}.rou.xml")),
         "--additional-files", str(add_file), "--end", str(SIM_END), "--seed", str(seed),
         "--step-length", str(STEP_LENGTH),
         "--no-step-log", "true", "--no-warnings", "true", "--duration-log.statistics", "true"],
        capture_output=True, text=True, check=True,
    ).stdout
    stats = {}
    for line in log.splitlines():
        key, sep, value = line.strip().partition(": ")
        if sep and value:
            stats[key] = value

    rows = [i.attrib for i in ET.parse(loops_out).getroot().iter("interval")]
    sim = pd.DataFrame(rows).astype({"begin": float, "nVehContrib": float, "speed": float})
    sim["detector"] = sim["id"].str.rsplit("_", n=1).str[0]
    sim["lane"] = sim["id"].str.rsplit("_", n=1).str[1].astype(int)
    sim["bin_start"] = sim["begin"].astype(int)
    sim["speed_x_n"] = sim["speed"].clip(lower=0) * sim["nVehContrib"]
    out = sim.groupby(["bin_start", "detector"]).agg(sim_count=("nVehContrib", "sum"), sx=("speed_x_n", "sum"))
    out["sim_speed_kmh"] = out["sx"] / out["sim_count"].replace(0, np.nan) * 3.6
    scored = sim[sim["bin_start"] >= WARMUP_BINS * BIN_MIN * 60]
    lanes = scored.groupby(["detector", "lane"])["nVehContrib"].sum()
    lane_share = (lanes / lanes.groupby(level=0).transform("sum")).rename("sim_share")
    return out.drop(columns="sx").reset_index(), lane_share, stats


def observed_lane_share(day):
    """Share of each lane in the detector's total count, 07:00-10:00."""
    raw = pd.read_csv(ROOT / "data" / f"detectors_{day}.csv")
    raw = raw[(raw["valid"] == "Y") & (raw["period_from"] >= "07:00")]
    raw["lane"] = raw["lane"].map(LANE_NAMES)
    vol = raw.groupby(["detector", "lane"])["volume"].sum()
    return (vol / vol.groupby(level=0).transform("sum")).rename("obs_share")


def observed(day):
    """Real 15-minute counts, periods available, and volume-weighted mean speed per detector."""
    counts = load_counts(day)
    raw = pd.read_csv(ROOT / "data" / f"detectors_{day}.csv")
    raw = raw[(raw["valid"] == "Y") & (raw["volume"] > 0)].copy()
    t = raw["period_from"].map(to_seconds) - to_seconds(START)
    raw["bin_start"] = (t // (BIN_MIN * 60) * BIN_MIN * 60).astype(int)
    raw["sx"] = raw["speed_kmh"] * raw["volume"]
    spd = raw.groupby(["bin_start", "detector"])[["sx", "volume"]].sum()
    spd["obs_speed_kmh"] = spd["sx"] / spd["volume"]
    return counts.merge(spd[["obs_speed_kmh"]].reset_index(), on=["bin_start", "detector"], how="left")


def compare(day, sim):
    df = observed(day).merge(sim, on=["bin_start", "detector"], how="left")
    df = df[df["bin_start"] >= WARMUP_BINS * BIN_MIN * 60].copy()
    if df["sim_count"].isna().any():
        raise ValueError("a detector-bin is missing from the simulation output")
    per_hour = 60 / BIN_MIN
    df["geh"] = geh(df["sim_count"] * per_hour, df["count_15min"] * per_hour)
    return df


def pass_rate(df, minutes):
    """Share of comparisons with GEH < 5 after summing counts into windows aligned at 07:00."""
    first = WARMUP_BINS * BIN_MIN * 60
    window = (df["bin_start"] - first) // (minutes * 60)
    g = df.groupby(["detector", window])[["sim_count", "count_15min"]].sum()
    per_hour = 60 / minutes
    return round(100 * (geh(g["sim_count"] * per_hour, g["count_15min"] * per_hour) < 5).mean(), 1)


def score(df):
    return {
        "geh_pass_pct": pass_rate(df, 15),
        "geh_pass_30min_pct": pass_rate(df, 30),
        "geh_pass_60min_pct": pass_rate(df, 60),
        "mean_geh": round(df["geh"].mean(), 2),
        "count_ratio": round(df["sim_count"].sum() / df["count_15min"].sum(), 3),
        "speed_mae_kmh": round((df["sim_speed_kmh"] - df["obs_speed_kmh"]).abs().mean(), 1),
    }


def evaluate(day, params, name, seed, routes=None, fleet="cars"):
    sim, lane_share, stats = run_sim(day, params, name, seed, routes, fleet)
    df = compare(day, sim)
    shares = pd.concat([lane_share, observed_lane_share(day)], axis=1).fillna(0)
    # Lane shares are compared only where the model and the detector have the same lanes
    # (not AID01222: 4 lanes in the model, 3 through lanes at the detector).
    n_sim = lane_share.groupby(level=0).size()
    n_obs = observed_lane_share(day).groupby(level=0).size()
    same = [d for d in n_obs.index if n_sim.get(d) == n_obs[d]]
    shares = shares[shares.index.get_level_values(0).isin(same)]
    teleports = stats.get("Teleports", "0")
    result = {**params, "seed": seed, **score(df),
              "lane_share_mae_pp": round(100 * (shares["sim_share"] - shares["obs_share"]).abs().mean(), 1),
              "teleports": int(teleports.split()[0]), "teleport_detail": teleports,
              "not_inserted": int(stats.get("Waiting", "0").split()[0]),
              "still_running": int(stats.get("Running", "0").split()[0])}
    # A run that matches counts only by teleporting vehicles, or that leaves vehicles
    # outside the network, is not a valid fit.
    result["valid"] = (result["teleports"] <= MAX_TELEPORTS and result["not_inserted"] == 0
                       and result["still_running"] == 0)
    return result, df


def summarise_seeds(runs, keys):
    """Mean over seeds per parameter set, with the spread across seeds."""
    return runs.groupby(keys).agg(
        seeds=("seed", "count"),
        geh_pass_pct=("geh_pass_pct", "mean"), geh_pass_min=("geh_pass_pct", "min"),
        geh_pass_max=("geh_pass_pct", "max"), mean_geh=("mean_geh", "mean"),
        lane_share_mae_pp=("lane_share_mae_pp", "mean"),
        speed_mae_kmh=("speed_mae_kmh", "mean"), max_teleports=("teleports", "max"),
        max_not_inserted=("not_inserted", "max"), all_valid=("valid", "all"),
    ).round(2).reset_index()


def grid_search(day=CALIBRATION_DAY, fleet="cars", workers=4):
    combos = [dict(zip(GRID, values)) for values in itertools.product(*GRID.values())]
    jobs = [(c, f"cal_{fleet}_" + "_".join(str(v) for v in c.values()) + f"_s{s}", s) for c in combos for s in SEEDS]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        runs = pd.DataFrame(pool.map(lambda j: evaluate(day, *j, fleet=fleet)[0], jobs))
    suffix = "" if fleet == "cars" else f"_{fleet}"
    RESULTS_DIR.mkdir(exist_ok=True)
    runs.to_csv(RESULTS_DIR / f"calibration_runs{suffix}.csv", index=False)
    table = summarise_seeds(runs, list(GRID))
    table = table.sort_values(["all_valid", "geh_pass_pct", "mean_geh"], ascending=[False, False, True])
    # Selection: among valid sets within TIE_PP of the best count fit (about the spread
    # across seeds), take the one whose lane use is closest to the detectors.
    valid = table[table["all_valid"]] if table["all_valid"].any() else table
    tied = valid[valid["geh_pass_pct"] >= valid["geh_pass_pct"].max() - TIE_PP]
    chosen = tied.sort_values("lane_share_mae_pp").iloc[0]
    table["selected"] = (table[list(GRID)] == chosen[list(GRID)]).all(axis=1)
    table.to_csv(RESULTS_DIR / f"calibration_grid{suffix}.csv", index=False)
    best = {k: float(chosen[k]) for k in GRID}
    (RESULTS_DIR / f"calibrated_params{suffix}.json").write_text(json.dumps(best, indent=2), encoding="utf-8")
    return table, best


if __name__ == "__main__":
    import sys

    table, best = grid_search(fleet=sys.argv[1] if len(sys.argv) > 1 else "cars")
    print(table.to_string(index=False))
    print("best:", best)
