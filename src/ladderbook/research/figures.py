"""Study A's two figures, drawn from results.json (needs the `research` extra: matplotlib)."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

SURFACE, INK, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#e4e3df"
SERIES = {"kalshi": ("#2a78d6", "o", "Kalshi mid"), "options": ("#eb6834", "s", "Options (DVOL)"), "realized": ("#1baf7a", "D", "Trailing realized vol")}


def _axes(title: str, ylabel: str):
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=150, facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=INK, fontsize=12, pad=12)
    ax.set_ylabel(ylabel, color=MUTED)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.axhline(0, color=MUTED, linewidth=1)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    return fig, ax


def _whiskers(ax, xs, ests, los, his, color, marker, label):
    ax.errorbar(xs, ests, yerr=[[e - lo for e, lo in zip(ests, los)], [hi - e for e, hi in zip(ests, his)]],
                fmt=marker, color=color, markersize=7, elinewidth=2, capsize=0, label=label,
                markeredgecolor=SURFACE, markeredgewidth=1.5)


def calibration(results: dict, path: Path) -> None:
    """Won minus forecast, per forecast bucket: 0 is perfectly calibrated."""
    fig, ax = _axes("Calibration error by forecast bucket (all looks, 95% CI by settlement hour)", "won − forecast (percentage points)")
    buckets = [r["bucket"] for r in results["calibration"]["kalshi"]]
    for j, (name, (color, marker, label)) in enumerate(SERIES.items()):
        table = {r["bucket"]: r for r in results["calibration"][name]}
        xs, est, lo, hi = [], [], [], []
        for i, b in enumerate(buckets):
            if b in table:
                r = table[b]
                xs.append(i + (j - 1) * 0.22)
                est.append((r["won"][0] - r["forecast"]) * 100)
                lo.append((r["won"][1] - r["forecast"]) * 100)
                hi.append((r["won"][2] - r["forecast"]) * 100)
        _whiskers(ax, xs, est, lo, hi, color, marker, label)
    ax.set_xticks(range(len(buckets)), buckets, color=MUTED, fontsize=8)
    ax.set_xlabel("forecast probability bucket", color=MUTED)
    ax.legend(frameon=False, labelcolor=INK, loc="best")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)


def longshot(results: dict, path: Path) -> None:
    """Return per dollar of buying at the touch, by price paid, before and after fees."""
    fig, ax = _axes("Buy at the touch, hold to settlement: return per $1 by price paid (95% CI)", "return per $1 staked (%)")
    rows = results["longshot"]
    for j, (key, color, marker, label) in enumerate((("gross", "#2a78d6", "o", "Before fees"), ("net", "#eb6834", "s", "After taker fee"))):
        xs = [i + (j - 0.5) * 0.25 for i in range(len(rows))]
        _whiskers(ax, xs, [r[key][0] * 100 for r in rows], [r[key][1] * 100 for r in rows], [r[key][2] * 100 for r in rows], color, marker, label)
    ax.set_xticks(range(len(rows)), [r["bucket"] for r in rows], color=MUTED, fontsize=8)
    ax.set_xlabel("price paid for the contract (YES at the ask or NO at 1 − bid)", color=MUTED)
    ax.legend(frameon=False, labelcolor=INK, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, facecolor=SURFACE)
    plt.close(fig)
