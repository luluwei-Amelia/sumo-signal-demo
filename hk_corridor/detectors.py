"""Place the six real Transport Department detectors on the SUMO network.

Each real detector has a latitude/longitude, a travel direction and a number of lanes.
Among the Aberdeen Praya Road edges heading the same way within SEARCH_RADIUS, an edge with
the same number of lanes as the real detector is preferred, then the nearest. Detector
coordinates can be tens of metres off: TDSAPR10001 lies nearest a 2-lane slip road but
reports 3 lanes, which matches the section just after Shek Pai Wan Road merges in (32 m away).
calibrate.py then puts one induction loop on every lane of that edge.

Known mismatch: AID01222 reports 3 lanes but no 3-lane candidate edge exists there; OSM codes
4 lanes, so the simulated count at AID01222 is summed over 4 lanes.
"""
from pathlib import Path

import pandas as pd
import sumolib

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
ROAD = "Aberdeen Praya Road"

DETECTORS = {
    "TDSAPR10001": "east", "AID01101": "east", "AID01102": "east",
    "TDSAPR20001": "west", "AID01223": "west", "AID01222": "west",
}
SEARCH_RADIUS = 40  # m


def real_lane_counts():
    """Number of lanes each real detector reports, read from the downloaded data."""
    sample = pd.read_csv(sorted(DATA_DIR.glob("detectors_*.csv"))[0])
    return sample.groupby("detector")["lane"].nunique().to_dict()


def _heading_east(edge):
    (x0, _), (x1, _) = edge.getShape()[0], edge.getShape()[-1]
    return x1 > x0


def locate_detectors(net_file):
    """Return one row per detector: id, direction, matched edge, position on edge, lanes."""
    net = sumolib.net.readNet(str(net_file))
    locations = pd.read_csv(DATA_DIR / "detector_locations.csv", encoding="utf-8-sig").set_index("AID_ID_Number")
    lanes_real = real_lane_counts()
    rows = []
    for det_id, direction in DETECTORS.items():
        lat, lon = locations.loc[det_id, ["Latitude", "Longitude"]]
        x, y = net.convertLonLat2XY(lon, lat)
        candidates = [
            (dist, edge)
            for edge, dist in net.getNeighboringEdges(x, y, SEARCH_RADIUS)
            if ROAD in edge.getName() and _heading_east(edge) == (direction == "east")
        ]
        if not candidates:
            raise ValueError(f"No {direction}bound {ROAD} edge within {SEARCH_RADIUS} m of {det_id}")
        dist, edge = min(candidates, key=lambda c: (c[1].getLaneNumber() != lanes_real[det_id], c[0]))
        pos = sumolib.geomhelper.polygonOffsetWithMinimumDistanceToPoint((x, y), edge.getShape())
        rows.append({
            "detector": det_id, "direction": direction, "edge": edge.getID(),
            "pos_m": round(min(max(pos, 1.0), edge.getLength() - 1.0), 1),
            "lanes": edge.getLaneNumber(), "lanes_real": lanes_real[det_id], "offset_m": round(dist, 1),
        })
    return pd.DataFrame(rows)


if __name__ == "__main__":
    from build_network import NET_FILE

    print(locate_detectors(NET_FILE).to_string(index=False))
