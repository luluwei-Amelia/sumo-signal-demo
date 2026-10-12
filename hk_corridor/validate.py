"""Check the fitted parameters on 23 September in two ways.

transfer  Demand re-estimated from all six 23 September counts; the 22 September
          parameters are reused. Tests whether the parameters transfer to another day,
          but the counts are partly matched by construction.
holdout   Demand built from the 22 September routes, rescaled per direction and
          15-minute bin by the 23 September count at the first detector of each
          direction (TDSAPR10001 eastbound, AID01222 westbound). The four interior
          detectors are never used and are predicted. A naive forecast (22 September
          interior count x the same ratio) is scored alongside as a benchmark.

Usage: python validate.py 20260923
"""
import json
import random
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from build_network import NET_FILE
from calibrate import (BIN_MIN, CALIBRATION_DAY, DEFAULT_PARAMS, NET_DIR, RESULTS_DIR, SEEDS, evaluate, geh,
                       summarise_seeds)
from counts import load_counts
from detectors import DETECTORS, locate_detectors

ENTRY = {"east": "TDSAPR10001", "west": "AID01222"}
INTERIOR = [d for d in DETECTORS if d not in ENTRY.values()]


def holdout_routes(day):
    """Write routes for `day` from the calibration-day routes, rescaled at the entry detectors."""
    located = locate_detectors(NET_FILE).set_index("detector")
    det_edges = {d: located.loc[d, "edge"] for d in DETECTORS}
    entry_edge = {direction: det_edges[d] for direction, d in ENTRY.items()}
    c_cal = load_counts(CALIBRATION_DAY).set_index(["bin_start", "detector"])["count_15min"]
    c_day = load_counts(day).set_index(["bin_start", "detector"])["count_15min"]

    tree = ET.parse(NET_DIR / f"routes_{CALIBRATION_DAY}.rou.xml")
    root = tree.getroot()
    vehicles = list(root.iter("vehicle"))
    groups = {}
    for v in vehicles:
        edges = v.find("route").get("edges").split()
        direction = next((d for d, e in entry_edge.items() if e in edges), None)
        b = int(float(v.get("depart")) // (BIN_MIN * 60) * BIN_MIN * 60)
        groups.setdefault((b, direction), []).append(v)

    rng = random.Random(42)
    keep = []
    for (b, direction), vs in sorted(groups.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        if direction is None:
            keep += vs  # vehicles that pass no entry detector are left as they were
            continue
        det = ENTRY[direction]
        ratio = c_day.get((b, det), np.nan) / c_cal.get((b, det), np.nan)
        target = len(vs) if np.isnan(ratio) else round(len(vs) * ratio)
        if target <= len(vs):
            keep += rng.sample(vs, target)
        else:
            keep += vs
            for k, v in enumerate(rng.choices(vs, k=target - len(vs))):
                copy = ET.fromstring(ET.tostring(v))
                copy.set("id", f"{v.get('id')}_dup{k}")
                keep.append(copy)
    for v in vehicles:
        root.remove(v)
    for v in sorted(keep, key=lambda x: float(x.get("depart"))):
        root.append(v)
    out = NET_DIR / f"routes_{day}_holdout.rou.xml"
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out, len(vehicles), len(keep)


def naive_forecast(day):
    """GEH of 'calibration-day interior count x entry ratio' against the observed interior count."""
    c_cal = load_counts(CALIBRATION_DAY).set_index(["bin_start", "detector"])["count_15min"]
    c_day = load_counts(day).set_index(["bin_start", "detector"])["count_15min"]
    direction = {d: ("east" if DETECTORS[d] == "east" else "west") for d in INTERIOR}
    rows = []
    for (b, det), obs in c_day.items():
        if det not in INTERIOR or b < BIN_MIN * 60:
            continue
        entry = ENTRY[direction[det]]
        pred = c_cal[(b, det)] * c_day[(b, entry)] / c_cal[(b, entry)]
        rows.append({"bin_start": b, "detector": det, "pred": pred, "obs": obs})
    df = pd.DataFrame(rows)
    g = geh(df["pred"] * 4, df["obs"] * 4)
    return round(100 * (g < 5).mean(), 1), round(g.mean(), 2)


def validate(day):
    calibrated = json.loads((RESULTS_DIR / "calibrated_params.json").read_text())
    holdout_file, n_cal, n_day = holdout_routes(day)
    print(f"holdout routes: {n_cal} vehicles on {CALIBRATION_DAY} -> {n_day} for {day}")
    jobs = [(d, label, params, s, None) for d in (CALIBRATION_DAY, day)
            for label, params in (("default", DEFAULT_PARAMS), ("calibrated", calibrated)) for s in SEEDS]
    jobs += [(day, "calibrated", calibrated, s, holdout_file.name) for s in SEEDS]

    def run(job):
        d, label, params, s, routes = job
        test = "holdout" if routes else ("calibration" if d == CALIBRATION_DAY else "transfer")
        result, df = evaluate(d, params, f"val_{test}_{label}_{d}_s{s}", s, routes=routes)
        interior = df[df["detector"].isin(INTERIOR)]
        return {"day": d, "test": test, "params": label, **result,
                "interior_geh_pass_pct": round(100 * (interior["geh"] < 5).mean(), 1),
                "interior_mean_geh": round(interior["geh"].mean(), 2)}

    with ThreadPoolExecutor(max_workers=4) as pool:
        runs = pd.DataFrame(pool.map(run, jobs))
    runs.to_csv(RESULTS_DIR / "validation_runs.csv", index=False)
    table = summarise_seeds(runs, ["day", "test", "params"])
    extra = runs.groupby(["day", "test", "params"])[
        ["geh_pass_30min_pct", "geh_pass_60min_pct", "interior_geh_pass_pct", "interior_mean_geh"]].mean().round(1)
    table = table.merge(extra.reset_index(), on=["day", "test", "params"])
    naive_pass, naive_geh = naive_forecast(day)
    table = pd.concat([table, pd.DataFrame([{"day": day, "test": "holdout", "params": "naive forecast",
                                             "interior_geh_pass_pct": naive_pass, "interior_mean_geh": naive_geh}])])
    table.to_csv(RESULTS_DIR / "validation_summary.csv", index=False)
    return table


if __name__ == "__main__":
    print(validate(sys.argv[1] if len(sys.argv) > 1 else "20260923").to_string(index=False))
