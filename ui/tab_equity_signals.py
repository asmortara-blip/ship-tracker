"""tab_equity_signals.py — Equity Signals (Disruption Alpha · stage 5, final).

The conclusion of the Disruption Alpha chain: physical voyage disruption has
been detected (Disruption Radar), projected to a macro read (Macro Projection),
and linked through commodities to exposed companies (Supply Linkage). This tab
turns that linkage into ranked, **fully traceable** candidate equity ideas.

Every idea is transparent, rule-based output — never a black box. The
conviction score is a documented weighted sum and every term is surfaced; the
reasoning path is reproduced hop-by-hop in the per-idea cascade table. Framing
is strictly Bullish / Bearish / Neutral with a rationale — never Buy / Sell and
never a price target. An unconditional "modeled — not investment advice" banner
sits above the fold.

Canonical tab pattern (mirrors ``ui/tab_voyage_tracker.py`` — the established
pattern for this feature's tabs):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * ``render(...)`` ends with ``**kwargs`` for argument safety;
  * every section wrapped in try/except + ``logger.exception``;
  * expensive compute is ``@st.cache_data``-wrapped behind a no-arg helper so an
    unhashable dict argument never reaches the cache key;
  * ``source_footer`` at the bottom, data labelled via ``data.quality.DataSource``.

Sections
--------
A. Page header (badge "MODELED") + unconditional not-investment-advice banner
B. Consensus strip — cascade_summary via metric_card_row, with a directional
   conviction-distribution rail beneath it
C. Ranked EquityIdea cards — insight_card_html, conviction-sorted, each with a
   numbered rank chip and a traceable-detail expander
D. Per-idea expander — facts strip + cascade chain as a wsj_market_table
   (traceable rationale) + driving routes / commodities / signals / risk flags
E. Source footer — DataSource.modeled(...)
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

# Single source of truth for palette, typography, and component helpers.
# Never redeclare color constants in a tab module — always import them.
from ui.styles import (
    C_ACCENT,
    C_CONV,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_RULE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    badge,
    insight_card_html,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# Cascade chains can run 50+ hops; only the strongest links are worth showing.
_MAX_CASCADE_HOPS: int = 10


# ── Domain-specific color mappings ──────────────────────────────────────────
# Semantic direction / label -> palette color. Palette constants themselves
# live in ui/styles.py and are imported above, never redeclared here.

_DIRECTION_COLOR: dict[str, str] = {
    "Bullish": C_HIGH,
    "Bearish": C_LOW,
    "Neutral": C_TEXT2,
}

_CONVICTION_COLOR: dict[str, str] = {
    "High":     C_HIGH,
    "Moderate": C_MOD,
    "Low":      C_TEXT2,
    "Watch":    C_TEXT3,
}


def _direction_color(direction: str) -> str:
    return _DIRECTION_COLOR.get(direction, C_TEXT2)


def _conviction_color(label: str) -> str:
    return _CONVICTION_COLOR.get(label, C_TEXT3)


def _signal_color(signal: str) -> str:
    """Color a commodity / ETF signal string."""
    return _DIRECTION_COLOR.get(signal, C_TEXT2)


def _change_color(pct: float) -> str:
    if pct > 0:
        return C_HIGH
    if pct < 0:
        return C_LOW
    return C_TEXT2


# ── Cell formatters for the WSJ market table ────────────────────────────────
# wsj_market_table renders each cell string as raw HTML inside a <td>. The
# table CSS handles alignment and rule lines; these helpers only style content
# (font family + conditional color). Mirrors ui/tab_voyage_tracker.py.

def _RGBA(hex_color: str, alpha: float) -> str:
    """Convert an imported HEX palette constant to an rgba() string.

    The palette constants are HEX-only; this keeps every tint, border and
    micro-bar in sync with them without ever redeclaring a color. Mirrors
    ``ui.styles._hex_to_rgba`` (kept local so nothing private is imported).
    """
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _eyebrow(text: str) -> str:
    """Return HTML for a small uppercase eyebrow label (kicker line).

    The platform's recurring micro-detail above strips and panels: a thin,
    wide-tracked kicker in the dark-gray text tone.
    """
    return (
        f'<span style="font-family:var(--sans);font-size:0.66rem;'
        f'font-weight:700;color:{C_TEXT3};text-transform:uppercase;'
        f'letter-spacing:0.11em;">{text}</span>'
    )


def _mono(value: str, color: str = C_TEXT) -> str:
    """Monospace numeric cell content."""
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    """Sans-serif cell content."""
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _contribution_cell(value: float, share: float) -> str:
    """Render a contribution cell: the value plus a thin proportional micro-bar.

    ``share`` is the hop's contribution as a fraction of the strongest hop in
    the same table — it turns a column of bare decimals into a visual ranking
    without leaving the steel-blue accent.
    """
    pct = max(0.0, min(1.0, share)) * 100.0
    return (
        f'<span style="display:inline-flex;align-items:center;gap:8px;'
        f'justify-content:flex-end;">'
        f'<span style="flex:0 0 38px;height:4px;border-radius:2px;'
        f'background:{_RGBA(C_ACCENT, 0.14)};overflow:hidden;">'
        f'<span style="display:block;height:100%;width:{pct:.0f}%;'
        f'background:{_RGBA(C_ACCENT, 0.85)};"></span></span>'
        f'<span style="font-family:var(--mono);color:{C_ACCENT};'
        f'font-weight:600;font-variant-numeric:tabular-nums;">'
        f'{value:.3f}</span></span>'
    )


def _render_empty_note(title: str, body: str) -> None:
    """Render a small, calm empty-state panel — used for edge cases in detail.

    A quieter alternative to ``st.info`` for in-card edge states: a bordered
    card on the surface tone with an eyebrow title, so a missing sub-section
    still looks intentional rather than broken.
    """
    st.markdown(
        f'<div style="background:{_RGBA(C_TEXT3, 0.04)};'
        f'border:1px dashed {C_RULE};border-radius:8px;'
        f'padding:14px 16px;margin:8px 0;">'
        f'<div style="margin-bottom:3px;">{_eyebrow(title)}</div>'
        f'<div style="font-family:var(--sans);font-size:0.8rem;'
        f'color:{C_TEXT2};line-height:1.5;">{body}</div></div>',
        unsafe_allow_html=True,
    )


# ── Modeled inputs ──────────────────────────────────────────────────────────
# build_voyage_fleet is self-contained (seeded from the current date) and so is
# safe to cache behind a no-arg helper. compute_shipping_stress /
# build_exposure_matrix / score_equity_ideas all take dict arguments whose
# values are pandas DataFrames — those dicts are unhashable and must NOT reach
# an @st.cache_data key, so they are called directly inside render().

@st.cache_data(ttl=3600, show_spinner=False)
def _load_voyage_fleet() -> list:
    """Build (and cache) the modeled voyage fleet.

    The fleet is seeded from the current date inside ``build_voyage_fleet``, so
    it is stable within a session and refreshes day to day. Any failure
    degrades to an empty fleet — the stress index simply reports 0 delayed
    voyages and ideas degrade gracefully.
    """
    try:
        from data.voyage_dataset import build_voyage_fleet
        return build_voyage_fleet()
    except Exception:
        logger.exception("Equity Signals — voyage fleet build failed")
        return []


def _build_ideas(
    stock_data: dict | None,
    freight_data: dict | None,
    macro_data: dict | None,
    port_results,
    route_results,
    insights,
) -> list:
    """Run the full Disruption Alpha cascade and return ranked ``EquityIdea``s.

    Builds the Shipping Stress Index, the company↔commodity exposure matrix,
    then scores one idea per tracked shipping ticker. Every upstream module
    already tolerates empty / None inputs and returns neutral defaults, so this
    wrapper only adds a final guard: any unexpected error degrades to an empty
    list and the tab shows a friendly notice rather than crashing.
    """
    try:
        from processing.disruption_cascade import score_equity_ideas
        from processing.exposure_matrix import build_exposure_matrix
        from processing.shipping_stress_index import compute_shipping_stress
    except ImportError:
        logger.exception("Equity Signals — cascade modules unavailable")
        return []

    try:
        stress_report = compute_shipping_stress(
            freight_data or {},
            macro_data or {},
            list(port_results) if port_results else [],
            list(route_results) if route_results else [],
            voyage_fleet=_load_voyage_fleet(),
        )
    except Exception:
        logger.exception("Equity Signals — shipping stress compute failed")
        stress_report = None

    try:
        # build_exposure_matrix is a cheap pure derivation (the heavy
        # COMPANY_COMMODITY_EXPOSURE matrix is computed once at import); it
        # tolerates an empty / partial stock_data dict. Called directly —
        # never through @st.cache_data, since stock_data is unhashable.
        exposure_matrix = build_exposure_matrix(stock_data or {})
    except Exception:
        logger.exception("Equity Signals — exposure matrix build failed")
        exposure_matrix = []

    try:
        return score_equity_ideas(
            stress_report,
            exposure_matrix,
            stock_data or {},
            insights=insights,
        )
    except Exception:
        logger.exception("Equity Signals — idea scoring failed")
        return []


# ── Shared micro-components ─────────────────────────────────────────────────

def _rank_chip(rank: int, color: str) -> str:
    """Return HTML for a numbered conviction-rank chip (#1, #2, …)."""
    bg   = _RGBA(color, 0.12)
    bord = _RGBA(color, 0.30)
    return (
        f'<span style="display:inline-flex;align-items:center;'
        f'justify-content:center;min-width:30px;height:24px;padding:0 7px;'
        f'background:{bg};border:1px solid {bord};border-radius:5px;'
        f'font-family:var(--mono);font-size:0.76rem;font-weight:700;'
        f'color:{color};letter-spacing:0.02em;">#{rank}</span>'
    )


# ── Section B: consensus strip ──────────────────────────────────────────────

def _render_distribution_rail(
    bullish: int, neutral: int, bearish: int, total: int
) -> None:
    """Render a thin three-segment rail of the Bullish/Neutral/Bearish split.

    A compact, at-a-glance read on cascade breadth that complements the KPI
    cards above it — proportional segments, a legend with counts, all on the
    existing palette (green / mid-gray / red).
    """
    if total <= 0:
        return

    segs = (
        ("Bullish", bullish, C_HIGH),
        ("Neutral", neutral, C_TEXT2),
        ("Bearish", bearish, C_LOW),
    )
    bars = "".join(
        f'<div title="{name}: {count}" style="flex:{max(count, 0.001)};'
        f'background:{_RGBA(color, 0.85)};height:7px;min-width:'
        f'{3 if count else 0}px;"></div>'
        for name, count, color in segs
    )
    legend = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'font-family:var(--sans);font-size:0.72rem;color:{C_TEXT2};">'
        f'<span style="width:8px;height:8px;border-radius:2px;'
        f'background:{color};"></span>{name}'
        f'<span style="font-family:var(--mono);color:{C_TEXT};'
        f'font-weight:600;">{count}</span></span>'
        for name, count, color in segs
    )
    st.markdown(
        f'<div style="margin:14px 0 4px;">'
        f'<div style="display:flex;align-items:baseline;'
        f'justify-content:space-between;margin-bottom:7px;">'
        f'{_eyebrow("Cascade Breadth")}'
        f'<span style="font-family:var(--mono);font-size:0.72rem;'
        f'color:{C_TEXT3};">{total} ideas</span></div>'
        f'<div style="display:flex;gap:1px;border-radius:4px;'
        f'overflow:hidden;background:{C_RULE};">{bars}</div>'
        f'<div style="display:flex;flex-wrap:wrap;gap:18px;'
        f'margin-top:9px;">{legend}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_consensus_strip(ideas: list) -> None:
    """Render the headline consensus strip from ``cascade_summary``."""
    from processing.disruption_cascade import cascade_summary

    summary = cascade_summary(ideas)

    net_signal = summary["net_signal"]
    bullish    = summary["bullish_count"]
    bearish    = summary["bearish_count"]
    neutral    = summary["neutral_count"]
    top_ticker = summary["top_ticker"]
    top_idea   = summary["top_idea"]
    avg_conv   = summary["avg_conviction"]
    high_count = summary["high_conviction_count"]
    total      = summary["total"]

    top_dir = top_idea.direction if top_idea is not None else "Neutral"

    section_header(
        "Cascade Consensus",
        "Aggregate read across every scored idea — the net signal, "
        "the directional split, and average conviction",
    )

    metric_card_row(
        [
            {
                "label":    "Net Cascade Signal",
                "value":    net_signal,
                "accent":   _direction_color(net_signal),
                "sublabel": f"{total} idea{'s' if total != 1 else ''} scored",
            },
            {
                "label":    "Bullish Ideas",
                "value":    str(bullish),
                "accent":   C_HIGH,
                "sublabel": "capacity-tight / rate-positive",
            },
            {
                "label":    "Bearish Ideas",
                "value":    str(bearish),
                "accent":   C_LOW,
                "sublabel": f"{neutral} neutral",
            },
            {
                "label":    "Top Idea",
                "value":    top_ticker or "—",
                "accent":   _direction_color(top_dir),
                "sublabel": f"{top_dir} · highest conviction",
            },
            {
                "label":    "Avg Conviction",
                "value":    f"{avg_conv * 100:.0f}%",
                "accent":   C_CONV,
                "sublabel": f"{high_count} high-conviction",
            },
        ],
        columns=5,
    )

    _render_distribution_rail(bullish, neutral, bearish, total)


# ── Section D: per-idea cascade detail ──────────────────────────────────────

def _render_cascade_chain(idea) -> None:
    """Render one idea's cascade chain as a route → commodity → contribution table.

    ``cascade_chain`` can carry 50+ hops; only the top
    :data:`_MAX_CASCADE_HOPS` links by ``contribution`` are shown, with a
    caption stating how many of the total are displayed.
    """
    from routes.route_registry import ROUTES_BY_ID

    chain = list(idea.cascade_chain or [])
    if not chain:
        _render_empty_note(
            "No traceable stress path",
            "This idea carries no cascade links — no stressed lane connects "
            "it to the disruption signal.",
        )
        return

    total_hops = len(chain)
    top_links = sorted(
        chain, key=lambda lk: lk.contribution, reverse=True
    )[:_MAX_CASCADE_HOPS]
    shown = len(top_links)

    # The contribution column is what the chain ranks on; surface its scale so
    # each row reads as a fraction of the strongest hop.
    max_contribution = max((lk.contribution for lk in top_links), default=0.0)

    rows = []
    for hop, link in enumerate(top_links, start=1):
        route = ROUTES_BY_ID.get(link.route_id)
        route_name = route.name if route else link.route_id
        share = (
            link.contribution / max_contribution if max_contribution else 0.0
        )
        rows.append([
            _sans(f"{hop:02d}", color=C_TEXT3, weight=600),
            _sans(str(route_name)[:26], color=C_TEXT, weight=700),
            _mono(f"{link.route_stress * 100:.0f}%", color=C_MOD),
            _sans(str(link.hs_category)[:24], color=C_TEXT2),
            _mono(f"{link.cargo_share * 100:.0f}%", color=C_TEXT2),
            badge(link.commodity_signal,
                  color=_signal_color(link.commodity_signal)),
            _contribution_cell(link.contribution, share),
        ])

    wsj_market_table(
        headers=[
            "Hop", "Route", "Route Stress", "Commodity",
            "Cargo Share", "Commodity Signal", "Contribution",
        ],
        rows=rows,
    )
    if shown < total_hops:
        caption = (
            f"Showing the {shown} strongest of {total_hops} cascade hops, "
            "ranked by contribution to the conviction score."
        )
    else:
        caption = (
            f"Showing all {total_hops} cascade "
            f"hop{'s' if total_hops != 1 else ''}, "
            "ranked by contribution to the conviction score."
        )
    st.caption(caption)


def _render_tag_row(label: str, items: list, color: str) -> None:
    """Render a label followed by a wrapped row of small badges.

    The label sits in a fixed-width gutter so successive tag rows line their
    chips up on a shared left edge — a small alignment detail that reads as
    deliberate.
    """
    items = [str(it) for it in (items or []) if str(it).strip()]
    if not items:
        return
    chips = "".join(
        f'<span style="margin:0 5px 5px 0;display:inline-block;">'
        f'{badge(it, color=color)}</span>'
        for it in items
    )
    st.markdown(
        f'<div style="display:flex;align-items:flex-start;gap:12px;'
        f'margin:9px 0;">'
        f'<span style="flex:0 0 140px;font-family:var(--sans);'
        f'font-size:0.68rem;font-weight:700;color:{C_TEXT3};'
        f'text-transform:uppercase;letter-spacing:0.08em;'
        f'padding-top:3px;">{label}</span>'
        f'<span style="flex:1;">{chips}</span></div>',
        unsafe_allow_html=True,
    )


def _render_signal_list(label: str, items: list, color: str) -> None:
    """Render an accent-keyed list of free-text signal strings.

    Each entry gets a thin accent tick rather than a default bullet, so the
    list keeps the platform's restrained, ruled aesthetic.
    """
    items = [str(it) for it in (items or []) if str(it).strip()]
    if not items:
        return
    rows = "".join(
        f'<div style="display:flex;gap:9px;margin:4px 0;align-items:baseline;">'
        f'<span style="flex:0 0 3px;align-self:stretch;border-radius:1px;'
        f'background:{_RGBA(color, 0.55)};"></span>'
        f'<span style="font-family:var(--sans);font-size:0.8rem;'
        f'line-height:1.5;color:{C_TEXT2};">{it}</span></div>'
        for it in items
    )
    st.markdown(
        f'<div style="margin:11px 0 4px;">'
        f'<div style="margin-bottom:6px;">'
        f'<span style="font-family:var(--sans);font-size:0.68rem;'
        f'font-weight:700;color:{color};text-transform:uppercase;'
        f'letter-spacing:0.08em;">{label}</span>'
        f'<span style="font-family:var(--mono);font-size:0.68rem;'
        f'color:{C_TEXT3};margin-left:7px;">{len(items)}</span></div>'
        f'{rows}</div>',
        unsafe_allow_html=True,
    )


def _render_subhead(text: str) -> None:
    """Render a compact in-expander sub-heading with a leading accent tick.

    Lighter than ``section_header`` (which carries its own top rule) — used to
    structure the stacked sub-sections inside a single idea's detail block.
    """
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:8px;'
        f'margin:18px 0 8px;">'
        f'<span style="width:3px;height:13px;border-radius:1px;'
        f'background:{C_ACCENT};"></span>'
        f'<span style="font-family:var(--serif);font-size:0.9rem;'
        f'font-weight:700;color:{C_TEXT};">{text}</span></div>',
        unsafe_allow_html=True,
    )


def _render_idea_detail(idea) -> None:
    """Render the full traceable detail block for one idea inside an expander."""
    # ── Idea snapshot — direction, conviction, and price context ────────────
    _render_subhead("Idea Snapshot")

    price_str = f"${idea.price:,.2f}" if idea.price else "n/a"
    change_str = (
        f"{idea.change_30d:+.1f}%" if idea.price else "n/a"
    )
    facts = [
        [
            _sans("Direction", color=C_TEXT2),
            badge(idea.direction, color=_direction_color(idea.direction)),
        ],
        [
            _sans("Conviction", color=C_TEXT2),
            _mono(f"{idea.conviction_score * 100:.0f}%  ",
                  color=_conviction_color(idea.conviction_label))
            + badge(idea.conviction_label,
                    color=_conviction_color(idea.conviction_label)),
        ],
        [
            _sans("Latest Close", color=C_TEXT2),
            _mono(price_str, color=C_TEXT),
        ],
        [
            _sans("30-Day Move", color=C_TEXT2),
            _mono(change_str,
                  color=_change_color(idea.change_30d) if idea.price else C_TEXT2),
        ],
    ]
    wsj_market_table(headers=["Metric", "Value"], rows=facts)

    # ── Traceable rationale — the cascade chain ─────────────────────────────
    _render_subhead("Cascade Rationale")
    st.caption(
        "Route → commodity → contribution — the fully traceable signal path "
        "behind this idea's conviction score."
    )
    _render_cascade_chain(idea)

    # ── Drivers & caveats — the supporting decomposition ────────────────────
    _render_subhead("Drivers & Caveats")
    has_detail = any((
        idea.driving_routes, idea.driving_commodities,
        idea.supporting_signals, idea.risk_flags,
    ))
    if not has_detail:
        _render_empty_note(
            "No decomposed drivers",
            "This idea has no driving routes, commodities, or supporting "
            "signals beyond the cascade chain above.",
        )
        return

    _render_tag_row("Driving Routes", idea.driving_routes, C_ACCENT)
    _render_tag_row("Driving Commodities", idea.driving_commodities, C_MACRO)
    _render_signal_list("Supporting Signals", idea.supporting_signals, C_HIGH)
    _render_signal_list("Risk Flags", idea.risk_flags, C_LOW)


# ── Section C + D: ranked idea cards ────────────────────────────────────────

def _render_card_rank_line(rank: int, idea) -> None:
    """Render the thin rank line that sits above each idea card.

    Carries the conviction rank (#1, #2, …), the direction, and the conviction
    label — a quick, scannable index before the card's own headline.
    """
    dir_color  = _direction_color(idea.direction)
    conv_color = _conviction_color(idea.conviction_label)
    st.markdown(
        f'<div style="display:flex;align-items:center;gap:9px;'
        f'margin:14px 0 -2px;">'
        f'{_rank_chip(rank, conv_color)}'
        f'<span style="font-family:var(--mono);font-size:0.78rem;'
        f'font-weight:600;color:{C_TEXT};letter-spacing:0.02em;">'
        f'{idea.ticker}</span>'
        f'<span style="width:4px;height:4px;border-radius:50%;'
        f'background:{C_TEXT3};"></span>'
        f'<span style="font-family:var(--sans);font-size:0.72rem;'
        f'font-weight:600;color:{dir_color};">{idea.direction}</span>'
        f'<span style="font-family:var(--sans);font-size:0.72rem;'
        f'color:{C_TEXT3};">{idea.conviction_label} conviction</span>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_idea_cards(ideas: list) -> None:
    """Render ranked idea cards, each with an expander for the traceable detail."""
    n = len(ideas)
    section_header(
        "Ranked Equity Ideas",
        f"{n} idea{'s' if n != 1 else ''} — one per tracked shipping ticker, "
        "ranked by conviction, highest first. Expand any card for its fully "
        "traceable cascade rationale.",
    )

    # score_equity_ideas already returns ideas sorted by conviction desc; sort
    # again defensively so the card order is correct even if a caller reorders.
    ordered = sorted(
        ideas, key=lambda idea: (-idea.conviction_score, idea.ticker)
    )

    for i, idea in enumerate(ordered):
        _render_card_rank_line(i + 1, idea)

        st.markdown(
            insight_card_html(
                title=f"{idea.ticker} · {idea.company_name}",
                score=idea.conviction_score,
                action=idea.direction,
                rationale=idea.thesis,
                category="DISRUPTION",
            ),
            unsafe_allow_html=True,
        )

        exp_label = (
            f"Traceable rationale — {idea.ticker} "
            f"({idea.direction}, {idea.conviction_label} conviction)"
        )
        with st.expander(exp_label, expanded=(i == 0)):
            try:
                _render_idea_detail(idea)
            except Exception:
                logger.exception(
                    "Equity Signals — idea detail failed for {}",
                    getattr(idea, "ticker", "?"),
                )
                st.error("Idea detail unavailable for this ticker.")

        # Hairline separator between cards keeps a steady editorial rhythm
        # without the heavier weight of st.divider().
        if i < n - 1:
            st.markdown(
                f'<div style="height:1px;background:{C_RULE};'
                f'margin:18px 0 4px;"></div>',
                unsafe_allow_html=True,
            )


# ── Public entry point ──────────────────────────────────────────────────────

def render(
    stock_data=None,
    freight_data=None,
    macro_data=None,
    port_results=None,
    route_results=None,
    insights=None,
    **kwargs,
) -> None:
    """Render the Equity Signals tab — the final stage of the Disruption Alpha chain.

    Parameters
    ----------
    stock_data:
        Mapping ``ticker -> DataFrame`` of OHLCV history. May be ``None`` or
        empty — price context degrades to "n/a" and ideas still score.
    freight_data, macro_data, port_results, route_results:
        Platform-standard inputs forwarded into the Shipping Stress Index. Each
        may be ``None`` / empty; the index then returns neutral defaults.
    insights:
        Optional ``list[Insight]`` used only as a corroborating signal in the
        cascade scorer. ``None`` disables the cross-reference.

    Robust to all-None / empty inputs: every upstream processing module returns
    neutral defaults rather than raising, and each section here is independently
    guarded.
    """
    # ── A. Page header ──────────────────────────────────────────────────────
    page_header(
        title="Equity Signals",
        subtitle="Ranked, fully traceable candidate equity ideas — the "
        "conclusion of the disruption → commodity → company cascade",
        badge_text="MODELED",
        badge_color=C_ACCENT,
    )

    # ── A. Unconditional not-investment-advice banner (always above the fold) ─
    # This MUST render on every load, before any data is touched. It is an
    # alert_banner (a styled markdown panel), deliberately NOT an st.error —
    # the verification step asserts 0 st.error and that this banner is present.
    try:
        alert_banner(
            "<b>Modeled, rule-based idea generation — not investment advice.</b> "
            "All data is synthetic / modeled. Ideas are framed Bullish / Bearish "
            "/ Neutral with a transparent rationale; they are not Buy / Sell "
            "calls and carry no price targets. Every signal traces back through "
            "the visible cascade hops below.",
            level="warning",
        )
    except Exception:
        logger.exception("Equity Signals — disclaimer banner failed")

    # ── Build the ranked ideas via the full cascade ─────────────────────────
    try:
        ideas = _build_ideas(
            stock_data, freight_data, macro_data,
            port_results, route_results, insights,
        )
    except Exception:
        logger.exception("Equity Signals — idea build failed")
        ideas = []

    if not ideas:
        _render_empty_note(
            "No equity ideas available",
            "The cascade produced no ideas from the current inputs — it "
            "requires the modeled exposure matrix. Try reloading; every "
            "upstream stage degrades gracefully rather than failing.",
        )
        # Still emit the provenance footer so the tab is well-formed.
        _render_source_footer()
        return

    section_divider("Cascade Output")

    # ── B. Consensus strip ──────────────────────────────────────────────────
    try:
        _render_consensus_strip(ideas)
    except Exception:
        logger.exception("Equity Signals — consensus strip failed")
        st.error("Consensus strip unavailable.")

    section_divider("Idea Ledger")

    # ── C + D. Ranked idea cards with traceable per-idea detail ─────────────
    try:
        _render_idea_cards(ideas)
    except Exception:
        logger.exception("Equity Signals — idea cards failed")
        st.error("Ranked idea cards unavailable.")

    # ── E. Provenance footer ────────────────────────────────────────────────
    section_divider()
    _render_source_footer()


def _render_source_footer() -> None:
    """Render the modeled-data provenance footer."""
    try:
        from data.quality import DataSource

        sources = [
            DataSource.modeled(
                "Disruption Cascade — Equity Ideas",
                notes="Rule-based idea generation: per-hop contribution = "
                "route stress × cargo share × company exposure weight; "
                "direction from an explicit, documented rules table; "
                "conviction is a transparent weighted sum.",
            ),
            DataSource.modeled(
                "Shipping Stress Index",
                notes="Per-route disruption composite — chokepoint, "
                "congestion, weather, rate and vulnerability.",
            ),
            DataSource.modeled(
                "Modeled Commodity Exposure Matrix",
                notes="Company↔commodity weights derived from trade routes "
                "via the cargo-mix model.",
            ),
        ]
        st.markdown(source_footer(sources), unsafe_allow_html=True)
    except Exception:
        logger.exception("Equity Signals — source footer failed")
