"""Observed vs simulated 15-minute counts for one stored run (no simulation is run here).

Reads results/<test>_<day>_comparison.csv, written by calibrate.py and validate.py for the
median seed of each test, and the matching row of the runs table for the caption.

Usage: python plot_calibration.py calibration
       python plot_calibration.py holdout
       python plot_calibration.py transfer
"""
import sys

import matplotlib.pyplot as plt
import pandas as pd

from sim import CALIBRATION_DAY, RESULTS_DIR, VALIDATION_DAY

OBS, SIM, INK, MUTED, GRID = "#0b0b0b", "#2a78d6", "#0b0b0b", "#898781", "#e6e5e0"
ORDER = ["TDSAPR10001", "AID01101", "AID01102", "AID01222", "AID01223", "TDSAPR20001"]
LABEL = {"TDSAPR10001": "Eastbound 1", "AID01101": "Eastbound 2", "AID01102": "Eastbound 3",
         "AID01222": "Westbound 1", "AID01223": "Westbound 2", "TDSAPR20001": "Westbound 3"}
HELD_OUT = {"AID01101", "AID01102", "AID01223", "TDSAPR20001"}
LOW_COVERAGE = 8  # periods of 30 s out of 30 in a bin
TITLE = {"calibration": "Calibration", "holdout": "Holdout test (interior detectors predicted)",
         "transfer": "Transfer test (demand re-fitted to all 23 Sep counts)"}


def caption(test, day):
    """Which seed is shown and how it compares with the other seeds."""
    if test == "calibration":
        runs = pd.read_csv(RESULTS_DIR / "calibration_runs.csv")
        grid = pd.read_csv(RESULTS_DIR / "calibration_grid.csv")
        chosen = grid[grid["selected"]].iloc[0]
        runs = runs[(runs["tau"] == chosen["tau"]) & (runs["minGap"] == chosen["minGap"])]
    else:
        runs = pd.read_csv(RESULTS_DIR / "validation_runs.csv", dtype={"day": str})
        runs = runs[(runs["test"] == test) & (runs["params"] == "calibrated")]
    ordered = runs.sort_values("geh_pass_pct").reset_index(drop=True)
    shown = ordered.iloc[(len(ordered) - 1) // 2]
    return (f"{TITLE[test]}, {day[:4]}-{day[4:6]}-{day[6:]}, tau={shown['tau']}, minGap={shown['minGap']}; "
            f"seed {int(shown['seed'])} shown (median of {len(runs)} seeds, "
            f"GEH<5 from {runs['geh_pass_pct'].min():.1f}% to {runs['geh_pass_pct'].max():.1f}%)")


def plot(test):
    day = CALIBRATION_DAY if test == "calibration" else VALIDATION_DAY
    df = pd.read_csv(RESULTS_DIR / f"{test}_{day}_comparison.csv")
    fig = plt.figure(figsize=(13, 7.5), dpi=140)
    grid = fig.add_gridspec(2, 4)
    for k, det in enumerate(ORDER):
        ax = fig.add_subplot(grid[k // 3, k % 3])
        d = df[df["detector"] == det].sort_values("bin_start").reset_index(drop=True)
        x = range(len(d))
        ax.plot(x, d["count_15min"] * 4, color=OBS, lw=2, label="Observed (TD detectors)")
        ax.plot(x, d["sim_count"] * 4, color=SIM, lw=2, ls="--", label="Simulated (SUMO)")
        low = d["periods"] < LOW_COVERAGE
        ax.scatter([i for i in x if low[i]], d.loc[low, "count_15min"] * 4, s=28, facecolor="white",
                   edgecolor=OBS, zorder=3, label=f"Observed from < {LOW_COVERAGE} of 30 periods")
        ok = (d["geh"] < 5).mean() * 100
        tag = ", held out" if test == "holdout" and det in HELD_OUT else ""
        ax.set_title(f"{LABEL[det]} · {det}\nGEH<5: {ok:.0f}%{tag}", fontsize=9, loc="left", color=INK)
        ax.set_ylim(0, 3200)
        ax.tick_params(labelsize=7, colors=MUTED, length=0)
        ax.set_xticks(range(0, len(d), 2), d["clock"].iloc[::2])
        ax.grid(axis="y", color=GRID, lw=0.8)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        if k % 3 == 0:
            ax.set_ylabel("veh/h", fontsize=8, color=MUTED)
    handles, labels = ax.get_legend_handles_labels()

    ax = fig.add_subplot(grid[:, 3])
    held = df["detector"].isin(HELD_OUT) if test == "holdout" else pd.Series(False, index=df.index)
    ax.scatter(df.loc[~held, "count_15min"] * 4, df.loc[~held, "sim_count"] * 4, s=16, color=SIM,
               alpha=0.8, label="Used to build demand")
    if held.any():
        ax.scatter(df.loc[held, "count_15min"] * 4, df.loc[held, "sim_count"] * 4, s=16,
                   color="#e0702b", alpha=0.8, label="Held out")
        ax.legend(fontsize=7, frameon=False, loc="upper left")
    ax.plot([0, 3200], [0, 3200], color=MUTED, lw=1)
    ax.set_xlim(0, 3200)
    ax.set_ylim(0, 3200)
    ax.set_aspect("equal")
    ax.set_xlabel("Observed (veh/h)", fontsize=8, color=MUTED)
    ax.set_ylabel("Simulated (veh/h)", fontsize=8, color=MUTED)
    ax.set_title(f"All {len(df)} 15-min comparisons\nGEH<5: {100 * (df['geh'] < 5).mean():.1f}%", fontsize=9, loc="left")
    ax.tick_params(labelsize=7, colors=MUTED, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle(caption(test, day), x=0.01, ha="left", fontsize=10)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    out = RESULTS_DIR / f"{test}_{day}.png"
    fig.savefig(out)
    return out


if __name__ == "__main__":
    print(plot(sys.argv[1] if len(sys.argv) > 1 else "calibration"))
