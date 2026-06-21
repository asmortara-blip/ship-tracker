"""tests/test_cascade_ribbon.py — the component → route → ticker cascade ribbon.

Defining properties of ``processing.cascade_ribbon.to_cascade_sankey``:

  * a hand-built attribution + ideas yields a VALID Plotly Sankey dict —
    source/target/value share one length, every index is in range, every value
    is strictly positive;
  * the full chokepoint → route → ticker chain is present;
  * duplicate edges are collapsed (their values summed);
  * empty / None inputs yield the all-empty dict and never crash;
  * a node never points to itself.
"""
from __future__ import annotations

from processing.cascade_ribbon import to_cascade_sankey
from processing.disruption_cascade import CascadeLink, EquityIdea
from processing.ssi_attribution import (
    ComponentContribution,
    RouteContribution,
    SSIAttributionReport,
)


# ---------------------------------------------------------------------------
# Hand-built fixtures
# ---------------------------------------------------------------------------


def _attribution() -> SSIAttributionReport:
    """A two-route attribution whose drivers map to two components.

    ``transpacific_eb`` is driven by "Chokepoint disruption" → the
    ``chokepoint`` component; ``asia_europe`` by "Port congestion" → the
    ``congestion`` component.
    """
    return SSIAttributionReport(
        ssi_total=0.55,
        ssi_label="Stressed",
        component_contributions=[
            ComponentContribution(
                component="chokepoint", raw_score=0.7, weight=0.29,
                weighted=0.203, pct_share=0.6,
            ),
            ComponentContribution(
                component="congestion", raw_score=0.5, weight=0.20,
                weighted=0.10, pct_share=0.3,
            ),
        ],
        route_contributions=[
            RouteContribution(
                route_id="transpacific_eb", route_name="Transpacific EB",
                stress_score=0.62, route_weight=2.0, weighted=1.24,
                pct_share=0.7, dominant_driver="Chokepoint disruption",
            ),
            RouteContribution(
                route_id="asia_europe", route_name="Asia–Europe",
                stress_score=0.48, route_weight=2.0, weighted=0.96,
                pct_share=0.3, dominant_driver="Port congestion",
            ),
        ],
        top_component="chokepoint",
        top_route="Transpacific EB (Chokepoint disruption)",
    )


def _ideas() -> list[EquityIdea]:
    """Two ideas whose cascade chains run through the attribution's routes."""
    return [
        EquityIdea(
            ticker="ZIM", company_name="ZIM", direction="Bullish",
            conviction_score=0.7, conviction_label="High", thesis="…",
            cascade_chain=[
                CascadeLink(
                    route_id="transpacific_eb", route_stress=0.62,
                    hs_category="electronics", cargo_share=0.3,
                    commodity_signal="Bullish", contribution=0.12,
                ),
                CascadeLink(
                    route_id="asia_europe", route_stress=0.48,
                    hs_category="apparel", cargo_share=0.2,
                    commodity_signal="Neutral", contribution=0.05,
                ),
            ],
        ),
        EquityIdea(
            ticker="MATX", company_name="MATX", direction="Bullish",
            conviction_score=0.5, conviction_label="Moderate", thesis="…",
            cascade_chain=[
                CascadeLink(
                    route_id="transpacific_eb", route_stress=0.62,
                    hs_category="electronics", cargo_share=0.25,
                    commodity_signal="Bullish", contribution=0.08,
                ),
            ],
        ),
    ]


# ---------------------------------------------------------------------------
# Validity of the Sankey dict
# ---------------------------------------------------------------------------


def test_sankey_is_structurally_valid() -> None:
    s = to_cascade_sankey(_attribution(), _ideas())

    # All four keys present.
    assert set(s) == {"labels", "source", "target", "value"}

    labels, source, target, value = (
        s["labels"], s["source"], s["target"], s["value"],
    )
    # Link arrays share one length.
    assert len(source) == len(target) == len(value)
    assert len(source) > 0

    # Every index is in range.
    n = len(labels)
    assert all(0 <= i < n for i in source)
    assert all(0 <= i < n for i in target)

    # Every flow is strictly positive.
    assert all(v > 0 for v in value)


def test_node_never_points_to_itself() -> None:
    s = to_cascade_sankey(_attribution(), _ideas())
    assert all(src != tgt for src, tgt in zip(s["source"], s["target"]))


def test_chokepoint_route_ticker_chain_present() -> None:
    """The full chokepoint → route → ticker path must be traceable."""
    s = to_cascade_sankey(_attribution(), _ideas())
    labels = s["labels"]
    edges = set(zip(s["source"], s["target"]))

    def idx(label: str) -> int:
        assert label in labels, f"{label!r} not in {labels}"
        return labels.index(label)

    chokepoint = idx("chokepoint")
    route = idx("Transpacific EB")
    zim = idx("ZIM")

    # component → route and route → ticker edges both exist.
    assert (chokepoint, route) in edges
    assert (route, zim) in edges


def test_route_node_is_shared_between_halves() -> None:
    """The same route_id must be ONE node carrying inbound + outbound ribbons."""
    s = to_cascade_sankey(_attribution(), _ideas())
    labels = s["labels"]
    # "Transpacific EB" should appear exactly once as a label (the route node
    # is reused across the component-half and the idea-half).
    assert labels.count("Transpacific EB") == 1

    route = labels.index("Transpacific EB")
    has_inbound = any(t == route for t in s["target"])
    has_outbound = any(src == route for src in s["source"])
    assert has_inbound and has_outbound


# ---------------------------------------------------------------------------
# Duplicate-edge collapsing
# ---------------------------------------------------------------------------


def test_duplicate_edges_are_summed() -> None:
    """Two cascade links on the same route → same ticker collapse to one edge
    whose value is the sum."""
    attribution = SSIAttributionReport(
        ssi_total=0.5, ssi_label="Stressed",
        component_contributions=[
            ComponentContribution(
                component="chokepoint", raw_score=0.7, weight=0.29,
                weighted=0.2, pct_share=1.0,
            ),
        ],
        route_contributions=[
            RouteContribution(
                route_id="transpacific_eb", route_name="Transpacific EB",
                stress_score=0.6, route_weight=2.0, weighted=1.2,
                pct_share=1.0, dominant_driver="Chokepoint disruption",
            ),
        ],
    )
    ideas = [
        EquityIdea(
            ticker="ZIM", company_name="ZIM", direction="Bullish",
            conviction_score=0.7, conviction_label="High", thesis="…",
            cascade_chain=[
                CascadeLink(
                    route_id="transpacific_eb", route_stress=0.6,
                    hs_category="electronics", cargo_share=0.3,
                    commodity_signal="Bullish", contribution=0.10,
                ),
                # Same route → same ticker; a second hop (different commodity).
                CascadeLink(
                    route_id="transpacific_eb", route_stress=0.6,
                    hs_category="machinery", cargo_share=0.2,
                    commodity_signal="Bullish", contribution=0.04,
                ),
            ],
        ),
    ]
    s = to_cascade_sankey(attribution, ideas)
    labels = s["labels"]
    route = labels.index("Transpacific EB")
    zim = labels.index("ZIM")

    # Exactly one route→ticker edge, value == 0.10 + 0.04.
    route_ticker = [
        v for src, tgt, v in zip(s["source"], s["target"], s["value"])
        if src == route and tgt == zim
    ]
    assert len(route_ticker) == 1
    assert abs(route_ticker[0] - 0.14) < 1e-9


# ---------------------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------------------


def _assert_empty(s: dict) -> None:
    assert s == {"labels": [], "source": [], "target": [], "value": []}


def test_both_none_is_empty() -> None:
    _assert_empty(to_cascade_sankey(None, None))


def test_empty_attribution_and_ideas_is_empty() -> None:
    _assert_empty(
        to_cascade_sankey(
            SSIAttributionReport(ssi_total=0.0, ssi_label="Calm"), [],
        )
    )


def test_attribution_only_with_no_ideas_still_chains_components_to_routes() -> None:
    """With routes but no ideas, the component → route half still renders."""
    s = to_cascade_sankey(_attribution(), [])
    assert s["labels"], "component→route half should produce nodes"
    # No ticker node should appear (no ideas).
    assert "ZIM" not in s["labels"]
    assert "MATX" not in s["labels"]
    # Edges are all component → route.
    assert len(s["source"]) > 0


def test_ideas_only_with_no_attribution_still_chains_routes_to_tickers() -> None:
    """With ideas but an empty attribution, the route → ticker half renders;
    route nodes fall back to the bare route_id label."""
    s = to_cascade_sankey(
        SSIAttributionReport(ssi_total=0.0, ssi_label="Calm"), _ideas(),
    )
    assert "ZIM" in s["labels"]
    # Route node falls back to route_id (no attribution name available).
    assert "transpacific_eb" in s["labels"]
    assert len(s["source"]) > 0


def test_zero_and_negative_flows_are_dropped() -> None:
    """A zero-weight route and a non-positive contribution produce no edge."""
    attribution = SSIAttributionReport(
        ssi_total=0.3, ssi_label="Elevated",
        component_contributions=[
            ComponentContribution(
                component="chokepoint", raw_score=0.0, weight=0.29,
                weighted=0.0, pct_share=0.0,
            ),
        ],
        route_contributions=[
            RouteContribution(
                route_id="transpacific_eb", route_name="Transpacific EB",
                stress_score=0.0, route_weight=2.0, weighted=0.0,  # zero flow
                pct_share=0.0, dominant_driver="Chokepoint disruption",
            ),
        ],
    )
    ideas = [
        EquityIdea(
            ticker="ZIM", company_name="ZIM", direction="Neutral",
            conviction_score=0.0, conviction_label="Watch", thesis="…",
            cascade_chain=[
                CascadeLink(
                    route_id="transpacific_eb", route_stress=0.0,
                    hs_category="electronics", cargo_share=0.0,
                    commodity_signal="Neutral", contribution=0.0,  # zero flow
                ),
            ],
        ),
    ]
    _assert_empty(to_cascade_sankey(attribution, ideas))
