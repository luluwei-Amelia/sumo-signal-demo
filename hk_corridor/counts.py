"""Turn the raw 30-second detector records into 15-minute vehicle counts.

The archive is incomplete: between 07:00 and 10:00 only 128 (22 Sep) and 140 (23 Sep) of the
360 thirty-second periods could be retrieved, and coverage per 15-minute bin varies from 3
to 18 of 30 periods. Each count is the observed vehicles per period scaled to 30 periods, so
bins with few periods carry a large sampling error; the number of periods is kept in the
output for that reason.
"""
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

START = "06:45"   # simulation time 0; the first 15 minutes serve as warm-up
END = "10:00"
BIN_MIN = 15
PERIODS_PER_BIN = BIN_MIN * 2  # 30-second periods


def to_seconds(hhmm):
    h, m = map(int, hhmm.split(":")[:2])
    return h * 3600 + m * 60


def load_counts(day):
    """One row per (bin, detector): observed periods, vehicles counted, scaled 15-min count."""
    raw = pd.read_csv(DATA_DIR / f"detectors_{day}.csv")
    raw = raw[raw["valid"] == "Y"]
    t = raw["period_from"].map(to_seconds) - to_seconds(START)
    raw = raw[(t >= 0) & (t < to_seconds(END) - to_seconds(START))].copy()
    raw["bin_start"] = (t // (BIN_MIN * 60) * BIN_MIN * 60).astype(int)

    grouped = raw.groupby(["bin_start", "detector"])
    counts = pd.DataFrame({
        "periods": grouped["period_from"].nunique(),
        "observed_veh": grouped["volume"].sum(),     # summed over all lanes
    }).reset_index()
    counts["count_15min"] = (counts["observed_veh"] / counts["periods"] * PERIODS_PER_BIN).round().astype(int)
    counts["clock"] = counts["bin_start"].map(
        lambda s: f"{(to_seconds(START) + s) // 3600:02d}:{(to_seconds(START) + s) % 3600 // 60:02d}"
    )
    return counts


CONSECUTIVE = [  # detectors in driving order along each direction
    ("TDSAPR10001", "AID01101"), ("AID01101", "AID01102"),
    ("AID01222", "AID01223"), ("AID01223", "TDSAPR20001"),
]


def derived_counts(counts, located, net):
    """Counts implied by flow conservation between consecutive detectors.

    Vehicles do not vanish: if between two detectors there is exactly one way off the
    corridor and no way on, it must carry (upstream - downstream); with exactly one way
    on and no way off, it carries (downstream - upstream). Where several side roads
    exist the split is unknown and nothing is added.
    Returns rows (bin_start, edge, count).
    """
    edge_of = dict(zip(located["detector"], located["edge"]))
    table = counts.pivot(index="bin_start", columns="detector", values="count_15min")
    rows = []
    for up, down in CONSECUTIVE:
        path, _ = net.getShortestPath(net.getEdge(edge_of[up]), net.getEdge(edge_of[down]))
        on_path = {e.getID() for e in path}
        nodes = [e.getToNode() for e in path[:-1]]
        offs = {o for n in nodes for o in n.getOutgoing() if o.getID() not in on_path
                and not any(o.getToNode() == p.getFromNode() and o.getFromNode() == p.getToNode() for p in path)}
        ons = {i for n in nodes for i in n.getIncoming() if i.getID() not in on_path
               and not any(i.getFromNode() == p.getToNode() and i.getToNode() == p.getFromNode() for p in path)}
        diff = table[up] - table[down]
        if len(offs) == 1 and not ons:
            side, values = offs.pop(), diff.clip(lower=0)
        elif len(ons) == 1 and not offs:
            side, values = ons.pop(), (-diff).clip(lower=0)
        else:
            continue
        rows += [(int(b), side.getID(), int(v)) for b, v in values.items()]
    return rows


def write_edgedata(counts, located, out_file, extra=()):
    """Write detector counts (plus any derived side-road counts) in SUMO's edgeData format."""
    edge_of = dict(zip(located["detector"], located["edge"]))
    extra_by_bin = {}
    for begin, edge, count in extra:
        extra_by_bin.setdefault(begin, []).append((edge, count))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<data>"]
    for begin, group in counts.groupby("bin_start"):
        lines.append(f'    <interval id="{begin}" begin="{begin}" end="{begin + BIN_MIN * 60}">')
        for r in group.itertuples():
            lines.append(f'        <edge id="{edge_of[r.detector]}" entered="{r.count_15min}"/>')
        for edge, count in extra_by_bin.get(begin, []):
            lines.append(f'        <edge id="{edge}" entered="{count}"/>')
        lines.append("    </interval>")
    lines.append("</data>")
    Path(out_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_file


if __name__ == "__main__":
    import sys

    for day in sys.argv[1:] or ["20260922"]:
        print(day)
        print(load_counts(day).pivot(index="clock", columns="detector", values="count_15min").to_string())
