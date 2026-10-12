"""Bar chart of mean delay per vehicle for each signal plan on both days (from signal_comparison.csv)."""
import matplotlib.pyplot as plt
import pandas as pd

from compare_signals import BASELINE, PLANS
from sim import CALIBRATION_DAY, RESULTS_DIR, SEEDS

INK, MUTED, GRID = "#0b0b0b", "#898781", "#e6e5e0"
COLOR = {"fixed": "#b8b6ae", "actuated": "#0b0b0b", "webster": "#2a78d6",
         "common_cycle": "#8cb8e8", "coordinated": "#1a9e6e"}
LABEL = {"fixed": "Fixed-time\n(netconvert)", "actuated": "Actuated\n(SUMO default)",
         "webster": "Webster\n(per junction)", "common_cycle": "Common cycle\n(offsets 0)",
         "coordinated": "Common cycle\n+ offsets"}


def main():
    df = pd.read_csv(RESULTS_DIR / "signal_comparison.csv", dtype={"day": str})
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), dpi=140, sharey=True)
    for ax, (day, d) in zip(axes, df.groupby("day")):
        d = d.set_index("plan").loc[PLANS]
        bars = ax.bar(range(len(PLANS)), d["mean_delay_s"], yerr=d["delay_ci95_s"], capsize=4,
                      color=[COLOR[p] for p in PLANS], error_kw={"ecolor": MUTED, "lw": 1})
        for bar, (plan, row) in zip(bars, d.iterrows()):
            text = f"{row.mean_delay_s:.0f} s"
            if plan != BASELINE:
                text += f"\n{row.diff_vs_actuated_s:+.0f} ± {row.diff_ci95_s:.0f} s"
            if row.valid_runs < len(SEEDS):
                bar.set_hatch("///")
                bar.set_edgecolor("white")
                text += f"\n{row.valid_runs:.0f} of {len(SEEDS)} runs valid"
            ax.text(bar.get_x() + bar.get_width() / 2, row.mean_delay_s + row.delay_ci95_s + 2,
                    text, ha="center", va="bottom", fontsize=8, color=INK)
        ax.set_xticks(range(len(PLANS)), [LABEL[p] for p in PLANS], fontsize=8)
        title = "22 Sep 2026, calibration demand" if day == CALIBRATION_DAY else "23 Sep 2026, holdout demand"
        ax.set_title(title, fontsize=10, loc="left")
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(colors=MUTED, length=0, labelsize=8)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
    axes[0].set_ylabel("Delay per vehicle incl. entry wait (s), 07:00–10:00", fontsize=8, color=MUTED)
    axes[0].set_ylim(0, (df["mean_delay_s"] + df["delay_ci95_s"]).max() * 1.3)
    fig.suptitle("Signal plans for the 4 junctions (5 seeds, 95% CI; second line: paired difference to "
                 "actuated). Hatched: runs fail the validity rule, so the delay is not comparable.",
                 x=0.01, ha="left", fontsize=10)
    fig.tight_layout()
    out = RESULTS_DIR / "signal_comparison.png"
    fig.savefig(out)
    return out


if __name__ == "__main__":
    print(main())
