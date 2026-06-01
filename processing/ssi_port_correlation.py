"""ssi_port_correlation.py — does SSI lead port supply deficits?

The platform exposes two related-but-independent stress signals:

  * **SSI** (`processing.shipping_stress_index`) — a fleet-wide 0-1 composite
    blended from chokepoint, congestion, weather, rate, vulnerability and
    anomaly components. Reflects *system-level* stress today.
  * **Port supply deficit** (`processing.port_supply_lines`) — per-port
    ``supply_deficit_days`` derived from regional equipment imbalance,
    throughput and cargo mix. Reflects *port-level* shortage state today.

The interesting empirical question the platform has never measured: do
*today's* spikes in SSI predict *future* spikes in port-supply deficit?
If so, SSI is a true leading indicator and is worth surfacing in the
operator UI as an early warning. If not, the two are independent signals
and any UI copy that implies SSI "leads" port shortages overclaims the
relationship.

This module supplies the analytical primitives to answer that. It is a
**read-only** consumer of SSI / port-supply outputs — it never imports
or modifies either source module. It operates on injected scalar
*histories* (one float per day) so the caller controls how the SSI and
deficit series are constructed.

Two public entry points:

  * :func:`compute_lag_correlation` — Pearson + Spearman r at a single
    lag.
  * :func:`analyze_leading_indicator_relationship` — sweep lag = 0..N,
    pick the best, narrate the result.

Plus :func:`generate_synthetic_paired_series` for deterministic test +
backtest fixtures with a known true lag.

Pure-function module: no Streamlit, no I/O. All public helpers tolerate
empty / short inputs and return defensible empty reports rather than
raising.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np

from data.quality import DataSource


__all__ = [
    "CrossCorrelationResult",
    "LeadingIndicatorReport",
    "compute_lag_correlation",
    "analyze_leading_indicator_relationship",
    "generate_synthetic_paired_series",
    "SSI_PORT_CORRELATION_SOURCE",
]


# scipy.stats is already used elsewhere in processing/* (see
# processing/freight_volatility.py, processing/risk_lab.py); we rely on
# it for both Pearson and Spearman with proper two-sided p-values.
try:
    from scipy.stats import pearsonr as _pearsonr
    from scipy.stats import spearmanr as _spearmanr
    _SCIPY_AVAILABLE = True
except ImportError:  # pragma: no cover - scipy is required in this repo
    _SCIPY_AVAILABLE = False


# ---------------------------------------------------------------------------
# Provenance marker — same shape as every other validator's source tag.
# ---------------------------------------------------------------------------

SSI_PORT_CORRELATION_SOURCE = DataSource.modeled(
    "SSI / Port-Deficit Cross-Correlation",
    notes=(
        "Lag-correlation analyzer that measures whether SSI history "
        "leads or coincides with per-port supply-deficit history. "
        "Pure function over injected scalar series — does not call "
        "shipping_stress_index or port_supply_lines."
    ),
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class CrossCorrelationResult:
    """One (SSI, deficit) lag's correlation coefficients.

    Conventions:

      * ``lag_days >= 0`` — non-negative lag of the *deficit* series
        relative to the SSI series. ``lag=3`` means "compare today's SSI
        to deficit 3 days later". A high positive Pearson r at lag=3
        therefore means SSI today predicts deficit 3 days from now.
      * ``n_pairs`` — number of overlapping (SSI[t], deficit[t+lag])
        pairs that fed the correlation. Always ``<= len(ssi) - lag``.
      * ``p_value`` — two-sided p-value from scipy.stats.pearsonr.
        ``1.0`` when the result is undefined (insufficient pairs or
        zero-variance inputs).
    """

    lag_days: int
    pearson_r: float
    spearman_r: float
    n_pairs: int
    p_value: float


@dataclass
class LeadingIndicatorReport:
    """Sweep of lags + the best one + a human-readable interpretation."""

    best_lag_days: int
    best_lag_r: float
    lag_correlations: list[CrossCorrelationResult] = field(default_factory=list)
    interpretation: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _empty_correlation(lag: int) -> CrossCorrelationResult:
    """Neutral CrossCorrelationResult when the inputs are too short or
    have zero variance — r=0, p=1, n_pairs=0."""
    return CrossCorrelationResult(
        lag_days=int(lag),
        pearson_r=0.0,
        spearman_r=0.0,
        n_pairs=0,
        p_value=1.0,
    )


def _safe_float(x: float) -> float:
    """Coerce NaN / inf to 0.0 — keeps downstream JSON serialisation
    safe and avoids surprising the operator with NaN in headlines."""
    if x is None:
        return 0.0
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(v):
        return 0.0
    return v


# ---------------------------------------------------------------------------
# Public API: single-lag correlation
# ---------------------------------------------------------------------------


def compute_lag_correlation(
    ssi_series: Sequence[float],
    deficit_series: Sequence[float],
    lag_days: int,
) -> CrossCorrelationResult:
    """Compute Pearson + Spearman r between SSI[t] and deficit[t+lag].

    Parameters
    ----------
    ssi_series:
        The "leading" series — typically a daily overall SSI history.
    deficit_series:
        The "lagging" series — typically a daily port supply-deficit
        history. Must be the same length as ``ssi_series`` for lag=0;
        a lag of N effectively drops the last N elements of SSI and the
        first N of deficit so the pairs align.
    lag_days:
        Non-negative shift of the deficit series. ``0`` → contemporaneous.
        ``3`` → compare ``ssi[t]`` to ``deficit[t+3]``. Negative lags
        are clamped to 0 (the analyser is asymmetric: SSI is the
        candidate leader, never the lagger).

    Returns
    -------
    CrossCorrelationResult
        Defensible neutral result (r=0, p=1, n_pairs=0) on:
          * empty inputs
          * mismatched lengths
          * fewer than 3 overlapping pairs after lag alignment
          * zero variance in either aligned series
    """
    lag = max(0, int(lag_days))
    ssi = np.asarray(list(ssi_series), dtype=float)
    deficit = np.asarray(list(deficit_series), dtype=float)

    if ssi.size == 0 or deficit.size == 0:
        return _empty_correlation(lag)
    if ssi.size != deficit.size:
        return _empty_correlation(lag)
    if lag >= ssi.size:
        return _empty_correlation(lag)

    # Align: take SSI[0 : N-lag] and deficit[lag : N]. After this both
    # arrays have length N-lag.
    if lag == 0:
        ssi_aligned = ssi
        def_aligned = deficit
    else:
        ssi_aligned = ssi[:-lag]
        def_aligned = deficit[lag:]

    n = int(ssi_aligned.size)
    if n < 3:
        return _empty_correlation(lag)

    # scipy.stats.pearsonr raises a constant-input warning + returns NaN
    # when either input has zero variance. Pre-check and short-circuit.
    if float(ssi_aligned.std()) == 0.0 or float(def_aligned.std()) == 0.0:
        return _empty_correlation(lag)

    if not _SCIPY_AVAILABLE:  # pragma: no cover - scipy is required here
        return _empty_correlation(lag)

    try:
        pr, p = _pearsonr(ssi_aligned, def_aligned)
        sr_result = _spearmanr(ssi_aligned, def_aligned)
        # scipy 1.10+ returns SignificanceResult with .statistic; older
        # versions return a 2-tuple. Handle both.
        if hasattr(sr_result, "statistic"):
            sr = sr_result.statistic
        else:
            sr = sr_result[0]
    except Exception:
        return _empty_correlation(lag)

    return CrossCorrelationResult(
        lag_days=lag,
        pearson_r=_safe_float(pr),
        spearman_r=_safe_float(sr),
        n_pairs=n,
        p_value=_safe_float(p) if p is not None else 1.0,
    )


# ---------------------------------------------------------------------------
# Public API: sweep + interpret
# ---------------------------------------------------------------------------


def _interpret_best_lag(
    best: CrossCorrelationResult,
    max_lag_days: int,
    sweep: list[CrossCorrelationResult],
) -> str:
    """One-sentence operator-facing description of what the sweep found."""
    if not sweep or best.n_pairs < 3:
        return "Insufficient data to assess the SSI → port-deficit lag relationship."

    r = best.pearson_r
    lag = best.best_lag_days if hasattr(best, "best_lag_days") else best.lag_days

    if abs(r) < 0.20:
        return (
            f"SSI shows no meaningful lag relationship with port deficits "
            f"across 0–{max_lag_days} day horizons (best |r|={abs(r):.2f} "
            f"at lag {lag}d). Signals appear independent."
        )

    direction = "positively" if r > 0 else "inversely"
    if lag == 0:
        when = "contemporaneously"
        cue = "coincident with"
    else:
        when = f"with a {lag}-day lead"
        cue = f"leading by ~{lag} days"

    strength = "strongly" if abs(r) >= 0.60 else "moderately"
    return (
        f"SSI is {strength} {direction} correlated with port deficits "
        f"{when} (Pearson r={r:.2f}, n={best.n_pairs}). "
        f"Treat SSI as {cue} port supply problems on this sample."
    )


def analyze_leading_indicator_relationship(
    ssi_history: Sequence[float],
    deficit_history: Sequence[float],
    max_lag_days: int = 14,
) -> LeadingIndicatorReport:
    """Sweep lag = 0..max_lag_days, pick the lag with strongest |Pearson r|.

    Parameters
    ----------
    ssi_history:
        Daily SSI history (newest last). Length N.
    deficit_history:
        Daily port supply-deficit history (newest last). Must be length N.
    max_lag_days:
        Inclusive upper bound on the lag to test. Internally clamped to
        ``len(ssi_history) - 3`` so at least 3 overlapping pairs remain
        at the deepest lag. Negative values become 0.

    Returns
    -------
    LeadingIndicatorReport
        On empty / mismatched / pathological inputs returns a defensible
        empty report with ``best_lag_days=0``, ``best_lag_r=0.0``, an
        empty ``lag_correlations`` list, and an "insufficient data"
        interpretation string.
    """
    ssi = list(ssi_history)
    deficit = list(deficit_history)

    if not ssi or not deficit or len(ssi) != len(deficit) or len(ssi) < 4:
        return LeadingIndicatorReport(
            best_lag_days=0,
            best_lag_r=0.0,
            lag_correlations=[],
            interpretation=(
                "Insufficient data to assess the SSI → port-deficit "
                "lag relationship."
            ),
        )

    # Clamp max_lag so the deepest lag still produces >= 3 overlapping pairs.
    requested = max(0, int(max_lag_days))
    effective_max = min(requested, len(ssi) - 3)
    if effective_max < 0:
        effective_max = 0

    sweep: list[CrossCorrelationResult] = []
    for lag in range(0, effective_max + 1):
        sweep.append(compute_lag_correlation(ssi, deficit, lag))

    # Pick best by |pearson_r|. Empty sweep → defensible empty report.
    valid = [r for r in sweep if r.n_pairs >= 3]
    if not valid:
        return LeadingIndicatorReport(
            best_lag_days=0,
            best_lag_r=0.0,
            lag_correlations=sweep,
            interpretation=(
                "Insufficient data to assess the SSI → port-deficit "
                "lag relationship."
            ),
        )

    best = max(valid, key=lambda r: abs(r.pearson_r))
    report = LeadingIndicatorReport(
        best_lag_days=int(best.lag_days),
        best_lag_r=float(best.pearson_r),
        lag_correlations=sweep,
        interpretation="",
    )
    report.interpretation = _interpret_best_lag(best, effective_max, sweep)
    return report


# ---------------------------------------------------------------------------
# Synthetic generator — deterministic paired series with a known lag
# ---------------------------------------------------------------------------


def generate_synthetic_paired_series(
    n_days: int,
    ssi_lead_days: int,
    noise: float = 0.0,
    seed: int = 20260526,
) -> tuple[list[float], list[float]]:
    """Generate (ssi, deficit) where deficit lags SSI by ``ssi_lead_days``.

    Used by both the property tests and the backtest validator. The SSI
    series is a deterministic smooth-ish walk in [0, 1]; the deficit
    series is that same walk shifted by ``ssi_lead_days`` days and
    rescaled into a plausible deficit-days range, optionally perturbed
    by Gaussian noise.

    Parameters
    ----------
    n_days:
        Output length for both series. Clamped to >= 4 so downstream
        analysis has enough overlap to compute a correlation.
    ssi_lead_days:
        How many days ahead the SSI series leads the deficit series.
        ``0`` → contemporaneous. Negative values become 0.
    noise:
        Std-dev of additive Gaussian noise applied to the deficit
        series. ``0.0`` → perfect lagged relationship; higher values
        progressively decorrelate.
    seed:
        Seed for ``numpy.random.default_rng``. Same seed → byte-identical
        outputs.

    Returns
    -------
    tuple[list[float], list[float]]
        (ssi_series, deficit_series), both length ``n_days``.
    """
    n = max(4, int(n_days))
    lead = max(0, int(ssi_lead_days))
    noise = max(0.0, float(noise))

    rng = np.random.default_rng(int(seed))

    # Underlying signal: a slow random walk in [0, 1] built from a small-
    # step Gaussian increment + logistic squashing. This gives a series
    # with realistic autocorrelation (so lag inference is informative
    # rather than trivial).
    steps = rng.normal(0.0, 0.08, size=n + lead + 4)
    raw = np.cumsum(steps)
    raw = raw - raw.min()
    if raw.max() > 0:
        raw = raw / raw.max()
    # Soft sinusoidal overlay so the series has visible peaks/troughs.
    t = np.arange(raw.size)
    overlay = 0.15 * (np.sin(2 * np.pi * t / 30.0) + 1.0) * 0.5
    base = np.clip(raw * 0.7 + overlay, 0.0, 1.0)

    # SSI series = the first n elements of the base signal.
    ssi = base[:n].tolist()

    # Deficit lags SSI by `lead` days. We construct it by taking the
    # same base signal shifted left by `lead` (so deficit[t] reflects
    # ssi[t - lead] aka SSI's value `lead` days earlier).
    # Implementation: deficit_full[i] = base[i - lead] for i >= lead;
    # for the first `lead` entries we pad with the initial value so
    # the series remains length n. We then rescale into a plausible
    # supply-deficit-days range (-8 .. +8 inclusive).
    deficit_signal = np.empty(n)
    for i in range(n):
        src = i - lead
        if src < 0:
            src = 0
        deficit_signal[i] = base[src]
    deficit_signal = (deficit_signal - 0.5) * 16.0   # ~ -8 .. +8 days

    if noise > 0:
        # Noise scale is 16.0 = full signal range, so noise=1.0 is one
        # full signal-range std-dev of additive Gaussian — enough to
        # bury the lagged relationship and let the backtest's
        # complementary "high-noise → low-recovery" property hold.
        deficit_signal = deficit_signal + rng.normal(0.0, noise * 16.0, size=n)

    return ssi, deficit_signal.tolist()
