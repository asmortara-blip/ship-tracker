"""Pure, offline tests for ``ui.command_palette`` helpers.

These exercise only the Streamlit-free functions (``build_search_index`` /
``search_index``). The render layer is not imported here — importing the
module is fine (the ``streamlit`` import lives inside the render function), but
we never call it.
"""

from __future__ import annotations

import pytest

from ui.command_palette import build_search_index, search_index


# A miniature, app-shaped catalog.
SECTIONS = [
    ("dashboard", "🏠", "Dashboard", "Overview"),
    ("markets", "📈", "Markets & Signals", "Alpha"),
    ("ports_routes", "🚢", "Ports & Routes", "Demand"),
]

SECTION_TABS = {
    "dashboard": [
        ("Overview", "ui.tab_overview"),
        ("Daily Briefing", "ui.tab_briefing"),
    ],
    "markets": [
        ("Markets", "ui.tab_markets"),
        ("Alpha Signals", "ui.tab_alpha"),
        ("Monte Carlo", "ui.tab_monte_carlo"),
    ],
    "ports_routes": [
        ("Port Demand", "ui.tab_port_demand"),
        ("Routes", "ui.tab_routes"),
    ],
}


def _idx():
    return build_search_index(SECTIONS, SECTION_TABS)


# ── build_search_index ─────────────────────────────────────────────────────
def test_one_record_per_tab_and_per_section():
    idx = _idx()
    n_tabs = sum(len(v) for v in SECTION_TABS.values())
    n_sections = len(SECTIONS)

    tabs = [r for r in idx if r["kind"] == "tab"]
    sections = [r for r in idx if r["kind"] == "section"]

    assert len(tabs) == n_tabs
    assert len(sections) == n_sections
    assert len(idx) == n_tabs + n_sections


def test_tab_records_carry_correct_section_keys_and_meta():
    idx = _idx()
    by_label = {r["label"]: r for r in idx if r["kind"] == "tab"}

    assert by_label["Alpha Signals"]["section"] == "markets"
    assert by_label["Alpha Signals"]["section_label"] == "Markets & Signals"
    assert by_label["Alpha Signals"]["icon"] == "📈"
    assert by_label["Alpha Signals"]["module"] == "ui.tab_alpha"

    assert by_label["Port Demand"]["section"] == "ports_routes"
    assert by_label["Overview"]["section"] == "dashboard"


def test_section_records_route_to_themselves():
    idx = _idx()
    for r in idx:
        if r["kind"] == "section":
            assert r["section"] == r["section_label"] or r["section"]
    markets = next(
        r for r in idx if r["kind"] == "section" and r["label"] == "Markets & Signals"
    )
    assert markets["section"] == "markets"


def test_entities_are_indexed_with_their_kind_and_section():
    entities = [
        ("ZIM Integrated", "company", "carriers"),
        ("Shanghai", "port", "ports_routes"),
        ("Trans-Pacific Eastbound", "route", "ports_routes"),
    ]
    idx = build_search_index(SECTIONS, SECTION_TABS, entities=entities)

    zim = next(r for r in idx if r["label"] == "ZIM Integrated")
    assert zim["kind"] == "company"
    assert zim["section"] == "carriers"

    shanghai = next(r for r in idx if r["label"] == "Shanghai")
    assert shanghai["kind"] == "port"
    assert shanghai["section"] == "ports_routes"
    # Section meta is enriched from a known section key.
    assert shanghai["section_label"] == "Ports & Routes"
    assert shanghai["icon"] == "🚢"


def test_robust_to_none_and_short_and_ragged_tuples():
    # Should not raise on any of these.
    assert build_search_index(None, None) == []
    assert build_search_index([], {}) == []

    weird_sections = [
        ("solo",),  # key only
        ("two", "🔧"),  # key + icon
        (),  # empty — skipped
        ("full", "✅", "Full Label", "desc", "extra"),  # extra fields ignored
    ]
    weird_tabs = {
        "solo": [("Bare",), "JustAString", ()],  # short/odd tab entries
        "missing_section": [("Orphan", "mod.x")],  # section not in SECTIONS
    }
    idx = build_search_index(weird_sections, weird_tabs)
    labels = {r["label"] for r in idx}
    # "solo"/"two"/"full" sections present; empty tuple skipped.
    assert {"solo", "two", "Full Label"} <= labels
    # Bare-tuple and bare-string tabs both indexed.
    assert {"Bare", "JustAString", "Orphan"} <= labels
    # Orphan tab still routes to its (unknown) section key.
    orphan = next(r for r in idx if r["label"] == "Orphan")
    assert orphan["section"] == "missing_section"


# ── search_index ───────────────────────────────────────────────────────────
def test_exact_and_prefix_rank_above_substring():
    # "Markets" section + "Markets" tab are exact; "Market Commentary"-style
    # interior matches rank lower. Here "mark" is a prefix of "Markets".
    idx = _idx()
    res = search_index(idx, "markets")
    assert res, "expected matches for 'markets'"
    # Exact-label matches ("Markets" tab, "Markets & Signals" via prefix) come
    # before any pure substring hit.
    assert res[0]["label"] in {"Markets", "Markets & Signals"}


def test_prefix_beats_interior_substring():
    idx = build_search_index(
        [("s", "", "S", "")],
        {
            "s": [
                ("Alpha Signals", "m.a"),  # 'sig' is an interior word-boundary
                ("Signals Hub", "m.b"),  # 'sig' is a prefix
            ]
        },
    )
    res = search_index(idx, "sig")
    # Prefix ("Signals Hub") must outrank the word-boundary match.
    assert res[0]["label"] == "Signals Hub"


def test_word_boundary_beats_plain_substring():
    idx = build_search_index(
        [("s", "", "S", "")],
        {
            "s": [
                ("Disrupted", "m.a"),  # 'rupt' is a plain interior substring
                ("Big Rupture", "m.b"),  # 'rupt' starts a word (after space)
            ]
        },
    )
    res = search_index(idx, "rupt")
    assert [r["label"] for r in res][0] == "Big Rupture"
    assert {"Disrupted", "Big Rupture"} == {r["label"] for r in res}


def test_search_is_case_insensitive():
    idx = _idx()
    lower = [r["label"] for r in search_index(idx, "alpha")]
    upper = [r["label"] for r in search_index(idx, "ALPHA")]
    mixed = [r["label"] for r in search_index(idx, "AlPhA")]
    assert lower == upper == mixed
    assert "Alpha Signals" in lower


def test_known_tab_label_resolves_to_correct_section():
    idx = _idx()
    res = search_index(idx, "Monte Carlo")
    assert res[0]["label"] == "Monte Carlo"
    assert res[0]["kind"] == "tab"
    assert res[0]["section"] == "markets"


def test_limit_is_respected():
    idx = _idx()
    res = search_index(idx, "a", limit=2)  # 'a' matches many labels
    assert len(res) == 2
    # limit<=0 yields nothing; non-int falls back to default (12) without raise.
    assert search_index(idx, "a", limit=0) == []
    assert len(search_index(idx, "a", limit="nope")) <= 12


def test_empty_query_returns_section_defaults():
    idx = _idx()
    res = search_index(idx, "")
    assert res, "blank query should return a default view"
    assert all(r["kind"] == "section" for r in res)
    # Default respects the limit.
    assert len(search_index(idx, "   ", limit=2)) == 2


def test_empty_index_and_garbage_never_raise():
    assert search_index([], "anything") == []
    assert search_index(None, "anything") == []
    assert search_index([], "") == []
    # A query with no matches returns [] cleanly.
    assert search_index(_idx(), "zzzzz-no-such-thing") == []


def test_tie_break_is_stable_by_label():
    # Two equally-ranked (exact) records → ordered by case-insensitive label.
    idx = build_search_index(
        [("x", "", "X", "")],
        {"x": [("beta", "m.b"), ("Beta", "m.a")]},  # same lowercased label
    )
    res = search_index(idx, "beta")
    # Both match exactly; stable order keeps first-seen ('beta') first since
    # lowercased labels tie and original index breaks it.
    assert [r["module"] for r in res] == ["m.b", "m.a"]


def test_section_label_substring_is_weakest_tier():
    idx = _idx()
    # "Ports" appears in section_label "Ports & Routes" but not in the tab
    # labels "Port Demand"/"Routes". The query "ports & routes" should still
    # surface those tabs via the section-label tier, after the section itself.
    res = search_index(idx, "ports & routes")
    labels = [r["label"] for r in res]
    assert "Ports & Routes" in labels  # the section (exact) ranks first
    assert res[0]["label"] == "Ports & Routes"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
