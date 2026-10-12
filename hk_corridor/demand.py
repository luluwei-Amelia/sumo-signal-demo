"""Generate simulated traffic that reproduces the real detector counts.

1. Candidate routes: the fastest path between every pair of trip ends. Trip ends are the
   network boundary plus Aberdeen Praya Road edges (frontage access, see make_candidate_routes).
   Each route gets a weight: lanes x road class at boundary ends, 1 at frontage ends, times
   the narrowest lane count on the route, so wide through routes are preferred.
2. routeSampler.py draws routes in proportion to those weights, per 15-min interval, until
   the six detector counts match. Detectors give volumes, not where cars leave; without the
   weights the sampler sent most westbound traffic down a one-lane slip road.

Usage: python demand.py 20260922
"""
import subprocess
import sys
from pathlib import Path

import sumolib
from sumolib import checkBinary

from build_network import NET_FILE, build_network
from counts import derived_counts, load_counts, write_edgedata
from detectors import ROAD, locate_detectors

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
TOOLS = Path(checkBinary("sumo")).parents[1] / "tools"
CANDIDATES = NET_DIR / "candidate_routes.rou.xml"
OPTIMIZE_BOUND = "10"  # max change in vehicles per route and interval during the optimisation


def _attractiveness(edge, boundary):
    """How much traffic a trip end plausibly carries: lanes x road class at the boundary,
    1 for frontage access on the corridor."""
    return edge.getLaneNumber() * max(edge.getPriority(), 1) if boundary else 1


def make_candidate_routes():
    net = sumolib.net.readNet(str(NET_FILE))
    edges = [e for e in net.getEdges() if e.allows("passenger")]
    # An entry has nothing flowing into it except its own reverse direction; an exit likewise.
    boundary_in = {e for e in edges if not [i for i in e.getIncoming() if i.getFromNode() != e.getToNode()]}
    boundary_out = {e for e in edges if not [o for o in e.getOutgoing() if o.getToNode() != e.getFromNode()]}
    # Trip ends: the network boundary (main-road ends and local-street stubs) plus Aberdeen
    # Praya Road edges longer than 20 m, which stand for frontage access (car parks,
    # buildings, kerbside stops). Detector edges are excluded: a trip that starts or ends on
    # the detector's own edge is counted there by routeSampler but may never pass the loop.
    detector_edges = set(locate_detectors(NET_FILE)["edge"])
    inner = sorted((e for e in edges if ROAD in e.getName() and e.getLength() > 20
                    and e.getID() not in detector_edges), key=lambda e: e.getID())
    entries = list(dict.fromkeys(sorted(boundary_in, key=lambda e: e.getID()) + inner))
    exits = list(dict.fromkeys(sorted(boundary_out, key=lambda e: e.getID()) + inner))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<routes>"]
    n = 0
    for origin in entries:
        for destination in exits:
            if origin == destination or (
                origin.getToNode() == destination.getFromNode() and origin.getFromNode() == destination.getToNode()
            ):
                continue  # same street, or a U-turn back out the same road
            path, _ = net.getFastestPath(origin, destination, vClass="passenger")
            if path:
                weight = (_attractiveness(origin, origin in boundary_in)
                          * _attractiveness(destination, destination in boundary_out)
                          * min(e.getLaneNumber() for e in path))
                lines.append(
                    f'    <route id="r{n}" edges="{" ".join(e.getID() for e in path)}" probability="{weight}"/>'
                )
                n += 1
    lines.append("</routes>")
    CANDIDATES.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(entries)} entries x {len(exits)} exits -> {n} candidate routes")


def sample_routes(day):
    located = locate_detectors(NET_FILE)
    counts_file = NET_DIR / f"counts_{day}.edgedata.xml"
    counts = load_counts(day)
    extra = derived_counts(counts, located, sumolib.net.readNet(str(NET_FILE)))
    print(f"derived side-road counts on: {sorted({e for _, e, _ in extra})}")
    write_edgedata(counts, located, counts_file, extra)
    out = NET_DIR / f"routes_{day}.rou.xml"
    subprocess.run(
        [
            sys.executable, str(TOOLS / "routeSampler.py"),
            "--route-files", CANDIDATES.name,
            "--edgedata-files", counts_file.name,
            "--output-file", out.name,
            "--mismatch-output", f"mismatch_{day}.xml",
            "--weighted",                              # sample in proportion to the prior weight
            "--min-count", "1",                        # only routes that pass a detector
            "--optimize", OPTIMIZE_BOUND,              # then let each route's count move a little
            "--attributes", 'departLane="best" departSpeed="max"',
            "--prefix", f"d{day}_",
            "--seed", "42",
        ],
        check=True, cwd=NET_DIR,  # relative paths keep the local directory out of the output header
    )
    return out


if __name__ == "__main__":
    if not NET_FILE.exists():
        build_network()
    if not CANDIDATES.exists():
        make_candidate_routes()
    for day in sys.argv[1:] or ["20260922"]:
        print(f"Routes written to {sample_routes(day)}")
