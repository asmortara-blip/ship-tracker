"""Statistical-significance haircuts for Sharpe ratios (rec R101).

Institutional allocators never accept a raw Sharpe at face value: it ignores
(a) the *length* and higher moments of the track record and (b) **selection
bias** — once you try many strategies/parameterizations, the best raw Sharpe is
inflated purely by luck. This module implements the standard corrections from
Bailey & López de Prado:

- **Probabilistic Sharpe Ratio (PSR)** — P(true SR > benchmark) given the
  observed SR, sample size, skew and kurtosis.
- **Deflated Sharpe Ratio (DSR)** — PSR against a benchmark set to the *expected
  maximum* Sharpe under ``n_trials`` independent attempts, i.e. a selection-bias
  haircut.
- **Minimum Track Record Length (minTRL)** — how many observations you'd need
  for the Sharpe to be significant at a confidence level.

All functions are pure (numpy/scipy), finite, and bounded. Sharpe inputs are in
a single consistent frequency (e.g. per-period, NOT annualized) so the
moment adjustment stays consistent — annualized SR with per-period moments
double-counts the horizon.

References: Bailey, López de Prado, "The Sharpe Ratio Efficient Frontier"
(2012) and "The Deflated Sharpe Ratio" (2014).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from scipy.stats import norm

_EULER_MASCHERONI = 0.5772156649015329


def _sr_standard_error(sr: float, n_obs: int, skew: float, kurt: float) -> Optional[float]:
    """Standard error of the Sharpe estimator (Mertens / Lo).

    σ(SR) = sqrt( (1 - skew*SR + (kurt-1)/4 * SR^2) / (n_obs - 1) ).

    ``kurt`` is the full (Pearson) kurtosis — 3.0 for a normal distribution.
    Returns None when undefined (n_obs < 2 or a non-positive variance term).
    """
    if n_obs is None or n_obs < 2:
        return None
    var_term = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    if var_term <= 0:
        return None
    return math.sqrt(var_term / (n_obs - 1))


def probabilistic_sharpe_ratio(
    sr: float,
    n_obs: int,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_benchmark: float = 0.0,
) -> float:
    """P(true Sharpe > ``sr_benchmark``) given the observed Sharpe and moments.

    Returns a probability in [0, 1]. At ``sr == sr_benchmark`` PSR == 0.5; it
    rises with the observed Sharpe and with ``n_obs`` (more evidence), and falls
    as skew/kurtosis make the estimate noisier.
    """
    se = _sr_standard_error(sr, n_obs, skew, kurt)
    if se is None or se == 0:
        # Undefined SE -> no statistical claim; return the agnostic 0.5.
        return 0.5
    return float(norm.cdf((sr - sr_benchmark) / se))


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """Expected maximum Sharpe across ``n_trials`` independent trials.

    E[max SR] ≈ sr_std * [ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(N·e)) ], with γ the
    Euler-Mascheroni constant. ``sr_std`` is the dispersion of Sharpe estimates
    across the trials. This is the bar a genuinely-skilled strategy must clear.
    """
    n = max(int(n_trials), 1)
    if n == 1 or sr_std <= 0:
        return 0.0
    g = _EULER_MASCHERONI
    z1 = norm.ppf(1.0 - 1.0 / n)
    z2 = norm.ppf(1.0 - 1.0 / (n * math.e))
    return float(sr_std * ((1.0 - g) * z1 + g * z2))


def deflated_sharpe_ratio(
    sr: float,
    n_obs: int,
    n_trials: int,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_std: Optional[float] = None,
    trial_sharpes: Optional[Sequence[float]] = None,
) -> float:
    """Deflated Sharpe: PSR against the selection-bias benchmark.

    Provide either ``trial_sharpes`` (the Sharpe of every strategy/variant
    tried — their std drives the deflation) or ``sr_std`` directly. With
    ``n_trials == 1`` this reduces to PSR against a zero benchmark. More trials
    raise the benchmark, so DSR falls — the multiple-testing haircut.

    Returns a probability in [0, 1]: P(true SR > expected-max-under-N-trials).
    """
    if trial_sharpes is not None and len(trial_sharpes) > 1:
        arr = np.asarray(list(trial_sharpes), dtype=float)
        sr_std = float(arr.std(ddof=1))
        n_trials = max(int(n_trials), len(arr))
    if sr_std is None:
        # Conservative default: assume unit dispersion of trial Sharpes.
        sr_std = 1.0
    benchmark = expected_max_sharpe(n_trials, sr_std)
    return probabilistic_sharpe_ratio(
        sr, n_obs, skew=skew, kurt=kurt, sr_benchmark=benchmark
    )


def min_track_record_length(
    sr: float,
    *,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_benchmark: float = 0.0,
    confidence: float = 0.95,
) -> Optional[float]:
    """Minimum number of observations for SR to beat ``sr_benchmark`` at
    ``confidence``. None when SR <= benchmark (never significant) or undefined.
    """
    if sr <= sr_benchmark:
        return None
    var_term = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * (sr ** 2)
    if var_term <= 0:
        return None
    z = norm.ppf(confidence)
    return float(1.0 + var_term * (z / (sr - sr_benchmark)) ** 2)


@dataclass
class SharpeSignificance:
    """A raw Sharpe and its honesty haircuts."""
    sharpe: float
    n_obs: int
    n_trials: int
    psr: float                       # P(true SR > 0)
    deflated_sr: float               # P(true SR > expected max under n_trials)
    min_track_record_length: Optional[float]
    is_significant: bool             # deflated_sr >= threshold
    verdict: str


def assess_sharpe(
    sr: float,
    n_obs: int,
    *,
    n_trials: int = 1,
    skew: float = 0.0,
    kurt: float = 3.0,
    sr_std: Optional[float] = None,
    trial_sharpes: Optional[Sequence[float]] = None,
    threshold: float = 0.95,
) -> SharpeSignificance:
    """Bundle PSR, deflated SR, and minTRL into one honest verdict.

    ``threshold`` is the deflated-SR probability required to call a Sharpe
    "significant" (0.95 is the conventional bar).
    """
    psr = probabilistic_sharpe_ratio(sr, n_obs, skew=skew, kurt=kurt, sr_benchmark=0.0)
    dsr = deflated_sharpe_ratio(
        sr, n_obs, n_trials, skew=skew, kurt=kurt, sr_std=sr_std, trial_sharpes=trial_sharpes
    )
    mtrl = min_track_record_length(sr, skew=skew, kurt=kurt, confidence=threshold)
    significant = dsr >= threshold
    if significant:
        verdict = f"Significant after a {n_trials}-trial haircut (DSR {dsr:.0%})."
    elif psr >= threshold:
        verdict = (
            f"Raw track record looks significant (PSR {psr:.0%}) but does NOT "
            f"survive the {n_trials}-trial selection-bias haircut (DSR {dsr:.0%})."
        )
    else:
        verdict = (
            f"Not statistically significant (PSR {psr:.0%}, DSR {dsr:.0%}); "
            "treat the Sharpe as noise."
        )
    return SharpeSignificance(
        sharpe=float(sr),
        n_obs=int(n_obs),
        n_trials=int(n_trials),
        psr=psr,
        deflated_sr=dsr,
        min_track_record_length=mtrl,
        is_significant=significant,
        verdict=verdict,
    )
