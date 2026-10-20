"""Figures: DQN learning curves and the comparison of all controllers per scenario.

Usage: python plot.py      (after dqn.py and evaluate.py)
"""
import json

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import RESULTS_DIR, ROOT, TRAIN_SEEDS
from evaluate import BASELINES, SCENARIOS

LABELS = {"webster": "Webster fixed-time", "actuated": "Actuated", "longest_queue": "Longest queue",
          "max_pressure": "Max-pressure", "dqn": "DQN"}


def learning_curves():
    fig, axes = plt.subplots(1, 2, figsize=(11, 4))
    for t in TRAIN_SEEDS:
        f = RESULTS_DIR / f"training_seed{t}.json"
        if not f.exists():
            continue
        log = pd.DataFrame(json.loads(f.read_text(encoding="utf-8")))
        axes[0].plot(log.episode, log.train_delay, lw=1, label=f"seed {t}")
        v = log.dropna(subset=["val_delay"])
        axes[1].plot(v.episode, v.val_delay, marker="o", ms=3, lw=1, label=f"seed {t}")
    axes[0].set(title="Training episodes (with exploration)", xlabel="episode", ylabel="mean delay (s/veh)",
                yscale="log")
    axes[1].set(title="Validation traffic (greedy policy)", xlabel="episode", ylabel="mean delay (s/veh)",
                yscale="log")
    for ax in axes:
        ax.legend(fontsize=8)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "learning_curves.png", dpi=150)


def comparison():
    runs = pd.read_csv(RESULTS_DIR / "eval_runs.csv")
    methods = BASELINES + ["dqn"]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    width = 0.16
    for j, metric in enumerate(["mean_delay", "max_wait"]):
        ax = axes[j]
        for i, m in enumerate(methods):
            means, lo, hi, dots = [], [], [], []
            for sc in SCENARIOS:
                r = runs[(runs.scenario == sc) & (runs.method == m)]
                per_seed = r.groupby("traffic_seed")[metric].mean()
                means.append(per_seed.mean())
                lo.append(per_seed.mean() - per_seed.min())
                hi.append(per_seed.max() - per_seed.mean())
                dots.append(r.groupby("train_seed")[metric].mean().to_numpy())
            # points rather than bars, because the axis is logarithmic
            x = np.arange(len(SCENARIOS)) + (i - 2) * width
            if m == "dqn":
                # mean of the seeds, plus one dot per training seed
                ax.scatter(x, means, marker="D", s=40, color=f"C{i}", label="DQN (mean of seeds)", zorder=3)
                for xi, d in zip(x, dots):
                    ax.scatter(np.full(len(d), xi + 0.04), d, s=12, color="black", zorder=4)
            else:
                ax.errorbar(x, means, yerr=[lo, hi], fmt="o", capsize=3, color=f"C{i}", label=LABELS[m])
        ax.set_xticks(np.arange(len(SCENARIOS)), SCENARIOS)
        ax.set_ylabel({"mean_delay": "mean delay (s/veh)", "max_wait": "longest single-vehicle wait (s)"}[metric])
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle("Markers: mean over 10 traffic seeds (log scale). Whiskers: min-max over traffic seeds. "
                 "Black dots: each of the 5 DQN training seeds", fontsize=9)
    fig.tight_layout()
    fig.savefig(RESULTS_DIR / "comparison.png", dpi=150)


def variants():
    """DQN variants a, b1a, b1b side by side, with actuated control and max-pressure for reference.

    Written to results/variants.png once all three variants have been evaluated.
    """
    files = {v: ROOT / f"results{s}" / "eval_runs.csv" for v, s in (("a", ""), ("b1a", "_b1a"), ("b1b", "_b1b"))}
    if not all(f.exists() for f in files.values()):
        return
    runs = {v: pd.read_csv(f) for v, f in files.items()}
    base = runs["a"]
    series = [("Actuated", base[base.method == "actuated"]), ("Max-pressure", base[base.method == "max_pressure"])]
    series += [(f"DQN {v}", r[r.method == "dqn"]) for v, r in runs.items()]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    width = 0.16
    for ax, metric in zip(axes, ["mean_delay", "max_wait"]):
        for i, (name, r) in enumerate(series):
            x = np.arange(len(SCENARIOS)) + (i - 2) * width
            means = [r[r.scenario == sc].groupby("traffic_seed")[metric].mean().mean() for sc in SCENARIOS]
            ax.scatter(x, means, marker="D" if name.startswith("DQN") else "o", s=40, color=f"C{i}",
                       label=name, zorder=3)
            if name.startswith("DQN"):
                for xi, sc in zip(x, SCENARIOS):
                    d = r[r.scenario == sc].groupby("train_seed")[metric].mean()
                    ax.scatter(np.full(len(d), xi + 0.04), d, s=10, color="black", zorder=4)
        ax.set_xticks(np.arange(len(SCENARIOS)), SCENARIOS)
        ax.set_ylabel({"mean_delay": "mean delay (s/veh)", "max_wait": "longest single-vehicle wait (s)"}[metric])
        ax.set_yscale("log")
        ax.grid(axis="y", alpha=0.3)
    axes[0].legend(fontsize=8)
    fig.suptitle("Mean over 10 traffic seeds (log scale). Black dots: each of the 5 training seeds. "
                 "a: 22 Sep demand; b1a: randomised demand; b1b: b1a + long-red weight", fontsize=9)
    fig.tight_layout()
    fig.savefig(ROOT / "results" / "variants.png", dpi=150)


if __name__ == "__main__":
    learning_curves()
    if (RESULTS_DIR / "eval_runs.csv").exists():
        comparison()
    variants()
    print("figures in", RESULTS_DIR)
