"""processing/convergence_analyzer.py — pairwise correlation convergence/divergence detection.

For a dict of time series (routes / indices / commodities / tickers), compute:

  • The current pairwise correlation matrix on a short and a long window.
  • Per pair, the change in correlation between long-window and short-window.
  • Classify each pair as Converging / Diverging / Decoupling / Stable.

The intuition: a "converging" pair is two series whose correlation has
strengthened (e.g., shipping rates increasingly tracking BDI again);
"diverging" means decoupling magnitude (relationships fading); "decoupling"
means the sign flipped (positive ↔ negative). These changes are often
where alpha lives — pairs decoupling on a fundamental shift, or pairs
re-converging after a temporary spread.

Pure-function module — no streamlit, no globals, no module-level mutable
state. Uses only numpy + pandas; the underlying Pearson calculation
matches what `engine.correlator` uses.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SHORT_WINDOW: int = 30
DEFAULT_LONG_WINDOW: int = 90
DEFAULT_MIN_OBS: int = 20             # min overlapping obs required per pair
DEFAULT_MIN_DELTA: float = 0.20       # |Δr| ≥ this counts as a meaningful shift
DEFAULT_DECOUPLE_LONG_R: float = 0.30 # long-window |r| floor for "decoupling"


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PairConvergence:
    """Convergence / divergence classification for one pair of series."""
    name_a: str
    name_b: str
    short_r: float                # rolling correlation over the SHORT window
    long_r: float                 # rolling correlation over the LONG window
    delta_r: float                # short_r - long_r
    direction: str                # "Converging" | "Diverging" | "Decoupling" | "Stable"
    n_obs_short: int              # observations supporting short_r
    n_obs_long: int               # observations supporting long_r
    interpretation: str           # one-line UI-ready summary


# ─────────────────────────────────────────────────────────────────────────────
# Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_series(s: pd.Series) -> pd.Series:
    """Drop NaNs, sort by index, drop duplicates (keep last)."""
    if s is None or len(s) == 0:
        return pd.Series(dtype=float)
    s = pd.Series(s).dropna().sort_index()
    if s.index.has_duplicates:
        s = s[~s.index.duplicated(keep="last")]
    return s


def _correlation_on_tail(
    a: pd.Series, b: pd.Series, window: int, min_obs: int,
) -> tuple[Optional[float], int]:
    """Pearson r on the trailing ``window`` rows of the inner-joined pair.

    Returns ``(r, n)`` where n is the number of obs actually used. ``r`` is
    None when fewer than ``min_obs`` overlap, or when one side is constant
    (zero std → undefined correlation).
    """
    combined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(combined) < min_obs:
        return None, len(combined)
    tail = combined.iloc[-window:]
    if len(tail) < min_obs:
        return None, len(tail)
    x = tail.iloc[:, 0].to_numpy()
    y = tail.iloc[:, 1].to_numpy()
    # Constant series → np.corrcoef returns NaN, not 0; explicit guard.
    if x.std(ddof=0) < 1e-12 or y.std(ddof=0) < 1e-12:
        return None, len(tail)
    r = float(np.corrcoef(x, y)[0, 1])
    if not math.isfinite(r):
        return None, len(tail)
    return r, len(tail)


def _classify(
    short_r: float, long_r: float,
    min_delta: float, decouple_long_r: float,
) -> str:
    """Bucket a (short_r, long_r) pair into one of four labels.

    Decoupling beats Converging/Diverging when the SIGN changes from a
    meaningful long-window correlation (|long_r| ≥ decouple_long_r) to the
    opposite sign on the short window. Otherwise, the magnitude shift
    versus ``min_delta`` decides Converging vs Diverging.
    """
    # Sign flip from a non-trivial long-window correlation → Decoupling.
    if (
        abs(long_r) >= decouple_long_r
        and (
            (long_r > 0 and short_r < 0)
            or (long_r < 0 and short_r > 0)
        )
    ):
        return "Decoupling"

    delta = abs(short_r) - abs(long_r)
    if delta >= min_delta:
        return "Converging"
    if delta <= -min_delta:
        return "Diverging"
    return "Stable"


def _interpret(name_a: str, name_b: str, classification: str,
               short_r: float, long_r: float) -> str:
    """One-line UI summary."""
    if classification == "Decoupling":
        return (
            f"{name_a} ↔ {name_b}: sign flipped — long-window r={long_r:+.2f}, "
            f"short-window r={short_r:+.2f}. Relationship has inverted."
        )
    if classification == "Converging":
        sign = "positive" if short_r > 0 else "negative"
        return (
            f"{name_a} ↔ {name_b}: {sign} correlation strengthening — "
            f"|r| moved from {abs(long_r):.2f} (long) to {abs(short_r):.2f} (short)."
        )
    if classification == "Diverging":
        return (
            f"{name_a} ↔ {name_b}: correlation weakening — "
            f"|r| dropped from {abs(long_r):.2f} to {abs(short_r):.2f}. "
            f"Pair becoming less linked."
        )
    return (
        f"{name_a} ↔ {name_b}: stable — short-window r={short_r:+.2f}, "
        f"long-window r={long_r:+.2f}, change below {0.20:.2f} threshold."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def compute_correlation_matrix(
    series_dict: dict,
    *,
    window: int = DEFAULT_LONG_WINDOW,
    min_obs: int = DEFAULT_MIN_OBS,
) -> pd.DataFrame:
    """Pairwise Pearson correlation matrix over the trailing ``window`` rows.

    Cells where the pair has fewer than ``min_obs`` overlapping observations
    are NaN. Diagonal is 1.0 by definition. Symmetric.
    """
    if not series_dict:
        return pd.DataFrame()

    names = list(series_dict.keys())
    series_clean = {n: _normalize_series(series_dict[n]) for n in names}

    mat = pd.DataFrame(np.nan, index=names, columns=names, dtype=float)
    for a in names:
        mat.loc[a, a] = 1.0
    for a, b in combinations(names, 2):
        r, _n = _correlation_on_tail(series_clean[a], series_clean[b], window, min_obs)
        if r is not None:
            mat.loc[a, b] = r
            mat.loc[b, a] = r
    return mat


def find_pair_convergence(
    series_dict: dict,
    *,
    short_window: int = DEFAULT_SHORT_WINDOW,
    long_window: int = DEFAULT_LONG_WINDOW,
    min_obs: int = DEFAULT_MIN_OBS,
    min_delta: float = DEFAULT_MIN_DELTA,
    decouple_long_r: float = DEFAULT_DECOUPLE_LONG_R,
) -> list[PairConvergence]:
    """Score every pair in ``series_dict`` on the convergence/divergence axis.

    Returns a list of :class:`PairConvergence`, sorted by ``|delta_r|``
    descending (biggest shifts first). Pairs with insufficient overlap are
    silently skipped — never returned as a None or zero result.
    """
    if not series_dict or len(series_dict) < 2:
        return []
    if short_window >= long_window:
        raise ValueError(
            f"short_window ({short_window}) must be < long_window ({long_window})"
        )

    names = list(series_dict.keys())
    series_clean = {n: _normalize_series(series_dict[n]) for n in names}

    out: list[PairConvergence] = []
    for a, b in combinations(names, 2):
        sa, sb = series_clean[a], series_clean[b]
        short_r, n_short = _correlation_on_tail(sa, sb, short_window, min_obs)
        long_r, n_long = _correlation_on_tail(sa, sb, long_window, min_obs)
        if short_r is None or long_r is None:
            continue
        classification = _classify(short_r, long_r, min_delta, decouple_long_r)
        out.append(PairConvergence(
            name_a=a,
            name_b=b,
            short_r=round(short_r, 4),
            long_r=round(long_r, 4),
            delta_r=round(short_r - long_r, 4),
            direction=classification,
            n_obs_short=n_short,
            n_obs_long=n_long,
            interpretation=_interpret(a, b, classification, short_r, long_r),
        ))

    out.sort(key=lambda p: abs(p.delta_r), reverse=True)
    return out


def find_converging(pairs: list[PairConvergence]) -> list[PairConvergence]:
    """Filter to only Converging pairs."""
    return [p for p in pairs if p.direction == "Converging"]


def find_diverging(pairs: list[PairConvergence]) -> list[PairConvergence]:
    """Filter to only Diverging pairs."""
    return [p for p in pairs if p.direction == "Diverging"]


def find_decoupling(pairs: list[PairConvergence]) -> list[PairConvergence]:
    """Filter to only Decoupling (sign-flipped) pairs."""
    return [p for p in pairs if p.direction == "Decoupling"]


__all__ = [
    "DEFAULT_SHORT_WINDOW",
    "DEFAULT_LONG_WINDOW",
    "DEFAULT_MIN_DELTA",
    "PairConvergence",
    "compute_correlation_matrix",
    "find_pair_convergence",
    "find_converging",
    "find_diverging",
    "find_decoupling",
]
