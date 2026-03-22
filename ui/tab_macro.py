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

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
C_BG      = "#0a0f1a"
C_SURFACE = "#111827"
C_CARD    = "#1a2235"
C_BORDER  = "rgba(255,255,255,0.08)"
C_HIGH    = "#10b981"
C_MOD     = "#f59e0b"
C_LOW     = "#ef4444"
C_ACCENT  = "#3b82f6"
C_TEXT    = "#f1f5f9"
C_TEXT2   = "#94a3b8"
C_TEXT3   = "#64748b"

# ---------------------------------------------------------------------------
# Mock data helpers
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
        {
            "factor":          "China Industrial Production",
            "segment":         "Dry Bulk",
            "current":         "5.6% YoY",
            "trend":           "UP",
            "impact":          "POSITIVE",
            "assessment":      "Steel & coal demand supports Capesize/Panamax rates",
        },
        {
            "factor":          "US Consumer Spending",
            "segment":         "Container",
            "current":         "+3.2% YoY",
            "trend":           "UP",
            "impact":          "POSITIVE",
            "assessment":      "Import volumes rising; TPEB rates firming",
        },
        {
            "factor":          "Global PMI",
            "segment":         "All Freight",
            "current":         "51.3",
            "trend":           "UP",
            "impact":          "POSITIVE",
            "assessment":      "Expansionary PMI correlates with BDI in 6-8 weeks",
        },
        {
            "factor":          "Oil Price (Brent)",
            "segment":         "Tanker / Bunker",
            "current":         "$82.4 / bbl",
            "trend":           "FLAT",
            "impact":          "NEUTRAL",
            "assessment":      "Elevated bunker costs compress TCE margins ~8%",
        },
        {
            "factor":          "USD / CNY",
            "segment":         "Container / Dry Bulk",
            "current":         "7.24",
            "trend":           "FLAT",
            "impact":          "NEUTRAL",
            "assessment":      "Weak CNY reduces Chinese export competitiveness",
        },
        {
            "factor":          "USD / EUR",
            "segment":         "Container",
            "current":         "1.083",
            "trend":           "DOWN",
            "impact":          "POSITIVE",
            "assessment":      "Stronger USD makes US imports cheaper; volume upside",
        },
    ]


def _mock_leading_indicators() -> list[dict]:
    return [
        {
            "indicator":    "ISM New Orders",
            "value":        "51.8",
            "trend":        "UP",
            "lead_time":    "4-6 wks",
            "implication":  "Near-term freight demand improvement expected",
        },
        {
            "indicator":    "Baltic Forward Curves",
            "value":        "C5TC $18,400",
            "trend":        "UP",
            "lead_time":    "Spot → 3M",
            "implication":  "FFA backwardation signals rate softness by Q3",
        },
        {
            "indicator":    "Port Booking Rates",
            "value":        "+4.1% WoW",
            "trend":        "UP",
            "lead_time":    "2-4 wks",
            "implication":  "Short-term container demand pulse; watch inventory builds",
        },
        {
            "indicator":    "Ocean Carrier Capacity",
            "value":        "23.4M TEU",
            "trend":        "UP",
            "lead_time":    "3-6 mo",
            "implication":  "Delivery overhang pressures container freight rates",
        },
        {
            "indicator":    "Inventory-to-Sales Ratio",
            "value":        "1.36x",
            "trend":        "DOWN",
            "lead_time":    "6-8 wks",
            "implication":  "Destocking cycle nearing end; restocking wave likely",
        },
        {
            "indicator":    "Global Trade Finance Volume",
            "value":        "$1.74T",
            "trend":        "UP",
            "lead_time":    "4-8 wks",
            "implication":  "Letters of credit up 6% MoM; trade activity accelerating",
        },
        {
            "indicator":    "OECD CLI",
            "value":        "100.4",
            "trend":        "UP",
            "lead_time":    "3-6 mo",
            "implication":  "Composite leading index above 100 signals expansion",
        },
        {
            "indicator":    "IMF WEO Revisions",
            "value":        "+0.1pp (2026)",
            "trend":        "UP",
            "lead_time":    "6-12 mo",
            "implication":  "Marginal upgrade; upside risk to trade volume projections",
        },
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
            {"type": "VLCC",       "spread_bps": 185, "all_in_pct": 7.16, "ltv_pct": 60},
            {"type": "Capesize",   "spread_bps": 200, "all_in_pct": 7.31, "ltv_pct": 60},
            {"type": "Panamax",    "spread_bps": 210, "all_in_pct": 7.41, "ltv_pct": 62},
            {"type": "Containership (LRG)","spread_bps": 175,"all_in_pct": 7.06,"ltv_pct": 60},
            {"type": "LNG Carrier","spread_bps": 160, "all_in_pct": 6.91, "ltv_pct": 65},
        ],
        "hy_spreads": {
            "Shipping HY OAS (bps)": 485,
            "vs 12M Avg (bps)":      +32,
            "vs Investment Grade":   "+318 bps",
            "Distressed Threshold":  "1000 bps",
        },
        "orderbook_sensitivity": [
            {"rate_scenario": "Rates -100bps", "new_orders_delta": "+18%",  "sentiment": "POSITIVE"},
            {"rate_scenario": "Rates Flat",    "new_orders_delta": "Flat",   "sentiment": "NEUTRAL"},
            {"rate_scenario": "Rates +100bps", "new_orders_delta": "-14%",  "sentiment": "NEGATIVE"},
            {"rate_scenario": "Rates +200bps", "new_orders_delta": "-29%",  "sentiment": "NEGATIVE"},
        ],
    }


def _mock_commodities() -> list[dict]:
    return [
        {"commodity": "WTI Crude",   "unit": "$/bbl", "price": 79.8,  "wow": -0.8, "mom": +2.1,  "yoy": -6.3,  "route": "MR Tanker / USGC-ARA"},
        {"commodity": "Brent Crude", "unit": "$/bbl", "price": 82.4,  "wow": -0.6, "mom": +1.8,  "yoy": -5.9,  "route": "VLCC / TD3C"},
        {"commodity": "LNG",         "unit": "$/mmBTU","price": 9.8,  "wow": +1.2, "mom": -3.1,  "yoy": -38.2, "route": "LNG Carrier / Pacific"},
        {"commodity": "Thermal Coal","unit": "$/t",   "price": 118.5, "wow": -1.4, "mom": -2.6,  "yoy": -21.0, "route": "Capesize / Richards Bay"},
        {"commodity": "Iron Ore",    "unit": "$/t",   "price": 107.2, "wow": +0.9, "mom": +3.2,  "yoy": -14.8, "route": "Capesize / C5 Australia-China"},
        {"commodity": "Copper",      "unit": "$/t",   "price": 9_340, "wow": +1.1, "mom": +4.6,  "yoy": +8.2,  "route": "Supramax / Any-China"},
        {"commodity": "Wheat",       "unit": "$/bu",  "price": 5.82,  "wow": -0.4, "mom": -1.7,  "yoy": -12.4, "route": "Handysize-Supramax / USEC-Asia"},
    ]


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _status_badge(status: str) -> str:
    """Return an inline HTML badge for EXPANDING / CONTRACTING / STABLE."""
    colors = {
        "EXPANDING":   (C_HIGH,   "#052e1a"),
        "CONTRACTING": (C_LOW,    "#2d0a0a"),
        "STABLE":      (C_MOD,    "#2d1f00"),
        "POSITIVE":    (C_HIGH,   "#052e1a"),
        "NEGATIVE":    (C_LOW,    "#2d0a0a"),
        "NEUTRAL":     (C_MOD,    "#2d1f00"),
        "UP":          (C_HIGH,   "#052e1a"),
        "DOWN":        (C_LOW,    "#2d0a0a"),
        "FLAT":        (C_MOD,    "#2d1f00"),
    }
    fg, bg = colors.get(status, (C_TEXT2, C_CARD))
    return (
        f'<span style="background:{bg};color:{fg};border:1px solid {fg}33;'
        f'padding:2px 8px;border-radius:4px;font-size:11px;font-weight:700;'
        f'letter-spacing:0.05em;">{status}</span>'
    )


def _delta_html(delta: float, unit: str = "") -> str:
    """Return coloured delta string with sign."""
    if delta > 0:
        color = C_HIGH
        sign  = "+"
    elif delta < 0:
        color = C_LOW
        sign  = ""
    else:
        color = C_TEXT3
        sign  = ""
    val = f"{sign}{delta:+.2f}".replace("++", "+")
    return f'<span style="color:{color};font-size:12px;">{val}{unit}</span>'


def _section_title(text: str, subtitle: str = "") -> None:
    sub_html = f'<div style="color:{C_TEXT3};font-size:12px;margin-top:2px;">{subtitle}</div>' if subtitle else ""
    st.markdown(
        f'<div style="margin:28px 0 14px 0;padding-bottom:10px;border-bottom:1px solid {C_BORDER};">'
        f'<span style="color:{C_TEXT};font-size:17px;font-weight:700;letter-spacing:-0.01em;">{text}</span>'
        f'{sub_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def _kpi_card(label: str, value: str, prior: str, delta: float,
              unit: str, status: str) -> str:
    """Return an HTML KPI card string."""
    delta_html = _delta_html(delta, unit)
    badge = _status_badge(status)
    return (
        f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:10px;'
        f'padding:14px 16px;min-width:140px;">'
        f'<div style="color:{C_TEXT3};font-size:11px;font-weight:600;letter-spacing:0.06em;'
        f'text-transform:uppercase;margin-bottom:6px;">{label}</div>'
        f'<div style="color:{C_TEXT};font-size:24px;font-weight:700;line-height:1;">'
        f'{value}{unit}</div>'
        f'<div style="margin-top:6px;display:flex;align-items:center;gap:8px;">'
        f'<span style="color:{C_TEXT3};font-size:11px;">Prior: {prior}{unit}</span>'
        f'{delta_html}'
        f'</div>'
        f'<div style="margin-top:8px;">{badge}</div>'
        f'</div>'
    )


def _region_header(title: str, color: str) -> None:
    st.markdown(
        f'<div style="color:{color};font-size:13px;font-weight:700;letter-spacing:0.06em;'
        f'text-transform:uppercase;margin:18px 0 8px 0;padding-left:10px;'
        f'border-left:3px solid {color};">{title}</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------------------
# Section 1: Global Macro Dashboard
# ---------------------------------------------------------------------------

def _render_macro_dashboard(kpis: dict) -> None:
    _section_title(
        "Global Macro Dashboard",
        "Real-time macro readings across World / US / China / EU with directional signals",
    )

    region_colors = {
        "world":  C_ACCENT,
        "us":     C_HIGH,
        "china":  C_LOW,
        "eu":     C_MOD,
    }
    region_labels = {
        "world": "World",
        "us":    "United States",
        "china": "China",
        "eu":    "European Union",
    }

    for region, data in kpis.items():
        try:
            _region_header(region_labels.get(region, region), region_colors.get(region, C_TEXT2))
            items   = list(data.items())
            n_cols  = min(len(items), 5)
            cols    = st.columns(n_cols)
            for i, (label, d) in enumerate(items):
                col_idx = i % n_cols
                unit    = d.get("unit", "")
                val     = d.get("value", 0)
                prior   = d.get("prior", 0)
                delta   = d.get("delta", 0.0)
                status  = d.get("status", "STABLE")
                val_str   = f"{val:,.1f}" if isinstance(val, float) else str(val)
                prior_str = f"{prior:,.1f}" if isinstance(prior, float) else str(prior)
                with cols[col_idx]:
                    st.markdown(
                        _kpi_card(label, val_str, prior_str, delta, unit, status),
                        unsafe_allow_html=True,
                    )
        except Exception as exc:
            logger.warning(f"Macro dashboard region {region} error: {exc}")


# ---------------------------------------------------------------------------
# Section 2: Shipping Demand Drivers
# ---------------------------------------------------------------------------

def _render_demand_drivers(drivers: list[dict]) -> None:
    _section_title(
        "Shipping Demand Drivers",
        "Macro factor → shipping segment cause-effect analysis",
    )
    try:
        header_style = (
            f"background:{C_SURFACE};color:{C_TEXT3};font-size:11px;font-weight:700;"
            f"letter-spacing:0.06em;text-transform:uppercase;padding:8px 12px;"
            f"border-bottom:1px solid {C_BORDER};"
        )
        row_style_a = f"background:{C_CARD};padding:10px 12px;border-bottom:1px solid {C_BORDER};"
        row_style_b = f"background:{C_SURFACE};padding:10px 12px;border-bottom:1px solid {C_BORDER};"

        header_html = (
            f'<div style="display:grid;grid-template-columns:1.8fr 1fr 1fr 0.7fr 0.7fr 2fr;'
            f'border-radius:8px 8px 0 0;overflow:hidden;border:1px solid {C_BORDER};">'
            f'<div style="{header_style}">Macro Factor</div>'
            f'<div style="{header_style}">Segment</div>'
            f'<div style="{header_style}">Current Reading</div>'
            f'<div style="{header_style}">Trend</div>'
            f'<div style="{header_style}">Impact</div>'
            f'<div style="{header_style}">Shipping Assessment</div>'
            f'</div>'
        )
        st.markdown(header_html, unsafe_allow_html=True)

        rows_html = f'<div style="border:1px solid {C_BORDER};border-top:none;border-radius:0 0 8px 8px;overflow:hidden;">'
        for i, d in enumerate(drivers):
            rs = row_style_a if i % 2 == 0 else row_style_b
            trend_badge  = _status_badge(d.get("trend",  "FLAT"))
            impact_badge = _status_badge(d.get("impact", "NEUTRAL"))
            rows_html += (
                f'<div style="display:grid;grid-template-columns:1.8fr 1fr 1fr 0.7fr 0.7fr 2fr;">'
                f'<div style="{rs}color:{C_TEXT};font-size:13px;font-weight:600;">{d["factor"]}</div>'
                f'<div style="{rs}color:{C_ACCENT};font-size:12px;">{d["segment"]}</div>'
                f'<div style="{rs}color:{C_TEXT2};font-size:12px;font-family:monospace;">{d["current"]}</div>'
                f'<div style="{rs}">{trend_badge}</div>'
                f'<div style="{rs}">{impact_badge}</div>'
                f'<div style="{rs}color:{C_TEXT2};font-size:12px;">{d["assessment"]}</div>'
                f'</div>'
            )
        rows_html += "</div>"
        st.markdown(rows_html, unsafe_allow_html=True)
    except Exception as exc:
        logger.warning(f"Demand drivers render error: {exc}")
        st.info("Demand drivers data unavailable.")


# ---------------------------------------------------------------------------
# Section 3: Leading Indicators
# ---------------------------------------------------------------------------

def _render_leading_indicators(indicators: list[dict]) -> None:
    _section_title(
        "Leading Indicators",
        "3-6 month forward view on freight market direction",
    )
    try:
        cols = st.columns(4)
        for i, ind in enumerate(indicators):
            try:
                trend   = ind.get("trend", "FLAT")
                c_trend = C_HIGH if trend == "UP" else (C_LOW if trend == "DOWN" else C_MOD)
                arrow   = "▲" if trend == "UP" else ("▼" if trend == "DOWN" else "▬")
                card_html = (
                    f'<div style="background:{C_CARD};border:1px solid {C_BORDER};'
                    f'border-radius:10px;padding:14px;margin-bottom:12px;">'
                    f'<div style="display:flex;justify-content:space-between;align-items:flex-start;">'
                    f'<div style="color:{C_TEXT};font-size:13px;font-weight:600;line-height:1.3;'
                    f'max-width:75%;">{ind["indicator"]}</div>'
                    f'<span style="color:{c_trend};font-size:18px;">{arrow}</span>'
                    f'</div>'
                    f'<div style="color:{C_ACCENT};font-size:20px;font-weight:700;margin:8px 0 4px 0;'
                    f'font-family:monospace;">{ind["value"]}</div>'
                    f'<div style="display:flex;gap:6px;align-items:center;margin-bottom:8px;">'
                    f'<span style="color:{C_TEXT3};font-size:11px;background:{C_SURFACE};'
                    f'padding:2px 6px;border-radius:4px;">Lead: {ind["lead_time"]}</span>'
                    f'</div>'
                    f'<div style="color:{C_TEXT2};font-size:11px;line-height:1.4;'
                    f'border-top:1px solid {C_BORDER};padding-top:8px;">{ind["implication"]}</div>'
                    f'</div>'
                )
                with cols[i % 4]:
                    st.markdown(card_html, unsafe_allow_html=True)
            except Exception as exc:
                logger.warning(f"Leading indicator card {i} error: {exc}")
    except Exception as exc:
        logger.warning(f"Leading indicators render error: {exc}")
        st.info("Leading indicators data unavailable.")


# ---------------------------------------------------------------------------
# Section 4: OECD / IMF Data Panel
# ---------------------------------------------------------------------------

def _load_oecd_imf() -> dict:
    """Try real feeds; fall back to mock."""
    try:
        from data import oecd_feed  # type: ignore
        data = oecd_feed.get_macro_summary()
        logger.info("OECD feed loaded successfully.")
        return data
    except Exception:
        pass
    try:
        from data import imf_feed  # type: ignore
        data = imf_feed.get_macro_summary()
        logger.info("IMF feed loaded successfully.")
        return data
    except Exception:
        pass
    logger.info("OECD/IMF feeds unavailable — using mock data.")
    return _mock_oecd_imf()


def _render_oecd_imf(data: dict) -> None:
    _section_title(
        "OECD / IMF Data Panel",
        "GDP forecasts, trade flows, and commodity outlooks (OECD/IMF sourced where available)",
    )
    try:
        tab_gdp, tab_trade, tab_comm = st.tabs(["GDP Forecasts", "Trade Flows", "Commodity Outlook"])

        with tab_gdp:
            try:
                rows = data.get("gdp_forecasts", [])
                th = f"color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;"
                tbl = (
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr>'
                    f'<th style="{th}text-align:left;padding:8px 12px;">Country</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">2025F</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">2026F</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">Revision</th>'
                    f'</tr></thead><tbody>'
                )
                for i, r in enumerate(rows):
                    bg   = C_CARD if i % 2 == 0 else C_SURFACE
                    rev  = r.get("revision", 0)
                    rev_c = C_HIGH if rev > 0 else (C_LOW if rev < 0 else C_TEXT3)
                    rev_s = f"+{rev:.1f}pp" if rev > 0 else f"{rev:.1f}pp"
                    bold  = "font-weight:700;" if r["country"] == "World" else ""
                    tbl += (
                        f'<tr style="background:{bg};">'
                        f'<td style="color:{C_TEXT};font-size:13px;{bold}padding:9px 12px;">{r["country"]}</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">{r["2025F"]:.1f}%</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">{r["2026F"]:.1f}%</td>'
                        f'<td style="color:{rev_c};font-size:13px;text-align:right;padding:9px 12px;font-weight:600;">{rev_s}</td>'
                        f'</tr>'
                    )
                tbl += "</tbody></table>"
                st.markdown(
                    f'<div style="border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">{tbl}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"GDP forecast table error: {exc}")

        with tab_trade:
            try:
                rows = data.get("trade_flows", [])
                th = f"color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;"
                tbl = (
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr>'
                    f'<th style="{th}text-align:left;padding:8px 12px;">Region</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">Volume ($B)</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">YoY %</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">Global Share</th>'
                    f'</tr></thead><tbody>'
                )
                for i, r in enumerate(rows):
                    bg   = C_CARD if i % 2 == 0 else C_SURFACE
                    yoy  = r.get("yoy_pct", 0)
                    yoy_c = C_HIGH if yoy > 0 else C_LOW
                    tbl += (
                        f'<tr style="background:{bg};">'
                        f'<td style="color:{C_TEXT};font-size:13px;padding:9px 12px;">{r["region"]}</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">${r["volume_bn"]:,.0f}B</td>'
                        f'<td style="color:{yoy_c};font-size:13px;font-weight:600;text-align:right;padding:9px 12px;">{"+" if yoy>0 else ""}{yoy:.1f}%</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">{r["share_pct"]:.1f}%</td>'
                        f'</tr>'
                    )
                tbl += "</tbody></table>"
                st.markdown(
                    f'<div style="border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">{tbl}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"Trade flow table error: {exc}")

        with tab_comm:
            try:
                rows = data.get("commodity_forecasts", [])
                th = f"color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;"
                tbl = (
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr>'
                    f'<th style="{th}text-align:left;padding:8px 12px;">Commodity</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">Unit</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">2025F</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">2026F</th>'
                    f'<th style="{th}text-align:right;padding:8px 12px;">Risk</th>'
                    f'</tr></thead><tbody>'
                )
                for i, r in enumerate(rows):
                    bg   = C_CARD if i % 2 == 0 else C_SURFACE
                    risk = r.get("risk", "FLAT")
                    tbl += (
                        f'<tr style="background:{bg};">'
                        f'<td style="color:{C_TEXT};font-size:13px;padding:9px 12px;">{r["commodity"]}</td>'
                        f'<td style="color:{C_TEXT3};font-size:12px;text-align:right;padding:9px 12px;">{r["unit"]}</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">{r["2025F"]:,.1f}</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 12px;">{r["2026F"]:,.1f}</td>'
                        f'<td style="text-align:right;padding:9px 12px;">{_status_badge(risk)}</td>'
                        f'</tr>'
                    )
                tbl += "</tbody></table>"
                st.markdown(
                    f'<div style="border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">{tbl}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"Commodity forecast table error: {exc}")

    except Exception as exc:
        logger.warning(f"OECD/IMF panel render error: {exc}")
        st.info("OECD/IMF panel data unavailable.")


# ---------------------------------------------------------------------------
# Section 5: Interest Rate & Credit Impact
# ---------------------------------------------------------------------------

def _render_rates_credit(data: dict) -> None:
    _section_title(
        "Interest Rate & Credit Impact",
        "How the current rate environment shapes vessel financing, orderbook, and credit spreads",
    )
    try:
        left, right = st.columns([1, 1])

        with left:
            try:
                st.markdown(
                    f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                    f'text-transform:uppercase;margin-bottom:10px;">Benchmark Rates</div>',
                    unsafe_allow_html=True,
                )
                base = data.get("base_rates", {})
                rate_html = f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">'
                for j, (name, val) in enumerate(base.items()):
                    bg = C_CARD if j % 2 == 0 else C_SURFACE
                    rate_html += (
                        f'<div style="background:{bg};display:flex;justify-content:space-between;'
                        f'align-items:center;padding:9px 14px;border-bottom:1px solid {C_BORDER};">'
                        f'<span style="color:{C_TEXT};font-size:13px;">{name}</span>'
                        f'<span style="color:{C_ACCENT};font-size:14px;font-weight:700;font-family:monospace;">{val:.2f}%</span>'
                        f'</div>'
                    )
                rate_html += "</div>"
                st.markdown(rate_html, unsafe_allow_html=True)
            except Exception as exc:
                logger.warning(f"Base rates render error: {exc}")

            st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

            try:
                st.markdown(
                    f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                    f'text-transform:uppercase;margin-bottom:10px;">HY Spreads — Shipping Bonds</div>',
                    unsafe_allow_html=True,
                )
                hy = data.get("hy_spreads", {})
                hy_html = f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">'
                for j, (name, val) in enumerate(hy.items()):
                    bg = C_CARD if j % 2 == 0 else C_SURFACE
                    val_color = C_TEXT
                    if isinstance(val, (int, float)):
                        val_str = f"{val:+.0f} bps" if "vs" in name else f"{val:,.0f} bps"
                        val_color = C_MOD if val > 400 else C_HIGH
                    else:
                        val_str = str(val)
                    hy_html += (
                        f'<div style="background:{bg};display:flex;justify-content:space-between;'
                        f'align-items:center;padding:9px 14px;border-bottom:1px solid {C_BORDER};">'
                        f'<span style="color:{C_TEXT};font-size:13px;">{name}</span>'
                        f'<span style="color:{val_color};font-size:14px;font-weight:700;font-family:monospace;">{val_str}</span>'
                        f'</div>'
                    )
                hy_html += "</div>"
                st.markdown(hy_html, unsafe_allow_html=True)
            except Exception as exc:
                logger.warning(f"HY spreads render error: {exc}")

        with right:
            try:
                st.markdown(
                    f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                    f'text-transform:uppercase;margin-bottom:10px;">Vessel Financing — All-In Cost</div>',
                    unsafe_allow_html=True,
                )
                vf   = data.get("vessel_financing", [])
                th   = f"color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;"
                vtbl = (
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr>'
                    f'<th style="{th}text-align:left;padding:8px 10px;">Vessel Type</th>'
                    f'<th style="{th}text-align:right;padding:8px 10px;">Spread</th>'
                    f'<th style="{th}text-align:right;padding:8px 10px;">All-In</th>'
                    f'<th style="{th}text-align:right;padding:8px 10px;">Max LTV</th>'
                    f'</tr></thead><tbody>'
                )
                for k, v in enumerate(vf):
                    bg = C_CARD if k % 2 == 0 else C_SURFACE
                    vtbl += (
                        f'<tr style="background:{bg};">'
                        f'<td style="color:{C_TEXT};font-size:13px;padding:9px 10px;">{v["type"]}</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;font-family:monospace;padding:9px 10px;">{v["spread_bps"]} bps</td>'
                        f'<td style="color:{C_MOD};font-size:13px;font-weight:700;text-align:right;font-family:monospace;padding:9px 10px;">{v["all_in_pct"]:.2f}%</td>'
                        f'<td style="color:{C_TEXT2};font-size:13px;text-align:right;padding:9px 10px;">{v["ltv_pct"]}%</td>'
                        f'</tr>'
                    )
                vtbl += "</tbody></table>"
                st.markdown(
                    f'<div style="border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">{vtbl}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as exc:
                logger.warning(f"Vessel financing table error: {exc}")

            st.markdown("<div style='height:16px;'></div>", unsafe_allow_html=True)

            try:
                st.markdown(
                    f'<div style="color:{C_TEXT2};font-size:12px;font-weight:700;letter-spacing:0.05em;'
                    f'text-transform:uppercase;margin-bottom:10px;">Newbuild Order Book — Rate Sensitivity</div>',
                    unsafe_allow_html=True,
                )
                obs  = data.get("orderbook_sensitivity", [])
                ob_html = f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:8px;overflow:hidden;">'
                for j, row in enumerate(obs):
                    bg  = C_CARD if j % 2 == 0 else C_SURFACE
                    snt = row.get("sentiment", "NEUTRAL")
                    ob_html += (
                        f'<div style="background:{bg};display:flex;justify-content:space-between;'
                        f'align-items:center;padding:9px 14px;border-bottom:1px solid {C_BORDER};">'
                        f'<span style="color:{C_TEXT};font-size:13px;">{row["rate_scenario"]}</span>'
                        f'<div style="display:flex;gap:10px;align-items:center;">'
                        f'<span style="color:{C_TEXT2};font-size:13px;font-family:monospace;">{row["new_orders_delta"]}</span>'
                        f'{_status_badge(snt)}'
                        f'</div>'
                        f'</div>'
                    )
                ob_html += "</div>"
                st.markdown(ob_html, unsafe_allow_html=True)
            except Exception as exc:
                logger.warning(f"Orderbook sensitivity render error: {exc}")

    except Exception as exc:
        logger.warning(f"Rates & credit panel render error: {exc}")
        st.info("Interest rate & credit data unavailable.")


# ---------------------------------------------------------------------------
# Section 6: Commodity Price Dashboard
# ---------------------------------------------------------------------------

def _render_commodities(rows: list[dict]) -> None:
    _section_title(
        "Commodity Price Dashboard",
        "Key commodities driving shipping demand — prices, momentum, and route sensitivity",
    )
    try:
        th = f"color:{C_TEXT3};font-size:11px;font-weight:700;letter-spacing:0.05em;text-transform:uppercase;"
        tbl = (
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_SURFACE};">'
            f'<th style="{th}text-align:left;padding:10px 14px;">Commodity</th>'
            f'<th style="{th}text-align:right;padding:10px 14px;">Price</th>'
            f'<th style="{th}text-align:right;padding:10px 14px;">WoW %</th>'
            f'<th style="{th}text-align:right;padding:10px 14px;">MoM %</th>'
            f'<th style="{th}text-align:right;padding:10px 14px;">YoY %</th>'
            f'<th style="{th}text-align:left;padding:10px 14px;">Key Shipping Route</th>'
            f'</tr></thead><tbody>'
        )
        for i, r in enumerate(rows):
            try:
                bg     = C_CARD if i % 2 == 0 else C_SURFACE
                price  = r.get("price", 0)
                unit   = r.get("unit", "")
                wow    = r.get("wow", 0)
                mom    = r.get("mom", 0)
                yoy    = r.get("yoy", 0)

                def _pct_cell(v: float) -> str:
                    c   = C_HIGH if v > 0 else (C_LOW if v < 0 else C_TEXT3)
                    sgn = "+" if v > 0 else ""
                    return f'<td style="color:{c};font-size:13px;font-weight:600;text-align:right;padding:10px 14px;font-family:monospace;">{sgn}{v:.1f}%</td>'

                price_str = f"{price:,.1f}" if price < 1000 else f"{price:,.0f}"
                tbl += (
                    f'<tr style="background:{bg};">'
                    f'<td style="color:{C_TEXT};font-size:13px;font-weight:600;padding:10px 14px;">{r["commodity"]}</td>'
                    f'<td style="color:{C_ACCENT};font-size:14px;font-weight:700;text-align:right;'
                    f'padding:10px 14px;font-family:monospace;">{price_str} <span style="color:{C_TEXT3};'
                    f'font-size:11px;font-weight:400;">{unit}</span></td>'
                    + _pct_cell(wow)
                    + _pct_cell(mom)
                    + _pct_cell(yoy)
                    + f'<td style="color:{C_TEXT2};font-size:12px;padding:10px 14px;">{r.get("route","—")}</td>'
                    f'</tr>'
                )
            except Exception as exc:
                logger.warning(f"Commodity row {i} error: {exc}")
        tbl += "</tbody></table>"
        st.markdown(
            f'<div style="border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">{tbl}</div>',
            unsafe_allow_html=True,
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
        # Page-level header
        st.markdown(
            f'<div style="display:flex;align-items:baseline;gap:14px;margin-bottom:6px;">'
            f'<span style="color:{C_TEXT};font-size:24px;font-weight:800;letter-spacing:-0.02em;">'
            f'Global Macro Intelligence</span>'
            f'<span style="color:{C_TEXT3};font-size:13px;">Goldman Sachs Global Markets | '
            f'Freight Market Analysis</span>'
            f'</div>'
            f'<div style="color:{C_TEXT3};font-size:12px;margin-bottom:24px;'
            f'padding-bottom:16px;border-bottom:1px solid {C_BORDER};">'
            f'Macro drivers, leading indicators, and commodity dynamics influencing '
            f'global shipping demand across all vessel segments'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as exc:
        logger.warning(f"Header render error: {exc}")

    # 1. Global Macro Dashboard
    try:
        kpis = {}
        if isinstance(macro_data, dict) and "kpis" in macro_data:
            kpis = macro_data["kpis"]
        else:
            kpis = _mock_global_kpis()
        _render_macro_dashboard(kpis)
    except Exception as exc:
        logger.error(f"Section 1 (Macro Dashboard) error: {exc}")
        st.error("Macro dashboard unavailable.")

    # 2. Shipping Demand Drivers
    try:
        drivers = []
        if isinstance(macro_data, dict) and "demand_drivers" in macro_data:
            drivers = macro_data["demand_drivers"]
        else:
            drivers = _mock_demand_drivers()
        _render_demand_drivers(drivers)
    except Exception as exc:
        logger.error(f"Section 2 (Demand Drivers) error: {exc}")
        st.error("Demand drivers unavailable.")

    # 3. Leading Indicators
    try:
        indicators = []
        if isinstance(macro_data, dict) and "leading_indicators" in macro_data:
            indicators = macro_data["leading_indicators"]
        else:
            indicators = _mock_leading_indicators()
        _render_leading_indicators(indicators)
    except Exception as exc:
        logger.error(f"Section 3 (Leading Indicators) error: {exc}")
        st.error("Leading indicators unavailable.")

    # 4. OECD / IMF Panel
    try:
        oecd_imf_data = {}
        if isinstance(macro_data, dict) and "oecd_imf" in macro_data:
            oecd_imf_data = macro_data["oecd_imf"]
        else:
            oecd_imf_data = _load_oecd_imf()
        _render_oecd_imf(oecd_imf_data)
    except Exception as exc:
        logger.error(f"Section 4 (OECD/IMF) error: {exc}")
        st.error("OECD/IMF panel unavailable.")

    # 5. Interest Rate & Credit
    try:
        rates_data = {}
        if isinstance(macro_data, dict) and "rates_credit" in macro_data:
            rates_data = macro_data["rates_credit"]
        else:
            rates_data = _mock_rates_credit()
        _render_rates_credit(rates_data)
    except Exception as exc:
        logger.error(f"Section 5 (Rates & Credit) error: {exc}")
        st.error("Interest rate & credit panel unavailable.")

    # 6. Commodity Price Dashboard
    try:
        commodities = []
        if isinstance(macro_data, dict) and "commodities" in macro_data:
            commodities = macro_data["commodities"]
        else:
            commodities = _mock_commodities()
        _render_commodities(commodities)
    except Exception as exc:
        logger.error(f"Section 6 (Commodities) error: {exc}")
        st.error("Commodity dashboard unavailable.")
