"""What an "above K" contract should cost, given the options market.

Kalshi's crypto contracts settle on the simple average of 60 one-second BRTI prints
before the hour, not on one print. Averaging lowers the variance of the settlement
value, and inside the final minute part of the average is already known. Both are
handled here exactly (for a lognormal price), not approximated away.

Model, in plain terms:
  * BTC follows a driftless lognormal path over the next hours (discounting and
    funding are below a hundredth of a cent at these horizons and are ignored).
  * Volatility comes from Deribit's implied-volatility smile for the nearest expiry,
    mapped onto the shorter horizon by standardised moneyness (how many standard
    deviations away the strike is), not by raw distance.
  * P(settle > K) is minus the slope of the call-price curve in K:
        P = N(d2) - vega * dvol/dK
    so the smile's skew moves the probability exactly as it moves option prices. The
    smile is a fitted SVI curve, so it has no kinks or wiggles for the probability to
    jump at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import least_squares
from scipy.special import ndtr, ndtri

SECONDS_PER_YEAR = 365.0 * 24 * 3600
WINDOW_TICKS = 60  # BRTI prints averaged at settlement, one per second


def averaged_variance_seconds(n: int, spacing: float = 1.0) -> float:
    """Variance (in seconds of Brownian time) of the mean of n prints spaced `spacing` apart,
    starting one spacing after now: Var(mean of B(i*dt), i=1..n) = dt*(n+1)(2n+1)/(6n)."""
    return spacing * (n + 1) * (2 * n + 1) / (6 * n)


@dataclass(frozen=True)
class Settlement:
    """Where we are relative to the 60-second averaging window.

    seconds_to_window: seconds until the first averaged print (0 once inside the window).
    observed_sum / observed_count: prints already inside the window (BRTI is streamed
    by Kalshi's cfbenchmarks_value channel, so these are known exactly).
    """

    seconds_to_window: float
    observed_sum: float = 0.0
    observed_count: int = 0

    @classmethod
    def from_seconds_to_close(cls, seconds: float) -> Settlement:
        return cls(seconds_to_window=max(seconds - WINDOW_TICKS, 0.0))

    @property
    def remaining_ticks(self) -> int:
        return WINDOW_TICKS - self.observed_count


def prob_above_flat(spot: float, strikes, sigma: float, where: Settlement) -> np.ndarray:
    """P(settlement average > K) with one volatility for every strike.

    The unknown part of the average is (remaining prints)/60 of the total; its mean is
    today's spot and its log-variance is sigma^2 times (time to the window plus the
    averaged-variance of the remaining prints), matched to a lognormal. At these
    horizons (sigma*sqrt(t) well under 1%) the lognormal match is accurate to well
    below a tick; tests check it against Monte Carlo.
    """
    strikes = np.asarray(strikes, dtype=float)
    m = where.remaining_ticks
    if m <= 0:
        average = where.observed_sum / WINDOW_TICKS
        return (average > strikes).astype(float)
    # Settlement = (observed_sum + m * future_mean) / 60  >  K   <=>  future_mean > K'
    threshold = (WINDOW_TICKS * strikes - where.observed_sum) / m
    var_seconds = where.seconds_to_window + averaged_variance_seconds(m)
    total_var = sigma**2 * var_seconds / SECONDS_PER_YEAR
    out = np.ones_like(threshold)
    live = threshold > 0
    sd = math.sqrt(total_var)
    out[live] = ndtr((np.log(spot / threshold[live]) - 0.5 * total_var) / sd)
    return out


@dataclass(frozen=True)
class Smile:
    """One option expiry's implied-volatility smile, as a fitted SVI curve (Gatheral 2004).

    SVI gives total implied variance w = vol^2 * T as a function of log-moneyness k = ln(K/F):
        w(k) = a + b * (rho * (k - m) + sqrt((k - m)^2 + sigma^2))
    Five numbers, smooth everywhere, linear wings (as Lee's moment formula requires).
    Fitting a curve rather than joining the quoted points matters: quoted marks wiggle
    between strikes, and every wiggle passed through would become a spurious bump in the
    probability density.
    """

    expiry_years: float
    a: float
    b: float
    rho: float
    m: float
    sigma: float

    @classmethod
    def from_points(cls, forward: float, expiry_years: float, strikes, vols, width: float = 3.0) -> Smile:
        """Least-squares SVI fit to quoted (strike, implied vol) points; needs at least 5.

        Errors are measured in volatility and weighted towards the money with a Gaussian
        in standardised moneyness (scale `width`). Far wings are thinly traded and
        venues often hold their marks flat there; left unweighted they would bend
        the curve where it is actually used.
        """
        strikes, vols = np.asarray(strikes, float), np.asarray(vols, float)
        if strikes.size < 5:
            raise ValueError("an SVI fit needs at least 5 quoted strikes")
        k = np.log(strikes / forward)
        atm_guess = float(vols[np.argmin(np.abs(k))])
        z = k / (atm_guess * math.sqrt(expiry_years))
        weight = np.exp(-0.5 * (z / width) ** 2)

        def residuals(x):
            smile = cls(expiry_years, *x)
            return (smile.vol(k) - vols) * weight

        w_atm = atm_guess**2 * expiry_years
        start = [w_atm * 0.5, w_atm / max(np.ptp(k), 1e-6), -0.2, 0.0, max(atm_guess * math.sqrt(expiry_years), 1e-4)]
        fit = least_squares(
            residuals,
            start,
            bounds=([-np.inf, 0.0, -0.999, -np.inf, 1e-5], [np.inf, np.inf, 0.999, np.inf, np.inf]),
            x_scale="jac",
        )
        return cls(expiry_years, *map(float, fit.x))

    def total_variance(self, k) -> np.ndarray:
        k = np.asarray(k, dtype=float)
        return self.a + self.b * (self.rho * (k - self.m) + np.sqrt((k - self.m) ** 2 + self.sigma**2))

    def vol(self, k) -> np.ndarray:
        return np.sqrt(np.maximum(self.total_variance(k), 1e-12) / self.expiry_years)

    @property
    def atm_vol(self) -> float:
        return float(self.vol(0.0))

    def dvol_dk(self, k) -> np.ndarray:
        k = np.asarray(k, dtype=float)
        dw = self.b * (self.rho + (k - self.m) / np.sqrt((k - self.m) ** 2 + self.sigma**2))
        return dw / (2 * self.expiry_years * self.vol(k))

    def butterfly_free(self, k) -> np.ndarray:
        """Gatheral's density condition g(k) >= 0 at each k (False = negative density there)."""
        k = np.asarray(k, dtype=float)
        root = np.sqrt((k - self.m) ** 2 + self.sigma**2)
        w = self.total_variance(k)
        dw = self.b * (self.rho + (k - self.m) / root)
        d2w = self.b * self.sigma**2 / root**3
        g = (1 - k * dw / (2 * w)) ** 2 - dw**2 / 4 * (1 / w + 0.25) + d2w / 2
        return g >= 0

    # The smile is carried to a shorter horizon by standardised moneyness z: a strike
    # z standard deviations away gets the volatility of the expiry's strike that is z
    # standard deviations away.
    def k_at_expiry(self, z) -> np.ndarray:
        return np.asarray(z, dtype=float) * self.atm_vol * math.sqrt(self.expiry_years)

    def vol_at(self, z) -> np.ndarray:
        return self.vol(self.k_at_expiry(z))

    def slope_at(self, z) -> np.ndarray:
        """d vol / d z."""
        return self.dvol_dk(self.k_at_expiry(z)) * self.atm_vol * math.sqrt(self.expiry_years)


def prob_above(spot: float, strikes, smile: Smile, where: Settlement) -> np.ndarray:
    """P(settlement average > K) using the smile.

    Before the averaging window starts, each strike gets the volatility found at the
    same standardised moneyness on the option expiry, with the window-adjusted variance,
    and the skew term -vega * dvol/dK is added. Inside the window the remaining
    uncertainty is seconds long and the smile is irrelevant; the flat formula with the
    ATM volatility is used.
    """
    strikes = np.asarray(strikes, dtype=float)
    if where.observed_count > 0 or where.seconds_to_window <= 0:
        return prob_above_flat(spot, strikes, smile.atm_vol, where)
    horizon = (where.seconds_to_window + averaged_variance_seconds(WINDOW_TICKS)) / SECONDS_PER_YEAR
    atm_sd = smile.atm_vol * math.sqrt(horizon)
    z = np.log(strikes / spot) / atm_sd
    vol = smile.vol_at(z)
    sd = vol * math.sqrt(horizon)
    d2 = (np.log(spot / strikes) - 0.5 * sd**2) / sd
    # vega = K * phi(d2) * sqrt(horizon);  dvol/dK = slope(z) / (K * atm_sd)
    skew_term = np.exp(-0.5 * d2**2) / math.sqrt(2 * math.pi) * math.sqrt(horizon) * smile.slope_at(z) / atm_sd
    return np.clip(ndtr(d2) - skew_term, 0.0, 1.0)


def implied_from_ladder(strikes, probs, where: Settlement, lo: float = 0.03, hi: float = 0.97) -> tuple[float, float]:
    """The (spot, volatility) a ladder of "above K" prices implies under the flat model.

    Under the flat model probit(P) = (ln S - v/2)/sqrt(v) - ln K / sqrt(v), a straight line
    in ln K, so a regression recovers S and v. Prices near 0 or 1 are dominated by tick
    rounding and are left out.
    """
    if where.observed_count > 0:
        raise ValueError("implied spot and vol are only defined before the averaging window")
    strikes, probs = np.asarray(strikes, float), np.asarray(probs, float)
    use = (probs > lo) & (probs < hi)
    if use.sum() < 2:
        raise ValueError("need at least two strikes priced away from 0 and 1")
    y = ndtri(probs[use])
    slope, intercept = np.polyfit(np.log(strikes[use]), y, 1)
    if slope >= 0:
        raise ValueError("ladder prices rise with strike: no lognormal fits")
    sd = -1.0 / slope
    spot = math.exp(intercept * sd + 0.5 * sd**2)
    horizon = (where.seconds_to_window + averaged_variance_seconds(WINDOW_TICKS)) / SECONDS_PER_YEAR
    return spot, sd / math.sqrt(horizon)
