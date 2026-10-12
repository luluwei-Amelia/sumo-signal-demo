"""Download the OpenStreetMap data around Aberdeen Praya Road and convert it to a SUMO network.

Usage: python build_network.py   (skips the download if the .osm.xml file already exists)
"""
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

import sumolib
from sumolib import checkBinary

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
OSM_FILE = NET_DIR / "aberdeen_bbox.osm.xml"
NET_FILE = NET_DIR / "aberdeen.net.xml"

# West, south, east, north: the six detectors plus ~300 m on each side
BBOX = (114.1463, 22.2444, 114.1620, 22.2514)
OSM_API = "https://api.openstreetmap.org/api/0.6/map?bbox={},{},{},{}"
MAIN_ROAD_TYPES = [
    f"highway.{t}" for t in (
        "trunk", "trunk_link", "primary", "primary_link",
        "secondary", "secondary_link", "tertiary", "tertiary_link",
    )
]
# No local streets: the one-lane town-centre grid deadlocked. Keeping just their first edges,
# or giving turning lanes exit-only stubs, also jammed once buses and trucks were added.
# demand.py lets trips start/end on corridor edges instead.

# Lane counts in OSM that disagree with the TD detectors. Applied to a copy of the OSM file.
OSM_LANE_CORRECTIONS = {
    # TDSAPR10001, eastbound: lanes=2, but turn:lanes lists 3 and the detector reports 3.
    "245466671": 3,
    # AID01222 reports 3 lanes where OSM has 4 (left|through|through|through). Left at 4:
    # cutting it to 3 created a lane drop that held back westbound traffic.
}
PATCHED_OSM = NET_DIR / "aberdeen_bbox_patched.osm.xml"


def download_osm():
    NET_DIR.mkdir(exist_ok=True)
    request = urllib.request.Request(
        OSM_API.format(*BBOX), headers={"User-Agent": "sumo-signal-demo/1.0 (research portfolio)"}
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        OSM_FILE.write_bytes(response.read())


def patch_osm():
    """Write a copy of the OSM file with OSM_LANE_CORRECTIONS applied (lanes and turn:lanes)."""
    tree = ET.parse(OSM_FILE)
    for way in tree.getroot().iter("way"):
        lanes = OSM_LANE_CORRECTIONS.get(way.get("id"))
        if lanes is None:
            continue
        for tag in list(way.iter("tag")):
            if tag.get("k") == "lanes":
                tag.set("v", str(lanes))
            elif tag.get("k") == "turn:lanes":
                way.remove(tag)  # would contradict the corrected lane count
    tree.write(PATCHED_OSM, encoding="utf-8", xml_declaration=True)
    return PATCHED_OSM


def fix_dead_end_lanes():
    """Connect lanes that lead nowhere to the road straight ahead.

    Dropping local streets leaves some turning lanes without a continuation; vehicles in
    them stall and block the road. Each such lane is joined to the lane of the main
    continuation (where most of the edge's other lanes go) nearest to it. Where this is a
    plain lane drop the junction becomes a zipper merge (vehicles alternate) instead of one
    lane always yielding to the other.
    """
    net = sumolib.net.readNet(str(NET_FILE))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<connections>"]
    zipper_nodes = set()
    fixed = 0
    for edge in net.getEdges():
        conns = [c for lane in edge.getLanes() for c in lane.getOutgoing()]
        if not conns:
            continue  # network boundary, nothing to fix
        counts = Counter(c.getTo() for c in conns)
        straight = {c.getTo() for c in conns if c.getDirection() == "s"}
        # Ties are broken by direction, then by ID; set order is not stable between runs.
        main = max(counts, key=lambda e: (counts[e], e in straight, e.getID()))
        for lane in edge.getLanes():
            if not lane.getOutgoing():
                to_lane = min(lane.getIndex(), main.getLaneNumber() - 1)
                lines.append(f'    <connection from="{edge.getID()}" to="{main.getID()}" '
                             f'fromLane="{lane.getIndex()}" toLane="{to_lane}"/>')
                node = edge.getToNode()
                # SUMO's zipper supports at most two conflicting lanes, so only plain lane drops.
                if node.getType() != "traffic_light" and len(node.getIncoming()) == 1:
                    zipper_nodes.add(node.getID())
                fixed += 1
    lines.append("</connections>")
    (NET_DIR / "fixes.con.xml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (NET_DIR / "fixes.nod.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n<nodes>\n'
        + "".join(f'    <node id="{n}" type="zipper"/>\n' for n in sorted(zipper_nodes)) + "</nodes>\n",
        encoding="utf-8")
    subprocess.run(
        [checkBinary("netconvert"), "--sumo-net-file", NET_FILE.name, "--connection-files", "fixes.con.xml",
         "--node-files", "fixes.nod.xml", "--lefthand", "true", "--no-warnings", "true",
         "--output-file", NET_FILE.name],
        check=True, cwd=NET_DIR,
    )
    return fixed


def build_network():
    if not OSM_FILE.exists():
        download_osm()
    patch_osm()
    typemap = Path(checkBinary("netconvert")).parents[1] / "data" / "typemap" / "osmNetconvert.typ.xml"
    subprocess.run(
        [
            checkBinary("netconvert"),
            "--osm-files", PATCHED_OSM.name,
            "--type-files", str(typemap),
            "--lefthand", "true",                      # Hong Kong drives on the left
            "--keep-edges.by-vclass", "passenger",     # drop footpaths, cycle tracks, etc.
            "--keep-edges.by-type", ",".join(MAIN_ROAD_TYPES),
            # Real signal timings are not published; actuated control adapts to arrivals,
            # which is a more neutral assumption than guessing a fixed plan.
            "--tls.default-type", "actuated",
            "--remove-edges.isolated", "true",
            "--geometry.remove", "true",
            "--ramps.guess", "true",
            "--junctions.join", "true",                # merge the several OSM nodes of one junction
            "--tls.join", "true",
            "--output.street-names", "true",
            "--no-warnings", "true",
            "--output-file", NET_FILE.name,
        ],
        # Relative paths, so the configuration header SUMO writes into the output
        # does not contain the local directory.
        check=True, cwd=NET_DIR,
    )
    print(f"Connected {fix_dead_end_lanes()} dead-end lanes")
    return NET_FILE


if __name__ == "__main__":
    print(f"Network written to {build_network()}")
