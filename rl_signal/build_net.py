"""Build the test junction: a 4-arm cross, left-hand traffic, with a protected right-turn phase.

Each approach has 3 lanes (kerbside: left + straight, middle: straight, offside: right only)
and each exit 3 lanes. The signal has 4 green phases:
    0  N-S straight and left        2  E-W straight and left
    1  N-S right (protected)        3  E-W right (protected)
In left-hand traffic the right turn crosses the opposing straight flow, so it gets its own
phase instead of waiting for gaps; opposing right turns pass each other and run together.

Two versions of the network are written: a static program (fixed-time plan, and the base
that every TraCI controller switches) and SUMO's actuated program with the same phases.
Green phase durations here are placeholders; the fixed-time plan sets its own (webster.py).

Usage: python build_net.py
"""
import subprocess

import sumolib
from sumolib import checkBinary

from common import ARM_LENGTH, MAX_GREEN, MIN_GREEN, NET_DIR, NET_FILE, NET_FILE_ACTUATED, SPEED, TLS_ID, YELLOW

ARMS = {"N": (0, ARM_LENGTH), "E": (ARM_LENGTH, 0), "S": (0, -ARM_LENGTH), "W": (-ARM_LENGTH, 0)}
# Turning, seen from each approach in left-hand traffic (driving on the left, so left is the easy turn).
TURNS = {"N": {"l": "E", "s": "S", "r": "W"}, "S": {"l": "W", "s": "N", "r": "E"},
         "E": {"l": "S", "s": "W", "r": "N"}, "W": {"l": "N", "s": "E", "r": "S"}}
LANES = {"l": [0], "s": [0, 1], "r": [2]}   # lane 0 is kerbside
AXIS = {"N": "NS", "S": "NS", "E": "EW", "W": "EW"}
GREEN_PHASES = [("NS", "ls"), ("NS", "r"), ("EW", "ls"), ("EW", "r")]


def write_plain():
    NET_DIR.mkdir(exist_ok=True)
    nodes = [f'    <node id="{TLS_ID}" x="0" y="0" type="traffic_light"/>']
    nodes += [f'    <node id="{a}" x="{x}" y="{y}" type="priority"/>' for a, (x, y) in ARMS.items()]
    edges, cons = [], []
    for a in ARMS:
        edges.append(f'    <edge id="{a}_in" from="{a}" to="{TLS_ID}" numLanes="3" speed="{SPEED}"/>')
        edges.append(f'    <edge id="{a}_out" from="{TLS_ID}" to="{a}" numLanes="3" speed="{SPEED}"/>')
        for d, to in TURNS[a].items():
            for lane in LANES[d]:
                cons.append(f'    <connection from="{a}_in" to="{to}_out" fromLane="{lane}" toLane="{lane}"/>')
    (NET_DIR / "junction.nod.xml").write_text("<nodes>\n" + "\n".join(nodes) + "\n</nodes>\n", encoding="utf-8")
    (NET_DIR / "junction.edg.xml").write_text("<edges>\n" + "\n".join(edges) + "\n</edges>\n", encoding="utf-8")
    (NET_DIR / "junction.con.xml").write_text("<connections>\n" + "\n".join(cons) + "\n</connections>\n",
                                              encoding="utf-8")


def netconvert(out_file, extra=()):
    # Relative paths, run inside network/, so the configuration header SUMO writes into the
    # output does not contain the local directory (as in hk_corridor/build_network.py).
    cmd = [checkBinary("netconvert"),
           "--node-files", "junction.nod.xml", "--edge-files", "junction.edg.xml",
           "--connection-files", "junction.con.xml",
           "--lefthand", "true", "--no-turnarounds", "true",
           "--output-file", out_file.name, *extra]
    subprocess.run(cmd, check=True, capture_output=True, text=True, cwd=NET_DIR)


def green_states(net_file):
    """State string of each green phase, built from the link indices netconvert assigned."""
    net = sumolib.net.readNet(str(net_file), withInternal=False)
    links = net.getTLS(TLS_ID).getConnections()   # (inLane, outLane, linkIndex)
    n = max(i for _, _, i in links) + 1
    states = []
    for axis, dirs in GREEN_PHASES:
        s = ["r"] * n
        for in_lane, out_lane, i in links:
            approach = in_lane.getEdge().getID()[0]
            exit_arm = out_lane.getEdge().getID()[0]
            d = next(k for k, v in TURNS[approach].items() if v == exit_arm)
            if AXIS[approach] == axis and d in dirs:
                s[i] = "G"
        states.append("".join(s))
    return states


def yellow_state(current, nxt):
    """Links that are green now and red next show yellow; everything else is red."""
    return "".join("y" if c in "Gg" and n == "r" else "r" for c, n in zip(current, nxt))


def write_tll(states, tls_type, out):
    phases = []
    for k, s in enumerate(states):
        nxt = states[(k + 1) % len(states)]
        if tls_type == "actuated":
            phases.append(f'        <phase duration="20" minDur="{MIN_GREEN}" maxDur="{MAX_GREEN}" state="{s}"/>')
        else:
            phases.append(f'        <phase duration="20" state="{s}"/>')
        phases.append(f'        <phase duration="{YELLOW}" state="{yellow_state(s, nxt)}"/>')
    xml = (f'<tlLogics>\n    <tlLogic id="{TLS_ID}" type="{tls_type}" programID="0" offset="0">\n'
           + "\n".join(phases) + "\n    </tlLogic>\n</tlLogics>\n")
    out.write_text(xml, encoding="utf-8")


if __name__ == "__main__":
    write_plain()
    netconvert(NET_FILE)   # first pass only to learn the link indices
    states = green_states(NET_FILE)
    for tls_type, out in (("static", NET_FILE), ("actuated", NET_FILE_ACTUATED)):
        tll = NET_DIR / f"junction_{tls_type}.tll.xml"
        write_tll(states, tls_type, tll)
        netconvert(out, ["--tllogic-files", tll.name])
    for (axis, dirs), s in zip(GREEN_PHASES, states):
        print(axis, dirs, s)
