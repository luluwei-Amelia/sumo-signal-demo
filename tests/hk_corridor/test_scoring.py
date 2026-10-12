"""GEH, count scaling, pass rates and the validity rule."""
import numpy as np
import pandas as pd
import pytest

import calibrate
import counts
import sim


def test_geh_known_values():
    assert calibrate.geh(1100, 1000) == pytest.approx(3.086, abs=1e-3)
    assert calibrate.geh(200, 100) == pytest.approx(8.165, abs=1e-3)
    assert calibrate.geh(0, 0) == 0


def test_to_seconds_ignores_seconds_field():
    assert counts.to_seconds("06:45") == 6 * 3600 + 45 * 60
    assert counts.to_seconds("07:00:30") == 7 * 3600


def fake_periods(rows):
    return lambda day: pd.DataFrame(rows, columns=["period_from", "detector", "volume"])


def test_load_counts_scales_to_30_periods(monkeypatch):
    # 3 of 30 periods in the 07:00 bin, mean 12 vehicles -> 12 x 30 = 360
    monkeypatch.setattr(counts.db, "volume_by_period", fake_periods(
        [("07:00:00", "D1", 10), ("07:05:00", "D1", 12), ("07:10:30", "D1", 14)]))
    c = counts.load_counts("x")
    assert len(c) == 1
    row = c.iloc[0]
    assert (row["bin_start"], row["periods"], row["observed_veh"], row["count_15min"]) == (900, 3, 36, 360)
    assert row["clock"] == "07:00"


def test_load_counts_drops_periods_outside_the_window(monkeypatch):
    monkeypatch.setattr(counts.db, "volume_by_period", fake_periods(
        [("06:40:00", "D1", 5), ("10:00:00", "D1", 5), ("07:00:00", "D1", 5)]))
    assert counts.load_counts("x")["periods"].sum() == 1


def test_pass_rate_windows_start_at_0700():
    # Two 15-min bins inside one 30-min window: each misses by 100 veh/h, the window sums match.
    df = pd.DataFrame({"detector": ["D1", "D1"], "bin_start": [900, 1800],
                       "sim_count": [300.0, 200.0], "count_15min": [200, 300]})
    assert calibrate.pass_rate(df, 15) == 0.0
    assert calibrate.pass_rate(df, 30) == 100.0


def test_validity_rule():
    ok = sim.validity({"Teleports": "3 (Collisions: 1, Jam: 2)", "Waiting": "0", "Running": "0"})
    assert ok["valid"] and ok["teleports"] == 3
    assert not sim.validity({"Teleports": "51 (Jam: 51)"})["valid"]
    assert not sim.validity({"Waiting": "12"})["valid"]
    assert not sim.validity({"Running": "1"})["valid"]
    assert sim.validity({})["valid"]  # SUMO omits the lines when there is nothing to report


def test_sampling_noise_is_zero_for_identical_periods(monkeypatch):
    monkeypatch.setattr(counts.db, "volume_by_period", fake_periods(
        [(f"07:0{i}:00", "D1", 20) for i in range(5)]))
    noise = counts.sampling_noise("x", n_boot=50)
    assert noise["share_geh_ge_5"].tolist() == [0.0]


def test_sampling_noise_grows_with_spread(monkeypatch):
    monkeypatch.setattr(counts.db, "volume_by_period", fake_periods(
        [("07:00:00", "D1", 2), ("07:05:00", "D1", 40), ("07:10:00", "D1", 6)]))
    assert counts.sampling_noise("x", n_boot=200)["share_geh_ge_5"].iloc[0] > 0.5


def test_median_run_picks_middle_pass_rate():
    runs = pd.DataFrame({"run": list("abcde"), "geh_pass_pct": [90.0, 80.0, 99.0, 85.0, 95.0]})
    assert calibrate.median_run(runs)["run"] == "a"
    assert np.isclose(calibrate.median_run(runs.iloc[:4])["geh_pass_pct"], 85.0)
