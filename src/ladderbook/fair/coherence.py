"""Make one hour's contract prices consistent with each other (Kroer, Dudik et al. 2016).

All of an hour's contracts ("above $60k", "above $61k", "$60k–60.5k range", ...) pay off
on the same settlement value. Cut the price line at every strike and the hour has a
finite list of outcomes: "settles in bin j". Prices are coherent exactly when they are
the payoffs averaged over some probability distribution on those bins. Example: "above
61k" can't cost more than "above 60k", and ranges that tile the line must sum to $1.

`project` finds the closest coherent prices to the market's, measuring distance with
the binary KL divergence (the natural geometry for probabilities, and the one LMSR
market makers use). Frank-Wolfe needs only one hard step per iteration: "which single
outcome is cheapest under the current gradient?", which here is a scan over bins. No
solver is needed.

The divergence left at the optimum measures how incoherent the market is. On mid
prices that is a signal, not an arbitrage: only bids and asks can be traded.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Contract:
    """Pays $1 if floor < settlement <= cap (None = unbounded)."""

    floor: float | None = None
    cap: float | None = None

    @classmethod
    def above(cls, strike: float) -> Contract:
        return cls(floor=strike)


def payoff_matrix(contracts: list[Contract]) -> np.ndarray:
    """A[c, j] = 1 if contract c pays when the settlement lands in bin j."""
    cuts = sorted({x for c in contracts for x in (c.floor, c.cap) if x is not None})
    # one representative point inside each bin: below the first cut, between cuts, above the last
    points = [cuts[0] - 1.0] + [(a + b) / 2 for a, b in zip(cuts, cuts[1:])] + [cuts[-1] + 1.0]
    A = np.zeros((len(contracts), len(points)))
    for i, c in enumerate(contracts):
        for j, x in enumerate(points):
            A[i, j] = (c.floor is None or x > c.floor) and (c.cap is None or x <= c.cap)
    return A


def _kl(mu, theta, w):
    mu = np.clip(mu, 1e-15, 1 - 1e-15)
    return float(np.sum(w * (mu * np.log(mu / theta) + (1 - mu) * np.log((1 - mu) / (1 - theta)))))


def _grad(mu, theta, w):
    mu = np.clip(mu, 1e-15, 1 - 1e-15)
    return w * (np.log(mu / (1 - mu)) - np.log(theta / (1 - theta)))


@dataclass(frozen=True)
class Projection:
    prices: np.ndarray  # coherent prices, one per contract
    bin_probs: np.ndarray  # the distribution over settlement bins that produces them
    divergence: float  # weighted KL from market to coherent: 0 when already coherent
    gap: float  # Frank-Wolfe duality gap: an upper bound on remaining suboptimality
    iterations: int


def project(A: np.ndarray, market: np.ndarray, weights: np.ndarray | None = None, tol: float = 1e-10,
            max_iter: int = 20_000) -> Projection:
    """Closest coherent prices to `market` (each strictly inside (0, 1)).

    Pairwise Frank-Wolfe: each step moves probability from the worst bin currently in
    use to the best bin overall. Unlike plain Frank-Wolfe, which zig-zags towards
    optima on a face of the polytope, this converges linearly (Lacoste-Julien & Jaggi 2015).
    """
    theta = np.asarray(market, dtype=float)
    if np.any((theta <= 0) | (theta >= 1)):
        raise ValueError("market prices must be strictly between 0 and 1")
    w = np.ones_like(theta) if weights is None else np.asarray(weights, dtype=float)
    n_bins = A.shape[1]
    p = np.full(n_bins, 1.0 / n_bins)  # start in the interior: every bin possible
    mu = A @ p
    gap = np.inf
    k = 0
    for k in range(1, max_iter + 1):
        g = _grad(mu, theta, w)
        scores = g @ A  # linear objective at every vertex (single outcome)
        best = int(np.argmin(scores))
        gap = float(g @ mu - scores[best])
        if gap <= tol:
            break
        active = np.flatnonzero(p > 0)
        worst = int(active[np.argmax(scores[active])])
        direction = A[:, best] - A[:, worst]
        # exact line search over the mass we can move; the objective is convex along it
        lo, hi = 0.0, p[worst]
        for _ in range(60):
            mid = (lo + hi) / 2
            if _grad(mu + mid * direction, theta, w) @ direction > 0:
                hi = mid
            else:
                lo = mid
        step = lo if hi < p[worst] else hi  # take the full move when the optimum is at the end
        mu = mu + step * direction
        p[best] += step
        p[worst] -= step
        if p[worst] < 1e-15:
            p[worst] = 0.0
    return Projection(mu, p, _kl(mu, theta, w), gap, k)
