"""End-to-end integration test for the Disruption Alpha pipeline.

The Disruption Alpha chain is the platform's flagship signal stack: a fleet of
modeled voyages and synthetic freight history feed the Shipping Stress Index;
the SSI plus a stock-data exposure matrix drives the disruption cascade; the
cascade's ranked ``EquityIdea`` list is then forward-validated against the same
synthetic price tape. The individual stages are unit-tested elsewhere
(``tests/test_voyage_dataset.py``, ``tests/test_shipping_stress_index.py``,
``tests/test_disruption_forecast.py``, ``tests/test_exposure_matrix.py``,
``tests/test_disruption_cascade.py``, ``tests/test_signal_validation.py``).
This file pins the *integration claims* — invariants that hold only when the
whole chain is walked end-to-end on a fresh deterministic seed:

* the pipeline completes without exception;
* every stage produces non-empty output;
* COHERENCE — a route surfaced in ``stress_report.top_disruptions`` is cited in
  at least one ``EquityIdea.driving_routes``;
* TRANSPARENCY — every ``CascadeLink.contribution`` decomposes as
  ``route_stress * cargo_share * exposure_weight`` within rounding;
* CONVICTION INVARIANTS — every score is in ``[0, 1]``, every label is in the
  documented set, and ``supporting_signals`` carries the conviction-term
  decomposition;
* DIRECTION FRAMING — direction is one of {Bullish, Bearish, Neutral} — never
  ``Buy``/``Sell``/price targets;
* VALIDATION — the rolled-up ``ValidationReport`` has bounded hit rates and a
  conviction-tier breakdown.

This is a MODELED/DEMO pipeline; the test runs on the platform's own synthetic
fixtures (``data.freight_scraper._synthetic_fallback``, deterministic stock
walks, ``build_voyage_fleet(seed=...)``) — that is the appropriate basis for an
integration test. No Streamlit, no live feed.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from data.freight_scraper import _synthetic_fallback
from data.voyage_dataset import build_voyage_fleet
from processing.disruption_cascade import (
    CascadeLink,
    EquityIdea,
    score_equity_ideas,
)
from processing.disruption_forecast import (
    StressForecast,
    forecast_all_stress,
)
from processing.exposure_matrix import (
    COMPANY_COMMODITY_EXPOSURE,
    CommodityExposure,
    build_exposure_matrix,
    company_commodity_weights,
)
from processing.shipping_stress_index import (
    ShippingStressReport,
    compute_shipping_stress,
)
from processing.signal_validation import (
    ValidationReport,
    build_validation_report,
    validate_signals,
)
from routes.route_registry import ROUTES_BY_ID

# Deterministic seed for the whole integration run. A single fixed integer
# stabilises the modeled voyage fleet, the synthetic price walks and the
# stock-data RNGs so every test in this file sees identical inputs.
_SEED = 20260520

_DIRECTIONS = {"Bullish", "Bearish", "Neutral"}
_CONVICTION_LABELS = {"High", "Moderate", "Low", "Watch"}
_SSI_LABELS = {"Calm", "Elevated", "Stressed", "Severe"}
# Directional words that must NEVER appear as a direction — the cascade is an
# analytical framing, not investment advice.
_FORBIDDEN_DIRECTIONS = {"Buy", "Sell", "Long", "Short"}


# ── Inline synthetic fixtures (pipeline-shape, deterministic) ───────────────


def _stock_frame(symbol: str, base: float, n: int, drift: float, seed: int) -> pd.DataFrame:
    """One synthetic OHLC price frame — geometric random walk with mild drift.

    Mirrors the shape ``stock_feed`` produces (and that the existing
    ``test_signal_validation.py`` synthesises): ``date``/``symbol``/OHLC/volume
    columns, daily frequency, deterministic from ``seed``.
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="D")
    log_returns = rng.normal(loc=drift, scale=0.02, size=n)
    close = base * np.exp(np.cumsum(log_returns))
    return pd.DataFrame({
        "date":   dates,
        "symbol": symbol,
        "open":   close,
        "high":   close * 1.01,
        "low":    close * 0.99,
        "close":  close,
        "volume": 1_000_000,
    })


def _build_stock_data() -> dict[str, pd.DataFrame]:
    """Synthetic price history spanning every tracked shipping ticker + ETFs.

    The cascade scorer reads price/30-day move per tracked ticker, the exposure
    matrix reads the commodity-ETF moves, and the validator pools every frame
    for its equal-weight baseline. A single dict covers all three.
    """
    # Tracked shipping tickers (from COMPANY_COMMODITY_EXPOSURE) plus the
    # commodity ETFs the exposure matrix maps to, plus the USO fuel overlay.
    shipping = list(COMPANY_COMMODITY_EXPOSURE.keys())
    etfs = ["XRT", "XLI", "XLB", "DBA", "DBB", "USO"]
    tickers = list(dict.fromkeys(shipping + etfs))   # de-duplicated, order-preserving
    return {
        # 300 rows ≈ 14 months of daily history — enough for the validator's
        # 21-day forward windows to produce a meaningful sample.
        t: _stock_frame(t, 20.0 + 4.0 * i, n=300, drift=0.0005, seed=_SEED + i)
        for i, t in enumerate(tickers)
    }


# ── Module-scope fixtures: build the chain once, assert against the result ──


@pytest.fixture(scope="module")
def voyage_fleet():
    """Modeled voyage fleet — the first stage of the chain."""
    return build_voyage_fleet(seed=_SEED)


@pytest.fixture(scope="module")
def freight_data() -> dict[str, pd.DataFrame]:
    """Synthetic freight history from the platform's documented fallback."""
    return _synthetic_fallback()


@pytest.fixture(scope="module")
def stock_data() -> dict[str, pd.DataFrame]:
    """Synthetic stock + ETF history used by both the cascade and the validator."""
    return _build_stock_data()


@pytest.fixture(scope="module")
def stress_report(
    voyage_fleet, freight_data: dict[str, pd.DataFrame],
) -> ShippingStressReport:
    """Stage 3 — the Shipping Stress Index."""
    return compute_shipping_stress(
        freight_data, {}, [], [], voyage_fleet=voyage_fleet,
    )


@pytest.fixture(scope="module")
def forecasts(
    freight_data: dict[str, pd.DataFrame], stress_report: ShippingStressReport,
) -> list[StressForecast]:
    """Stage 4 — forward stress projection per route."""
    return forecast_all_stress(freight_data, {}, [], stress_report=stress_report)


@pytest.fixture(scope="module")
def exposure_matrix(stock_data: dict[str, pd.DataFrame]) -> list[CommodityExposure]:
    """Stage 5 — company-commodity exposure matrix."""
    return build_exposure_matrix(stock_data)


@pytest.fixture(scope="module")
def equity_ideas(
    stress_report: ShippingStressReport,
    exposure_matrix: list[CommodityExposure],
    stock_data: dict[str, pd.DataFrame],
) -> list[EquityIdea]:
    """Stage 6 — ranked, traceable equity ideas (the cascade conclusion)."""
    return score_equity_ideas(stress_report, exposure_matrix, stock_data, insights=None)


@pytest.fixture(scope="module")
def validation_report(
    equity_ideas: list[EquityIdea],
    stock_data: dict[str, pd.DataFrame],
) -> ValidationReport:
    """Stage 7 — hit-rate scorecard rolled up against synthetic forward returns."""
    return validate_signals(equity_ideas, [], stock_data)


# ── 1. Pipeline completion + non-empty stage outputs ────────────────────────


def test_full_pipeline_runs_and_each_stage_produces_output(
    voyage_fleet,
    freight_data: dict[str, pd.DataFrame],
    stress_report: ShippingStressReport,
    forecasts: list[StressForecast],
    exposure_matrix: list[CommodityExposure],
    equity_ideas: list[EquityIdea],
    validation_report: ValidationReport,
) -> None:
    """The full chain completes and every stage yields non-empty output.

    This is the load-bearing integration claim: if the pipeline is wired
    correctly, walking voyages → freight → SSI → forecast → exposure → cascade
    → validation produces something useful at every hop.
    """
    # Stage 1 — modeled voyage fleet.
    assert isinstance(voyage_fleet, list) and len(voyage_fleet) > 0

    # Stage 2 — synthetic freight (one frame per registry route).
    assert freight_data and all(
        hasattr(df, "columns") and "rate_usd_per_feu" in df.columns
        for df in freight_data.values()
    )

    # Stage 3 — Shipping Stress Index report.
    assert isinstance(stress_report, ShippingStressReport)
    assert len(stress_report.route_stress) > 0
    assert 0.0 <= stress_report.overall_ssi <= 1.0
    assert stress_report.ssi_label in _SSI_LABELS

    # Stage 4 — forward stress forecasts.
    assert isinstance(forecasts, list) and len(forecasts) > 0
    assert all(isinstance(f, StressForecast) for f in forecasts)
    for fc in forecasts:
        assert 0.0 <= fc.stress_7d <= 1.0
        assert 0.0 <= fc.stress_30d <= 1.0

    # Stage 5 — exposure matrix.
    assert isinstance(exposure_matrix, list) and len(exposure_matrix) > 0
    assert all(isinstance(ce, CommodityExposure) for ce in exposure_matrix)

    # Stage 6 — cascade equity ideas (one per tracked ticker).
    assert isinstance(equity_ideas, list)
    assert len(equity_ideas) == len(COMPANY_COMMODITY_EXPOSURE)
    assert all(isinstance(i, EquityIdea) for i in equity_ideas)

    # Stage 7 — validation report.
    assert isinstance(validation_report, ValidationReport)
    # The validator sees every cascade ticker's synthetic frame above, so at
    # least one signal must be validated rather than skipped.
    assert validation_report.n_signals_validated > 0


# ── 2. COHERENCE — disruptions surface as equity ideas ──────────────────────


def test_top_disruption_surfaces_as_an_equity_idea_driver(
    stress_report: ShippingStressReport,
    equity_ideas: list[EquityIdea],
) -> None:
    """A route featured in ``top_disruptions`` must drive at least one EquityIdea.

    ``top_disruptions`` carries human-readable strings of the form
    ``"<Route Name>: <driver> stress elevated"`` (or alert titles from
    ``engine.alert_engine``). The cascade chain carries registry ``route_id``s
    on each ``CascadeLink`` and exposes them as the ``driving_routes`` list.
    The bridge is the route registry: every route_id in ``ROUTES_BY_ID`` has a
    canonical ``.name``, which is exactly the prefix
    ``shipping_stress_index._build_top_disruptions`` uses.
    """
    assert stress_report.top_disruptions, "stress report produced no top_disruptions"

    # Build the reverse lookup: route name → route_id.
    name_to_id: dict[str, str] = {
        route.name: route.id for route in ROUTES_BY_ID.values()
    }
    disrupted_ids: set[str] = set()
    for entry in stress_report.top_disruptions:
        # Each route-stress disruption string is "<Route Name>: <…>".
        head = entry.split(":", 1)[0].strip() if ":" in entry else entry.strip()
        if head in name_to_id:
            disrupted_ids.add(name_to_id[head])

    # ``top_disruptions`` mixes route-derived lines with alert titles whose
    # text is not a route name. Either at least one entry resolves to a route,
    # OR every entry is a pure alert title that the cascade still treats as
    # driving stress somewhere — fall back to the worst-stressed routes the
    # SSI itself reports.
    if not disrupted_ids:
        # Use the headline-stressed routes from the report (worst-first list).
        disrupted_ids = {
            rs.route_id for rs in stress_report.route_stress[:3]
            if rs.stress_score >= 0.30
        }

    assert disrupted_ids, (
        "Could not extract any disrupted route_id from the stress report — "
        "neither top_disruptions nor route_stress yielded a routable entry."
    )

    # At least one disrupted route must surface as a driver in at least one
    # equity idea. This is the load-bearing coherence claim — the chain is
    # only valuable if a disruption actually flows through to an idea.
    surfaced_in: list[str] = []
    for idea in equity_ideas:
        if disrupted_ids.intersection(idea.driving_routes):
            surfaced_in.append(idea.ticker)

    assert surfaced_in, (
        f"None of the disrupted routes {sorted(disrupted_ids)} appear in any "
        f"EquityIdea.driving_routes — the SSI top disruptions are not flowing "
        f"into the cascade."
    )


# ── 3. TRANSPARENCY — every cascade link decomposes exactly ─────────────────


def test_every_cascade_link_decomposes_as_documented(
    equity_ideas: list[EquityIdea],
) -> None:
    """``CascadeLink.contribution`` == ``route_stress * cargo_share * exposure_weight``.

    This is the headline transparency claim: the cascade is not a black box.
    Every link records the three factors that produced it and the product they
    were combined into — the test multiplies the stored factors back together
    and confirms the published contribution matches within rounding.

    Each factor is stored rounded for display (``route_stress``/``cargo_share``
    to 4 dp, ``contribution`` to 6 dp), so a 1e-3 tolerance absorbs the
    rounding without hiding a real algebra error.
    """
    checked_links = 0
    for idea in equity_ideas:
        weights = company_commodity_weights(idea.ticker)
        for link in idea.cascade_chain:
            assert isinstance(link, CascadeLink)
            exposure_weight = weights.get(link.hs_category, 0.0)
            expected = link.route_stress * link.cargo_share * exposure_weight
            assert abs(expected - link.contribution) < 1e-3, (
                f"{idea.ticker}:{link.route_id}:{link.hs_category} "
                f"expected {expected:.6f} got {link.contribution:.6f}"
            )
            checked_links += 1

    # The chain must actually carry cascade links — a vacuous pass on an empty
    # chain would silently hide a wiring break.
    assert checked_links > 0, (
        "No CascadeLink objects to decompose — the cascade chain is empty for "
        "every idea, which would make the transparency claim vacuous."
    )


# ── 4. CONVICTION INVARIANTS + DIRECTION FRAMING ────────────────────────────


def test_conviction_invariants_and_direction_framing(
    equity_ideas: list[EquityIdea],
) -> None:
    """Every idea satisfies the documented conviction + direction invariants.

    Pinned here, end-to-end on the real pipeline output:

    * ``conviction_score`` is a float in ``[0, 1]``;
    * ``conviction_label`` is one of {High, Moderate, Low, Watch};
    * ``supporting_signals`` is non-empty and carries the conviction weight-set
      decomposition (the "Conviction weight set: ..." line + the cascade-term
      and signal-agreement counts);
    * ``direction`` is one of {Bullish, Bearish, Neutral} — never
      ``Buy``/``Sell``/``Long``/``Short`` and never a price target;
    * ``thesis`` is a non-empty string and contains no price-target lexicon.
    """
    # Patterns the thesis text must not contain — price-target framing of any
    # kind would break the "modeled idea, not investment advice" contract.
    price_target_re = re.compile(
        r"\b(price\s+target|pt\s*:\s*\$?\d|\$\d+(\.\d+)?\s*target|target\s*\$\d)",
        flags=re.IGNORECASE,
    )

    for idea in equity_ideas:
        # Conviction score & label.
        assert isinstance(idea.conviction_score, float)
        assert 0.0 <= idea.conviction_score <= 1.0, idea.ticker
        assert idea.conviction_label in _CONVICTION_LABELS, idea.ticker

        # Direction framing.
        assert idea.direction in _DIRECTIONS, idea.ticker
        assert idea.direction not in _FORBIDDEN_DIRECTIONS, idea.ticker

        # Thesis text must exist and must not introduce price-target framing.
        # The platform's standard disclaimer says "...not investment advice,
        # not a price target" — by construction it mentions the phrase only to
        # deny it, so strip the disclaimer wording before regex-checking.
        assert isinstance(idea.thesis, str) and idea.thesis, idea.ticker
        cleaned = re.sub(
            r"not\s+(?:a\s+)?price\s+target", "", idea.thesis, flags=re.IGNORECASE
        )
        assert not price_target_re.search(cleaned), (
            f"{idea.ticker} thesis carries price-target framing: {idea.thesis!r}"
        )

        # supporting_signals must exist and must carry the published
        # conviction-term decomposition — the chosen weight set is named, the
        # cascade-magnitude term is reported, and the signal-agreement count is
        # surfaced. All three are documented contract points.
        assert isinstance(idea.supporting_signals, list)
        assert idea.supporting_signals, idea.ticker
        joined = " ".join(idea.supporting_signals)
        assert "Conviction weight set:" in joined, idea.ticker
        assert "Cascade magnitude" in joined, idea.ticker
        assert "Signal-agreement count" in joined, idea.ticker


# ── 5. VALIDATION — bounded hit rates + tier breakdown ──────────────────────


def test_validation_report_aggregate_bounds_and_tiers(
    validation_report: ValidationReport,
) -> None:
    """The ValidationReport hit rates and tier breakdown satisfy their invariants.

    * ``overall_hit_rate`` is in ``[0, 1]``;
    * ``overall_baseline_hit_rate`` is in ``[0, 1]``;
    * ``overall_edge`` equals the difference (within 4dp rounding);
    * every ``TierScore.hit_rate`` and ``baseline_hit_rate`` is in ``[0, 1]``;
    * the tier breakdown exists — at least one conviction-tier row is present;
    * every per-signal hit count is bounded by its observation count.
    """
    # Aggregate bounds.
    assert 0.0 <= validation_report.overall_hit_rate <= 1.0
    assert 0.0 <= validation_report.overall_baseline_hit_rate <= 1.0
    assert validation_report.overall_edge == pytest.approx(
        validation_report.overall_hit_rate
        - validation_report.overall_baseline_hit_rate,
        abs=1e-4,
    )

    # Tier breakdown: at least one of the four conviction tiers is present
    # (every cascade always emits ideas in some tier).
    tier_labels = {t.tier for t in validation_report.tiers}
    assert tier_labels, "validation report has no conviction-tier breakdown"
    assert tier_labels.intersection(_CONVICTION_LABELS), (
        f"no recognised conviction tier in the breakdown — got {tier_labels}"
    )

    # Every tier's hit rates are in [0, 1].
    for tier in validation_report.tiers:
        assert 0.0 <= tier.hit_rate <= 1.0, tier.tier
        assert 0.0 <= tier.baseline_hit_rate <= 1.0, tier.tier
        assert tier.n_signals >= 0
        assert tier.n_observations >= 0

    # Per-signal sanity: hits cannot exceed observations.
    for sig in validation_report.signals:
        assert 0 <= sig.n_hits <= sig.n_observations
        assert 0.0 <= sig.hit_rate <= 1.0


def test_build_validation_report_runs_full_pipeline(
    stress_report: ShippingStressReport,
    exposure_matrix: list[CommodityExposure],
    stock_data: dict[str, pd.DataFrame],
) -> None:
    """``build_validation_report`` drives the whole chain end-to-end.

    This is the convenience wrapper the UI uses — it runs the cascade scorer
    and commodity-shipping analyser against the same inputs the upstream
    stages produced, then forwards everything into ``validate_signals``. A
    fresh end-to-end call against the same stress report and exposure matrix
    must produce a coherent, in-bounds report.
    """
    rep = build_validation_report(stress_report, exposure_matrix, stock_data)

    assert isinstance(rep, ValidationReport)
    assert rep.n_signals_validated > 0
    assert 0.0 <= rep.overall_hit_rate <= 1.0
    assert 0.0 <= rep.overall_baseline_hit_rate <= 1.0
    # The default forward horizon (~1 trading month) must be preserved.
    assert rep.forward_days == 21
    # Provenance is stamped — the report carries its DataSource.
    assert rep.source is not None
