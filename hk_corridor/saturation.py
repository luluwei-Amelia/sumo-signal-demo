"""Measure queue-discharge saturation flow at the junctions of the fitted model.

Runs the 22 Sep morning with actuated control, once per seed. When a lane turns green ('G')
with at least MIN_QUEUE cars stopped on it, the queue order is stored. A queued car counts as
discharged when it moves off the lane into the junction or onto the next road; a car that
changes lane out of the queue breaks the chain and no headway is taken across it. Headways
from the 5th car on count as saturation headways (the first four include start-up loss).
Saturation flow = 3600 / mean headway, in cars per hour per lane.

Usage: python saturation.py
Output: results/saturation_flow.csv (per lane and seed) and results/saturation_flow.json
"""
import json
import statistics
from concurrent.futures import ProcessPoolExecutor

import pandas as pd
import traci
from sumolib import checkBinary

from build_network import NET_FILE
from calibrate import vtype_xml
from sim import CALIBRATION_DAY, NET_DIR, RESULTS_DIR, RUN_DIR, SEEDS, SIM_END, STEP_LENGTH, WORKERS

MIN_QUEUE = 6
SKIP = 4  # queued cars whose headways include start-up lost time


def junction_lanes():
    """{tls id: {incoming lane: [link indices]}} for the multi-phase signals (not the crossings)."""
    out = {}
    for tls in traci.trafficlight.getIDList():
        links = traci.trafficlight.getControlledLinks(tls)
        if len(links) <= 2:
            continue  # the pedestrian crossings control one or two links
        lanes = {}
        for i, link in enumerate(links):
            for inc, _, _ in link:
                lanes.setdefault(inc, []).append(i)
        out[tls] = lanes
    return out


def measure(seed):
    params = json.loads((RESULTS_DIR / "calibrated_params.json").read_text())
    RUN_DIR.mkdir(exist_ok=True)
    add = RUN_DIR / f"saturation_vtype_{seed}.add.xml"
    add.write_text(f"<additional>\n{vtype_xml(params)}\n</additional>\n", encoding="utf-8")
    traci.start([checkBinary("sumo"), "-n", str(NET_FILE), "-r", str(NET_DIR / f"routes_{CALIBRATION_DAY}.rou.xml"),
                 "-a", str(add), "--step-length", str(STEP_LENGTH), "--seed", str(seed), "--end", str(SIM_END),
                 "--no-step-log", "true", "--no-warnings", "true"])
    lanes = junction_lanes()
    green = {(t, l): False for t, ls in lanes.items() for l in ls}
    active = {}  # (tls, lane) -> {"queue": [car ids, front first], "out": {car: time or None}}
    headways = []
    while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < SIM_END:
        traci.simulationStep()
        now = traci.simulation.getTime()
        alive = set(traci.vehicle.getIDList())
        for tls, ls in lanes.items():
            state = traci.trafficlight.getRedYellowGreenState(tls)
            for lane, idx in ls.items():
                key = (tls, lane)
                is_green = any(state[i] == "G" for i in idx)
                if is_green and not green[key]:
                    cars = traci.lane.getLastStepVehicleIDs(lane)  # from lane start to stop line
                    queue = [v for v in reversed(cars) if traci.vehicle.getSpeed(v) < 0.1]
                    if len(queue) >= MIN_QUEUE:
                        active[key] = {"queue": queue, "out": {}}
                elif not is_green and green[key] and key in active:
                    rec = active.pop(key)
                    times = [rec["out"].get(v) for v in rec["queue"]]
                    for k in range(SKIP + 1, len(times)):
                        if times[k] is not None and times[k - 1] is not None:
                            headways.append({"seed": seed, "tls": tls, "lane": lane, "headway_s": times[k] - times[k - 1]})
                green[key] = is_green
                if key in active:
                    edge = lane.rsplit("_", 1)[0]
                    on_lane = set(traci.lane.getLastStepVehicleIDs(lane))
                    for v in active[key]["queue"]:
                        if v in on_lane or v in active[key]["out"]:
                            continue
                        if v not in alive:
                            active[key]["out"][v] = None  # arrived or teleported: no headway
                        elif traci.vehicle.getRoadID(v) == edge:
                            active[key]["out"][v] = None  # changed lane out of the queue
                        else:
                            active[key]["out"][v] = now   # crossed the stop line
    traci.close()
    return pd.DataFrame(headways)


def main():
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        h = pd.concat(pool.map(measure, SEEDS), ignore_index=True)
    per_lane_seed = h.groupby(["tls", "lane", "seed"])["headway_s"].agg(["count", "mean"]).reset_index()
    per_lane_seed["sat_flow_veh_h_lane"] = (3600 / per_lane_seed["mean"]).round(0)
    per_lane_seed.to_csv(RESULTS_DIR / "saturation_flow.csv", index=False)
    per_seed = 3600 / h.groupby("seed")["headway_s"].mean()
    mean_h = h["headway_s"].mean()
    summary = {"seeds": len(per_seed), "headways": len(h), "lanes": int(h["lane"].nunique()),
               "junctions": int(h["tls"].nunique()), "mean_headway_s": round(mean_h, 2),
               "median_headway_s": round(statistics.median(h["headway_s"]), 2),
               "sat_flow_veh_h_lane": round(3600 / mean_h),
               "sat_flow_range_over_seeds": [round(per_seed.min()), round(per_seed.max())],
               "largest_lane_share_of_headways": round(h["lane"].value_counts(normalize=True).iloc[0], 2)}
    (RESULTS_DIR / "saturation_flow.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(per_lane_seed.groupby(["tls", "lane"])[["count", "sat_flow_veh_h_lane"]].agg(
        {"count": "sum", "sat_flow_veh_h_lane": ["min", "max"]}).to_string())
    print(summary)


if __name__ == "__main__":
    main()
