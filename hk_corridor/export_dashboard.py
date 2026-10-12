"""Export the results to docs/data/*.json for the static dashboard (docs/index.html).

Usage: python export_dashboard.py
"""
import json

import pandas as pd
import sumolib

import db
from build_network import NET_FILE
from detectors import DETECTORS, ROAD
from sim import CALIBRATION_DAY, DATA_DIR, RESULTS_DIR, ROOT, VALIDATION_DAY

OUT = ROOT.parent / "docs" / "data"


def no_nan(obj):
    """JSON has no NaN; browsers reject it, so write null instead."""
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, dict):
        return {k: no_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [no_nan(v) for v in obj]
    return obj


def write(name, obj):
    text = json.dumps(no_nan(obj), separators=(",", ":"), allow_nan=False)
    (OUT / f"{name}.json").write_text(text, encoding="utf-8")


def network():
    net = sumolib.net.readNet(str(NET_FILE))
    corridor = []
    for e in net.getEdges():
        if ROAD in e.getName():
            corridor.append([[round(lat, 6), round(lon, 6)] for lon, lat in
                             (net.convertXY2LonLat(x, y) for x, y in e.getShape())])
    signals = []
    for tls in net.getTrafficLights():
        nodes = {c[0].getEdge().getToNode() for c in tls.getConnections()}
        x = sum(n.getCoord()[0] for n in nodes) / len(nodes)
        y = sum(n.getCoord()[1] for n in nodes) / len(nodes)
        lon, lat = net.convertXY2LonLat(x, y)
        kind = "crossing" if len(tls.getConnections()) <= 2 else "junction"
        signals.append({"lat": round(lat, 6), "lon": round(lon, 6), "kind": kind})
    loc = pd.read_csv(DATA_DIR / "detector_locations.csv", encoding="utf-8-sig").set_index("AID_ID_Number")
    detectors = [{"id": d, "direction": DETECTORS[d], "lat": float(loc.loc[d, "Latitude"]),
                  "lon": float(loc.loc[d, "Longitude"]), "road": loc.loc[d, "Road_EN"].strip()} for d in DETECTORS]
    return {"corridor": corridor, "signals": signals, "detectors": detectors}


def series(test, day):
    df = pd.read_csv(RESULTS_DIR / f"{test}_{day}_comparison.csv")
    out = {}
    for det, d in df.sort_values("bin_start").groupby("detector"):
        out[det] = {"clock": d["clock"].tolist(), "observed": (d["count_15min"] * 4).tolist(),
                    "simulated": (d["sim_count"] * 4).round().astype(int).tolist(),
                    "periods": d["periods"].tolist(), "geh": d["geh"].round(2).tolist()}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    write("network", network())
    write("counts", {"calibration": series("calibration", CALIBRATION_DAY), "holdout": series("holdout", VALIDATION_DAY)})
    grid = pd.read_csv(RESULTS_DIR / "calibration_grid.csv")
    write("calibration_grid", grid[["tau", "minGap", "geh_pass_pct", "geh_pass_min", "geh_pass_max",
                                    "lane_share_mae_pp", "all_valid", "selected"]].to_dict("records"))
    sig = pd.read_csv(RESULTS_DIR / "signal_comparison.csv", dtype={"day": str})
    write("signals", sig[["day", "plan", "mean_delay_s", "delay_ci95_s", "diff_vs_actuated_s", "diff_ci95_s",
                          "valid_runs"]].to_dict("records"))
    summary = pd.read_csv(RESULTS_DIR / "validation_summary.csv", dtype={"day": str})
    tests = json.loads((RESULTS_DIR / "holdout_tests.json").read_text())
    sat = json.loads((RESULTS_DIR / "saturation_flow.json").read_text())
    write("summary", {"validation": summary.where(summary.notna(), None).to_dict("records"),
                      "holdout_tests": tests, "saturation": sat, "git_commit": db.git_commit()})
    print("written to", OUT)


if __name__ == "__main__":
    main()
