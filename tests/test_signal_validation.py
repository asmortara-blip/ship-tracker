"""Pure-function tests for processing.signal_validation.

The real-signal validator measures the platform's *actual* signals — the
disruption cascade's ranked ``EquityIdea`` list and the commodity-shipping
signals — against forward returns over the synthetic price history, producing a
transparent hit-rate scorecard. These tests pin:

* the [0, 1] bound on every hit rate (per-signal, per-tier, overall);
* the conviction-tier breakdown shape and observation-weighted aggregation;
* graceful handling of empty / missing / degenerate inputs.

No Streamlit, no live feed — everything runs on small synthetic fixtures.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from processing.commodity_shipping import CommodityShippingSignal
from processing.disruption_cascade import EquityIdea
from processing.signal_validation import (
    SignalValidation,
    TierScore,
    ValidationReport,
    build_validation_report,
    validate_signals,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _price_df(base: float, n: int = 300, drift: float = 0.0005, seed: int = 0) -> pd.DataFrame:
    """A synthetic OHLC price frame — geometric random walk with mild drift."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="D")
    log_returns = rng.normal(loc=drift, scale=0.02, size=n)
    close = base * np.exp(np.cumsum(log_returns))
    return pd.DataFrame({
        "date": dates,
        "symbol": "TEST",
        "open": close,
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1_000_000,
    })


@pytest.fixture(scope="module")
def stock_data() -> dict[str, pd.DataFrame]:
    """Synthetic price history for a spread of shipping + commodity tickers."""
    tickers = ["ZIM", "MATX", "SBLK", "DAC", "CMRE", "USO", "DBA", "DBB", "XLB"]
    return {
        t: _price_df(20.0 + 4.0 * i, drift=0.0006, seed=i)
        for i, t in enumerate(tickers)
    }


@pytest.fixture(scope="module")
def equity_ideas() -> list[EquityIdea]:
    """A hand-built spread of cascade ideas across all four conviction tiers."""
    return [
        EquityIdea(
            ticker="ZIM", company_name="ZIM", direction="Bullish",
            conviction_score=0.78, conviction_label="High",
            thesis="synthetic test idea",
        ),
        EquityIdea(
            ticker="MATX", company_name="Matson", direction="Bullish",
            conviction_score=0.55, conviction_label="Moderate",
            thesis="synthetic test idea",
        ),
        EquityIdea(
            ticker="SBLK", company_name="Star Bulk", direction="Bearish",
            conviction_score=0.30, conviction_label="Low",
            thesis="synthetic test idea",
        ),
        EquityIdea(
            ticker="DAC", company_name="Danaos", direction="Neutral",
            conviction_score=0.10, conviction_label="Watch",
            thesis="synthetic test idea",
        ),
    ]


@pytest.fixture(scope="module")
def commodity_signals() -> list[CommodityShippingSignal]:
    """A pair of commodity-shipping signals with opposite directions."""
    return [
        CommodityShippingSignal(
            commodity_ticker="DBB", commodity_name="Base Metals",
            current_price=22.0, price_change_30d=0.08, direction="bullish",
            shipping_hypothesis="test", affected_routes=[], affected_stocks=[],
            signal_strength=0.6, trade_idea="test",
        ),
        CommodityShippingSignal(
            commodity_ticker="USO", commodity_name="Oil Fund",
            current_price=70.0, price_change_30d=-0.06, direction="bearish",
            shipping_hypothesis="test", affected_routes=[], affected_stocks=[],
            signal_strength=0.4, trade_idea="test",
        ),
    ]


@pytest.fixture(scope="module")
def report(
    equity_ideas: list[EquityIdea],
    commodity_signals: list[CommodityShippingSignal],
    stock_data: dict[str, pd.DataFrame],
) -> ValidationReport:
    """A full validation report over the synthetic fixtures."""
    return validate_signals(equity_ideas, commodity_signals, stock_data)


# ── Hit-rate bounds — the core invariant ────────────────────────────────────


def test_all_hit_rates_within_unit_interval(report: ValidationReport) -> None:
    """Every hit rate — per-signal, per-tier, overall, baseline — lands in [0, 1]."""
    assert 0.0 <= report.overall_hit_rate <= 1.0
    assert 0.0 <= report.overall_baseline_hit_rate <= 1.0

    for s in report.signals:
        assert isinstance(s, SignalValidation)
        assert 0.0 <= s.hit_rate <= 1.0, f"{s.signal_id} hit_rate out of bounds"
        assert 0.0 <= s.baseline_hit_rate <= 1.0
        # n_hits can never exceed the windows it was counted from.
        assert 0 <= s.n_hits <= s.n_observations

    for t in report.tiers:
        assert isinstance(t, TierScore)
        assert 0.0 <= t.hit_rate <= 1.0, f"tier {t.tier} hit_rate out of bounds"
        assert 0.0 <= t.baseline_hit_rate <= 1.0


def test_edge_is_hit_rate_minus_baseline(report: ValidationReport) -> None:
    """``edge_vs_baseline`` must equal hit_rate − baseline_hit_rate everywhere."""
    assert report.overall_edge == pytest.approx(
        report.overall_hit_rate - report.overall_baseline_hit_rate, abs=1e-6
    )
    for s in report.signals:
        assert s.edge_vs_baseline == pytest.approx(
            s.hit_rate - s.baseline_hit_rate, abs=1e-6
        )
    for t in report.tiers:
        assert t.edge_vs_baseline == pytest.approx(
            t.hit_rate - t.baseline_hit_rate, abs=1e-6
        )


# ── Conviction-tier breakdown ───────────────────────────────────────────────


def test_conviction_tier_breakdown(
    report: ValidationReport,
    equity_ideas: list[EquityIdea],
) -> None:
    """Each cascade tier present in the ideas appears once in the breakdown,
    plus a separate 'Commodity' bucket for the commodity signals."""
    tier_labels = {t.tier for t in report.tiers}
    # Every distinct cascade conviction label is represented.
    assert {"High", "Moderate", "Low", "Watch"} <= tier_labels
    # Commodity signals bucket under their own synthetic tier.
    assert "Commodity" in tier_labels

    # Tier signal counts must sum to the number of validated signals.
    assert sum(t.n_signals for t in report.tiers) == report.n_signals_validated

    # Observation-weighted aggregation: a tier's window count is the sum of its
    # members' window counts.
    by_tier: dict[str, list[SignalValidation]] = {}
    for s in report.signals:
        by_tier.setdefault(s.conviction_label, []).append(s)
    for t in report.tiers:
        members = by_tier.get(t.tier, [])
        assert t.n_observations == sum(m.n_observations for m in members)
        assert t.n_signals == len(members)


def test_overall_hit_rate_is_observation_weighted(report: ValidationReport) -> None:
    """The overall hit rate must equal total hits / total windows across signals.

    Scorecard figures are stored rounded to 4 decimal places, so the comparison
    allows a half-ulp-of-4dp tolerance.
    """
    total_obs = sum(s.n_observations for s in report.signals)
    total_hits = sum(s.n_hits for s in report.signals)
    expected = (total_hits / total_obs) if total_obs else 0.0
    assert report.overall_hit_rate == pytest.approx(expected, abs=5e-5)


# ── Graceful degradation on empty / missing data ────────────────────────────


def test_empty_inputs_yield_neutral_report() -> None:
    """No signals + no price history → a valid, zeroed, non-raising report."""
    rep = validate_signals([], [], {})
    assert isinstance(rep, ValidationReport)
    assert rep.n_signals_validated == 0
    assert rep.n_signals_skipped == 0
    assert rep.overall_hit_rate == 0.0
    assert rep.overall_baseline_hit_rate == 0.0
    assert rep.tiers == []
    assert rep.signals == []
    assert rep.source is not None  # provenance always stamped
    assert "No real signals" in rep.summary


def test_missing_price_history_counts_as_skipped(
    equity_ideas: list[EquityIdea],
) -> None:
    """Signals whose ticker has no usable price frame are skipped, not crashed."""
    # stock_data missing every signalled ticker, plus one empty/garbage frame.
    junk = {"ZIM": pd.DataFrame(), "MATX": pd.DataFrame({"foo": [1, 2, 3]})}
    rep = validate_signals(equity_ideas, [], junk)
    assert isinstance(rep, ValidationReport)
    # All four ideas reference tickers with no usable history → all skipped.
    assert rep.n_signals_validated == 0
    assert rep.n_signals_skipped == len(equity_ideas)
    assert rep.overall_hit_rate == 0.0


def test_none_inputs_do_not_raise(stock_data: dict[str, pd.DataFrame]) -> None:
    """``None`` passed for signal lists or stock_data degrades gracefully."""
    assert validate_signals(None, None, None).n_signals_validated == 0
    rep = validate_signals(None, None, stock_data)
    assert isinstance(rep, ValidationReport)
    assert rep.n_signals_validated == 0  # no signals, but no crash


def test_build_validation_report_runs_real_pipeline(
    stock_data: dict[str, pd.DataFrame],
) -> None:
    """The convenience wrapper drives the real cascade pipeline end-to-end."""
    from processing.exposure_matrix import build_exposure_matrix
    from processing.shipping_stress_index import compute_shipping_stress

    stress = compute_shipping_stress({}, {}, [], [], voyage_fleet=None)
    matrix = build_exposure_matrix(stock_data)
    rep = build_validation_report(stress, matrix, stock_data)

    assert isinstance(rep, ValidationReport)
    # The real cascade emits one idea per tracked ticker; with synthetic price
    # history present, validated signals should be produced.
    assert rep.n_signals_validated > 0
    assert 0.0 <= rep.overall_hit_rate <= 1.0
    assert rep.forward_days == 21
