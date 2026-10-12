"""Webster's cycle formula."""
import pytest

import signal_plans as sp


def test_webster_cycle_textbook_case():
    # L = 8 s, Y = 0.5: C = (1.5 * 8 + 5) / (1 - 0.5) = 34 s
    assert sp.webster_cycle(8, 0.5) == pytest.approx(34.0)


def test_webster_cycle_is_capped():
    assert sp.webster_cycle(8, 0.95) == sp.MAX_CYCLE
    assert sp.webster_cycle(8, 1.2) == sp.MAX_CYCLE
