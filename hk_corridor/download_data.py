"""Download historical detector data for the study corridor from DATA.GOV.HK.

The Transport Department feed covers all ~800 detectors; each archived version holds the
most recent 30-second periods, so consecutive versions overlap. The versions time-stamped
07:00-09:59 are fetched for each study day and only the six corridor detectors are kept.
Many versions are missing or return HTTP 404, so the saved periods run from about 06:53 to
09:50 with gaps (see counts.py).

Usage: python download_data.py 20260922 20260923
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from detectors import DETECTORS

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FEED = "https://resource.data.one.gov.hk/td/traffic-detectors/rawSpeedVol-all.xml"
ARCHIVE = "https://api.data.gov.hk/v1/historical-archive"
HOURS = ("07", "08", "09")
HEADERS = {"User-Agent": "sumo-signal-demo/1.0 (research portfolio)"}


def _get(url, retries=3):
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=120) as r:
                return r.read()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(5)


def list_versions(day):
    query = urllib.parse.urlencode({"url": FEED, "start": day, "end": day})
    versions = json.loads(_get(f"{ARCHIVE}/list-file-versions?{query}"))["timestamps"]
    return [t for t in versions if t[9:11] in HOURS]


def fetch_version(day, timestamp):
    """Return one row per (30-second period, detector, lane) for our detectors.

    A few archived versions are listed but cannot be fetched (HTTP 404); those are skipped.
    """
    query = urllib.parse.urlencode({"url": FEED, "time": timestamp})
    try:
        root = ET.fromstring(_get(f"{ARCHIVE}/get-file?{query}"))
    except urllib.error.HTTPError as err:
        if err.code == 404:
            return []
        raise
    rows = []
    for period in root.iter("period"):
        start = period.findtext("period_from")
        for det in period.iter("detector"):
            det_id = det.findtext("detector_id")
            if det_id not in DETECTORS:
                continue
            for lane in det.iter("lane"):
                rows.append({
                    "date": day, "period_from": start, "detector": det_id,
                    "lane": lane.findtext("lane_id"), "volume": int(lane.findtext("volume")),
                    "speed_kmh": float(lane.findtext("speed")), "occupancy": float(lane.findtext("occupancy")),
                    "valid": lane.findtext("valid"),
                })
    return rows


def download_day(day):
    versions = list_versions(day)
    print(f"{day}: {len(versions)} files between 07:00 and 10:00")
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(lambda t: fetch_version(day, t), versions))
    print(f"{day}: {sum(not b for b in batches)} of {len(versions)} files could not be fetched")
    df = pd.DataFrame([row for batch in batches for row in batch])
    # Consecutive files overlap; keep each 30-second period once.
    df = df.drop_duplicates(["period_from", "detector", "lane"]).sort_values(["period_from", "detector", "lane"])
    out = DATA_DIR / f"detectors_{day}.csv"
    df.to_csv(out, index=False)
    print(f"{day}: {df['period_from'].nunique()} periods saved to {out.name}")
    return out


if __name__ == "__main__":
    for day in sys.argv[1:]:
        download_day(day)
