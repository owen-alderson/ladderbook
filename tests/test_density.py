import math

import numpy as np
import pytest
from scipy.special import ndtr

from ladderbook.fair.density import (
    implied_from_ladder,
    SECONDS_PER_YEAR,
    Settlement,
    Smile,
    averaged_variance_seconds,
    prob_above,
    prob_above_flat,
)

SPOT = 81_800.0
SIGMA = 0.45


def test_averaged_variance_matches_brute_force_covariance():
    for n in (1, 2, 10, 60):
        brute = sum(min(i, j) for i in range(1, n + 1) for j in range(1, n + 1)) / n**2
        assert averaged_variance_seconds(n) == pytest.approx(brute, rel=1e-12)
    assert averaged_variance_seconds(60) == pytest.approx(20.5028, abs=1e-4)  # vs 60 for one print


def test_flat_model_is_black_scholes_digital_with_effective_time():
    strikes = np.array([80_000, 81_800, 83_000.0])
    where = Settlement(seconds_to_window=1800)
    v = SIGMA**2 * (1800 + averaged_variance_seconds(60)) / SECONDS_PER_YEAR
    expected = ndtr((np.log(SPOT / strikes) - v / 2) / math.sqrt(v))
    assert prob_above_flat(SPOT, strikes, SIGMA, where) == pytest.approx(expected, abs=1e-12)


def _simulate(seconds_to_window, observed, n_paths=200_000, seed=7):
    """Per-second GBM; settlement = mean of observed prints + the remaining prints."""
    rng = np.random.default_rng(seed)
    remaining = 60 - len(observed)
    # one exact lognormal jump to the window, then one step per printed second
    times = np.array([seconds_to_window] + [1.0] * remaining) / SECONDS_PER_YEAR
    shocks = rng.standard_normal((n_paths, len(times))) * SIGMA * np.sqrt(times) - 0.5 * SIGMA**2 * times
    paths = SPOT * np.exp(np.cumsum(shocks, axis=1))
    future = paths[:, 1:]
    return (sum(observed) + future.sum(axis=1)) / 60


@pytest.mark.parametrize("seconds_to_window", [0, 600, 3540])
def test_flat_model_matches_monte_carlo_before_the_window(seconds_to_window):
    settle = _simulate(seconds_to_window, [])
    strikes = SPOT * np.array([0.995, 0.999, 1.0, 1.001, 1.005])
    model = prob_above_flat(SPOT, strikes, SIGMA, Settlement(seconds_to_window))
    mc = (settle[:, None] > strikes).mean(axis=0)
    assert np.all(np.abs(model - mc) < 4 * np.sqrt(mc * (1 - mc) / settle.size) + 2e-4)


def test_flat_model_matches_monte_carlo_inside_the_window():
    observed = [SPOT * (1 + 0.0002 * i) for i in range(30)]  # half the window already printed
    settle = _simulate(0, observed)
    strikes = np.array([81_850, 81_900, 81_950.0])
    where = Settlement(0, observed_sum=sum(observed), observed_count=30)
    model = prob_above_flat(SPOT, strikes, SIGMA, where)
    mc = (settle[:, None] > strikes).mean(axis=0)
    assert np.all(np.abs(model - mc) < 4 * np.sqrt(mc * (1 - mc) / settle.size) + 2e-4)


def test_averaging_moves_out_of_the_money_prices():
    # 0.3% out of the money, 40 minutes to close: one print vs the 60-second average
    strike = np.array([SPOT * 1.003])
    averaged = prob_above_flat(SPOT, strike, SIGMA, Settlement.from_seconds_to_close(2400))[0]
    v_single = SIGMA**2 * 2400 / SECONDS_PER_YEAR
    single = ndtr((math.log(SPOT / strike[0]) - v_single / 2) / math.sqrt(v_single))
    assert averaged < single  # less variance -> out-of-the-money contracts are worth less


def test_fully_observed_window_is_certain():
    where = Settlement(0, observed_sum=60 * 81_000.0, observed_count=60)
    assert list(prob_above_flat(SPOT, [80_999.0, 81_000.0, 81_001.0], SIGMA, where)) == [1.0, 0.0, 0.0]


def test_strike_already_out_of_reach_inside_window_is_one():
    # 59 prints far above the strike: even a crash on the last print cannot pull the average below
    where = Settlement(0, observed_sum=59 * 90_000.0, observed_count=59)
    assert prob_above_flat(SPOT, [50_000.0], SIGMA, where)[0] == 1.0


def _smile(vols):
    T = 0.5 / 365
    strikes = SPOT * np.exp(np.linspace(-0.2, 0.2, len(vols)))
    return Smile.from_points(SPOT, T, strikes, vols)


def test_flat_smile_reproduces_flat_model():
    smile = _smile([SIGMA] * 21)
    strikes = SPOT * np.linspace(0.99, 1.01, 11)
    where = Settlement(1800)
    assert prob_above(SPOT, strikes, smile, where) == pytest.approx(
        prob_above_flat(SPOT, strikes, SIGMA, where), abs=1e-6
    )


def test_put_skew_raises_probability_of_finishing_above():
    # Higher vol for low strikes (dvol/dK < 0): P(S>K) = N(d2) - vega * dvol/dK goes UP.
    skew = _smile(np.linspace(0.60, 0.35, 21))
    flat = _smile([skew.atm_vol] * 21)
    strikes = SPOT * np.array([0.998, 1.0, 1.002])
    where = Settlement(1800)
    assert np.all(prob_above(SPOT, strikes, skew, where) > prob_above(SPOT, strikes, flat, where))


def test_smile_probabilities_fall_as_strike_rises():
    # Deribit quotes its daily BTC expiry out to roughly +/-25%; a typical smile, curved in log-moneyness
    k = np.linspace(-0.25, 0.25, 21)
    smile = Smile.from_points(SPOT, 0.5 / 365, SPOT * np.exp(k), 0.44 - 0.3 * k + 2.0 * k**2)
    p = prob_above(SPOT, SPOT * np.linspace(0.95, 1.05, 1001), smile, Settlement(3000))
    assert np.all(np.diff(p) <= 1e-12)


def test_svi_fit_recovers_a_known_smile():
    truth = Smile(expiry_years=0.5 / 365, a=1e-5, b=2e-4, rho=-0.3, m=0.002, sigma=0.02)
    k = np.linspace(-0.2, 0.2, 25)
    fitted = Smile.from_points(SPOT, truth.expiry_years, SPOT * np.exp(k), truth.vol(k))
    assert fitted.vol(k) == pytest.approx(truth.vol(k), rel=1e-4)
    assert np.all(fitted.butterfly_free(k))


def test_svi_fit_smooths_noisy_marks():
    rng = np.random.default_rng(1)
    k = np.linspace(-0.2, 0.2, 33)
    clean = 0.44 - 0.3 * k + 2.0 * k**2
    noisy = clean + rng.normal(0, 0.004, k.size)  # marks quoted to a tenth of a vol point, plus noise
    fitted = Smile.from_points(SPOT, 0.5 / 365, SPOT * np.exp(k), noisy)
    near = np.abs(k) / (0.44 * math.sqrt(0.5 / 365)) <= 4  # where Kalshi's ladder maps to
    assert np.max(np.abs(fitted.vol(k[near]) - clean[near])) < 0.005


def test_too_few_strikes_for_svi():
    with pytest.raises(ValueError):
        Smile.from_points(100.0, 1.0, [90, 100, 110], [0.5, 0.4, 0.3])


def test_butterfly_check_flags_negative_density():
    # a smile with a very steep, sharply curved wing violates Gatheral's condition
    bad = Smile(expiry_years=1.0, a=-0.5, b=2.0, rho=0.999, m=0.0, sigma=0.05)
    assert not np.all(bad.butterfly_free(np.linspace(-0.5, 0.5, 101)))


def test_implied_spot_and_vol_round_trip():
    strikes = SPOT + np.arange(-500, 501, 100.0)
    where = Settlement(1800)
    probs = prob_above_flat(SPOT, strikes, SIGMA, where)
    spot, vol = implied_from_ladder(strikes, probs, where)
    assert spot == pytest.approx(SPOT, rel=1e-9)
    assert vol == pytest.approx(SIGMA, rel=1e-9)


def test_implied_needs_a_real_ladder():
    with pytest.raises(ValueError):
        implied_from_ladder([1.0, 2.0], [0.999, 0.001], Settlement(100))
    with pytest.raises(ValueError):
        implied_from_ladder([1.0, 2.0], [0.4, 0.6], Settlement(100))
