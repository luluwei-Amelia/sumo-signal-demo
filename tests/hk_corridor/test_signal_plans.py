"""Signal-programme manipulation used for the plan comparison."""
import pytest

import compare_signals as cs


def test_is_green():
    assert cs.is_green("GGrr") and cs.is_green("rrgg")
    assert not cs.is_green("yyrr")


def test_crossings_are_single_green_programmes():
    programmes = {"crossing": [(79, "G"), (6, "y"), (5, "r")],
                  "junction": [(30, "GGrr"), (6, "yyrr"), (30, "rrGG"), (6, "rryy")]}
    assert cs.crossings(programmes) == ["crossing"]


def test_apply_greens_keeps_yellows_and_floors_greens():
    base = {"J": [(30, "GGrr"), (6, "yyrr"), (30, "rrGG"), (6, "rryy")]}
    out = cs.apply_greens(base, {"J": [3, None, 25, None]})
    assert out["J"] == [(cs.MIN_GREEN, "GGrr"), (6, "yyrr"), (25, "rrGG"), (6, "rryy")]


def test_apply_greens_rejects_changed_phase_structure():
    with pytest.raises(ValueError):
        cs.apply_greens({"J": [(30, "GG"), (6, "yy")]}, {"J": [20, None, 10]})


def test_stretch_to_common_cycle():
    programmes = {"A": [(20, "Gr"), (6, "yr"), (10, "rG"), (6, "ry")],      # 42 s
                  "B": [(30, "Gr"), (6, "yr"), (20, "rG"), (6, "ry")]}      # 62 s
    out, cycle = cs.stretch_to_common_cycle(programmes)
    assert cycle == 62
    for phases in out.values():
        assert sum(d for d, _ in phases) == 62
        assert [d for d, s in phases if "y" in s] == [6, 6]  # yellows untouched
