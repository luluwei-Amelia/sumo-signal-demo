"""Check the fitted parameters on 23 September in two ways.

transfer  Demand re-estimated from all six 23 September counts; the 22 September
          parameters are reused. The counts are partly matched by construction.
holdout   Spatial holdout on the four interior detectors, conditional on the 22 September
          route pattern: the 22 September routes are rescaled per direction and 15-min bin by
          the 23 September count at the first detector of each direction (TDSAPR10001
          eastbound, AID01222 westbound). Interior counts are never used, so they are a
          prediction. A naive forecast (22 September interior count x the same ratio) is the
          benchmark, compared bin by bin with a sign test.

The interior scores are also given at 60 min, without bins built from fewer than 8 of 30
periods, and against a sampling-noise ceiling (counts.sampling_noise): the share of bins a
perfect model would pass given how few periods were retrieved.

Usage: python validate.py
"""
import json
import random
import shutil
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
from scipy.stats import binomtest

import db
from build_network import NET_FILE
from calibrate import DEFAULT_PARAMS, evaluate, geh, median_run, pass_rate, summarise_seeds
from counts import BIN_MIN, load_counts, sampling_noise
from detectors import DETECTORS, locate_detectors
from sim import CALIBRATION_DAY, NET_DIR, RESULTS_DIR, RUN_DIR, SEEDS, VALIDATION_DAY, WORKERS

ENTRY = {"east": "TDSAPR10001", "west": "AID01222"}
INTERIOR = [d for d in DETECTORS if d not in ENTRY.values()]
LOW_COVERAGE = 8  # bins built from fewer of the 30 periods are left out in the sensitivity check


def holdout_routes(day=VALIDATION_DAY):
    """Write routes for `day` from the calibration-day routes, rescaled at the entry detectors."""
    located = locate_detectors(NET_FILE).set_index("detector")
    entry_edge = {direction: located.loc[d, "edge"] for direction, d in ENTRY.items()}
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


def naive_forecast(day=VALIDATION_DAY):
    """Interior bins: calibration-day count x entry-detector ratio, against the observed count."""
    c_cal = load_counts(CALIBRATION_DAY).set_index(["bin_start", "detector"])["count_15min"]
    obs = load_counts(day)
    c_day = obs.set_index(["bin_start", "detector"])["count_15min"]
    rows = []
    for r in obs.itertuples():
        if r.detector not in INTERIOR or r.bin_start < BIN_MIN * 60:
            continue
        entry = ENTRY[DETECTORS[r.detector]]
        pred = c_cal[(r.bin_start, r.detector)] * c_day[(r.bin_start, entry)] / c_cal[(r.bin_start, entry)]
        rows.append({"bin_start": r.bin_start, "detector": r.detector, "periods": r.periods,
                     "pred": pred, "count_15min": r.count_15min})
    df = pd.DataFrame(rows)
    df["geh"] = geh(df["pred"] * 4, df["count_15min"] * 4)
    return df


def interior_scores(df, pred):
    """Pass rates on the interior detectors at 15 and 60 min, and at 15 min without low-coverage bins."""
    inner = df[df["detector"].isin(INTERIOR)]
    covered = inner[inner["periods"] >= LOW_COVERAGE]
    g15 = geh(covered[pred] * 4, covered["count_15min"] * 4)
    return {"interior_15min_pct": pass_rate(inner, 15, pred), "interior_60min_pct": pass_rate(inner, 60, pred),
            "interior_15min_covered_pct": round(100 * (g15 < 5).mean(), 1),
            "interior_mean_geh": round(geh(inner[pred] * 4, inner["count_15min"] * 4).mean(), 2)}


def validate(day=VALIDATION_DAY):
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
        return {"day": d, "test": test, "params": label, **result, **interior_scores(df, "sim_count")}

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        runs = pd.DataFrame(pool.map(run, jobs))
    runs.to_csv(RESULTS_DIR / "validation_runs.csv", index=False)
    db.store("validation_runs", runs)

    table = summarise_seeds(runs, ["day", "test", "params"])
    extra = runs.groupby(["day", "test", "params"])[
        ["geh_pass_30min_pct", "geh_pass_60min_pct", "interior_15min_pct", "interior_60min_pct",
         "interior_15min_covered_pct", "interior_mean_geh"]].mean().round(1)
    table = table.merge(extra.reset_index(), on=["day", "test", "params"])
    naive = naive_forecast(day)
    table = pd.concat([table, pd.DataFrame([{"day": day, "test": "holdout", "params": "naive forecast",
                                             **interior_scores(naive, "pred")}])])
    table.to_csv(RESULTS_DIR / "validation_summary.csv", index=False)

    # Figures show the median seed of each test.
    for test in ("holdout", "transfer"):
        shown = median_run(runs[(runs["test"] == test) & (runs["params"] == "calibrated")])
        shutil.copy(RUN_DIR / f"compare_{shown['run']}.csv", RESULTS_DIR / f"{test}_{day}_comparison.csv")

    # Bin by bin on the interior detectors: is the model (mean GEH over seeds) closer than the
    # naive forecast? Sign test over the bins where they differ.
    hold = pd.concat([pd.read_csv(RUN_DIR / f"compare_{r}.csv") for r in runs.loc[runs["test"] == "holdout", "run"]])
    model_geh = hold[hold["detector"].isin(INTERIOR)].groupby(["bin_start", "detector"])["geh"].mean()
    paired = naive.set_index(["bin_start", "detector"])["geh"].to_frame("naive").join(model_geh.rename("model"))
    model_better = int((paired["model"] < paired["naive"]).sum())
    differ = int((paired["model"] != paired["naive"]).sum())
    noise = sampling_noise(day)
    noise = noise[noise["detector"].isin(INTERIOR) & (noise["bin_start"] >= BIN_MIN * 60)]
    summary = {
        "interior_bins": len(paired), "bins_model_closer": model_better,
        "sign_test_p": round(binomtest(model_better, differ, 0.5).pvalue, 3),
        "perfect_model_ceiling_15min_pct": round(100 * (1 - noise["share_geh_ge_5"].mean()), 1),
    }
    (RESULTS_DIR / "holdout_tests.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return table


if __name__ == "__main__":
    print(validate().to_string(index=False))
