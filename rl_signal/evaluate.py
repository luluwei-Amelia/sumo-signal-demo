"""Score every controller on every test scenario, with the same traffic for all of them.

Methods: Webster fixed-time and actuated (SUMO's own programs), longest-queue, max-pressure, and
the DQN from each training seed (the checkpoint chosen on validation traffic, see dqn.py).
Scenarios: train (same demand as training, unseen traffic seeds), day23, plus20, flip.
Each (scenario, traffic seed) route file is shared by all methods, so differences are paired.

Writes eval_runs.csv (one row per run) and eval_summary.csv to RESULTS_DIR, and the tables
rl_eval_runs and rl_training (suffixed _b1a, _b1b for those variants) to hk_corridor's SQLite
store, tagged with the code version. Under VARIANT b1a/b1b only the DQN is run; the baseline rows
are read from the variant-a results, since the baselines do not change.

Usage: python evaluate.py
"""
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

import demand
from common import BASELINE_RESULTS, EVAL_SEEDS, RESULTS_DIR, TRAIN_SEEDS, VARIANT, WORKERS
from controllers import LongestQueue, MaxPressure
from env import JunctionEnv

sys.path.append(str(Path(__file__).resolve().parent.parent / "hk_corridor"))
import db  # noqa: E402  (hk_corridor)

SCENARIOS = ["train", "day23", "plus20", "flip"]
BASELINES = ["webster", "actuated", "longest_queue", "max_pressure"]


def run(task):
    scenario, seed, method, train_seed = task
    routes = demand.write_routes(scenario, seed)
    label = f"eval_{VARIANT}_{scenario}_{seed}_{method}_{train_seed}"
    if method in ("webster", "actuated"):
        env = JunctionEnv(routes, seed, label, mode="native", plan=method)
        env.reset()
        env.run_native()
    else:
        if method == "dqn":
            import dqn
            ctl = dqn.DQNController(dqn.load(train_seed))
        else:
            ctl = LongestQueue() if method == "longest_queue" else MaxPressure()
        env = JunctionEnv(routes, seed, label)
        obs = env.reset()
        ctl.reset(env)
        done = False
        while not done:
            obs, _, done = env.step(ctl.act(env, obs))
    return {"scenario": scenario, "traffic_seed": seed, "method": method,
            "train_seed": train_seed, **env.score()}


def tasks():
    out = []
    for sc in SCENARIOS:
        for s in EVAL_SEEDS:
            if VARIANT == "a":
                out += [(sc, s, m, -1) for m in BASELINES]
            out += [(sc, s, "dqn", t) for t in TRAIN_SEEDS]
    return out


def paired_ci(diff):
    """Mean and 95% confidence interval (t distribution) of paired differences."""
    d = np.asarray(diff, float)
    half = stats.t.ppf(0.975, len(d) - 1) * d.std(ddof=1) / np.sqrt(len(d)) if len(d) > 1 else np.nan
    return d.mean(), half


def summarise(runs):
    """One row per scenario and method. DQN is first averaged over training seeds per traffic seed;
    its spread across training seeds is reported separately."""
    rows = []
    per_seed = runs.groupby(["scenario", "method", "traffic_seed"])[
        ["mean_delay", "mean_wait", "p95_delay", "max_wait"]].mean().reset_index()
    for sc in SCENARIOS:
        ref = per_seed[per_seed.scenario == sc].pivot(index="traffic_seed", columns="method",
                                                      values="mean_delay")
        for m in BASELINES + ["dqn"]:
            g = per_seed[(per_seed.scenario == sc) & (per_seed.method == m)]
            r = runs[(runs.scenario == sc) & (runs.method == m)]
            row = {"scenario": sc, "method": m, "runs": len(r), "valid_runs": int(r.valid.sum()),
                   "mean_delay": g.mean_delay.mean(), "mean_wait": g.mean_wait.mean(),
                   "p95_delay": g.p95_delay.mean(), "max_wait": g.max_wait.mean()}
            for other in ("max_pressure", "actuated"):
                if m != other:
                    d, h = paired_ci(ref[m] - ref[other])
                    row[f"vs_{other}"], row[f"vs_{other}_ci95"] = d, h
            if m == "dqn":
                by_train = r.groupby("train_seed").mean_delay.mean()
                row["dqn_train_seed_min"], row["dqn_train_seed_max"] = by_train.min(), by_train.max()
            rows.append(row)
    return pd.DataFrame(rows)


def training_log():
    rows = []
    for t in TRAIN_SEEDS:
        f = RESULTS_DIR / f"training_seed{t}.json"
        if f.exists():
            rows += json.loads(f.read_text(encoding="utf-8"))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    RESULTS_DIR.mkdir(exist_ok=True)
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        runs = pd.DataFrame(list(pool.map(run, tasks())))
    if VARIANT != "a":
        base = pd.read_csv(BASELINE_RESULTS / "eval_runs.csv")
        runs = pd.concat([base[base.method != "dqn"], runs], ignore_index=True)
    runs.to_csv(RESULTS_DIR / "eval_runs.csv", index=False)
    summary = summarise(runs)
    summary.to_csv(RESULTS_DIR / "eval_summary.csv", index=False)
    suffix = "" if VARIANT == "a" else f"_{VARIANT}"
    db.store("rl_eval_runs" + suffix, runs)
    log = training_log()
    if len(log):
        db.store("rl_training" + suffix, log)
    pd.set_option("display.width", 200)
    print(summary.round(1).to_string(index=False))
