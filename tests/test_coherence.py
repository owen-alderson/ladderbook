import numpy as np
import pytest

from ladderbook.fair.coherence import Contract, payoff_matrix, project


def test_payoff_matrix_bins():
    A = payoff_matrix([Contract.above(60), Contract.above(61), Contract(floor=60, cap=61)])
    # bins: <=60, (60,61], >61
    assert A.tolist() == [[0, 1, 1], [0, 0, 1], [0, 1, 0]]


def test_coherent_prices_are_left_alone():
    A = payoff_matrix([Contract.above(k) for k in (60, 61, 62)])
    market = np.array([0.7, 0.5, 0.2])
    result = project(A, market)
    assert result.prices == pytest.approx(market, abs=1e-6)
    assert result.divergence == pytest.approx(0, abs=1e-9)


def test_inverted_strikes_meet_in_the_middle():
    # "above 61" priced above "above 60" is impossible; by symmetry the KL projection is 0.5 / 0.5
    A = payoff_matrix([Contract.above(60), Contract.above(61)])
    result = project(A, np.array([0.4, 0.6]))
    assert result.prices == pytest.approx([0.5, 0.5], abs=1e-4)
    assert result.divergence > 0


def test_ranges_that_tile_the_line_are_scaled_to_sum_to_one():
    A = payoff_matrix([Contract(cap=60), Contract(floor=60, cap=61), Contract(floor=61)])
    result = project(A, np.array([0.3, 0.3, 0.3]))
    assert result.prices == pytest.approx([1 / 3] * 3, abs=1e-4)
    assert result.bin_probs.sum() == pytest.approx(1.0)


def test_random_ladders_come_out_monotone_and_consistent_with_their_ranges():
    rng = np.random.default_rng(0)
    strikes = np.arange(60, 70)
    contracts = [Contract.above(k) for k in strikes] + [Contract(floor=a, cap=a + 1) for a in strikes[:-1]]
    A = payoff_matrix(contracts)
    for _ in range(5):
        market = np.clip(rng.uniform(0.02, 0.98, len(contracts)), 0.02, 0.98)
        result = project(A, market)
        above = result.prices[: len(strikes)]
        ranges = result.prices[len(strikes) :]
        assert np.all(np.diff(above) <= 1e-6)
        assert ranges == pytest.approx(above[:-1] - above[1:], abs=1e-6)
        assert result.gap < 1e-6


def test_prices_on_the_boundary_are_rejected():
    with pytest.raises(ValueError):
        project(payoff_matrix([Contract.above(1)]), np.array([1.0]))
