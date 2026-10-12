"""Measure the saturation flow of the intersection in the simulation itself.

Webster's signal-timing method needs the saturation flow s: how many vehicles per
hour one lane can discharge during green while a queue is always waiting. Instead of
assuming a textbook value (~1,800 veh/h/lane), we measure it, because vehicles that
must wait for gaps in oncoming traffic before turning lower it noticeably.

Method: keep north-south on permanent green, feed the north approach more traffic
than it can handle while the opposing south approach carries its normal demand, and
count how many northern vehicles get through per hour once the queue has built up.
(Saturating both directions would leave no gaps at all for turning vehicles and
understate the flow.)
"""
from pathlib import Path
from statistics import mean

import sumolib
import traci
from sumolib import checkBinary

from demand import BASE_VOLUME, DESTINATION, TURN_SHARE

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"

MEASURE_FROM, MEASURE_TO = 300, 1500  # seconds; skip the first 5 min while queues form
SEEDS = range(3)


def _write_inputs(net_file):
    net = sumolib.net.readNet(str(net_file), withPrograms=True)
    tls = net.getTrafficLights()[0]
    ns_green = tls.getPrograms()["0"].getPhases()[0].state  # phase 0 serves north-south

    tls_file = NET_DIR / "saturation_test.add.xml"
    tls_file.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<additional>\n'
        f'    <tlLogic id="{tls.getID()}" type="static" programID="saturation" offset="0">\n'
        f'        <phase duration="100000" state="{ns_green}"/>\n'
        "    </tlLogic>\n</additional>\n",
        encoding="utf-8",
    )

    # North: one vehicle per second, far more than two lanes can discharge.
    # South (opposing): its normal medium-level demand.
    arrivals_per_s = {"N": 1.0, "S": BASE_VOLUME["S"] / 3600}
    flows = [
        f'    <flow id="{o}_{m}" begin="0" end="{MEASURE_TO}" from="{o}2C" '
        f'to="{DESTINATION[o][m]}" probability="{rate * share:.4f}" departLane="best" departSpeed="max"/>'
        for o, rate in arrivals_per_s.items()
        for m, share in TURN_SHARE.items()
    ]
    route_file = NET_DIR / "saturation_test.rou.xml"
    route_file.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<routes>\n' + "\n".join(flows) + "\n</routes>\n",
        encoding="utf-8",
    )
    return tls.getID(), tls_file, route_file, net.getEdge("N2C").getLaneNumber()


def measure_saturation_flow(net_file):
    """Return the measured saturation flow in vehicles per hour per lane."""
    tls_id, tls_file, route_file, lanes = _write_inputs(net_file)
    rates = []
    for seed in SEEDS:
        traci.start([
            checkBinary("sumo"),
            "--net-file", str(net_file),
            "--route-files", str(route_file),
            "--additional-files", str(tls_file),
            "--seed", str(seed),
            "--no-step-log", "true",
            "--no-warnings", "true",
        ])
        traci.trafficlight.setProgram(tls_id, "saturation")
        passed = 0
        while traci.simulation.getTime() < MEASURE_TO:
            traci.simulationStep()
            if traci.simulation.getTime() > MEASURE_FROM:
                passed += sum(v.startswith("N_") for v in traci.simulation.getArrivedIDList())
        traci.close()
        hours = (MEASURE_TO - MEASURE_FROM) / 3600
        rates.append(passed / hours / lanes)
    return mean(rates)


if __name__ == "__main__":
    from build_network import build_network

    print(f"Saturation flow: {measure_saturation_flow(build_network()):.0f} veh/h/lane")
