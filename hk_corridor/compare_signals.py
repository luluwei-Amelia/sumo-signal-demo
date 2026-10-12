"""Compare signal plans on the calibrated Aberdeen Praya Road model.

9 signals: 4 junctions and 5 mid-block pedestrian crossings. No pedestrians are modelled,
so the crossings keep netconvert's fixed timing in every plan and only the junctions change:

  fixed         netconvert phases and durations, fixed-time
  actuated      same phases, green extended on demand (what calibration used)
  webster       cycle and splits per junction from tlsCycleAdaptation
  common_cycle  webster splits stretched to one common cycle, offsets 0
  coordinated   common_cycle + offsets from tlsCoordinator

common_cycle is there to separate the cycle-length effect from the offsets. Yellows stay at
the network's values and greens are at least MIN_GREEN, so plans differ only in greens and
offsets. Webster is designed on 22 Sep demand with the measured saturation headway
(saturation.py). Plans are run on the 22 Sep calibration demand and on the 23 Sep holdout
demand (validate.py); every run is checked with the same validity rule as calibration.

Delay = timeLoss + departDelay (waiting to enter), warm-up departures excluded. Differences
to actuated are paired by seed. Degree of saturation X = Y C / (C - L) per junction, with Y
from tlsCycleAdaptation, goes to results/signal_design.csv.

Usage: python compare_signals.py
"""
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from scipy import stats
from sumolib import checkBinary

import db
from build_network import NET_FILE
from calibrate import vtype_xml
from sim import CALIBRATION_DAY, NET_DIR, RESULTS_DIR, RUN_DIR, SEEDS, VALIDATION_DAY, WORKERS, run_sumo, validity
from validate import holdout_routes

PLAN_DIR = NET_DIR / "signal_plans"
# 22 Sep runs on the calibration demand. 23 Sep runs on the holdout demand: the transfer
# demand (re-fitted to all 23 Sep counts) does not pass the validity rule with these parameters.
DEMAND = {CALIBRATION_DAY: f"routes_{CALIBRATION_DAY}.rou.xml", VALIDATION_DAY: f"routes_{VALIDATION_DAY}_holdout.rou.xml"}
WARMUP_S = 900          # 06:45-07:00
MIN_GREEN = 7           # s
YELLOW_S = 6            # netconvert's yellow at 3 of the 4 junctions (5 s at the fourth)
LOST_TIME_S = 4         # start-up plus clearance lost time per phase (tlsCycleAdaptation default)
PLANS = ["fixed", "actuated", "webster", "common_cycle", "coordinated"]
BASELINE = "actuated"


def net_programs():
    """{tls id: [(duration, state), ...]} of the network's own programmes."""
    return {tl.get("id"): [(int(float(p.get("duration"))), p.get("state")) for p in tl.iter("phase")]
            for tl in ET.parse(NET_FILE).getroot().iter("tlLogic")}


def is_green(state):
    return "G" in state or "g" in state


def crossings(programs):
    """Signals with a single green phase: pedestrian crossings in this network."""
    return sorted(t for t, phases in programs.items() if sum(is_green(s) for _, s in phases) == 1)


def write_programs(programs, path, program_id):
    add = ET.Element("additional")
    for tls, phases in sorted(programs.items()):
        tl = ET.SubElement(add, "tlLogic", id=tls, type="static", programID=program_id, offset="0")
        for duration, state in phases:
            ET.SubElement(tl, "phase", duration=str(duration), state=state)
    ET.indent(add)
    ET.ElementTree(add).write(path, encoding="utf-8", xml_declaration=True)
    return path


def sumo_tools():
    return Path(checkBinary("sumo")).parents[1] / "tools"


def saturation_headway():
    """Mean queue-discharge headway measured in the fitted model (run saturation.py first)."""
    path = RESULTS_DIR / "saturation_flow.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} missing: run saturation.py before compare_signals.py")
    return json.loads(path.read_text())["mean_headway_s"]


def webster_greens(skip, unified):
    """Green durations from tlsCycleAdaptation, keyed by tls id (one entry per phase, None for
    non-green), and the sum of critical flow ratios Y per junction."""
    raw = PLAN_DIR / "webster_raw.add.xml"
    cmd = [sys.executable, str(sumo_tools() / "tlsCycleAdaptation.py"), "-n", f"../{NET_FILE.name}",
           "-r", f"../routes_{CALIBRATION_DAY}.rou.xml", "--min-cycle", "40", "--max-cycle", "120",
           "-y", str(YELLOW_S),  # the network's yellow; the tool's default (4 s) would understate lost time
           "-l", str(LOST_TIME_S), "-H", str(saturation_headway()), "--write-critical-flows",
           "--skip", ",".join(skip), "-o", raw.name]
    log = subprocess.run(cmd + (["-u"] if unified else []), check=True, capture_output=True, text=True,
                         cwd=PLAN_DIR).stdout.splitlines()
    flow_ratio = {}
    for prev, line in zip(log, log[1:]):
        if line.startswith("sum of the critical flow ratios"):
            flow_ratio[prev.split()[0]] = float(line.split(":")[1])
    greens = {tl.get("id"): [int(float(p.get("duration"))) if is_green(p.get("state")) else None
                             for p in tl.iter("phase")]
              for tl in ET.parse(raw).getroot().iter("tlLogic")}
    raw.unlink()
    return greens, flow_ratio


def apply_greens(base, greens):
    """Network phases with Webster green durations (at least MIN_GREEN); yellow and red unchanged."""
    out = {}
    for tls, new in greens.items():
        if tls not in base:
            continue
        if len(new) != len(base[tls]):
            raise ValueError(f"{tls}: tlsCycleAdaptation changed the phase structure")
        out[tls] = [(max(MIN_GREEN, g) if g is not None else d, s) for g, (d, s) in zip(new, base[tls])]
    return out


def stretch_to_common_cycle(programs):
    """Lengthen green phases proportionally so every junction runs the longest cycle."""
    target = max(sum(d for d, _ in phases) for phases in programs.values())
    out = {}
    for tls, phases in programs.items():
        green_total = sum(d for d, s in phases if is_green(s))
        extra = target - sum(d for d, _ in phases)
        new = [(round(d * (1 + extra / green_total)) if is_green(s) else d, s) for d, s in phases]
        longest = max((i for i, (_, s) in enumerate(new) if is_green(s)), key=lambda i: new[i][0])
        d, s = new[longest]
        new[longest] = (d + target - sum(x for x, _ in new), s)  # fix rounding
        out[tls] = new
    return out, target


def design_plans():
    """Write the signal programmes for every plan; return {plan: [additional files]} and a design table."""
    PLAN_DIR.mkdir(exist_ok=True)
    base = net_programs()
    ped = crossings(base)
    junctions = {t: p for t, p in base.items() if t not in ped}
    files = {"crossings": write_programs({t: base[t] for t in ped}, PLAN_DIR / "crossings.add.xml", "crossing")}

    fixed = write_programs(junctions, PLAN_DIR / "fixed.add.xml", "fixed")
    greens, flow_ratio = webster_greens(ped, unified=False)
    webster = apply_greens(junctions, greens)
    common, cycle = stretch_to_common_cycle(apply_greens(junctions, webster_greens(ped, unified=True)[0]))
    write_programs(webster, PLAN_DIR / "webster.add.xml", "webster")
    write_programs(common, PLAN_DIR / "common_cycle.add.xml", "common_cycle")
    write_programs(common, PLAN_DIR / "coordinated.add.xml", "coordinated")
    offsets_raw = PLAN_DIR / "offsets_raw.add.xml"
    subprocess.run([sys.executable, str(sumo_tools() / "tlsCoordinator.py"), "-n", f"../{NET_FILE.name}",
                    "-r", f"../routes_{CALIBRATION_DAY}.rou.xml", "-a", "coordinated.add.xml",
                    "-o", offsets_raw.name], check=True, capture_output=True, text=True, cwd=PLAN_DIR)
    offsets = {tl.get("id"): tl.get("offset") for tl in ET.parse(offsets_raw).getroot().iter("tlLogic")
               if tl.get("id") in junctions}
    offsets_raw.unlink()
    off = ET.Element("additional")
    for tls, value in sorted(offsets.items()):
        ET.SubElement(off, "tlLogic", id=tls, programID="coordinated", offset=value)
    ET.indent(off)
    ET.ElementTree(off).write(PLAN_DIR / "coordinated_offsets.add.xml", encoding="utf-8", xml_declaration=True)

    plan_files = {
        "fixed": [fixed], "actuated": [],
        "webster": [PLAN_DIR / "webster.add.xml"], "common_cycle": [PLAN_DIR / "common_cycle.add.xml"],
        "coordinated": [PLAN_DIR / "coordinated.add.xml", PLAN_DIR / "coordinated_offsets.add.xml"],
    }
    plan_files = {p: [files["crossings"], *f] for p, f in plan_files.items()}
    def degree_of_saturation(t, phases):
        """Webster's intersection degree of saturation X = Y C / (C - L) for a given programme."""
        y = flow_ratio.get(t)
        c = sum(d for d, _ in phases)
        lost = LOST_TIME_S * sum(is_green(s) for _, s in phases)
        return None if y is None else round(y * c / (c - lost), 2)

    design = pd.DataFrame([
        {"junction": t, "flow_ratio_Y": flow_ratio.get(t),
         "fixed_cycle_s": sum(d for d, _ in junctions[t]), "X_fixed": degree_of_saturation(t, junctions[t]),
         "webster_cycle_s": sum(d for d, _ in webster[t]), "X_webster": degree_of_saturation(t, webster[t]),
         "common_cycle_s": cycle, "X_common": degree_of_saturation(t, common[t]),
         "offset_s": offsets.get(t), "webster_greens_s": " / ".join(str(d) for d, s in webster[t] if is_green(s)),
         "saturation_headway_s": saturation_headway()}
        for t in junctions])
    design.to_csv(RESULTS_DIR / "signal_design.csv", index=False)
    return plan_files, design, ped


def run(plan, plan_files, day, seed):
    name = f"sig_{plan}_{day}_{seed}"
    trips = RUN_DIR / f"{name}.tripinfo.xml"
    adds = [RUN_DIR / "calibrated_vtype.add.xml", *plan_files]
    stats = run_sumo(NET_DIR / DEMAND[day], adds, seed, ["--tripinfo-output", str(trips)])
    t = pd.DataFrame([x.attrib for x in ET.parse(trips).getroot().iter("tripinfo")])
    t = t.astype({"depart": float, "departDelay": float, "duration": float, "timeLoss": float})
    t = t[t["depart"] >= WARMUP_S].copy()
    t["delay"] = t["timeLoss"] + t["departDelay"]
    t["entry"] = t["departLane"].str.rsplit("_", n=1).str[0]
    worst = t.groupby("entry")["departDelay"].mean()
    trips.unlink()
    return {"plan": plan, "day": day, "seed": seed, "vehicles": len(t),
            "mean_delay_s": t["delay"].mean(), "mean_time_loss_s": t["timeLoss"].mean(),
            "mean_depart_delay_s": t["departDelay"].mean(), "p95_delay_s": t["delay"].quantile(0.95),
            "worst_entry": worst.idxmax(), "worst_entry_wait_s": worst.max(), **validity(stats)}


def ci95(x):
    return stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / len(x) ** 0.5


def summarise(runs):
    base = runs[runs["plan"] == BASELINE].set_index(["day", "seed"])["mean_delay_s"]
    runs = runs.join(base.rename("baseline_delay_s"), on=["day", "seed"])
    runs["diff_vs_actuated_s"] = runs["mean_delay_s"] - runs["baseline_delay_s"]
    summary = runs.groupby(["day", "plan"]).agg(
        mean_delay_s=("mean_delay_s", "mean"), delay_ci95_s=("mean_delay_s", ci95),
        mean_time_loss_s=("mean_time_loss_s", "mean"), mean_depart_delay_s=("mean_depart_delay_s", "mean"),
        diff_vs_actuated_s=("diff_vs_actuated_s", "mean"), diff_ci95_s=("diff_vs_actuated_s", ci95),
        p95_delay_s=("p95_delay_s", "mean"), worst_entry_wait_s=("worst_entry_wait_s", "max"),
        vehicles=("vehicles", "mean"), max_teleports=("teleports", "max"),
        valid_runs=("valid", "sum")).round(1).reset_index()
    baseline_mean = summary[summary["plan"] == BASELINE].set_index("day")["mean_delay_s"]
    summary["diff_vs_actuated_pct"] = (100 * summary["diff_vs_actuated_s"]
                                       / summary["day"].map(baseline_mean)).round(0)
    summary["plan"] = pd.Categorical(summary["plan"], PLANS)
    return summary.sort_values(["day", "plan"])


def main():
    RUN_DIR.mkdir(exist_ok=True)
    params = json.loads((RESULTS_DIR / "calibrated_params.json").read_text())
    (RUN_DIR / "calibrated_vtype.add.xml").write_text(
        f"<additional>\n{vtype_xml(params)}\n</additional>\n", encoding="utf-8")
    if not (NET_DIR / DEMAND[VALIDATION_DAY]).exists():
        holdout_routes(VALIDATION_DAY)
    plan_files, design, ped = design_plans()
    print(f"pedestrian crossings held fixed: {ped}")
    print(design.to_string(index=False))
    jobs = [(p, plan_files[p], d, s) for p in PLANS for d in DEMAND for s in SEEDS]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        runs = pd.DataFrame(pool.map(lambda j: run(*j), jobs))
    runs.to_csv(RESULTS_DIR / "signal_runs.csv", index=False)
    db.store("signal_runs", runs)
    summary = summarise(runs)
    summary.to_csv(RESULTS_DIR / "signal_comparison.csv", index=False)
    return summary


if __name__ == "__main__":
    print(main().to_string(index=False))
