"""SQLite store for detector readings and experiment runs (data/tiasim.sqlite).

readings  one row per date, 30-second period, detector and lane (from data/detectors_*.csv,
          which stay the committed source; the database is rebuilt from them if missing)
<table>   each experiment writes its per-run results (calibration_runs, validation_runs,
          signal_runs, ...) with the git commit and time of the run

Usage: python db.py          rebuild the database from the CSV files and list its tables
"""
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent
# Quick runs (QUICK=1) get their own file so they never replace the full experiment tables.
DB_FILE = ROOT / "data" / ("tiasim_quick.sqlite" if os.environ.get("QUICK") == "1" else "tiasim.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS readings (
    date        TEXT NOT NULL,     -- YYYYMMDD
    period_from TEXT NOT NULL,     -- start of the 30-second period, HH:MM:SS
    detector    TEXT NOT NULL,
    lane        TEXT NOT NULL,     -- TD lane label: Slow / Middle / Fast Lane
    volume      INTEGER,
    speed_kmh   REAL,
    occupancy   REAL,
    valid       TEXT,              -- 'Y' or 'N' as published by TD
    PRIMARY KEY (date, period_from, detector, lane)
);
"""


def connect():
    con = sqlite3.connect(DB_FILE)
    con.executescript(SCHEMA)
    return con


def add_readings(df):
    """Insert or replace detector readings (columns as in data/detectors_*.csv)."""
    cols = ["date", "period_from", "detector", "lane", "volume", "speed_kmh", "occupancy", "valid"]
    rows = df[cols].astype({"date": str}).itertuples(index=False, name=None)
    with connect() as con:
        con.executemany(f"INSERT OR REPLACE INTO readings ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)


def build():
    """Load every committed detectors_*.csv into the readings table."""
    for csv in sorted((ROOT / "data").glob("detectors_*.csv")):
        add_readings(pd.read_csv(csv))


def ensure():
    with connect() as con:
        empty = con.execute("SELECT COUNT(*) FROM readings").fetchone()[0] == 0
    if empty:
        build()


def readings(day):
    """All readings for one day as a DataFrame."""
    ensure()
    with connect() as con:
        return pd.read_sql_query("SELECT * FROM readings WHERE date = ? ORDER BY period_from, detector, lane",
                                 con, params=(day,))


def volume_by_period(day):
    """Valid vehicles per 30-second period and detector, summed over lanes."""
    ensure()
    query = """
        SELECT period_from, detector, SUM(volume) AS volume
        FROM readings
        WHERE date = ? AND valid = 'Y'
        GROUP BY period_from, detector
        ORDER BY period_from, detector
    """
    with connect() as con:
        return pd.read_sql_query(query, con, params=(day,))


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        pass
    # No git on PATH: read .git directly (HEAD is either a hash or a ref to one).
    git_dir = ROOT.parent / ".git"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if head.startswith("ref: "):
            ref = head[5:]
            ref_file = git_dir / ref
            if ref_file.exists():
                return ref_file.read_text().strip()[:7]
            for line in (git_dir / "packed-refs").read_text().splitlines():
                if line.endswith(" " + ref):
                    return line[:7]
            return "unknown"
        return head[:7]
    except OSError:
        return "unknown"


def store(table, df):
    """Replace an experiment table with the latest runs, tagged with the code version."""
    out = df.copy()
    out["git_commit"] = git_commit()
    out["stored_at"] = datetime.now().isoformat(timespec="seconds")
    with connect() as con:
        out.to_sql(table, con, if_exists="replace", index=False)


if __name__ == "__main__":
    build()
    with connect() as con:
        for (name,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"):
            print(name, con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
