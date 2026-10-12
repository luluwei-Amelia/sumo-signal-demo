"""Generate route files (traffic demand) for different demand levels."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ROUTE_DIR = ROOT / "network"

# Vehicles per hour entering from each approach at the "medium" level.
# North-south is the busy corridor, east-west the minor road.
BASE_VOLUME = {"N": 500, "S": 500, "E": 250, "W": 250}

# Share of vehicles going through / turning left / turning right.
TURN_SHARE = {"through": 0.70, "left": 0.15, "right": 0.15}

# Destination edge for each (origin, movement). Left-hand traffic (Hong Kong):
# the right turn is the one that crosses oncoming traffic.
DESTINATION = {
    "N": {"through": "C2S", "left": "C2E", "right": "C2W"},
    "S": {"through": "C2N", "left": "C2W", "right": "C2E"},
    "E": {"through": "C2W", "left": "C2S", "right": "C2N"},
    "W": {"through": "C2E", "left": "C2N", "right": "C2S"},
}

DEMAND_LEVELS = {"low": 0.6, "medium": 1.0, "high": 1.4}
DURATION = 3600  # seconds of demand


def write_routes(level, multiplier):
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<routes>",
        '    <vType id="car" accel="2.6" decel="4.5" sigma="0.5" length="5" maxSpeed="13.89"/>',
    ]
    for origin, volume in BASE_VOLUME.items():
        for movement, share in TURN_SHARE.items():
            veh_per_hour = volume * multiplier * share
            # Random (Bernoulli) arrivals each second, so different seeds give different traffic.
            lines.append(
                f'    <flow id="{origin}_{movement}" type="car" begin="0" end="{DURATION}" '
                f'from="{origin}2C" to="{DESTINATION[origin][movement]}" '
                f'probability="{veh_per_hour / 3600:.4f}" departLane="best" departSpeed="max"/>'
            )
    lines.append("</routes>")

    route_file = ROUTE_DIR / f"demand_{level}.rou.xml"
    route_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return route_file


def write_all_routes():
    return {level: write_routes(level, m) for level, m in DEMAND_LEVELS.items()}


if __name__ == "__main__":
    for level, path in write_all_routes().items():
        print(f"{level}: {path}")
