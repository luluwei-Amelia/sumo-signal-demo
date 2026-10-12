"""Measure queue-discharge saturation flow at the junctions of the fitted model.

Runs the 22 Sep morning with actuated control. When a lane turns green ('G') with at least
MIN_QUEUE cars stopped on it, the queue order is stored and the time each car leaves the lane
is logged. Headways from the 5th car on count as saturation headways (the first four include
start-up loss). Saturation flow = 3600 / mean headway, in cars per hour per lane.

Usage: python saturation.py
Output: results/saturation_flow.csv (per lane) and results/saturation_flow.json (overall)
"""
import json
import statistics

import pandas as pd
import traci
from sumolib import checkBinary

from build_network import NET_FILE
from calibrate import CALIBRATION_DAY, NET_DIR, RESULTS_DIR, RUN_DIR, SIM_END, STEP_LENGTH, vtype_xml

MIN_QUEUE = 6
SKIP = 4  # queued vehicles whose headways include start-up lost time


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


def measure(seed=0):
    params = json.loads((RESULTS_DIR / "calibrated_params.json").read_text())
    add = RUN_DIR / "saturation_vtype.add.xml"
    RUN_DIR.mkdir(exist_ok=True)
    add.write_text(f"<additional>\n{vtype_xml(params)}\n</additional>\n", encoding="utf-8")
    traci.start([checkBinary("sumo"), "-n", str(NET_FILE), "-r", str(NET_DIR / f"routes_{CALIBRATION_DAY}.rou.xml"),
                 "-a", str(add), "--step-length", str(STEP_LENGTH), "--seed", str(seed), "--end", str(SIM_END),
                 "--no-step-log", "true", "--no-warnings", "true"])
    lanes = junction_lanes()
    green = {(t, l): False for t, ls in lanes.items() for l in ls}
    active = {}      # (tls, lane) -> {"queue": [veh ids in order], "left": {veh: time}}
    headways = []
    while traci.simulation.getMinExpectedNumber() > 0 and traci.simulation.getTime() < SIM_END:
        traci.simulationStep()
        now = traci.simulation.getTime()
        for tls, ls in lanes.items():
            state = traci.trafficlight.getRedYellowGreenState(tls)
            for lane, idx in ls.items():
                is_green = any(state[i] == "G" for i in idx)
                key = (tls, lane)
                if is_green and not green[key]:
                    vehs = traci.lane.getLastStepVehicleIDs(lane)  # ordered from lane start to end
                    halted = [v for v in reversed(vehs) if traci.vehicle.getSpeed(v) < 0.1]
                    if len(halted) >= MIN_QUEUE:
                        active[key] = {"queue": halted, "left": {}}
                elif not is_green and green[key] and key in active:
                    rec = active.pop(key)
                    times = [rec["left"].get(v) for v in rec["queue"]]
                    for k in range(SKIP + 1, len(times)):
                        if times[k] is not None and times[k - 1] is not None and times[k] >= times[k - 1]:
                            headways.append({"tls": tls, "lane": lane, "headway_s": times[k] - times[k - 1]})
                green[key] = is_green
                if key in active:
                    present = set(traci.lane.getLastStepVehicleIDs(lane))
                    for v in active[key]["queue"]:
                        if v not in present and v not in active[key]["left"]:
                            active[key]["left"][v] = now
    traci.close()
    return pd.DataFrame(headways)


def main():
    h = measure()
    per_lane = h.groupby(["tls", "lane"])["headway_s"].agg(["count", "mean"]).reset_index()
    per_lane["sat_flow_veh_h_lane"] = (3600 / per_lane["mean"]).round(0)
    per_lane.to_csv(RESULTS_DIR / "saturation_flow.csv", index=False)
    mean_h = h["headway_s"].mean()
    summary = {"headways": len(h), "lanes": int(len(per_lane)), "mean_headway_s": round(mean_h, 2),
               "median_headway_s": round(statistics.median(h["headway_s"]), 2),
               "sat_flow_veh_h_lane": round(3600 / mean_h)}
    (RESULTS_DIR / "saturation_flow.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(per_lane.to_string(index=False))
    print(summary)


if __name__ == "__main__":
    main()
