"""Settings shared by the experiment scripts, and one function to run SUMO.

Set QUICK=1 in the environment for a reduced run (1 seed, small grid) that reproduces the
shape of every table in about 20 minutes; its outputs go to results_quick/.
Set WORKERS=n to change the number of parallel SUMO runs (default 4).
"""
import os
import subprocess
from pathlib import Path

from sumolib import checkBinary

from counts import END, START, to_seconds

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
DATA_DIR = ROOT / "data"
RUN_DIR = ROOT / "runs"  # raw per-run output, not committed
QUICK = os.environ.get("QUICK") == "1"
RESULTS_DIR = ROOT / ("results_quick" if QUICK else "results")

CALIBRATION_DAY = "20260922"
VALIDATION_DAY = "20260923"
SEEDS = range(1) if QUICK else range(5)
WORKERS = int(os.environ.get("WORKERS", "4"))

# Step must not exceed tau: with 1 s steps, tau < 1 gave thousands of collisions/teleports
# while the counts still looked fine.
STEP_LENGTH = 0.5
SIM_END = to_seconds(END) - to_seconds(START) + 900  # 06:45-10:15, lets the last vehicles finish
MAX_TELEPORTS = 50  # out of ~17,000 vehicles


def run_sumo(routes, additionals, seed, extra=()):
    """Run SUMO on the corridor network; return its end-of-run statistics as a dict."""
    RUN_DIR.mkdir(exist_ok=True)
    cmd = [checkBinary("sumo"), "--net-file", str(NET_DIR / "aberdeen.net.xml"),
           "--route-files", str(routes), "--additional-files", ",".join(str(a) for a in additionals),
           "--end", str(SIM_END), "--seed", str(seed), "--step-length", str(STEP_LENGTH),
           "--no-step-log", "true", "--no-warnings", "true", "--duration-log.statistics", "true", *extra]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"SUMO failed (seed {seed}):\n{result.stderr[-2000:]}")
    stats = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.strip().partition(": ")
        if sep and value:
            stats[key] = value
    return stats


def validity(stats):
    """Teleports, vehicles never inserted, vehicles still in the network, and whether the run is valid.

    A run that only matches counts by teleporting vehicles, or that leaves vehicles outside or
    inside the network at the end, does not count.
    """
    teleports = stats.get("Teleports", "0")
    out = {"teleports": int(teleports.split()[0]), "teleport_detail": teleports,
           "not_inserted": int(stats.get("Waiting", "0").split()[0]),
           "still_running": int(stats.get("Running", "0").split()[0])}
    out["valid"] = out["teleports"] <= MAX_TELEPORTS and out["not_inserted"] == 0 and out["still_running"] == 0
    return out
