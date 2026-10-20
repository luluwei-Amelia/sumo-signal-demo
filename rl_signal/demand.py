"""Generate vehicle arrivals for the test junction.

Shape: the 15-min counts on Aberdeen Praya Road, 06:45-10:00 (13 bins), at the first detector of
each direction (TDSAPR10001 eastbound, AID01222 westbound), read from hk_corridor's SQLite store.
The main road runs west-east, so eastbound traffic enters from W and westbound from E.

Assumptions (no data for them):
- The side road (N, S) follows the mean shape of the two main-road directions, at MINOR_SHARE
  of it; southbound (from N) is set higher than northbound so the junction is not symmetric.
- Turning shares are the same on every approach (TURN_SHARE).
- One scale factor sets the load so that Webster's flow ratio Y in the peak hour of the training
  day is TARGET_Y (webster.py prints the resulting plan).

Arrivals are a Poisson process: in each 15-min bin, each movement gets a Poisson number of
vehicles, departing at uniform random times. A seed fixes the draw.

Scenarios:
    train   22 Sep shape                         (training and in-sample test, different seeds)
    day23   23 Sep shape                         (a day never used in training)
    plus20  22 Sep shape, every flow +20%
    flip    22 Sep shape with directions swapped (W<->E and N<->S)
    random  22 Sep shape with random factors per approach (training demand of b1a, b1b)
"""
import os
import sys
from pathlib import Path

import numpy as np

from build_net import TURNS
from common import RANDOM_LEVEL, RANDOM_SHARE, ROUTE_DIR, TEST_DAY, TRAIN_DAY

# Appended, not prepended: hk_corridor also has a demand.py, which must not shadow this one.
sys.path.append(str(Path(__file__).resolve().parent.parent / "hk_corridor"))
import counts  # noqa: E402  (hk_corridor)

BIN_S = 900
MAIN = {"W": "TDSAPR10001", "E": "AID01222"}   # approach -> detector whose direction enters there
MINOR_SHARE = {"N": 0.45, "S": 0.30}
TURN_SHARE = {"l": 0.15, "s": 0.70, "r": 0.15}
TARGET_Y = 0.70
SAT_FLOW = {"ls": 1658, "r": 1739}   # veh/h/lane, measured by saturation.py
SCENARIOS = {"train": (TRAIN_DAY, 1.0, False), "day23": (TEST_DAY, 1.0, False),
             "plus20": (TRAIN_DAY, 1.2, False), "flip": (TRAIN_DAY, 1.0, True)}


def raw_profile(day):
    """Vehicles per hour by approach and 15-min bin, before scaling (dict of arrays, 13 bins)."""
    c = counts.load_counts(day).pivot(index="bin_start", columns="detector", values="count_15min")
    prof = {a: c[det].to_numpy(float) * 4 for a, det in MAIN.items()}
    mean_main = (prof["W"] + prof["E"]) / 2
    for a, share in MINOR_SHARE.items():
        prof[a] = mean_main * share
    return prof


def flow_ratio_y(prof, k):
    """Webster's Y (sum of critical lane flow ratios over the 4 phases) in bin k.

    Straight traffic splits evenly over lanes 0 and 1, and lane 0 also takes the left turns,
    so the busier through lane carries about (left + straight) / 2.
    """
    def lane_ratio(a, kind):
        q = prof[a][k]
        if kind == "ls":
            return q * (TURN_SHARE["l"] + TURN_SHARE["s"]) / 2 / SAT_FLOW["ls"]
        return q * TURN_SHARE["r"] / SAT_FLOW["r"]
    return sum(max(lane_ratio(a, kind) for a in axis) for axis in (("N", "S"), ("E", "W")) for kind in ("ls", "r"))


def peak_hour(prof):
    """Mean flows of the busiest 4 consecutive bins, and the index of the first one.

    Single 15-min bins are noisy (some come from 3-8 of the 30 periods), so the load is set on
    the peak hour, as Webster's method is normally applied.
    """
    n = len(prof["W"])
    hours = [{a: v[k:k + 4].mean(keepdims=True) for a, v in prof.items()} for k in range(n - 3)]
    k = max(range(n - 3), key=lambda i: flow_ratio_y(hours[i], 0))
    return hours[k], k


def scale_factor():
    """One factor for every scenario, fixed on the training day's peak hour."""
    hour, _ = peak_hour(raw_profile(TRAIN_DAY))
    return TARGET_Y / flow_ratio_y(hour, 0)


def profile(scenario):
    day, mult, flip = SCENARIOS[scenario]
    prof = raw_profile(day)
    s = scale_factor() * mult
    prof = {a: v * s for a, v in prof.items()}
    if flip:
        prof = {"W": prof["E"], "E": prof["W"], "N": prof["S"], "S": prof["N"]}
    return prof


def random_factors(rng):
    """Scale factors per approach for the b1a/b1b training demand: one in RANDOM_SHARE per approach,
    times one in RANDOM_LEVEL for the junction, drawn per episode.

    S can then be busier than N, or W busier than E, as in the flip test, so flip is no longer
    unseen for these variants. plus20 lifts the whole junction above RANDOM_LEVEL, and day23 is
    another day's profile.
    """
    level = rng.uniform(*RANDOM_LEVEL)
    return {a: level * rng.uniform(*RANDOM_SHARE) for a in TURNS}


def write_routes(scenario, seed):
    """Write routes/<scenario>_<seed>.rou.xml and return its path.

    scenario is one of SCENARIOS, or "random" (randomised training demand, see random_factors).
    """
    ROUTE_DIR.mkdir(exist_ok=True)
    rng = np.random.default_rng(seed)
    if scenario == "random":
        factors = random_factors(rng)
        prof = {a: v * factors[a] for a, v in profile("train").items()}
    else:
        prof = profile(scenario)
    trips = []
    for a in sorted(prof):
        for k, rate in enumerate(prof[a]):
            for d, share in TURN_SHARE.items():
                n = rng.poisson(rate * share * BIN_S / 3600)
                for t in rng.uniform(k * BIN_S, (k + 1) * BIN_S, n):
                    trips.append((round(float(t), 2), a, TURNS[a][d]))
    trips.sort()
    lines = ['<routes>', '    <vType id="car" vClass="passenger"/>']
    for i, (t, a, to) in enumerate(trips):
        lines.append(f'    <trip id="{i}" type="car" depart="{t:.2f}" from="{a}_in" to="{to}_out" '
                     f'departLane="best" departSpeed="max"/>')
    lines.append('</routes>')
    out = ROUTE_DIR / f"{scenario}_{seed}.rou.xml"
    text = "\n".join(lines) + "\n"
    if out.exists() and out.read_text(encoding="utf-8") == text:
        return out
    # Parallel workers may write the same file (e.g. validation routes): write a private temp
    # file and swap it in, so SUMO never reads a half-written one. If another worker got there
    # first and the file is open (Windows refuses the swap), its copy is identical.
    tmp = out.with_name(f"{out.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    try:
        os.replace(tmp, out)
    except PermissionError:
        tmp.unlink()
    return out


def demand_end():
    return len(raw_profile(TRAIN_DAY)["W"]) * BIN_S


if __name__ == "__main__":
    print(f"scale factor {scale_factor():.3f}")
    for sc in SCENARIOS:
        prof = profile(sc)
        ys = [flow_ratio_y(prof, k) for k in range(len(prof["W"]))]
        total = sum(v.sum() * BIN_S / 3600 for v in prof.values())
        print(f"{sc:7s} vehicles {total:6.0f}  Y by bin " + " ".join(f"{y:.2f}" for y in ys))
