"""Global Macro Intelligence Tab — Goldman Sachs Global Markets quality.

render(macro_data, stock_data=None, insights=None) is the public entry point.

Sections
--------
1. Global Macro Dashboard       — KPI cards for World / US / China / EU
2. Shipping Demand Drivers      — cause-effect table: macro factor → shipping impact
3. Leading Indicators           — 8 forward-looking signals (3-6 month view)
4. OECD / IMF Data Panel        — GDP forecasts, trade flows, commodity forecasts
5. Interest Rate & Credit       — vessel financing, newbuild sensitivity, HY spreads
6. Commodity Price Dashboard    — Oil, LNG, Coal, Iron Ore, Copper, Grain
"""
from __future__ import annotations

from loguru import logger
import streamlit as st

from ui.styles import (
    C_ACCENT,
    C_BORDER,
    C_CARD,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_SURFACE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    badge,
    metric_card_row,
    page_header,
    section_header,
    wsj_market_table,
)


# ---------------------------------------------------------------------------
# Status → palette/badge mapping
# ---------------------------------------------------------------------------

_STATUS_COLOR: dict[str, str] = {
    "EXPANDING":   "green",
    "CONTRACTING": "red",
    "STABLE":      "yellow",
    "POSITIVE":    "green",
    "NEGATIVE":    "red",
    "NEUTRAL":     "yellow",
    "UP":          "green",
    "DOWN":        "red",
    "FLAT":        "yellow",
}

_STATUS_FG: dict[str, str] = {
    "EXPANDING":   C_HIGH, "CONTRACTING": C_LOW, "STABLE":      C_MOD,
    "POSITIVE":    C_HIGH, "NEGATIVE":    C_LOW, "NEUTRAL":     C_MOD,
    "UP":          C_HIGH, "DOWN":        C_LOW, "FLAT":        C_MOD,
}


# ---------------------------------------------------------------------------
# Mock data
# ---------------------------------------------------------------------------

def _mock_global_kpis() -> dict:
    return {
        "world": {
            "Global PMI":          {"value": 51.3, "prior": 50.8, "delta": +0.5, "status": "EXPANDING"},
            "Global Trade Growth": {"value": 2.4,  "prior": 1.9,  "delta": +0.5, "unit": "%", "status": "EXPANDING"},
            "World GDP Forecast":  {"value": 3.1,  "prior": 3.0,  "delta": +0.1, "unit": "%", "status": "STABLE"},
        },
        "us": {
            "GDP Growth":          {"value": 2.8,  "prior": 2.5,  "delta": +0.3, "unit": "%", "status": "EXPANDING"},
            "CPI":                 {"value": 3.2,  "prior": 3.4,  "delta": -0.2, "unit": "%", "status": "STABLE"},
            "Fed Funds Rate":      {"value": 5.25, "prior": 5.25, "delta":  0.0, "unit": "%", "status": "STABLE"},
            "ISM Mfg PMI":         {"value": 49.1, "prior": 48.7, "delta": +0.4, "status": "CONTRACTING"},
            "Consumer Confidence": {"value": 102.3,"prior": 99.8, "delta": +2.5, "status": "EXPANDING"},
        },
        "china": {
            "GDP Growth":          {"value": 4.9,  "prior": 5.0,  "delta": -0.1, "unit": "%", "status": "STABLE"},
            "Manufacturing PMI":   {"value": 49.7, "prior": 49.1, "delta": +0.6, "status": "CONTRACTING"},
            "Trade Balance":       {"value": 75.3, "prior": 68.1, "delta": +7.2, "unit": "B USD", "status": "EXPANDING"},
            "PPI":                 {"value": -1.4, "prior": -1.8, "delta": +0.4, "unit": "%", "status": "STABLE"},
        },
        "eu": {
            "GDP Growth":          {"value": 0.7,  "prior": 0.5,  "delta": +0.2, "unit": "%", "status": "STABLE"},
            "Manufacturing PMI":   {"value": 47.6, "prior": 46.9, "delta": +0.7, "status": "CONTRACTING"},
            "ECB Rate":            {"value": 4.00, "prior": 4.50, "delta": -0.5, "unit": "%", "status": "STABLE"},
        },
    }


def _mock_demand_drivers() -> list[dict]:
    return [
        {"factor": "China Industrial Production", "segment": "Dry Bulk",            "current": "5.6% YoY",    "trend": "UP",   "impact": "POSITIVE", "assessment": "Steel & coal demand supports Capesize/Panamax rates"},
        {"factor": "US Consumer Spending",         "segment": "Container",          "current": "+3.2% YoY",   "trend": "UP",   "impact": "POSITIVE", "assessment": "Import volumes rising; TPEB rates firming"},
        {"factor": "Global PMI",                   "segment": "All Freight",        "current": "51.3",         "trend": "UP",   "impact": "POSITIVE", "assessment": "Expansionary PMI correlates with BDI in 6-8 weeks"},
        {"factor": "Oil Price (Brent)",            "segment": "Tanker / Bunker",    "current": "$82.4 / bbl",  "trend": "FLAT", "impact": "NEUTRAL",  "assessment": "Elevated bunker costs compress TCE margins ~8%"},
        {"factor": "USD / CNY",                    "segment": "Container / Dry Bulk","current": "7.24",        "trend": "FLAT", "impact": "NEUTRAL",  "assessment": "Weak CNY reduces Chinese export competitiveness"},
        {"factor": "USD / EUR",                    "segment": "Container",          "current": "1.083",        "trend": "DOWN", "impact": "POSITIVE", "assessment": "Stronger USD makes US imports cheaper; volume upside"},
    ]


def _mock_leading_indicators() -> list[dict]:
    return [
        {"indicator": "ISM New Orders",             "value": "51.8",         "trend": "UP",   "lead_time": "4-6 wks", "implication": "Near-term freight demand improvement expected"},
        {"indicator": "Baltic Forward Curves",      "value": "C5TC $18,400", "trend": "UP",   "lead_time": "Spot → 3M","implication": "FFA backwardation signals rate softness by Q3"},
        {"indicator": "Port Booking Rates",         "value": "+4.1% WoW",    "trend": "UP",   "lead_time": "2-4 wks", "implication": "Short-term container demand pulse; watch inventory builds"},
        {"indicator": "Ocean Carrier Capacity",     "value": "23.4M TEU",    "trend": "UP",   "lead_time": "3-6 mo",  "implication": "Delivery overhang pressures container freight rates"},
        {"indicator": "Inventory-to-Sales Ratio",   "value": "1.36x",        "trend": "DOWN", "lead_time": "6-8 wks", "implication": "Destocking cycle nearing end; restocking wave likely"},
        {"indicator": "Global Trade Finance Volume","value": "$1.74T",       "trend": "UP",   "lead_time": "4-8 wks", "implication": "Letters of credit up 6% MoM; trade activity accelerating"},
        {"indicator": "OECD CLI",                   "value": "100.4",        "trend": "UP",   "lead_time": "3-6 mo",  "implication": "Composite leading index above 100 signals expansion"},
        {"indicator": "IMF WEO Revisions",          "value": "+0.1pp (2026)","trend": "UP",   "lead_time": "6-12 mo", "implication": "Marginal upgrade; upside risk to trade volume projections"},
    ]


def _mock_oecd_imf() -> dict:
    return {
        "gdp_forecasts": [
            {"country": "United States", "2025F": 2.7, "2026F": 2.2, "revision": +0.1},
            {"country": "China",         "2025F": 4.6, "2026F": 4.2, "revision": -0.2},
            {"country": "Euro Area",     "2025F": 0.8, "2026F": 1.3, "revision": +0.1},
            {"country": "Japan",         "2025F": 0.9, "2026F": 0.8, "revision":  0.0},
            {"country": "India",         "2025F": 6.5, "2026F": 6.3, "revision": +0.2},
            {"country": "Brazil",        "2025F": 2.2, "2026F": 2.0, "revision": -0.1},
            {"country": "World",         "2025F": 3.1, "2026F": 3.2, "revision": +0.1},
        ],
        "trade_flows": [
            {"region": "Asia-Pacific", "volume_bn": 4820, "yoy_pct": +3.8, "share_pct": 38.4},
            {"region": "North America","volume_bn": 2140, "yoy_pct": +1.9, "share_pct": 17.1},
            {"region": "Europe",       "volume_bn": 2980, "yoy_pct": +0.7, "share_pct": 23.8},
            {"region": "Middle East",  "volume_bn":  890, "yoy_pct": +4.2, "share_pct":  7.1},
            {"region": "Other",        "volume_bn": 1700, "yoy_pct": +2.1, "share_pct": 13.6},
        ],
        "commodity_forecasts": [
            {"commodity": "Crude Oil (Brent)", "unit": "$/bbl",  "2025F": 80.0, "2026F": 77.0, "risk": "DOWN"},
            {"commodity": "LNG",               "unit": "$/mmBTU","2025F": 10.2, "2026F": 9.6,  "risk": "DOWN"},
            {"commodity": "Thermal Coal",      "unit": "$/t",    "2025F": 115.0,"2026F": 105.0,"risk": "DOWN"},
            {"commodity": "Iron Ore",          "unit": "$/t",    "2025F": 105.0,"2026F": 95.0, "risk": "DOWN"},
            {"commodity": "Copper",            "unit": "$/t",    "2025F": 9200, "2026F": 9800, "risk": "UP"},
        ],
    }


def _mock_rates_credit() -> dict:
    return {
        "base_rates": {
            "Fed Funds":  5.25,
            "SOFR":       5.31,
            "LIBOR 3M":   5.44,
            "ECB Depo":   4.00,
            "SONIA":      5.20,
        },
        "vessel_financing": [
            {"type": "VLCC",               "spread_bps": 185, "all_in_pct": 7.16, "ltv_pct": 60},
            {"type": "Capesize",           "spread_bps": 200, "all_in_pct": 7.31, "ltv_pct": 60},
            {"type": "Panamax",            "spread_bps": 210, "all_in_pct": 7.41, "ltv_pct": 62},
            {"type": "Containership (LRG)","spread_bps": 175, "all_in_pct": 7.06, "ltv_pct": 60},
            {"type": "LNG Carrier",        "spread_bps": 160, "all_in_pct": 6.91, "ltv_pct": 65},
        ],
        "hy_spreads": {
            "Shipping HY OAS (bps)": 485,
            "vs 12M Avg (bps)":      +32,
            "vs Investment Grade":   "+318 bps",
            "Distressed Threshold":  "1000 bps",
        },
        "orderbook_sensitivity": [
            {"rate_scenario": "Rates -100bps", "new_orders_delta": "+18%",  "sentiment": "POSITIVE"},
            {"rate_scenario": "Rates Flat",    "new_orders_delta": "Flat",  "sentiment": "NEUTRAL"},
            {"rate_scenario": "Rates +100bps", "new_orders_delta": "-14%",  "sentiment": "NEGATIVE"},
            {"rate_scenario": "Rates +200bps", "new_orders_delta": "-29%",  "sentiment": "NEGATIVE"},
        ],
    }


def _mock_commodities() -> list[dict]:
    return [
        {"commodity": "WTI Crude",   "unit": "$/bbl",  "price": 79.8,  "wow": -0.8, "mom": +2.1, "yoy": -6.3,  "route": "MR Tanker / USGC-ARA"},
        {"commodity": "Brent Crude", "unit": "$/bbl",  "price": 82.4,  "wow": -0.6, "mom": +1.8, "yoy": -5.9,  "route": "VLCC / TD3C"},
        {"commodity": "LNG",         "unit": "$/mmBTU","price": 9.8,   "wow": +1.2, "mom": -3.1, "yoy": -38.2, "route": "LNG Carrier / Pacific"},
        {"commodity": "Thermal Coal","unit": "$/t",    "price": 118.5, "wow": -1.4, "mom": -2.6, "yoy": -21.0, "route": "Capesize / Richards Bay"},
        {"commodity": "Iron Ore",    "unit": "$/t",    "price": 107.2, "wow": +0.9, "mom": +3.2, "yoy": -14.8, "route": "Capesize / C5 Australia-China"},
        {"commodity": "Copper",      "unit": "$/t",    "price": 9_340, "wow": +1.1, "mom": +4.6, "yoy": +8.2,  "route": "Supramax / Any-China"},
        {"commodity": "Wheat",       "unit": "$/bu",   "price": 5.82,  "wow": -0.4, "mom": -1.7, "yoy": -12.4, "route": "Handysize-Supramax / USEC-Asia"},
    ]


# ---------------------------------------------------------------------------
# Cell formatters + region header
# ---------------------------------------------------------------------------

def _mono(value: str, color: str = C_TEXT) -> str:
    return f'<span style="font-family:var(--mono);color:{color};font-variant-numeric:tabular-nums;">{value}</span>'


def _sans(value: str, color: str = C_TEXT2, weight: int = 400) -> str:
    return f'<span style="font-family:var(--sans);color:{color};font-weight:{weight};">{value}</span>'


def _pct(value: float) -> str:
    color = C_HIGH if value > 0 else (C_LOW if value < 0 else C_TEXT3)
    sign  = "+" if value > 0 else ""
    return _mono(f"{sign}{value:.1f}%", color=color)


def _region_header(title: str, color: str) -> None:
    st.html(
        f'<div style="color:{color};font-size:13px;font-weight:700;letter-spacing:0.06em;'
        f'text-transform:uppercase;margin:18px 0 8px 0;padding-left:10px;'
        f'border-left:3px solid {color};">{title}</div>'
    )


# ---------------------------------------------------------------------------
# Section 1: Global Macro Dashboard
# ---------------------------------------------------------------------------

def _render_macro_dashboard(kpis: dict) -> None:
    section_header(
        "Global Macro Dashboard",
        "Real-time macro readings across World / US / China / EU with directional signals",
    )

    region_colors = {"world": C_ACCENT, "us": C_HIGH, "china": C_LOW, "eu": C_MOD}
    region_labels = {"world": "World", "us": "United States", "china": "China", "eu": "European Union"}

    for region, data in kpis.items():
        try:
            _region_header(region_labels.get(region, region), region_colors.get(region, C_TEXT2))
            cards = []
            for label, d in data.items():
                unit   = d.get("unit", "")
                val    = d.get("value", 0)
                prior  = d.get("prior", 0)
                delta  = d.get("delta", 0.0)
                status = d.get("status", "STABLE")
                val_s   = f"{val:,.1f}{unit}" if isinstance(val, float) else f"{val}{unit}"
                prior_s = f"{prior:,.1f}" if isinstance(prior, float) else str(prior)
                delta_s = f"{delta:+.2f}{unit}"
                cards.append({
                    "label": label,
                    "value": val_s,
                    "accent": region_colors.get(region, C_ACCENT),
                    "delta": delta_s,
                    "delta_color": C_HIGH if delta > 0 else (C_LOW if delta < 0 else C_TEXT3),
                    "sublabel": f"Prior: {prior_s}{unit} · {status}",
                })
            metric_card_row(cards, columns=min(len(cards), 5))
        except Exception as exc:
            logger.warning(f"Macro dashboard region {region} error: {exc}")


# ---------------------------------------------------------------------------
# Section 2: Shipping Demand Drivers
# ---------------------------------------------------------------------------

def _render_demand_drivers(drivers: list[dict]) -> None:
    section_header(
        "Shipping Demand Drivers",
        "Macro factor → shipping segment cause-effect analysis",
    )
    try:
        headers = ["Macro Factor", "Segment", "Current Reading", "Trend", "Impact", "Shipping Assessment"]
        rows = []
        for d in drivers:
            rows.append([
                _sans(d["factor"], color=C_TEXT, weight=600),
                _sans(d["segment"], color=C_ACCENT),
                _mono(d["current"]),
                badge(d.get("trend", "FLAT"),   _STATUS_COLOR.get(d.get("trend", "FLAT"),   "yellow")),
                badge(d.get("impact", "NEUTRAL"), _STATUS_COLOR.get(d.get("impact", "NEUTRAL"), "yellow")),
                _sans(d["assessment"]),
            ])
        wsj_market_table(headers, rows)
    except Exception as exc:
        logger.warning(f"Demand drivers render error: {exc}")
        st.info("Demand drivers data unavailable.")


# ---------------------------------------------------------------------------
# Section 3: Leading Indicators
# ---------------------------------------------------------------------------

def _render_leading_indicators(indicators: list[dict]) -> None:
    section_header(
        "Leading Indicators",
        "3-6 month forward view on freight market direction",
    )
    try:
        cols = st.columns(4)
        for i, ind in enumerate(indicators):
            try:
                trend   = ind.get("trend", "FLAT")
                c_trend = _STATUS_FG.get(trend, C_MOD)
                arrow   = "▲" if trend == "UP" else ("▼" if trend == "DOWN" else "▬")
                card_html = (
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-radius:6px;padding:14px;margin-bottom:12px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                    f'<div style="color:{C_TEXT};font-size:13px;font-weight:600;line-height:1.3;max-width:75%;">{ind["indicator"]}</div>'
                    f'<span style="color:{c_trend};font-size:18px;">{arrow}</span>'
                    f'</div>'
                    f'<div style="color:{C_ACCENT};font-size:20px;font-weight:700;margin:8px 0 4px 0;'
                    f'font-family:var(--mono);">{ind["value"]}</div>'
                    f'<div style="margin-bottom:8px;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;background:{C_SURFACE};'
                    f'padding:2px 6px;border-radius:4px;">Lead: {ind["lead_time"]}</span>'
                    f'</div>'
                    f'<div style="color:{C_TEXT2};font-size:11px;line-height:1.4;'
                    f'border-top:1px solid {C_BORDER};padding-top:8px;">{ind["implication"]}</div>'
                    f'</div>'
                )
                with cols[i % 4]:
                    st.html(card_html)
            except Exception as exc:
                logger.warning(f"Leading indicator card {i} error: {exc}")
    except Exception as exc:
        logger.warning(f"Leading indicators render error: {exc}")
        st.info("Leading indicators data unavailable.")


# ---------------------------------------------------------------------------
# Section 4: OECD / IMF Data Panel
# ---------------------------------------------------------------------------

def _load_oecd_imf() -> dict:
    try:
        from data import oecd_feed  # type: ignore
        return oecd_feed.get_macro_summary()
    except Exception:
        pass
    try:
        from data import imf_feed  # type: ignore
        return imf_feed.get_macro_summary()
    except Exception:
        pass
    logger.info("OECD/IMF feeds unavailable — using mock data.")
    return _mock_oecd_imf()


def _render_oecd_imf(data: dict) -> None:
    section_header(
        "OECD / IMF Data Panel",
        "GDP forecasts, trade flows, and commodity outlooks (OECD/IMF sourced where available)",
    )
    try:
        tab_gdp, tab_trade, tab_comm = st.tabs(["GDP Forecasts", "Trade Flows", "Commodity Outlook"])

        with tab_gdp:
            rows_data = data.get("gdp_forecasts", [])
            rows = []
            for r in rows_data:
                rev   = r.get("revision", 0)
                rev_c = C_HIGH if rev > 0 else (C_LOW if rev < 0 else C_TEXT3)
                rev_s = f"+{rev:.1f}pp" if rev > 0 else f"{rev:.1f}pp"
                is_world = r["country"] == "World"
                rows.append([
                    _sans(r["country"], color=C_TEXT, weight=700 if is_world else 400),
                    _mono(f"{r['2025F']:.1f}%"),
                    _mono(f"{r['2026F']:.1f}%"),
                    _mono(rev_s, color=rev_c),
                ])
            wsj_market_table(["Country", "2025F", "2026F", "Revision"], rows)

        with tab_trade:
            rows_data = data.get("trade_flows", [])
            rows = []
            for r in rows_data:
                yoy = r.get("yoy_pct", 0)
                rows.append([
                    _sans(r["region"], color=C_TEXT, weight=600),
                    _mono(f"${r['volume_bn']:,.0f}B"),
                    _mono(f"{'+' if yoy>0 else ''}{yoy:.1f}%", color=C_HIGH if yoy > 0 else C_LOW),
                    _mono(f"{r['share_pct']:.1f}%"),
                ])
            wsj_market_table(["Region", "Volume ($B)", "YoY %", "Global Share"], rows)

        with tab_comm:
            rows_data = data.get("commodity_forecasts", [])
            rows = []
            for r in rows_data:
                risk = r.get("risk", "FLAT")
                rows.append([
                    _sans(r["commodity"], color=C_TEXT, weight=600),
                    _sans(r["unit"], color=C_TEXT3),
                    _mono(f"{r['2025F']:,.1f}"),
                    _mono(f"{r['2026F']:,.1f}"),
                    badge(risk, _STATUS_COLOR.get(risk, "yellow")),
                ])
            wsj_market_table(["Commodity", "Unit", "2025F", "2026F", "Risk"], rows)
    except Exception as exc:
        logger.warning(f"OECD/IMF panel render error: {exc}")
        st.info("OECD/IMF panel data unavailable.")


# ---------------------------------------------------------------------------
# Section 5: Interest Rate & Credit Impact
# ---------------------------------------------------------------------------

def _render_rates_credit(data: dict) -> None:
    section_header(
        "Interest Rate & Credit Impact",
        "How the current rate environment shapes vessel financing, orderbook, and credit spreads",
    )
    try:
        left, right = st.columns([1, 1])

        with left:
            st.html(
                f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                f'text-transform:uppercase;margin-bottom:10px;">Benchmark Rates</div>'
            )
            base = data.get("base_rates", {})
            base_rows = [
                [_sans(name, color=C_TEXT), _mono(f"{val:.2f}%", color=C_ACCENT)]
                for name, val in base.items()
            ]
            wsj_market_table(["Rate", "Value"], base_rows)

            st.html("<div style='height:16px;'></div>")
            st.html(
                f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                f'text-transform:uppercase;margin-bottom:10px;">HY Spreads — Shipping Bonds</div>'
            )
            hy = data.get("hy_spreads", {})
            hy_rows = []
            for name, val in hy.items():
                if isinstance(val, (int, float)):
                    val_str = f"{val:+.0f} bps" if "vs" in name else f"{val:,.0f} bps"
                    val_color = C_MOD if val > 400 else C_HIGH
                else:
                    val_str = str(val)
                    val_color = C_TEXT
                hy_rows.append([_sans(name, color=C_TEXT), _mono(val_str, color=val_color)])
            wsj_market_table(["Metric", "Value"], hy_rows)

        with right:
            st.html(
                f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                f'text-transform:uppercase;margin-bottom:10px;">Vessel Financing — All-In Cost</div>'
            )
            vf = data.get("vessel_financing", [])
            vf_rows = [
                [
                    _sans(v["type"], color=C_TEXT),
                    _mono(f"{v['spread_bps']} bps"),
                    _mono(f"{v['all_in_pct']:.2f}%", color=C_MOD),
                    _mono(f"{v['ltv_pct']}%"),
                ]
                for v in vf
            ]
            wsj_market_table(["Vessel Type", "Spread", "All-In", "Max LTV"], vf_rows)

            st.html("<div style='height:16px;'></div>")
            st.html(
                f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                f'text-transform:uppercase;margin-bottom:10px;">Newbuild Order Book — Rate Sensitivity</div>'
            )
            obs = data.get("orderbook_sensitivity", [])
            ob_rows = [
                [
                    _sans(row["rate_scenario"], color=C_TEXT),
                    _mono(row["new_orders_delta"]),
                    badge(row.get("sentiment", "NEUTRAL"),
                          _STATUS_COLOR.get(row.get("sentiment", "NEUTRAL"), "yellow")),
                ]
                for row in obs
            ]
            wsj_market_table(["Rate Scenario", "New Orders Δ", "Sentiment"], ob_rows)
    except Exception as exc:
        logger.warning(f"Rates & credit panel render error: {exc}")
        st.info("Interest rate & credit data unavailable.")


# ---------------------------------------------------------------------------
# Section 6: Commodity Price Dashboard
# ---------------------------------------------------------------------------

def _render_commodities(rows_in: list[dict]) -> None:
    section_header(
        "Commodity Price Dashboard",
        "Key commodities driving shipping demand — prices, momentum, and route sensitivity",
    )
    try:
        rows = []
        for r in rows_in:
            price = r.get("price", 0)
            unit  = r.get("unit", "")
            price_str = f"{price:,.1f}" if price < 1000 else f"{price:,.0f}"
            price_cell = (
                f'<span style="font-family:var(--mono);color:{C_ACCENT};font-weight:700;">{price_str}</span>'
                f'<span style="color:{C_TEXT3};font-size:11px;font-weight:400;"> {unit}</span>'
            )
            rows.append([
                _sans(r["commodity"], color=C_TEXT, weight=600),
                price_cell,
                _pct(r.get("wow", 0)),
                _pct(r.get("mom", 0)),
                _pct(r.get("yoy", 0)),
                _sans(r.get("route", "—")),
            ])
        wsj_market_table(
            ["Commodity", "Price", "WoW %", "MoM %", "YoY %", "Key Shipping Route"],
            rows,
        )
    except Exception as exc:
        logger.warning(f"Commodity dashboard render error: {exc}")
        st.info("Commodity price data unavailable.")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render(macro_data, stock_data=None, insights=None) -> None:
    """Render the Global Macro Intelligence tab."""
    try:
        page_header(
            title="Global Macro Intelligence",
            subtitle=(
                "Macro drivers, leading indicators, and commodity dynamics influencing "
                "global shipping demand across all vessel segments"
            ),
            icon="🌐",
            badge_text="Goldman Sachs Quality",
            badge_color=C_ACCENT,
        )
    except Exception as exc:
        logger.warning(f"Header render error: {exc}")

    try:
        if isinstance(macro_data, dict) and "kpis" in macro_data:
            kpis = macro_data["kpis"]
        else:
            kpis = _mock_global_kpis()
        _render_macro_dashboard(kpis)
    except Exception as exc:
        logger.error(f"Section 1 (Macro Dashboard) error: {exc}")
        st.error("Macro dashboard unavailable.")

    try:
        if isinstance(macro_data, dict) and "demand_drivers" in macro_data:
            drivers = macro_data["demand_drivers"]
        else:
            drivers = _mock_demand_drivers()
        _render_demand_drivers(drivers)
    except Exception as exc:
        logger.error(f"Section 2 (Demand Drivers) error: {exc}")
        st.error("Demand drivers unavailable.")

    try:
        if isinstance(macro_data, dict) and "leading_indicators" in macro_data:
            indicators = macro_data["leading_indicators"]
        else:
            indicators = _mock_leading_indicators()
        _render_leading_indicators(indicators)
    except Exception as exc:
        logger.error(f"Section 3 (Leading Indicators) error: {exc}")
        st.error("Leading indicators unavailable.")

    try:
        if isinstance(macro_data, dict) and "oecd_imf" in macro_data:
            oecd_imf_data = macro_data["oecd_imf"]
        else:
            oecd_imf_data = _load_oecd_imf()
        _render_oecd_imf(oecd_imf_data)
    except Exception as exc:
        logger.error(f"Section 4 (OECD/IMF) error: {exc}")
        st.error("OECD/IMF panel unavailable.")

    try:
        if isinstance(macro_data, dict) and "rates_credit" in macro_data:
            rates_data = macro_data["rates_credit"]
        else:
            rates_data = _mock_rates_credit()
        _render_rates_credit(rates_data)
    except Exception as exc:
        logger.error(f"Section 5 (Rates & Credit) error: {exc}")
        st.error("Interest rate & credit panel unavailable.")

    try:
        if isinstance(macro_data, dict) and "commodities" in macro_data:
            commodities = macro_data["commodities"]
        else:
            commodities = _mock_commodities()
        _render_commodities(commodities)
    except Exception as exc:
        logger.error(f"Section 6 (Commodities) error: {exc}")
        st.error("Commodity dashboard unavailable.")
