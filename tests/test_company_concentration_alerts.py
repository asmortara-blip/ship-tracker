"""Defining-property tests for processing/company_concentration_alerts.py."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from processing.company_concentration_alerts import (
    CONCENTRATION_BANDS,
    CompanyConcentrationAlert,
    compute_concentration_alerts,
    concentration_band,
)


# ── Fixture helpers ─────────────────────────────────────────────────────


@dataclass
class _StubPort:
    """Mimics the per-port entry CompanyPortFootprint exposes."""
    locode: str
    share_within_company: float


@dataclass
class _StubFootprint:
    """Mimics CompanyPortFootprint for the parts our module touches."""
    ticker: str
    port_count: int
    ports: list = field(default_factory=list)


def _diversified(ticker: str, n: int = 4) -> _StubFootprint:
    """n ports each carrying 1/n share → HHI = n × (1/n)² = 1/n."""
    share = 1.0 / n
    ports = [_StubPort(locode=f"P{i}", share_within_company=share) for i in range(n)]
    return _StubFootprint(ticker=ticker, port_count=n, ports=ports)


def _concentrated(ticker: str, dominant_share: float = 0.7) -> _StubFootprint:
    """Three-port footprint where the first dominates."""
    rem = (1.0 - dominant_share) / 2
    ports = [
        _StubPort(locode="DOM", share_within_company=dominant_share),
        _StubPort(locode="ALT", share_within_company=rem),
        _StubPort(locode="OTH", share_within_company=rem),
    ]
    return _StubFootprint(ticker=ticker, port_count=3, ports=ports)


# ── Band classifier ─────────────────────────────────────────────────────


def test_concentration_band_uses_lower_bound_inclusive() -> None:
    """Each band's lower bound is inclusive — value exactly at boundary
    maps to the band starting at that boundary."""
    assert concentration_band(0.00) == "Diversified"
    assert concentration_band(0.25) == "Moderate"
    assert concentration_band(0.45) == "Concentrated"
    assert concentration_band(0.65) == "Highly Concentrated"
    assert concentration_band(0.85) == "Single-Port Risk"


def test_concentration_band_degrades_defensively_outside_domain() -> None:
    assert concentration_band(-0.5) == "Diversified"
    assert concentration_band(2.0) == "Single-Port Risk"


# ── Empty / degenerate inputs ──────────────────────────────────────────


def test_empty_footprints_returns_empty_alerts() -> None:
    assert compute_concentration_alerts([]) == []


def test_footprint_with_zero_port_count_skipped() -> None:
    fp = _StubFootprint(ticker="ABC", port_count=0, ports=[])
    assert compute_concentration_alerts([fp]) == []


def test_footprint_missing_share_field_skipped() -> None:
    """A footprint whose ports carry no share data → no usable HHI."""
    fp = _StubFootprint(
        ticker="ABC", port_count=2,
        ports=[_StubPort(locode="A", share_within_company=None),  # type: ignore[arg-type]
               _StubPort(locode="B", share_within_company=None)],  # type: ignore[arg-type]
    )
    # ``None`` shares are filtered; the resulting empty shares list short-circuits.
    assert compute_concentration_alerts([fp]) == []


# ── HHI math ────────────────────────────────────────────────────────────


def test_perfectly_diversified_footprint_does_not_fire() -> None:
    """20 equal-share ports → HHI=0.05, below the 0.45 fire threshold."""
    fp = _diversified("ABC", n=20)
    assert compute_concentration_alerts([fp]) == []


def test_single_port_footprint_fires_critical() -> None:
    """One port at 100% share → HHI=1.0 → CRITICAL severity, single-port band."""
    fp = _StubFootprint(
        ticker="ABC", port_count=1,
        ports=[_StubPort(locode="DOM", share_within_company=1.0)],
    )
    alerts = compute_concentration_alerts([fp])
    assert len(alerts) == 1
    assert alerts[0].ticker == "ABC"
    assert alerts[0].hhi == pytest.approx(1.0)
    assert alerts[0].concentration_band == "Single-Port Risk"
    assert alerts[0].severity == "CRITICAL"


def test_moderately_concentrated_footprint_fires_high() -> None:
    """Dominant 70% port → HHI=0.49 + 0.0225*2 ≈ 0.535 → HIGH severity."""
    fp = _concentrated("ABC", dominant_share=0.7)
    alerts = compute_concentration_alerts([fp])
    assert len(alerts) == 1
    assert alerts[0].hhi == pytest.approx(0.535, abs=0.01)
    assert alerts[0].severity == "HIGH"
    assert alerts[0].concentration_band == "Concentrated"


# ── Threshold tuning ───────────────────────────────────────────────────


def test_fire_threshold_raises_to_filter_more_aggressively() -> None:
    """A HHI=0.5 footprint is suppressed when fire_threshold=0.6."""
    fp = _concentrated("ABC", dominant_share=0.7)   # HHI ≈ 0.54
    assert compute_concentration_alerts(
        [fp], fire_threshold_hhi=0.60,
    ) == []


def test_critical_threshold_lower_promotes_high_to_critical() -> None:
    """HHI=0.54 normally HIGH; with critical=0.5 it becomes CRITICAL."""
    fp = _concentrated("ABC", dominant_share=0.7)
    alerts = compute_concentration_alerts(
        [fp], critical_threshold_hhi=0.5,
    )
    assert len(alerts) == 1
    assert alerts[0].severity == "CRITICAL"


# ── Output ordering + body content ──────────────────────────────────────


def test_alerts_sorted_by_hhi_descending() -> None:
    """The worst single-point-of-failure candidate appears first."""
    fps = [
        _concentrated("MILD", dominant_share=0.5),    # lower HHI
        _StubFootprint(
            ticker="WORST", port_count=1,
            ports=[_StubPort(locode="P", share_within_company=1.0)],
        ),
        _concentrated("MEDIUM", dominant_share=0.75),
    ]
    alerts = compute_concentration_alerts(fps)
    hhis = [a.hhi for a in alerts]
    assert hhis == sorted(hhis, reverse=True)
    assert alerts[0].ticker == "WORST"


def test_top_ports_capped_at_default_three() -> None:
    """Even with a 5-port footprint that fires, only top 3 shares
    appear in top_ports. Shares 0.7 / 0.1 / 0.1 / 0.05 / 0.05 →
    HHI = 0.49 + 0.01*2 + 0.0025*2 = 0.515 → above 0.45 fire threshold."""
    ports = [
        _StubPort("A", 0.7),
        _StubPort("B", 0.1),
        _StubPort("C", 0.1),
        _StubPort("D", 0.05),
        _StubPort("E", 0.05),
    ]
    fp = _StubFootprint(ticker="ABC", port_count=5, ports=ports)
    alerts = compute_concentration_alerts([fp])
    assert len(alerts) == 1
    assert len(alerts[0].top_ports) == 3
    # And sorted by share desc
    shares_in_body = [s for _locode, s in alerts[0].top_ports]
    assert shares_in_body == sorted(shares_in_body, reverse=True)


def test_top_ports_override_respected() -> None:
    ports = [
        _StubPort("A", 0.7), _StubPort("B", 0.1),
        _StubPort("C", 0.1), _StubPort("D", 0.05), _StubPort("E", 0.05),
    ]
    fp = _StubFootprint(ticker="ABC", port_count=5, ports=ports)
    alerts = compute_concentration_alerts([fp], top_ports_in_body=2)
    assert len(alerts[0].top_ports) == 2


def test_summary_string_includes_ticker_hhi_and_band() -> None:
    fp = _StubFootprint(
        ticker="ZIM", port_count=1,
        ports=[_StubPort(locode="CNSHA", share_within_company=1.0)],
    )
    alerts = compute_concentration_alerts([fp])
    s = alerts[0].summary
    assert "ZIM" in s
    assert "HHI=1.00" in s
    assert "Single-Port Risk" in s
    assert "CNSHA" in s


def test_concentration_prefers_full_footprint_hhi_over_capped_shares() -> None:
    """Regression (#2): HHI must come from the builder's full-footprint value,
    NOT recomputed over the top-N-capped port_exposures. A footprint whose
    *capped* shares look concentrated (50/50 → HHI 0.5, would fire) but whose
    true full-footprint HHI is low must NOT raise a false alert."""
    from types import SimpleNamespace as NS
    from processing.company_concentration_alerts import compute_concentration_alerts

    fp = NS(
        ticker="DIVERSE",
        port_exposures=[
            NS(port_locode="AAA", exposure_weight=5.0),
            NS(port_locode="BBB", exposure_weight=5.0),
        ],
        concentration_hhi=0.08,   # diversified over the FULL (uncapped) footprint
    )
    assert compute_concentration_alerts([fp]) == []


def test_concentration_fires_on_high_precomputed_hhi() -> None:
    """When the builder's full-footprint HHI is genuinely high, it fires and
    that precomputed value drives the band/severity decision."""
    from types import SimpleNamespace as NS
    from processing.company_concentration_alerts import compute_concentration_alerts

    fp = NS(
        ticker="CONC",
        port_exposures=[
            NS(port_locode="AAA", exposure_weight=9.0),
            NS(port_locode="BBB", exposure_weight=1.0),
        ],
        concentration_hhi=0.90,   # single-port-risk over the full footprint
    )
    alerts = compute_concentration_alerts([fp])
    assert len(alerts) == 1
    assert alerts[0].ticker == "CONC"
    assert alerts[0].hhi == 0.90
    assert alerts[0].severity == "CRITICAL"   # >= 0.85 critical threshold


def test_stub_fixture_without_precomputed_hhi_still_computes_from_shares() -> None:
    """Back-compat: a stub footprint (no concentration_hhi) falls back to the
    shares it exposes, so existing fixture-driven behavior is unchanged."""
    from types import SimpleNamespace as NS
    from processing.company_concentration_alerts import compute_concentration_alerts

    fp = NS(
        ticker="STUB",
        ports=[
            NS(locode="AAA", share_within_company=0.95),
            NS(locode="BBB", share_within_company=0.05),
        ],
    )
    alerts = compute_concentration_alerts([fp])
    assert len(alerts) == 1                      # 0.95^2+0.05^2 = 0.905 → fires
    assert alerts[0].severity == "CRITICAL"
