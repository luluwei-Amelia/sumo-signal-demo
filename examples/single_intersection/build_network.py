"""Compile the plain node/edge XML into a SUMO network with netconvert."""
import subprocess
from pathlib import Path

from sumolib import checkBinary

ROOT = Path(__file__).resolve().parent
NET_DIR = ROOT / "network"
NET_FILE = NET_DIR / "intersection.net.xml"


def build_network():
    subprocess.run(
        [
            checkBinary("netconvert"),
            "--node-files", "intersection.nod.xml",
            "--edge-files", "intersection.edg.xml",
            "--tls.default-type", "static",
            "--no-turnarounds", "true",
            "--lefthand", "true",  # Hong Kong drives on the left
            "--output-file", NET_FILE.name,
        ],
        # Relative paths keep the local directory out of the header netconvert writes.
        check=True, cwd=NET_DIR,
    )
    return NET_FILE


if __name__ == "__main__":
    print(f"Network written to {build_network()}")
