"""Settings shared by the rl_signal scripts.

Set QUICK=1 for a short run (1 training seed, few episodes, 2 evaluation seeds) that checks
the whole chain; its outputs go to results_quick/.

Set VARIANT to choose the DQN training set-up (each writes its own models and results, so the
others are kept):
    a     training demand = 22 Sep profile only; reward = total waiting       (models/, results/)
    b1a   training demand randomised per approach (demand.py); same reward    (models_b1a/, results_b1a/)
    b1b   as b1a, plus extra weight on queues of phases red for LONG_RED s    (models_b1b/, results_b1b/)
The baselines do not depend on the variant and are run once, under a.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
ROUTE_DIR = ROOT / "routes"      # generated, not committed
RUN_DIR = ROOT / "runs"          # raw per-run output, not committed
QUICK = os.environ.get("QUICK") == "1"
VARIANT = os.environ.get("VARIANT", "a")
assert VARIANT in ("a", "b1a", "b1b"), VARIANT
_SUFFIX = "" if VARIANT == "a" else f"_{VARIANT}"
MODEL_DIR = ROOT / ("models_quick" + _SUFFIX if QUICK else "models" + _SUFFIX)   # small, committed
RESULTS_DIR = ROOT / ("results_quick" + _SUFFIX if QUICK else "results" + _SUFFIX)
BASELINE_RESULTS = ROOT / ("results_quick" if QUICK else "results")   # where the baselines are
WORKERS = int(os.environ.get("WORKERS", "4"))

# b1: training-demand randomisation and the long-red weight
RANDOM_SHARE = (0.6, 1.4)   # each approach's flow multiplied by an independent factor in this range
RANDOM_LEVEL = (0.85, 1.1)  # and the whole junction by one factor in this range
LONG_RED = 90               # s
LONG_RED_WEIGHT = 1.0 if VARIANT == "b1b" else 0.0

# Junction: 4 arms, 3 lanes in and 3 out, 300 m long, 50 km/h, left-hand traffic.
ARM_LENGTH = 300.0
SPEED = 13.89
NET_FILE = NET_DIR / "junction.net.xml"
NET_FILE_ACTUATED = NET_DIR / "junction_actuated.net.xml"
TLS_ID = "C"

# Signal rules every controller has to follow (enforced in env.py, not learned).
STEP_LENGTH = 1.0
DECISION_INTERVAL = 5   # s between decisions
MIN_GREEN = 10          # s
MAX_GREEN = 60          # s, same cap as maxDur of the actuated program
MAX_RED = 180           # s; a phase red this long with vehicles on its lanes is served next. The actuated
                        # program can reach about 3 x 60 + 12 = 192 s, so this does not favour it.
YELLOW = 3              # s

# Demand: shape of the 15-min counts on Aberdeen Praya Road (hk_corridor), 06:45-10:00.
TRAIN_DAY = "20260922"
TEST_DAY = "20260923"
CLEARANCE = 900         # s after the last departure for the network to empty

TRAIN_SEEDS = range(1) if QUICK else range(5)
EVAL_SEEDS = range(100, 102) if QUICK else range(100, 110)
