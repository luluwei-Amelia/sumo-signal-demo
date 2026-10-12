"""Plot observed vs simulated 15-minute counts for one day, one parameter set and one seed.

Usage: python plot_calibration.py calibration 20260922
       python plot_calibration.py validation 20260923
"""
import json
import sys

import matplotlib.pyplot as plt

from calibrate import CALIBRATION_DAY, RESULTS_DIR, evaluate

OBS, SIM, INK, MUTED, GRID = "#0b0b0b", "#2a78d6", "#0b0b0b", "#898781", "#e6e5e0"
ORDER = ["TDSAPR10001", "AID01101", "AID01102", "AID01222", "AID01223", "TDSAPR20001"]
LABEL = {"TDSAPR10001": "Eastbound 1", "AID01101": "Eastbound 2", "AID01102": "Eastbound 3",
         "AID01222": "Westbound 1", "AID01223": "Westbound 2", "TDSAPR20001": "Westbound 3"}
LOW_COVERAGE = 8  # periods of 30 s out of 30 in a bin
SEED = 0


def plot(day, params, tag):
    result, df = evaluate(day, params, f"{tag}_{day}", SEED)
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
        ax.set_title(f"{LABEL[det]} · {det}  (GEH<5: {ok:.0f}%)", fontsize=9, loc="left", color=INK)
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
    ax.scatter(df["count_15min"] * 4, df["sim_count"] * 4, s=16, color=SIM, alpha=0.8)
    ax.plot([0, 3200], [0, 3200], color=MUTED, lw=1)
    ax.set_xlim(0, 3200)
    ax.set_ylim(0, 3200)
    ax.set_aspect("equal")
    ax.set_xlabel("Observed (veh/h)", fontsize=8, color=MUTED)
    ax.set_ylabel("Simulated (veh/h)", fontsize=8, color=MUTED)
    ax.set_title(f"All {len(df)} 15-min comparisons\nGEH<5: {result['geh_pass_pct']}%", fontsize=9, loc="left")
    ax.tick_params(labelsize=7, colors=MUTED, length=0)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)

    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, fontsize=9)
    fig.suptitle(f"{tag.capitalize()}: {day[:4]}-{day[4:6]}-{day[6:]}, 07:00–10:00, Aberdeen Praya Road "
                 f"(tau={params['tau']}, minGap={params['minGap']}, seed {SEED})",
                 x=0.01, ha="left", fontsize=11)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    out = RESULTS_DIR / f"{tag}_{day}.png"
    fig.savefig(out)
    df.to_csv(RESULTS_DIR / f"{tag}_{day}_comparison.csv", index=False)
    return out, result


if __name__ == "__main__":
    tag = sys.argv[1] if len(sys.argv) > 1 else "calibration"
    day = sys.argv[2] if len(sys.argv) > 2 else CALIBRATION_DAY
    params = json.loads((RESULTS_DIR / "calibrated_params.json").read_text())
    print(plot(day, params, tag))
