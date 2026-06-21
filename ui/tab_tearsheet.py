"""Instrument Tearsheet tab — a deep-linkable, ticker-first single-screen view.

One page that composes everything Ship already knows about a single shipping
equity: the company profile, the disruption-cascade EquityIdea, the company's
commodity-exposure vector, open alerts, and recent price action.

Deep-linkable: the active ticker is resolved from ``?entity=<TICKER>`` (read in
``app.py`` into ``st.session_state['active_entity']`` via ``ui.url_state``), so a
tearsheet is shareable + survives a refresh. A selectbox fallback over the
tracked universe lets a user pick one when no entity is routed in.

HONESTY: every section shows real data where available and a labeled
empty-state / fallback otherwise — nothing is fabricated. The cascade here runs
**macro-only** (this tab has no live freight/port/route feeds), exactly like
``tab_portfolio._render_book_cascade``, so route-stress terms are muted and the
direction is labeled as such.

Pure-render module — all heavy logic lives in the processing/engine layer; this
file only resolves the ticker and composes the existing builders behind a
try/except-per-section pattern with provenance footers.
"""
from __future__ import annotations

import streamlit as st
from loguru import logger

from ui.styles import (
    C_HIGH, C_LOW, C_ACCENT, C_MOD, C_TEXT, C_TEXT2, C_TEXT3,
    badge,
    metric_card_row,
    page_header,
    section_header,
    source_footer,
    wsj_market_table,
)


# ---------------------------------------------------------------------------
# Cell formatters (match the house style in tab_portfolio)
# ---------------------------------------------------------------------------

def _mono(value: str, color: str = C_TEXT) -> str:
    return (
        f'<span style="font-family:var(--mono);color:{color};'
        f'font-variant-numeric:tabular-nums;">{value}</span>'
    )


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return (
        f'<span style="font-family:var(--sans);color:{color};'
        f'font-weight:{weight};">{value}</span>'
    )


def _dir_color(direction: str) -> str:
    d = (direction or "").lower()
    if d.startswith("bull"):
        return C_HIGH
    if d.startswith("bear"):
        return C_LOW
    return C_TEXT3


# ---------------------------------------------------------------------------
# Universe + ticker resolution
# ---------------------------------------------------------------------------

def _tracked_universe() -> list[str]:
    """Sorted list of tracked shipping tickers (the tearsheet universe).

    Drawn from the COMPANY_COMMODITY_EXPOSURE / COMPANY_PROFILES keys — the same
    universe the cascade scorer walks. Tolerant of an import failure (returns []
    so the empty-state still renders).
    """
    try:
        from processing.exposure_matrix import COMPANY_COMMODITY_EXPOSURE
        return sorted(COMPANY_COMMODITY_EXPOSURE.keys())
    except Exception:
        try:
            from processing.company_profiler import COMPANY_PROFILES
            return sorted(COMPANY_PROFILES.keys())
        except Exception:
            return []


def _resolve_ticker(universe: list[str]) -> str:
    """Resolve the active ticker: the ?entity= route, else a selectbox fallback.

    ``st.session_state['active_entity']`` is set by the ?entity= route in app.py.
    When it names a tracked ticker that is the default selectbox choice; the user
    can still switch via the selectbox (the choice is mirrored back into
    ``active_entity`` so the rest of the page + any re-render agree). Returns ""
    when the universe is empty or nothing is selected.
    """
    if not universe:
        return ""

    routed = str(st.session_state.get("active_entity", "") or "").upper().strip()
    default_index = universe.index(routed) if routed in universe else 0

    choices = ["— select an instrument —"] + universe
    select_index = (default_index + 1) if routed in universe else 0
    try:
        picked = st.selectbox(
            "Instrument",
            choices,
            index=select_index,
            key="tearsheet_ticker",
            help="Deep-link any instrument with ?entity=<TICKER> in the URL.",
        )
    except Exception:
        # Headless / no-op streamlit (smoke tests) — fall back to the route.
        picked = routed if routed in universe else "— select an instrument —"

    if picked == "— select an instrument —":
        return ""
    # Mirror the selection back so the URL/session stays coherent.
    st.session_state["active_entity"] = picked
    return picked


# ---------------------------------------------------------------------------
# Section: header + company profile
# ---------------------------------------------------------------------------

def _render_profile(ticker: str, stock_data) -> None:
    """Header KPI strip: company name / sector / fleet, from company_profiler."""
    try:
        from processing.company_profiler import COMPANY_PROFILES

        profile = COMPANY_PROFILES.get(ticker, {})
        name = str(profile.get("name", ticker))
        sector = str(profile.get("sector", "—"))
        hq = str(profile.get("hq", "—"))
        fleet = profile.get("fleet_size")
        teu = profile.get("fleet_teu_capacity")
        dwt = profile.get("dwt_capacity")

        section_header(
            f"{name} · {ticker}",
            f"{sector} · HQ {hq}" if hq != "—" else sector,
        )

        if not profile:
            st.info(
                f"No company profile on file for {ticker} — header limited to "
                f"the ticker symbol."
            )
            return

        fleet_val = f"{int(fleet):,}" if fleet else "—"
        if teu:
            cap_val, cap_sub = f"{int(teu):,}", "TEU capacity"
        elif dwt:
            cap_val, cap_sub = f"{int(dwt):,}", "DWT capacity"
        else:
            cap_val, cap_sub = "—", "capacity n/a"
        founded = profile.get("founded")
        founded_val = str(int(founded)) if founded else "—"

        metric_card_row([
            {"label": "Sector", "value": sector, "accent": C_ACCENT,
             "sublabel": "shipping sub-sector"},
            {"label": "Fleet Size", "value": fleet_val, "accent": C_TEXT,
             "sublabel": "vessels"},
            {"label": "Capacity", "value": cap_val, "accent": C_TEXT,
             "sublabel": cap_sub},
            {"label": "Founded", "value": founded_val, "accent": C_TEXT,
             "sublabel": str(profile.get("hq", ""))},
        ], columns=4)

        edge = str(profile.get("competitive_edge", "")).strip()
        risk = str(profile.get("risk_factor", "")).strip()
        if edge:
            st.caption(f"Edge — {edge}")
        if risk:
            st.caption(f"Risk — {risk}")

        st.markdown(source_footer([
            {"name": "Company master data (company_profiler — illustrative profiles)",
             "kind": "modeled", "quality": "modeled"},
        ]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"tearsheet profile error: {exc}")


# ---------------------------------------------------------------------------
# Section: recent price + 30d change
# ---------------------------------------------------------------------------

def _render_price(ticker: str, stock_data) -> None:
    """Latest real close + 30-day change from stock_data, honest n/a otherwise."""
    try:
        from processing.disruption_cascade import _price_and_change

        price, change_30d = _price_and_change(ticker, stock_data or {})
        section_header("Price", "Latest close and 30-day move from cached closes")

        if price <= 0.0:
            st.info(
                f"No price history available for {ticker} — momentum context "
                f"could not be computed."
            )
            return

        chg_color = C_HIGH if change_30d >= 0 else C_LOW
        metric_card_row([
            {"label": "Last Close", "value": f"${price:,.2f}", "accent": C_TEXT,
             "sublabel": "latest cached close"},
            {"label": "30-Day Change", "value": f"{change_30d * 100:+.1f}%",
             "accent": chg_color, "sublabel": "vs. ~30 sessions ago"},
        ], columns=2)
        st.markdown(source_footer([
            {"name": "Historical closes (yfinance / cached)", "kind": "real",
             "quality": "live"},
        ]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"tearsheet price error: {exc}")


# ---------------------------------------------------------------------------
# Section: the cascade EquityIdea for this ticker
# ---------------------------------------------------------------------------

def _build_idea(ticker: str, stock_data, macro_data, insights):
    """Build the cascade EquityIdea for one ticker.

    Reuses the SAME macro-only chain ``tab_portfolio._render_book_cascade`` uses:
    compute_shipping_stress({}, macro_data, [], []) -> build_exposure_matrix ->
    score_equity_ideas, then picks the idea for this ticker. Returns None on any
    failure so the caller renders an honest empty-state.
    """
    try:
        from processing.disruption_cascade import score_equity_ideas
        from processing.exposure_matrix import build_exposure_matrix
        from processing.shipping_stress_index import compute_shipping_stress

        stress = compute_shipping_stress({}, macro_data or {}, [], [])
        exposure = build_exposure_matrix(stock_data or {})
        ideas = score_equity_ideas(stress, exposure, stock_data or {}, insights)
        return next((i for i in ideas if i.ticker == ticker), None)
    except Exception as exc:
        logger.debug(f"tearsheet idea unavailable: {exc}")
        return None


def _render_idea(ticker: str, stock_data, macro_data, insights) -> None:
    """The disruption-cascade EquityIdea: direction / conviction / thesis."""
    try:
        section_header(
            "Disruption-Cascade Idea",
            "Direction + conviction from the macro-driven cascade",
        )
        idea = _build_idea(ticker, stock_data, macro_data, insights)
        if idea is None:
            st.info(
                f"No cascade idea could be scored for {ticker} from the current "
                f"inputs."
            )
            return

        dir_color = _dir_color(idea.direction)
        metric_card_row([
            {"label": "Direction", "value": idea.direction, "accent": dir_color,
             "sublabel": "framed as a view — not advice"},
            {"label": "Conviction", "value": idea.conviction_label,
             "accent": C_MOD, "sublabel": f"score {idea.conviction_score:.2f} / 1.0"},
            {"label": "Cascade Hops", "value": str(len(idea.cascade_chain)),
             "accent": C_ACCENT,
             "sublabel": f"{len(idea.driving_routes)} route(s) · "
                         f"{len(idea.driving_commodities)} commodity(ies)"},
        ], columns=3)

        if idea.thesis:
            st.markdown(
                f'<div style="background:rgba(53,114,176,0.06);'
                f'border-left:3px solid {C_ACCENT};padding:12px 16px;'
                f'border-radius:3px;margin:6px 0 10px 0;font-size:0.86rem;'
                f'line-height:1.55;color:{C_TEXT2}">{idea.thesis}</div>',
                unsafe_allow_html=True,
            )

        # Cascade chain — the traceable hops.
        if idea.cascade_chain:
            rows = []
            for lk in idea.cascade_chain[:8]:
                rows.append([
                    _mono(lk.route_id, color=C_TEXT),
                    _sans(lk.hs_category.title(), color=C_TEXT2),
                    _mono(f"{lk.route_stress:.2f}"),
                    _mono(f"{lk.cargo_share:.2f}"),
                    _mono(f"{lk.contribution:.4f}", color=C_MOD),
                ])
            wsj_market_table(
                ["Route", "Commodity", "Stress", "Cargo Share", "Contribution"],
                rows,
            )

        for flag in idea.risk_flags[:4]:
            st.caption(f"⚠ {flag}")
        st.caption(
            "⚠ Macro-only cascade (this tab has no live freight/port/route "
            "feeds) — route-stress terms are muted, so read this as a "
            "macro-driven tilt, not a full cascade."
        )
        st.markdown(source_footer([
            {"name": "Disruption cascade (modeled — macro-only inputs here)",
             "kind": "modeled", "quality": "modeled"},
        ]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"tearsheet idea render error: {exc}")


# ---------------------------------------------------------------------------
# Section: commodity-exposure vector
# ---------------------------------------------------------------------------

def _render_exposure(ticker: str) -> None:
    """Company commodity-exposure vector as a ranked table with weight bars."""
    try:
        from processing.exposure_matrix import company_commodity_weights
        from processing.cargo_analyzer import HS_CATEGORIES

        section_header(
            "Commodity Exposure",
            "Company HS-category weight vector (sums to 1.0)",
        )
        weights = company_commodity_weights(ticker)
        if not weights:
            st.info(f"No commodity-exposure vector available for {ticker}.")
            return

        ranked = sorted(weights.items(), key=lambda kv: kv[1], reverse=True)
        rows = []
        for cat, w in ranked:
            label = HS_CATEGORIES.get(cat, {}).get("label", cat.title())
            bar_w = max(0, min(100, int(round(w * 100))))
            bar = (
                f'<div style="background:rgba(255,255,255,0.06);border-radius:3px;'
                f'height:9px;width:120px;display:inline-block;vertical-align:middle">'
                f'<div style="background:{C_ACCENT};height:9px;border-radius:3px;'
                f'width:{bar_w}%"></div></div>'
            )
            rows.append([
                _sans(label, color=C_TEXT, weight=600),
                _mono(f"{w * 100:.1f}%", color=C_TEXT2),
                bar,
            ])
        wsj_market_table(["Commodity", "Weight", ""], rows)
        st.markdown(source_footer([
            {"name": "Exposure matrix (derived from route cargo-mix — illustrative)",
             "kind": "modeled", "quality": "modeled"},
        ]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"tearsheet exposure error: {exc}")


# ---------------------------------------------------------------------------
# Section: open alerts for this ticker
# ---------------------------------------------------------------------------

_SEV_COLOR = {
    "CRITICAL": C_LOW, "HIGH": C_LOW, "MEDIUM": C_MOD, "LOW": C_TEXT3,
}


def _render_alerts(ticker: str) -> None:
    """Open alerts filtered to this ticker (alert_engine_v2.load_alerts)."""
    try:
        from engine.alert_engine_v2 import load_alerts

        section_header("Open Alerts", "Recent alerts tagged to this instrument")
        try:
            alerts = load_alerts(max_age_days=30)
        except Exception as exc:
            logger.debug(f"tearsheet load_alerts unavailable: {exc}")
            st.caption("Alert store unavailable.")
            return

        mine = [a for a in (alerts or []) if getattr(a, "ticker", "") == ticker]
        if not mine:
            st.info(f"No open alerts for {ticker} in the last 30 days.")
            return

        rows = []
        for a in mine[:12]:
            sev = getattr(a, "severity", "LOW")
            rows.append([
                badge(sev, color=_SEV_COLOR.get(sev, C_TEXT3)),
                _sans(getattr(a, "alert_type", ""), color=C_TEXT2),
                _sans(getattr(a, "title", ""), color=C_TEXT, weight=600),
                _mono(str(getattr(a, "created_at", ""))[:10], color=C_TEXT3),
            ])
        wsj_market_table(["Severity", "Type", "Title", "Date"], rows)
        st.markdown(source_footer([
            {"name": "Alert store (alert_engine_v2)", "kind": "real",
             "quality": "live"},
        ]), unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"tearsheet alerts error: {exc}")


# ---------------------------------------------------------------------------
# Public render
# ---------------------------------------------------------------------------

def render(stock_data=None, macro_data=None, insights=None, **_kwargs) -> None:
    """Render the Instrument Tearsheet tab.

    Resolves the active ticker from the ?entity= route (session
    ``active_entity``) or a selectbox fallback, then composes the profile,
    price, cascade idea, commodity exposure, and open alerts — each in its own
    try/except section with a provenance footer.
    """
    try:
        page_header(
            title="Instrument Tearsheet",
            subtitle=(
                "One screen for a single shipping equity — profile, "
                "disruption-cascade idea, commodity exposure, alerts, and "
                "price. Deep-link any instrument via ?entity=<TICKER>."
            ),
            badge_text="TEARSHEET",
            badge_color=C_ACCENT,
        )

        universe = _tracked_universe()
        if not universe:
            st.info("No tracked instruments are available.")
            return

        ticker = _resolve_ticker(universe)
        if not ticker:
            st.info(
                "Select an instrument above, or deep-link one with "
                "`?entity=<TICKER>` in the URL (e.g. `?entity=ZIM`)."
            )
            return

        _render_profile(ticker, stock_data)
        _render_price(ticker, stock_data)
        _render_idea(ticker, stock_data, macro_data, insights)
        _render_exposure(ticker)
        _render_alerts(ticker)

    except Exception as exc:
        logger.exception(f"Tearsheet tab crash: {exc}")
        try:
            st.error(f"Instrument tearsheet encountered an error: {exc}")
        except Exception:
            pass
