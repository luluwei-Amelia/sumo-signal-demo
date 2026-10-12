"""Write alternative traffic-signal programs for the intersection.

Three strategies are compared at each demand level:
  equal     fixed-time, both directions get the same 30 s green (an untuned plan)
  webster   fixed-time, cycle length and green split from Webster's formula
  actuated  green extends while detectors still see arriving vehicles (5-50 s)
"""
from pathlib import Path

import sumolib

from demand import BASE_VOLUME

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"

STRATEGIES = ["equal", "webster", "actuated"]
EQUAL_GREEN = 30           # s per direction for the untuned plan
LOST_TIME_PER_PHASE = 4    # s: start-up delay + part of the yellow nobody uses
MIN_GREEN = 10             # s: shortest green we allow
MAX_CYCLE = 120            # s: cap used when demand exceeds capacity


def green_phases(tls, phases):
    """Map each green phase index to the approach edges it serves (e.g. {'N2C', 'S2C'})."""
    served = {}
    for i, phase in enumerate(phases):
        edges = {
            in_lane.getEdge().getID()
            for in_lane, _, link in tls.getConnections()
            if phase.state[link] in "Gg"
        }
        if edges:
            served[i] = edges
    return served


def webster_cycle(lost, total_y):
    """Webster (1958) minimum-delay cycle C = (1.5 L + 5) / (1 - Y), capped at MAX_CYCLE."""
    return MAX_CYCLE if total_y >= 1 else min(MAX_CYCLE, (1.5 * lost + 5) / (1 - total_y))


def webster_greens(net, served, phases, multiplier, sat_flow):
    """Webster cycle with green split by y_i / Y.

    y_i is the critical flow ratio of phase i: the busiest lane's flow divided by
    the saturation flow. Y is the sum over phases, L the total lost time per cycle.
    """
    y = {
        i: max(
            BASE_VOLUME[edge[0]] * multiplier / net.getEdge(edge).getLaneNumber()
            for edge in edges
        ) / sat_flow
        for i, edges in served.items()
    }
    total_y = sum(y.values())
    lost = LOST_TIME_PER_PHASE * len(served)
    cycle = webster_cycle(lost, total_y)

    greens = {}
    for i in served:
        effective_green = (cycle - lost) * y[i] / total_y
        yellow = phases[(i + 1) % len(phases)].duration
        # Displayed green = effective green + lost time - yellow
        greens[i] = max(MIN_GREEN, round(effective_green + LOST_TIME_PER_PHASE - yellow))
    return greens


def write_signal_plans(net_file, level, multiplier, sat_flow):
    """Write all strategies for one demand level; return (tls id, file, webster greens)."""
    net = sumolib.net.readNet(str(net_file), withPrograms=True)
    tls = net.getTrafficLights()[0]
    phases = tls.getPrograms()["0"].getPhases()
    served = green_phases(tls, phases)
    plans = {
        "equal": {i: EQUAL_GREEN for i in served},
        "webster": webster_greens(net, served, phases, multiplier, sat_flow),
        "actuated": {i: EQUAL_GREEN for i in served},
    }

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<additional>"]
    for strategy in STRATEGIES:
        greens = plans[strategy]
        tls_type = "actuated" if strategy == "actuated" else "static"
        lines.append(f'    <tlLogic id="{tls.getID()}" type="{tls_type}" programID="{strategy}" offset="0">')
        for i, phase in enumerate(phases):
            if i not in greens:  # yellow phases keep their default length
                lines.append(f'        <phase duration="{phase.duration}" state="{phase.state}"/>')
            elif strategy == "actuated":
                lines.append(
                    f'        <phase duration="{greens[i]}" minDur="5" maxDur="50" state="{phase.state}"/>'
                )
            else:
                lines.append(f'        <phase duration="{greens[i]}" state="{phase.state}"/>')
        lines.append("    </tlLogic>")
    lines.append("</additional>")

    tls_file = NET_DIR / f"signal_plans_{level}.add.xml"
    tls_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tls.getID(), tls_file, plans["webster"]
