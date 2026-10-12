"""Draw the study network with the six detectors and the traffic signals."""
from pathlib import Path

import matplotlib.pyplot as plt
import sumolib

from build_network import NET_FILE
from detectors import ROAD, locate_detectors

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / "network_map.png"
EAST, WEST, INK, MUTED = "#2a78d6", "#eb6834", "#0b0b0b", "#898781"


def plot():
    net = sumolib.net.readNet(str(NET_FILE), withPrograms=True)
    fig, ax = plt.subplots(figsize=(11, 5.5), dpi=150)
    for edge in net.getEdges():
        xs, ys = zip(*edge.getShape())
        on_road = ROAD in edge.getName()
        ax.plot(xs, ys, color="#52514e" if on_road else "#d4d3cd", lw=2.2 if on_road else 0.8, zorder=1)

    for tls in net.getTrafficLights():
        x, y = tls.getConnections()[0][0].getEdge().getToNode().getCoord()
        ax.scatter(x, y, s=40, marker="s", color=MUTED, zorder=2)

    for r in locate_detectors(NET_FILE).itertuples():
        edge = net.getEdge(r.edge)
        x, y = sumolib.geomhelper.positionAtShapeOffset(edge.getShape(), r.pos_m)
        color = EAST if r.direction == "east" else WEST
        ax.scatter(x, y, s=90, color=color, edgecolor="white", linewidth=1.5, zorder=3)
        ax.annotate(r.detector, (x, y), textcoords="offset points", fontsize=8, color=INK,
                    xytext=(0, 10 if r.direction == "east" else -16), ha="center")

    ax.scatter([], [], s=90, color=EAST, label="Detector, eastbound")
    ax.scatter([], [], s=90, color=WEST, label="Detector, westbound")
    ax.scatter([], [], s=40, marker="s", color=MUTED, label="Traffic signal")
    ax.plot([], [], color="#52514e", lw=2.2, label=ROAD)
    ax.legend(frameon=False, loc="upper left", fontsize=8)
    ax.set_title("Study area: Aberdeen Praya Road, Hong Kong (left-hand traffic)", loc="left", color=INK)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    OUT.parent.mkdir(exist_ok=True)
    fig.savefig(OUT)
    return OUT


if __name__ == "__main__":
    print(plot())
