"""Measure the saturation flow of the junction (vehicles per hour of green per lane).

Webster's method needs the saturation flow, so it is measured rather than assumed. Every
approach gets more traffic than it can carry, and the 4 green phases run 40 s each in turn (no
yellow, since only the discharge is measured). For vehicles already queued when a green starts,
the stop-line headways are recorded at the 1 s step used in the experiments, leaving out
vehicles 1-4 of each queue (start-up loss), as in the usual survey method.

Usage: python saturation.py          prints headways and saturation flow per movement type
"""
import statistics

import traci
from sumolib import checkBinary

from build_net import TURNS, green_states
from common import NET_FILE, RUN_DIR, TLS_ID

SIM_END = 1800


def write_routes(path):
    lines = ['<routes>', '    <vType id="car" vClass="passenger"/>']
    for a, turns in TURNS.items():
        for d, to in turns.items():
            per_hour = 1800 if d == "s" else 900
            lines.append(f'    <flow id="{a}{d}" type="car" begin="0" end="{SIM_END}" vehsPerHour="{per_hour}" '
                         f'from="{a}_in" to="{to}_out" departLane="best" departSpeed="max"/>')
    lines.append('</routes>')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    RUN_DIR.mkdir(exist_ok=True)
    routes = RUN_DIR / "saturation.rou.xml"
    write_routes(routes)
    states = green_states(NET_FILE)
    traci.start([checkBinary("sumo"), "-n", str(NET_FILE), "-r", str(routes), "--seed", "1",
                 "--no-step-log", "true", "--no-warnings", "true", "--time-to-teleport", "-1"])
    lanes = [f"{a}_in_{i}" for a in TURNS for i in range(3)]
    passed = {lane: set() for lane in lanes}     # vehicles that have crossed the stop line
    queue_at_green = {}
    headways = {"ls": [], "r": []}
    phase, t_phase, last_cross = 0, 0, {}
    traci.trafficlight.setRedYellowGreenState(TLS_ID, states[0])
    for t in range(SIM_END):
        if t_phase == 40:
            phase = (phase + 1) % len(states)
            t_phase = 0
            traci.trafficlight.setRedYellowGreenState(TLS_ID, states[phase])
            # queued vehicles, nearest to the stop line first
            queue_at_green = {lane: sorted((v for v in traci.lane.getLastStepVehicleIDs(lane)
                                            if traci.vehicle.getSpeed(v) < 0.1),
                                           key=traci.vehicle.getLanePosition, reverse=True)
                              for lane in lanes}
            last_cross = {}
        traci.simulationStep()
        t_phase += 1
        for lane in lanes:
            on_lane = set(traci.lane.getLastStepVehicleIDs(lane))
            for veh in queue_at_green.get(lane, []):
                if veh not in on_lane and veh not in passed[lane]:
                    passed[lane].add(veh)
                    pos = queue_at_green[lane].index(veh)
                    if lane in last_cross and pos >= 4:
                        kind = "r" if lane.endswith("_2") else "ls"
                        headways[kind].append(t - last_cross[lane])
                    last_cross[lane] = t
    traci.close()
    for kind, h in headways.items():
        med = statistics.median(h)
        print(f"{kind}: {len(h)} headways, median {med:.2f} s, mean {statistics.mean(h):.2f} s, "
              f"saturation flow {3600 / statistics.mean(h):.0f} veh/h/lane (from mean)")


if __name__ == "__main__":
    main()
