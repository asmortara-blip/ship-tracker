"""signal_validation.py — a transparent validator for the platform's *real* signals.

The platform already ships a backtester (``processing.backtester`` /
``processing.backtest_engine``) but it only ever tests ~8 **hard-coded heuristic**
signals — BDI momentum, z-score reversion, seasonal calendar triggers and the
like. The signals the platform actually surfaces to a user — the disruption
cascade's ranked :class:`~processing.disruption_cascade.EquityIdea` list and the
:class:`~processing.commodity_shipping.CommodityShippingSignal` list — were never
validated. This module closes that gap.

What this does — and what it honestly does not
----------------------------------------------
A *fully faithful* historical re-run would reconstruct the Shipping Stress Index
and the exposure matrix at every past date and re-score the whole cascade
day-by-day. That is infeasible from a single price snapshot (the SSI needs
contemporaneous port / route / macro state that is not stored historically).

So this validator takes the **honest, transparent** path the brief asks for: it
takes each *current* signal object — already produced by the real pipeline — and
measures its **directional claim** against forward returns over the price
history the platform does have. For a Bullish call on ZIM we ask, at every
historical day with enough forward data: *did the price actually rise over the
next N days?* A "hit" is a forward move that agrees with the signal's direction.

The result is a plain **hit-rate scorecard**:

* per-signal hit / miss counts and average forward return,
* an aggregate hit rate broken down by the cascade's own conviction tiers
  (High / Moderate / Low / Watch),
* every tier compared against an **equal-weight synthetic baseline** — the hit
  rate of a naive "always long, every ticker, every day" rule over the same
  windows. A tier only earns its keep if it beats that baseline.

Everything here is arithmetic on observed forward returns. There is **no fitted
model**, no black box, nothing learned — every number traces to a visible count
of up-days vs down-days. And because the platform runs on synthetic / modeled
price history, every result is explicitly stamped ``DataKind.MODELED`` /
``DataKind.DEMO`` via :mod:`data.quality`. This is a signal-quality scorecard on
synthetic data — never investment advice, never a price target.

Pure processing module — **no Streamlit imports, no ``st.`` calls.** Every public
function tolerates empty / missing / degenerate inputs and returns neutral
defaults rather than raising, matching the rest of the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from loguru import logger

from data.quality import DataKind, DataQuality, DataSource

# Read-only imports of the real-signal types. Importing the *types* keeps this
# module self-documenting; the validator also duck-types so it never hard-fails
# if a caller passes look-alike objects.
from processing.commodity_shipping import CommodityShippingSignal  # noqa: F401
from processing.disruption_cascade import EquityIdea  # noqa: F401


# ---------------------------------------------------------------------------
# Tunables — all explicit, documented, calibrated (never learned)
# ---------------------------------------------------------------------------

# Forward horizon, in trading rows, over which a directional claim is judged.
# 21 rows ≈ one trading month — the same 1-month horizon the existing
# backtest_engine treats as its primary hold period.
_DEFAULT_FORWARD_DAYS: int = 21

# A directional signal "hits" only when the forward move clears this dead-band
# (fractional). Inside the band the move is too small to call either way and is
# scored as a miss for a directional signal. Matches the 3% dead-band used by
# commodity_shipping / exposure_matrix so the whole platform agrees on "flat".
_FLAT_BAND: float = 0.005

# A signal needs at least this many forward-return observations before its
# hit-rate is treated as meaningful rather than noise. Below it the signal is
# still listed but flagged ``low-sample``.
_MIN_OBSERVATIONS: int = 8

# Spacing (in rows) between successive historical entry points sampled for one
# signal. Sampling weekly rather than daily keeps overlapping forward windows
# from massively over-counting a single price swing.
_SAMPLE_STRIDE: int = 5

# The cascade's four conviction tiers, ordered strongest-first. Used to give the
# tier breakdown a stable, deterministic row order.
_CONVICTION_TIERS: tuple[str, ...] = ("High", "Moderate", "Low", "Watch")

# Conviction-tier labels are produced by disruption_cascade; commodity signals
# have no tier, so they are bucketed under this synthetic label and reported
# separately from the cascade tiers.
_COMMODITY_TIER: str = "Commodity"


# ---------------------------------------------------------------------------
# Dataclasses — the scorecard shape
# ---------------------------------------------------------------------------


@dataclass
class SignalValidation:
    """The validated track record of one real platform signal.

    A signal here is one :class:`EquityIdea` or one
    :class:`CommodityShippingSignal`. Every field is a plain count or an average
    of observed forward returns — nothing is modeled or fitted.
    """

    signal_id: str               # e.g. "ZIM" or "USO" — the ticker carried
    signal_kind: str             # "cascade idea" | "commodity signal"
    direction: str               # "Bullish" | "Bearish" | "Neutral"
    conviction_label: str        # "High"|"Moderate"|"Low"|"Watch"|"Commodity"
    conviction_score: float      # [0, 1] — cascade score, or signal_strength
    forward_days: int            # horizon each claim was judged over
    n_observations: int          # forward-return windows sampled
    n_hits: int                  # windows whose move agreed with the direction
    hit_rate: float              # n_hits / n_observations, in [0, 1]
    avg_forward_return: float    # mean signed forward return over the windows
    directional_return: float    # mean return *in the signal's favour* (sign-
    #                              flipped for Bearish) — a Bearish call that
    #                              correctly predicts a fall scores positive
    baseline_hit_rate: float     # equal-weight always-long baseline, same windows
    edge_vs_baseline: float      # hit_rate - baseline_hit_rate
    low_sample: bool             # True when n_observations < _MIN_OBSERVATIONS
    note: str = ""               # plain-language status / caveat


@dataclass
class TierScore:
    """Aggregate hit-rate statistics for one conviction tier."""

    tier: str                    # "High" | "Moderate" | "Low" | "Watch" | …
    n_signals: int               # how many signals fell in this tier
    n_observations: int          # total forward windows across the tier
    hit_rate: float              # tier-wide hit rate, in [0, 1]
    avg_forward_return: float    # tier-wide mean signed forward return
    directional_return: float    # tier-wide mean in-favour return
    baseline_hit_rate: float     # equal-weight baseline over the same windows
    edge_vs_baseline: float      # hit_rate - baseline_hit_rate


@dataclass
class ValidationReport:
    """The full transparent scorecard for a run of the real-signal validator."""

    signals: list[SignalValidation] = field(default_factory=list)
    tiers: list[TierScore] = field(default_factory=list)
    overall_hit_rate: float = 0.0
    overall_baseline_hit_rate: float = 0.0
    overall_edge: float = 0.0
    overall_directional_return: float = 0.0
    n_signals_validated: int = 0
    n_signals_skipped: int = 0      # signals with no usable price history
    forward_days: int = _DEFAULT_FORWARD_DAYS
    price_history_days: int = 0     # rows of synthetic history actually used
    source: DataSource | None = None
    generated_at: str = ""
    summary: str = ""               # one-line plain-language headline


# ---------------------------------------------------------------------------
# Internal numeric helpers
# ---------------------------------------------------------------------------


def _clamp01(value: float) -> float:
    """Clamp *value* into ``[0.0, 1.0]`` — hit rates can never escape that box."""
    if value != value:  # NaN guard
        return 0.0
    return max(0.0, min(1.0, float(value)))


def _safe_close_series(df) -> pd.Series | None:
    """Extract a clean, chronologically-ordered ``close`` price Series.

    Returns ``None`` for anything unusable — ``None``/empty frame, no ``close``
    column, or fewer than two finite prices. Never raises.
    """
    if df is None:
        return None
    try:
        if getattr(df, "empty", True) or "close" not in getattr(df, "columns", []):
            return None
        work = df.copy()
        if "date" in work.columns:
            # Order by date so "forward" genuinely means forward in time.
            work = work.sort_values("date")
        close = pd.to_numeric(work["close"], errors="coerce").dropna()
        if len(close) < 2:
            return None
        return close.reset_index(drop=True)
    except Exception:  # pragma: no cover - defensive
        return None


def _forward_returns(close: pd.Series, forward_days: int, stride: int) -> list[float]:
    """Sample fractional forward returns from a price Series.

    At every ``stride``-th row that still has ``forward_days`` of history ahead,
    record ``(price[t+forward_days] - price[t]) / price[t]``. Sampling on a
    stride (rather than every row) stops heavily-overlapping windows from
    over-counting a single price swing.
    """
    n = len(close)
    if n <= forward_days or forward_days < 1 or stride < 1:
        return []
    out: list[float] = []
    for t in range(0, n - forward_days, stride):
        p0 = float(close.iloc[t])
        p1 = float(close.iloc[t + forward_days])
        if p0 == 0.0:
            continue
        out.append((p1 - p0) / p0)
    return out


def _direction_sign(direction: str) -> int:
    """Map a direction word to ``+1`` (Bullish), ``-1`` (Bearish), ``0`` (Neutral).

    Case-insensitive so it accepts both the cascade's title-case ("Bullish")
    and commodity_shipping's lower-case ("bullish") conventions.
    """
    d = str(direction).strip().lower()
    if d == "bullish":
        return +1
    if d == "bearish":
        return -1
    return 0


def _hit_stats(
    forward_returns: list[float],
    direction: str,
) -> tuple[int, int, float, float]:
    """Score a list of forward returns against a directional claim.

    Returns ``(n_observations, n_hits, avg_signed_return, directional_return)``:

    * a **Bullish** claim hits when a forward return clears ``+_FLAT_BAND``;
    * a **Bearish** claim hits when it clears ``-_FLAT_BAND`` to the downside;
    * a **Neutral** claim hits when the move stays *inside* ``±_FLAT_BAND`` —
      Neutral genuinely predicts "no decisive move", so a flat tape is correct.

    ``directional_return`` is the mean return expressed *in the signal's
    favour*: it is the raw mean for Bullish, the sign-flipped mean for Bearish
    (so a correctly-predicted fall reads positive), and the mean *absolute*
    distance from flat for Neutral.
    """
    if not forward_returns:
        return 0, 0, 0.0, 0.0

    arr = np.asarray(forward_returns, dtype=float)
    n = int(arr.size)
    sign = _direction_sign(direction)

    if sign > 0:                       # Bullish
        hits = int(np.sum(arr > _FLAT_BAND))
        directional = float(arr.mean())
    elif sign < 0:                     # Bearish
        hits = int(np.sum(arr < -_FLAT_BAND))
        directional = float(-arr.mean())
    else:                              # Neutral — correct when the tape is flat
        hits = int(np.sum(np.abs(arr) <= _FLAT_BAND))
        directional = float(-np.abs(arr).mean())

    avg_signed = float(arr.mean())
    return n, hits, avg_signed, directional


# ---------------------------------------------------------------------------
# Equal-weight synthetic baseline
# ---------------------------------------------------------------------------


def _baseline_pool(stock_data: dict, forward_days: int, stride: int) -> list[float]:
    """Pool every forward return across every ticker with usable history.

    This pooled list *is* the equal-weight synthetic baseline: it represents a
    naive "hold every name, all the time" rule. A real signal only adds value if
    its hit rate beats the hit rate of this pool.
    """
    pool: list[float] = []
    for df in (stock_data or {}).values():
        close = _safe_close_series(df)
        if close is None:
            continue
        pool.extend(_forward_returns(close, forward_days, stride))
    return pool


def _baseline_hit_rate(pool: list[float], direction: str) -> float:
    """Hit rate of the equal-weight baseline judged for one direction.

    The baseline is *always long*, so for a Bullish signal the comparison is the
    fraction of pooled windows that rose. For a Bearish signal the fair naive
    comparator is the fraction that fell (a coin-flip-symmetric baseline); for
    Neutral it is the fraction that stayed flat. This keeps ``edge_vs_baseline``
    an apples-to-apples "did the signal beat doing nothing" measure.
    """
    if not pool:
        return 0.0
    _n, hits, _avg, _dir = _hit_stats(pool, direction)
    return _clamp01(hits / len(pool)) if pool else 0.0


# ---------------------------------------------------------------------------
# Per-signal validation
# ---------------------------------------------------------------------------


def _validate_one(
    *,
    signal_id: str,
    signal_kind: str,
    direction: str,
    conviction_label: str,
    conviction_score: float,
    close: pd.Series | None,
    baseline_pool: list[float],
    forward_days: int,
    stride: int,
) -> SignalValidation | None:
    """Validate a single directional claim against its ticker's forward returns.

    Returns ``None`` when the ticker has no usable synthetic price history — the
    caller counts that as a skip. Otherwise returns a fully-populated
    :class:`SignalValidation`. Never raises.
    """
    if close is None:
        return None

    fwd = _forward_returns(close, forward_days, stride)
    n_obs, n_hits, avg_ret, directional = _hit_stats(fwd, direction)
    if n_obs == 0:
        return None

    hit_rate = _clamp01(n_hits / n_obs)
    base_hr = _baseline_hit_rate(baseline_pool, direction)
    low_sample = n_obs < _MIN_OBSERVATIONS

    if low_sample:
        note = (
            f"Low sample ({n_obs} forward window(s)) — hit rate is indicative "
            f"only; needs ≥ {_MIN_OBSERVATIONS} to be read with confidence."
        )
    elif _direction_sign(direction) == 0:
        note = (
            "Neutral call — scored as correct whenever the synthetic tape "
            "stayed inside the flat band over the forward window."
        )
    else:
        edge = hit_rate - base_hr
        if edge > 0.05:
            note = (
                f"Beats the equal-weight synthetic baseline by "
                f"{edge * 100:.0f} pts over a {forward_days}-day horizon."
            )
        elif edge < -0.05:
            note = (
                f"Trails the equal-weight synthetic baseline by "
                f"{abs(edge) * 100:.0f} pts — direction not confirmed on "
                f"synthetic history."
            )
        else:
            note = (
                "Roughly in line with the equal-weight synthetic baseline — "
                "no clear edge on synthetic history."
            )

    # Round once, then derive the edge from the *rounded* fields so the
    # scorecard is internally consistent (edge == hit_rate - baseline exactly).
    hit_rate_r = round(hit_rate, 4)
    base_hr_r = round(base_hr, 4)
    return SignalValidation(
        signal_id=signal_id,
        signal_kind=signal_kind,
        direction=str(direction),
        conviction_label=conviction_label,
        conviction_score=round(float(conviction_score), 4),
        forward_days=forward_days,
        n_observations=n_obs,
        n_hits=n_hits,
        hit_rate=hit_rate_r,
        avg_forward_return=round(avg_ret, 6),
        directional_return=round(directional, 6),
        baseline_hit_rate=base_hr_r,
        edge_vs_baseline=round(hit_rate_r - base_hr_r, 4),
        low_sample=low_sample,
        note=note,
    )


# ---------------------------------------------------------------------------
# Tier aggregation
# ---------------------------------------------------------------------------


def _aggregate_tier(tier: str, members: list[SignalValidation]) -> TierScore:
    """Roll a tier's member signals up into one observation-weighted score.

    The tier hit rate is weighted by each signal's observation count (a signal
    backed by 40 windows counts more than one backed by 8), so the tier number
    reflects the actual evidence, not a flat average of small samples.
    """
    total_obs = sum(m.n_observations for m in members)
    if total_obs == 0:
        return TierScore(
            tier=tier, n_signals=len(members), n_observations=0,
            hit_rate=0.0, avg_forward_return=0.0, directional_return=0.0,
            baseline_hit_rate=0.0, edge_vs_baseline=0.0,
        )

    total_hits = sum(m.n_hits for m in members)
    hit_rate = _clamp01(total_hits / total_obs)

    # Observation-weighted means for the return + baseline figures.
    avg_ret = sum(m.avg_forward_return * m.n_observations for m in members) / total_obs
    directional = sum(m.directional_return * m.n_observations for m in members) / total_obs
    base_hr = sum(m.baseline_hit_rate * m.n_observations for m in members) / total_obs

    # Round once, derive the edge from the rounded fields for self-consistency.
    hit_rate_r = round(hit_rate, 4)
    base_hr_r = round(_clamp01(base_hr), 4)
    return TierScore(
        tier=tier,
        n_signals=len(members),
        n_observations=total_obs,
        hit_rate=hit_rate_r,
        avg_forward_return=round(avg_ret, 6),
        directional_return=round(directional, 6),
        baseline_hit_rate=base_hr_r,
        edge_vs_baseline=round(hit_rate_r - base_hr_r, 4),
    )


def _tier_sort_key(tier: str) -> tuple[int, str]:
    """Sort key putting the cascade tiers in strength order, others after."""
    if tier in _CONVICTION_TIERS:
        return (_CONVICTION_TIERS.index(tier), tier)
    return (len(_CONVICTION_TIERS), tier)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_signals(
    equity_ideas: list,
    commodity_signals: list,
    stock_data: dict,
    *,
    forward_days: int = _DEFAULT_FORWARD_DAYS,
    sample_stride: int = _SAMPLE_STRIDE,
) -> ValidationReport:
    """Validate the platform's *real* signals against synthetic forward returns.

    This is the module's single entry point. It takes the live output of the
    real pipeline — the disruption cascade's ranked
    :class:`~processing.disruption_cascade.EquityIdea` list and the
    :class:`~processing.commodity_shipping.CommodityShippingSignal` list — plus
    the platform's synthetic ``stock_data``, and measures each signal's
    **directional claim** against forward returns over the price history.

    For every signal it records a hit / miss track record; it then aggregates
    those records by the cascade's own conviction tiers and compares every tier
    against an equal-weight, always-long synthetic baseline. The whole result is
    a transparent hit-rate scorecard — there is no fitted model anywhere.

    Parameters
    ----------
    equity_ideas:
        ``list[EquityIdea]`` from
        :func:`processing.disruption_cascade.score_equity_ideas`. Each idea
        carries ``ticker``, ``direction``, ``conviction_score`` and
        ``conviction_label``. May be empty / ``None`` — those signals are simply
        absent from the report.
    commodity_signals:
        ``list[CommodityShippingSignal]`` from
        :func:`processing.commodity_shipping.analyze_commodity_signals`. Each
        carries ``commodity_ticker``, ``direction`` and ``signal_strength``.
        May be empty / ``None``.
    stock_data:
        Mapping ``ticker -> DataFrame`` with at least ``date`` and ``close``
        columns — the platform's synthetic price history. A signal whose ticker
        is missing here cannot be validated and is counted as skipped. An empty
        mapping yields an empty-but-valid report.
    forward_days:
        Forward horizon, in trading rows, each directional claim is judged over.
        Defaults to ``21`` (≈ one trading month).
    sample_stride:
        Spacing, in rows, between sampled historical entry points. Defaults to
        ``5`` (weekly) so overlapping forward windows do not over-count.

    Returns
    -------
    ValidationReport
        The full scorecard — always a valid report. Degenerate inputs (no
        signals, no price history) yield a neutral, clearly-labelled empty
        report rather than an exception.
    """
    forward_days = max(1, int(forward_days))
    sample_stride = max(1, int(sample_stride))
    stock_data = stock_data or {}
    equity_ideas = equity_ideas or []
    commodity_signals = commodity_signals or []

    # Provenance — this validator runs entirely on synthetic / modeled history,
    # so the scorecard is stamped DEMO-quality, MODELED-kind. The UI surfaces
    # this pill so the synthetic basis is never hidden.
    source = DataSource(
        name="Real-Signal Validator (synthetic history)",
        kind=DataKind.MODELED,
        quality=DataQuality.DEMO,
        as_of=datetime.now(timezone.utc),
        notes=(
            "Hit-rate scorecard: each live cascade / commodity signal's "
            "direction tested against forward returns over the platform's "
            "synthetic price history. Modeled demo data — not investment advice."
        ),
    )

    # The equal-weight always-long baseline pool, computed once and reused for
    # every signal's edge_vs_baseline comparison.
    baseline_pool = _baseline_pool(stock_data, forward_days, sample_stride)

    # Longest usable history actually seen — reported for transparency.
    max_history = 0
    for df in stock_data.values():
        close = _safe_close_series(df)
        if close is not None:
            max_history = max(max_history, len(close))

    validations: list[SignalValidation] = []
    n_skipped = 0

    # ---- 1. Validate every cascade EquityIdea ---------------------------
    for idea in equity_ideas:
        try:
            ticker = str(getattr(idea, "ticker", "") or "")
            if not ticker:
                continue
            close = _safe_close_series(stock_data.get(ticker))
            result = _validate_one(
                signal_id=ticker,
                signal_kind="cascade idea",
                direction=str(getattr(idea, "direction", "Neutral")),
                conviction_label=str(getattr(idea, "conviction_label", "Watch")),
                conviction_score=float(getattr(idea, "conviction_score", 0.0) or 0.0),
                close=close,
                baseline_pool=baseline_pool,
                forward_days=forward_days,
                stride=sample_stride,
            )
            if result is None:
                n_skipped += 1
            else:
                validations.append(result)
        except Exception:  # pragma: no cover - defensive
            logger.exception("signal_validation: failed to validate a cascade idea")
            n_skipped += 1

    # ---- 2. Validate every CommodityShippingSignal ----------------------
    for sig in commodity_signals:
        try:
            ticker = str(getattr(sig, "commodity_ticker", "") or "")
            if not ticker:
                continue
            close = _safe_close_series(stock_data.get(ticker))
            result = _validate_one(
                signal_id=ticker,
                signal_kind="commodity signal",
                direction=str(getattr(sig, "direction", "neutral")),
                conviction_label=_COMMODITY_TIER,
                conviction_score=float(getattr(sig, "signal_strength", 0.0) or 0.0),
                close=close,
                baseline_pool=baseline_pool,
                forward_days=forward_days,
                stride=sample_stride,
            )
            if result is None:
                n_skipped += 1
            else:
                validations.append(result)
        except Exception:  # pragma: no cover - defensive
            logger.exception("signal_validation: failed to validate a commodity signal")
            n_skipped += 1

    # ---- 3. Aggregate by conviction tier --------------------------------
    by_tier: dict[str, list[SignalValidation]] = {}
    for v in validations:
        by_tier.setdefault(v.conviction_label, []).append(v)

    tiers: list[TierScore] = [
        _aggregate_tier(tier, members)
        for tier, members in sorted(by_tier.items(), key=lambda kv: _tier_sort_key(kv[0]))
    ]

    # ---- 4. Overall (observation-weighted) figures ----------------------
    total_obs = sum(v.n_observations for v in validations)
    total_hits = sum(v.n_hits for v in validations)
    if total_obs > 0:
        overall_hr = _clamp01(total_hits / total_obs)
        overall_base = _clamp01(
            sum(v.baseline_hit_rate * v.n_observations for v in validations) / total_obs
        )
        overall_dir = (
            sum(v.directional_return * v.n_observations for v in validations) / total_obs
        )
    else:
        overall_hr = overall_base = overall_dir = 0.0

    # ---- 5. One-line plain-language summary -----------------------------
    if not validations:
        summary = (
            "No real signals could be validated — no synthetic price history "
            "was available for any signalled ticker."
        )
    else:
        edge = overall_hr - overall_base
        if edge > 0.05:
            verdict = f"beating the equal-weight baseline by {edge * 100:.0f} pts"
        elif edge < -0.05:
            verdict = f"trailing the equal-weight baseline by {abs(edge) * 100:.0f} pts"
        else:
            verdict = "roughly in line with the equal-weight baseline"
        summary = (
            f"{len(validations)} real signal(s) validated on synthetic history: "
            f"{overall_hr * 100:.0f}% aggregate hit rate over a "
            f"{forward_days}-day horizon, {verdict}. "
            f"Modeled demo — not investment advice."
        )

    # Round once, derive the overall edge from the rounded fields.
    overall_hr_r = round(overall_hr, 4)
    overall_base_r = round(overall_base, 4)
    report = ValidationReport(
        signals=sorted(
            validations,
            key=lambda v: (-v.conviction_score, v.signal_id),
        ),
        tiers=tiers,
        overall_hit_rate=overall_hr_r,
        overall_baseline_hit_rate=overall_base_r,
        overall_edge=round(overall_hr_r - overall_base_r, 4),
        overall_directional_return=round(overall_dir, 6),
        n_signals_validated=len(validations),
        n_signals_skipped=n_skipped,
        forward_days=forward_days,
        price_history_days=max_history,
        source=source,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=summary,
    )

    logger.debug(
        "validate_signals: {} validated, {} skipped; overall hit rate {:.1%} "
        "vs baseline {:.1%}",
        report.n_signals_validated,
        report.n_signals_skipped,
        report.overall_hit_rate,
        report.overall_baseline_hit_rate,
    )
    return report


def build_validation_report(
    stress_report,
    exposure_matrix,
    stock_data: dict,
    *,
    insights=None,
    forward_days: int = _DEFAULT_FORWARD_DAYS,
) -> ValidationReport:
    """Convenience wrapper: score the real signals, then validate them.

    The UI does not have a pre-computed cascade in hand, so this helper runs the
    real pipeline end-to-end — :func:`score_equity_ideas` and
    :func:`analyze_commodity_signals` — and feeds the result straight into
    :func:`validate_signals`. It is a thin, transparent convenience layer: it
    introduces no new modeling, only the two existing real-signal generators.

    Parameters
    ----------
    stress_report:
        A ``ShippingStressReport`` (or ``None``) — passed through to
        :func:`processing.disruption_cascade.score_equity_ideas`.
    exposure_matrix:
        ``list[CommodityExposure]`` (or ``None``) — passed through to the cascade
        scorer.
    stock_data:
        Mapping ``ticker -> DataFrame`` of synthetic price history.
    insights:
        Optional decision-engine insights — forwarded to the cascade scorer for
        corroboration only (never changes a direction).
    forward_days:
        Forward horizon for the validation, in trading rows.

    Returns
    -------
    ValidationReport
        Always valid. If the real pipeline cannot be invoked for any reason the
        function logs and returns an empty-but-valid report rather than raising.
    """
    stock_data = stock_data or {}
    try:
        from processing.commodity_shipping import analyze_commodity_signals
        from processing.disruption_cascade import score_equity_ideas

        equity_ideas = score_equity_ideas(
            stress_report, exposure_matrix, stock_data, insights=insights
        )
        commodity_signals = analyze_commodity_signals(stock_data)
    except Exception:  # pragma: no cover - defensive
        logger.exception("build_validation_report: real-signal pipeline failed")
        equity_ideas, commodity_signals = [], []

    return validate_signals(
        equity_ideas,
        commodity_signals,
        stock_data,
        forward_days=forward_days,
    )


def build_ledger_validation_report(
    stock_data: dict,
    *,
    forward_days: int = _DEFAULT_FORWARD_DAYS,
) -> ValidationReport:
    """Causal, look-ahead-free validation from the FROZEN signal ledger (rec R016).

    :func:`build_validation_report` re-scores TODAY's cascade ideas over PAST
    return windows — a look-ahead the platform openly admitted (DATA_PROVENANCE).
    This reads the point-in-time ledger instead: every idea was frozen AT ISSUE
    (close + direction + conviction) and is marked ONLY on strictly-later closes,
    so each row is one genuinely out-of-sample, causal observation. There is no
    resampling of overlapping windows — one realized outcome per frozen signal,
    fewer but honest.

    Per conviction tier it reports the realized directional hit-rate against an
    **equal-weight always-long base rate** over the same names/holding windows
    (the fraction of those names that simply rose) — a tier only earns its keep
    by beating naive long-everything. Returns an empty-but-valid report (never
    raises) until the daily freeze job has accrued marked history.
    """
    report = ValidationReport(
        forward_days=forward_days,
        generated_at=datetime.now(timezone.utc).isoformat(),
        source=DataSource(
            name="Frozen point-in-time signal ledger (causal, look-ahead-free)",
            kind=DataKind.MODELED, quality=DataQuality.GOOD,
        ),
    )
    try:
        from state.signal_ledger import mark_ledger
        marks = [m for m in mark_ledger(stock_data)
                 if m.get("issue_close") and m.get("current_close")]
    except Exception:  # pragma: no cover - defensive
        logger.exception("build_ledger_validation_report: ledger read failed")
        marks = []

    if not marks:
        report.summary = (
            "No frozen, marked signals yet — the causal track record accrues as "
            "ideas are issued and marked forward (look-ahead-free)."
        )
        return report

    # Direction-adjusted naive baseline. A book that ALWAYS predicts a name's
    # direction is right at the market's drift rate FOR THAT DIRECTION: a Bullish
    # call's naive baseline is the fraction of names that rose; a Bearish call's
    # is the fraction that fell. Comparing a bearish tier's hit-rate to the
    # always-LONG (up) rate would be apples-to-oranges and systematically
    # overstate it — so each signal is scored against its own directional drift.
    raw = [(m["current_close"] - m["issue_close"]) / m["issue_close"] for m in marks]
    n_raw = len(raw)
    up_rate = sum(1 for r in raw if r > 0) / n_raw
    down_rate = sum(1 for r in raw if r < 0) / n_raw

    def _dir_baseline(direction: str) -> float:
        return down_rate if _direction_sign(direction) < 0 else up_rate

    signals: list[SignalValidation] = []
    for m, raw_ret in zip(marks, raw):
        signed = m["signed_return_pct"] / 100.0
        win = signed > 0
        b = _dir_baseline(str(m.get("direction") or ""))
        signals.append(SignalValidation(
            signal_id=str(m.get("ticker") or ""),
            signal_kind="frozen ledger idea",
            direction=str(m.get("direction") or ""),
            conviction_label=str(m.get("conviction_label") or "?"),
            conviction_score=round(float(m.get("conviction_score") or 0.0), 4),
            forward_days=forward_days,
            n_observations=1,                 # one causal OOS outcome per signal
            n_hits=1 if win else 0,
            hit_rate=1.0 if win else 0.0,
            avg_forward_return=round(raw_ret, 4),
            directional_return=round(signed, 4),
            baseline_hit_rate=round(b, 4),
            edge_vs_baseline=round((1.0 if win else 0.0) - b, 4),
            low_sample=True,                  # a single realized outcome so far
            note="single realized out-of-sample outcome (point-in-time)",
        ))

    # Tier aggregation — stable cascade order first, then any extras. The tier
    # baseline is the mean of its signals' OWN direction-adjusted baselines
    # (so a mixed-direction tier is still scored fairly).
    by_tier: dict[str, list] = {}
    for s in signals:
        by_tier.setdefault(s.conviction_label, []).append(s)
    ordered = [t for t in _CONVICTION_TIERS if t in by_tier] + \
              sorted(t for t in by_tier if t not in _CONVICTION_TIERS)
    tiers: list[TierScore] = []
    for tier in ordered:
        ss = by_tier[tier]
        n = len(ss)
        hr = round(sum(s.n_hits for s in ss) / n, 4)
        tier_base = round(sum(s.baseline_hit_rate for s in ss) / n, 4)
        tiers.append(TierScore(
            tier=tier, n_signals=n, n_observations=n, hit_rate=hr,
            avg_forward_return=round(sum(s.avg_forward_return for s in ss) / n, 4),
            directional_return=round(sum(s.directional_return for s in ss) / n, 4),
            baseline_hit_rate=tier_base,
            edge_vs_baseline=round(hr - tier_base, 4),
        ))

    n_all = len(signals)
    overall_hr = round(sum(s.n_hits for s in signals) / n_all, 4)
    baseline = round(sum(s.baseline_hit_rate for s in signals) / n_all, 4)
    report.signals = signals
    report.tiers = tiers
    report.overall_hit_rate = overall_hr
    report.overall_baseline_hit_rate = baseline
    report.overall_edge = round(overall_hr - baseline, 4)
    report.overall_directional_return = round(
        sum(s.directional_return for s in signals) / n_all, 4)
    report.n_signals_validated = n_all
    report.summary = (
        f"{n_all} frozen signal(s) marked forward (causal, look-ahead-free): "
        f"{overall_hr * 100:.0f}% directional hit-rate vs {baseline * 100:.0f}% "
        f"always-long base rate ({(overall_hr - baseline) * 100:+.0f} pts edge)."
    )
    return report


__all__ = [
    "SignalValidation",
    "TierScore",
    "ValidationReport",
    "validate_signals",
    "build_validation_report",
    "build_ledger_validation_report",
]
