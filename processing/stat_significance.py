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


# ─────────────────────────────────────────────────────────────────────────────
#  Multiple-testing correction over a SELECTION surface (rec R114)
#
#  The deflated Sharpe (above) haircuts a SINGLE chosen strategy for the number
#  of trials it was selected from. The complementary control is family-wise:
#  when you run a whole panel of tests (e.g. 9 cointegration pairs, or N momentum
#  classes) and report "the best", you must control the false-discovery rate
#  across the family or you will publish noise. These add Benjamini-Hochberg FDR
#  and Holm-Bonferroni FWER over a family of p-values.
#
#  IMPORTANT — family homogeneity: BH/Holm assume a comparable null across the
#  family. Correct WITHIN one homogeneous family (all EG p-values together, or
#  all Sharpe-derived p-values together) — do NOT pool an EG p-value with a
#  Sharpe-derived one. ``Trial.family`` labels the family; callers correct per
#  family. For a Sharpe-based test the one-sided p-value is ``1 - PSR`` (PSR is
#  P(true SR > 0)); use ``sharpe_pvalue``.
# ─────────────────────────────────────────────────────────────────────────────


def _clean_pvalues(pvalues: Sequence[float]) -> list[float]:
    """Coerce to finite floats clamped to [0, 1]; non-numeric / NaN → 1.0 (a
    p-value of 1.0 never survives, so a malformed trial fails closed)."""
    out: list[float] = []
    for p in pvalues:
        try:
            v = float(p)
        except (TypeError, ValueError):
            v = 1.0
        if not math.isfinite(v):
            v = 1.0
        out.append(min(1.0, max(0.0, v)))
    return out


def sharpe_pvalue(psr: float) -> float:
    """One-sided p-value for a Sharpe test from its PSR = P(true SR > 0).

    H0: true SR <= 0, so p = P(observe this or better under H0) = 1 - PSR.
    Clamped to [0, 1]. A degenerate PSR (NaN) → p = 1.0 (fails closed).
    """
    try:
        v = 1.0 - float(psr)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(v):
        return 1.0
    return min(1.0, max(0.0, v))


def benjamini_hochberg(pvalues: Sequence[float], *, alpha: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg FDR survivor mask, aligned to the INPUT order.

    Step-up: sort p ascending; find the largest rank k (1-based) with
    ``p_(k) <= (k/m)·alpha``; reject (survive=True) every test up to that rank.
    Controls the expected false-discovery rate at ``alpha`` under independence
    or positive-regression-dependence. Empty input → ``[]``.
    """
    p = _clean_pvalues(pvalues)
    m = len(p)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p[i])  # ascending p
    max_k = 0
    for rank, idx in enumerate(order, start=1):
        if p[idx] <= (rank / m) * alpha:
            max_k = rank
    survive = [False] * m
    for rank, idx in enumerate(order, start=1):
        if rank <= max_k:
            survive[idx] = True
    return survive


def bh_qvalues(pvalues: Sequence[float]) -> list[float]:
    """Benjamini-Hochberg adjusted p-values (q-values), aligned to input order.

    ``q_(i) = min_{k>=i} (m/k)·p_(k)``, enforced monotone non-decreasing in p and
    clamped to [0, 1]. Each q-value is >= its raw p-value. Empty input → ``[]``.
    Useful for display: a pair "survives FDR at alpha" iff its q-value <= alpha.
    """
    p = _clean_pvalues(pvalues)
    m = len(p)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p[i])  # ascending p
    q = [0.0] * m
    prev = 1.0
    for rank in range(m, 0, -1):          # largest p → smallest, cumulative min
        idx = order[rank - 1]
        val = min(prev, (m / rank) * p[idx])
        val = min(1.0, max(0.0, val))
        q[idx] = val
        prev = val
    return q


def holm_bonferroni(pvalues: Sequence[float], *, alpha: float = 0.05) -> list[bool]:
    """Holm-Bonferroni family-wise survivor mask, aligned to the INPUT order.

    Step-down: sort p ascending; reject ``p_(i) <= alpha/(m - i)`` (0-based i)
    and STOP at the first failure (all subsequent are non-rejected). Controls the
    family-wise error rate at ``alpha`` — strictly more conservative than BH, so
    the Holm survivor set is always a subset of the BH set. Empty input → ``[]``.
    """
    p = _clean_pvalues(pvalues)
    m = len(p)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: p[i])  # ascending p
    survive = [False] * m
    for i, idx in enumerate(order):
        if p[idx] <= alpha / (m - i):
            survive[idx] = True
        else:
            break  # step-down halts at the first non-rejection
    return survive


@dataclass(frozen=True)
class Trial:
    """One test in a multiple-comparisons family."""

    name: str
    pvalue: float
    sharpe: Optional[float] = None
    n_obs: int = 0
    family: str = "default"


@dataclass(frozen=True)
class CorrectedVerdict:
    """A trial's verdict after family-wise / FDR correction."""

    name: str
    pvalue: float
    bh_qvalue: float
    survives_fdr: bool
    survives_holm: bool
    rank: int          # 1-based rank by ascending p within the family
    n_trials: int


def correct_family(trials: Sequence[Trial], *, alpha: float = 0.05) -> list[CorrectedVerdict]:
    """Apply BH-FDR + Holm-FWER over a homogeneous family of trials.

    Returns one :class:`CorrectedVerdict` per trial, sorted by ascending p-value
    (rank 1 = smallest p). The reported "alpha set" is the FDR-surviving subset —
    not best-of-N. Empty family → ``[]``.
    """
    trials = list(trials)
    m = len(trials)
    if m == 0:
        return []
    pvals = [t.pvalue for t in trials]
    cleaned = _clean_pvalues(pvals)
    fdr = benjamini_hochberg(pvals, alpha=alpha)
    holm = holm_bonferroni(pvals, alpha=alpha)
    q = bh_qvalues(pvals)
    ranks = {
        idx: r
        for r, idx in enumerate(sorted(range(m), key=lambda i: cleaned[i]), start=1)
    }
    out = [
        CorrectedVerdict(
            name=t.name,
            pvalue=cleaned[i],
            bh_qvalue=q[i],
            survives_fdr=fdr[i],
            survives_holm=holm[i],
            rank=ranks[i],
            n_trials=m,
        )
        for i, t in enumerate(trials)
    ]
    out.sort(key=lambda v: v.rank)
    return out


class TrialsLedger:
    """Accumulate (name, p-value) trials within ONE comparable family, then apply
    BH-FDR / Holm-FWER. Pure + in-memory: every selection surface in Ship is
    deterministically reconstructible per render (the cointegration panel, the
    factor-model carriers, …), so no persistence is needed — the trial set has no
    cross-session identity to durably record (R114).
    """

    def __init__(self, family: str = "default") -> None:
        self.family = family
        self._trials: list[Trial] = []

    def add(self, name: str, pvalue: float, *, sharpe: Optional[float] = None,
            n_obs: int = 0) -> "TrialsLedger":
        self._trials.append(Trial(
            name=str(name), pvalue=float(pvalue),
            sharpe=sharpe, n_obs=int(n_obs), family=self.family))
        return self

    def add_sharpe(self, name: str, psr: float, *, n_obs: int = 0,
                   sharpe: Optional[float] = None) -> "TrialsLedger":
        """Add a Sharpe-based trial from its PSR (p = 1 - PSR)."""
        return self.add(name, sharpe_pvalue(psr), sharpe=sharpe, n_obs=n_obs)

    @property
    def n_trials(self) -> int:
        """The family size — the single source of truth for the DSR n_trials and
        the FDR family size, so the two corrections agree on how many were run."""
        return len(self._trials)

    def corrected(self, *, alpha: float = 0.05) -> list[CorrectedVerdict]:
        return correct_family(self._trials, alpha=alpha)
