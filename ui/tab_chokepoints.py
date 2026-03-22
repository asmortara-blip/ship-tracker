"""
Strategic Waterway & Chokepoint Intelligence Tab

Comprehensive analysis of the world's critical maritime chokepoints including:
  1. Chokepoint Status Board        — hero HTML table, all 6 major chokepoints
  2. Canal Deep Dives               — Panama + Suez side-by-side cards
  3. Red Sea Crisis Monitor         — Houthi timeline, carrier policies, insurance
  4. Chokepoint Traffic Map         — Plotly scatter_geo with routes
  5. Rate Premium Analysis          — per-route disruption premium table
  6. Historical Disruption Comparison — major incidents comparison table
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

try:
    from data.canal_feed import fetch_panama_stats, fetch_suez_stats, get_canal_shipping_impact
    _CANAL_OK = True
except Exception as _ce:
    _CANAL_OK = False
    logger.warning(f"canal_feed unavailable: {_ce}")

# ---------------------------------------------------------------------------
# Palette
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
# Static Data
# ---------------------------------------------------------------------------

_CHOKEPOINTS = [
    {
        "name":          "Suez Canal",
        "daily_transits": "~45 / day",
        "status":        "RESTRICTED",
        "wait_time":     "3–7 days",
        "rate_premium":  "+$1,800–2,400/TEU",
        "risk_level":    "CRITICAL",
        "note":          "40–50% traffic reduction due to Red Sea conflict",
    },
    {
        "name":          "Panama Canal",
        "daily_transits": "~34 / day",
        "status":        "RESTRICTED",
        "wait_time":     "5–12 days",
        "rate_premium":  "+$400–800/TEU",
        "risk_level":    "HIGH",
        "note":          "Drought recovery — Gatun Lake levels rising but below norm",
    },
    {
        "name":          "Strait of Malacca",
        "daily_transits": "~84,000 / year",
        "status":        "NORMAL",
        "wait_time":     "< 1 day",
        "rate_premium":  "Minimal",
        "risk_level":    "LOW",
        "note":          "Heaviest traffic chokepoint globally — piracy risk residual",
    },
    {
        "name":          "Bab-el-Mandeb (Red Sea)",
        "daily_transits": "~21,000 / year",
        "status":        "HIGH RISK",
        "wait_time":     "N/A — avoidance",
        "rate_premium":  "+$2,000–3,500/TEU",
        "risk_level":    "CRITICAL",
        "note":          "Houthi missile/drone attacks; most carriers avoiding entirely",
    },
    {
        "name":          "Strait of Hormuz",
        "daily_transits": "~21M bbl/day oil",
        "status":        "ELEVATED",
        "wait_time":     "1–2 days",
        "rate_premium":  "+$0.5–1.5/bbl tanker premium",
        "risk_level":    "HIGH",
        "note":          "Iran tensions elevated; military presence increased",
    },
    {
        "name":          "Danish Straits (Baltic)",
        "daily_transits": "Baltic access",
        "status":        "NORMAL",
        "wait_time":     "< 1 day",
        "rate_premium":  "Minimal",
        "risk_level":    "LOW",
        "note":          "NATO monitoring; undersea cable incidents; ice seasonal risk",
    },
]

_STATUS_COLORS = {
    "CRITICAL":  C_LOW,
    "HIGH":      "#f97316",
    "ELEVATED":  C_MOD,
    "NORMAL":    C_HIGH,
    "HIGH RISK": C_LOW,
}

_RISK_COLORS = {
    "CRITICAL":  C_LOW,
    "HIGH":      "#f97316",
    "MODERATE":  C_MOD,
    "LOW":       C_HIGH,
}

_HOUTHI_INCIDENTS = [
    {"date": "Oct 19 2023",  "vessel": "Multiple US warships",     "type": "Missile/drone",   "outcome": "Intercepted by USS Carney"},
    {"date": "Nov 19 2023",  "vessel": "Galaxy Leader",            "type": "Seizure",         "outcome": "Vessel and 25 crew captured; held"},
    {"date": "Dec 03 2023",  "vessel": "Unity Explorer / others",  "type": "Missile attack",  "outcome": "Damage; crew evacuated"},
    {"date": "Jan 09 2024",  "vessel": "Maersk Hangzhou",          "type": "Drone/missile",   "outcome": "US Navy intervened; Maersk paused Red Sea"},
    {"date": "Jan 26 2024",  "vessel": "Marlin Luanda (BP tanker)","type": "Missile strike",  "outcome": "Fire onboard; diverted"},
    {"date": "Feb 18 2024",  "vessel": "Rubymar (bulk carrier)",   "type": "Anti-ship missile","outcome": "Sank Feb 27 — first sinking of crisis"},
    {"date": "Mar 06 2024",  "vessel": "True Confidence",          "type": "Missile",         "outcome": "3 crew killed; partial sinking"},
    {"date": "Jun 12 2024",  "vessel": "Tutor (Greek tanker)",     "type": "Drone + boat",    "outcome": "Sank Jun 18 after sustained damage"},
    {"date": "Sep 02 2024",  "vessel": "Groton (container)",       "type": "Missile",         "outcome": "Damage; crew evacuated"},
    {"date": "Jan 15 2025",  "vessel": "Multiple vessels",         "type": "Ballistic missile","outcome": "Operations Prosperity Guardian engaged"},
]

_CARRIER_POLICIES = [
    {"carrier": "Maersk",      "policy": "Avoiding Red Sea", "since": "Jan 2024", "escort": "No"},
    {"carrier": "MSC",         "policy": "Avoiding Red Sea", "since": "Dec 2023", "escort": "No"},
    {"carrier": "CMA CGM",     "policy": "Avoiding Red Sea", "since": "Dec 2023", "escort": "No"},
    {"carrier": "Hapag-Lloyd", "policy": "Avoiding Red Sea", "since": "Dec 2023", "escort": "No"},
    {"carrier": "Evergreen",   "policy": "Avoiding Red Sea", "since": "Jan 2024", "escort": "No"},
    {"carrier": "COSCO",       "policy": "Selective transit","since": "—",        "escort": "Naval escort"},
    {"carrier": "ONE",         "policy": "Avoiding Red Sea", "since": "Jan 2024", "escort": "No"},
    {"carrier": "Yang Ming",   "policy": "Avoiding Red Sea", "since": "Jan 2024", "escort": "No"},
]

_RATE_PREMIUMS = [
    {"route": "Asia → North Europe",       "baseline": "$1,200/TEU", "current": "$3,000–4,200/TEU", "premium": "+$1,800–3,000/TEU", "driver": "Red Sea / Suez avoidance"},
    {"route": "Asia → Mediterranean",      "baseline": "$900/TEU",   "current": "$2,700–3,800/TEU", "premium": "+$1,800–2,900/TEU", "driver": "Red Sea / Suez avoidance"},
    {"route": "US East Coast → Asia",      "baseline": "$800/TEU",   "current": "$1,200–2,000/TEU", "premium": "+$400–1,200/TEU",   "driver": "Panama drought surcharge"},
    {"route": "US West Coast → Asia",      "baseline": "$600/TEU",   "current": "$700–900/TEU",     "premium": "Minimal",           "driver": "No major chokepoint active"},
    {"route": "Middle East → Asia (oil)",  "baseline": "WS 60",      "current": "WS 75–90",         "premium": "+WS 15–30",         "driver": "Hormuz tension / insurance"},
    {"route": "N. Europe → US East Coast", "baseline": "$500/TEU",   "current": "$600–750/TEU",     "premium": "+$100–250/TEU",     "driver": "Indirect Cape re-routing lag"},
]

_HISTORICAL_INCIDENTS = [
    {
        "incident":  "Ever Given — Suez Blockage",
        "date":      "Mar 23–29, 2021",
        "duration":  "6 days",
        "route":     "Asia ↔ Europe (Suez)",
        "rate_impact": "+30–40% spot rates; $54B/day trade delayed",
        "vessels":   "369 vessels queued",
    },
    {
        "incident":  "Houthi / Red Sea Crisis",
        "date":      "Oct 2023 – present",
        "duration":  "Ongoing (15+ months)",
        "route":     "Asia ↔ Europe (Red Sea / Suez)",
        "rate_impact": "+150–200% Asia-Europe spot from Dec 2023 lows",
        "vessels":   "~70% of Suez container traffic rerouted",
    },
    {
        "incident":  "Panama Canal Drought",
        "date":      "Jul 2023 – early 2024",
        "duration":  "~9 months",
        "route":     "US East Coast ↔ Asia; LNG",
        "rate_impact": "+20–40% US East Coast surcharges; LNG delays",
        "vessels":   "Max ~24/day (vs 36–38 normal)",
    },
    {
        "incident":  "Shanghai / China Lockdowns",
        "date":      "Apr–Jun 2022",
        "duration":  "~2 months",
        "route":     "Trans-Pacific; Asia origin",
        "rate_impact": "SCFI peaked $5,100/TEU; port congestion cascaded globally",
        "vessels":   "500,000+ TEU of floating inventory",
    },
    {
        "incident":  "Gulf of Aden Piracy Peak",
        "date":      "2008–2011",
        "duration":  "3+ years",
        "route":     "Asia ↔ Europe (Red Sea / Suez)",
        "rate_impact": "+$500–800/TEU war risk insurance; BMP protocols",
        "vessels":   "~1,000 attacks; 188 vessels hijacked",
    },
    {
        "incident":  "COVID Port Congestion",
        "date":      "2020–2022",
        "duration":  "~24 months",
        "route":     "Global (Los Angeles, Rotterdam, Singapore)",
        "rate_impact": "SCFI +600% from pre-COVID; WCSA/ECSA all records",
        "vessels":   "500,000+ TEU stranded at peak",
    },
]

# ---------------------------------------------------------------------------
# Helper: shared plotly layout
# ---------------------------------------------------------------------------

def _dark_layout(**kwargs) -> dict:
    base = dict(
        paper_bgcolor=C_BG,
        plot_bgcolor=C_SURFACE,
        font=dict(family="Inter, sans-serif", color=C_TEXT2, size=12),
        margin=dict(l=16, r=16, t=32, b=16),
        showlegend=True,
        legend=dict(
            bgcolor="rgba(0,0,0,0)",
            bordercolor=C_BORDER,
            font=dict(color=C_TEXT2, size=11),
        ),
    )
    base.update(kwargs)
    return base


# ---------------------------------------------------------------------------
# Section 1: Chokepoint Status Board
# ---------------------------------------------------------------------------

def _render_status_board() -> None:
    try:
        rows_html = ""
        for cp in _CHOKEPOINTS:
            sc  = _STATUS_COLORS.get(cp["status"],  C_TEXT2)
            rc  = _RISK_COLORS.get(cp["risk_level"], C_TEXT2)
            rows_html += (
                f'<tr style="border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:12px 14px;font-weight:600;color:{C_TEXT};font-size:13px;">{cp["name"]}</td>'
                f'<td style="padding:12px 14px;color:{C_TEXT2};font-size:12px;">{cp["daily_transits"]}</td>'
                f'<td style="padding:12px 14px;">'
                f'<span style="background:{sc}22;color:{sc};padding:3px 10px;border-radius:20px;'
                f'font-size:11px;font-weight:700;letter-spacing:0.05em;">{cp["status"]}</span></td>'
                f'<td style="padding:12px 14px;color:{C_TEXT2};font-size:12px;">{cp["wait_time"]}</td>'
                f'<td style="padding:12px 14px;color:{C_MOD};font-size:12px;font-weight:600;">{cp["rate_premium"]}</td>'
                f'<td style="padding:12px 14px;">'
                f'<span style="background:{rc}22;color:{rc};padding:3px 10px;border-radius:20px;'
                f'font-size:11px;font-weight:700;letter-spacing:0.05em;">{cp["risk_level"]}</span></td>'
                f'<td style="padding:12px 14px;color:{C_TEXT3};font-size:11px;">{cp["note"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:12px;'
            f'overflow:hidden;margin-bottom:24px;">'
            f'<div style="padding:16px 20px;border-bottom:1px solid {C_BORDER};">'
            f'<span style="font-size:15px;font-weight:700;color:{C_TEXT};">Global Chokepoint Status</span>'
            f'<span style="margin-left:12px;background:{C_LOW}22;color:{C_LOW};padding:3px 10px;'
            f'border-radius:20px;font-size:11px;font-weight:600;">2 CRITICAL</span>'
            f'<span style="margin-left:6px;background:#f9743622;color:#f97316;padding:3px 10px;'
            f'border-radius:20px;font-size:11px;font-weight:600;">2 HIGH</span>'
            f'<span style="margin-left:6px;background:{C_HIGH}22;color:{C_HIGH};padding:3px 10px;'
            f'border-radius:20px;font-size:11px;font-weight:600;">2 NORMAL</span>'
            f'</div>'
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead>'
            f'<tr style="background:{C_CARD};border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Chokepoint</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Daily Transits</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Status</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Wait Time</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Rate Premium</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Risk Level</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;font-weight:600;'
            f'color:{C_TEXT3};text-transform:uppercase;letter-spacing:0.08em;">Intel Note</th>'
            f'</tr>'
            f'</thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"status_board render error: {e}")
        st.error("Chokepoint status board unavailable.")


# ---------------------------------------------------------------------------
# Section 2: Canal Deep Dives
# ---------------------------------------------------------------------------

def _water_level_gauge(level_m: float) -> str:
    """Return an HTML water-level gauge CSS progress bar."""
    try:
        pct = min(100, max(0, (level_m - 22) / (30 - 22) * 100))
        if level_m < 25.9:
            bar_color = C_LOW
            label = "CRITICAL"
        elif level_m < 27.0:
            bar_color = C_MOD
            label = "LOW"
        else:
            bar_color = C_HIGH
            label = "NORMAL"

        return (
            f'<div style="margin:8px 0;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
            f'<span style="font-size:11px;color:{C_TEXT2};">Gatun Lake Level</span>'
            f'<span style="font-size:11px;color:{bar_color};font-weight:600;">{level_m:.1f} m — {label}</span>'
            f'</div>'
            f'<div style="background:{C_BORDER};border-radius:4px;height:8px;position:relative;">'
            f'<div style="background:{bar_color};width:{pct:.1f}%;height:8px;border-radius:4px;'
            f'transition:width 0.4s ease;"></div>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;margin-top:2px;">'
            f'<span style="font-size:10px;color:{C_TEXT3};">22 m (floor)</span>'
            f'<span style="font-size:10px;color:{C_LOW};">25.9 m</span>'
            f'<span style="font-size:10px;color:{C_MOD};">27.0 m</span>'
            f'<span style="font-size:10px;color:{C_TEXT3};">30 m (full)</span>'
            f'</div>'
            f'</div>'
        )
    except Exception as e:
        logger.error(f"water_level_gauge error: {e}")
        return ""


def _render_panama_card() -> None:
    try:
        panama_data: dict = {}
        if _CANAL_OK:
            try:
                panama_data = fetch_panama_stats() or {}
            except Exception as pe:
                logger.warning(f"fetch_panama_stats failed: {pe}")

        daily_transits = panama_data.get("daily_transits", 34)
        nb_wait        = panama_data.get("northbound_wait_days", 8)
        sb_wait        = panama_data.get("southbound_wait_days", 6)
        lake_level     = panama_data.get("gatun_lake_level_m", 26.4)

        gauge_html = _water_level_gauge(lake_level)

        fees_rows = (
            f'<tr><td style="padding:6px 8px;color:{C_TEXT2};font-size:12px;">Neopanamax Container</td>'
            f'<td style="padding:6px 8px;color:{C_MOD};font-size:12px;font-weight:600;">$800,000–1.2M</td></tr>'
            f'<tr><td style="padding:6px 8px;color:{C_TEXT2};font-size:12px;">Panamax Container</td>'
            f'<td style="padding:6px 8px;color:{C_MOD};font-size:12px;font-weight:600;">$350,000–500,000</td></tr>'
            f'<tr><td style="padding:6px 8px;color:{C_TEXT2};font-size:12px;">LNG Carrier</td>'
            f'<td style="padding:6px 8px;color:{C_MOD};font-size:12px;font-weight:600;">$600,000–900,000</td></tr>'
            f'<tr><td style="padding:6px 8px;color:{C_TEXT2};font-size:12px;">Bulk Carrier (Panamax)</td>'
            f'<td style="padding:6px 8px;color:{C_MOD};font-size:12px;font-weight:600;">$200,000–320,000</td></tr>'
        )

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:20px;height:100%;">'
            f'<div style="font-size:14px;font-weight:700;color:{C_TEXT};margin-bottom:16px;">'
            f'Panama Canal — Deep Dive</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-bottom:16px;">'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{C_MOD};">{daily_transits}</div>'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">Transits/Day</div></div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{C_LOW};">{nb_wait}d</div>'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">Northbound Wait</div></div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{C_LOW};">{sb_wait}d</div>'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">Southbound Wait</div></div>'
            f'</div>'
            f'{gauge_html}'
            f'<div style="margin:14px 0 8px;font-size:12px;font-weight:600;color:{C_TEXT2};">Vessel Size Restrictions</div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;margin-bottom:14px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Neopanamax max beam</span>'
            f'<span style="font-size:12px;color:{C_TEXT};font-weight:600;">49 m</span></div>'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:6px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Max draft (DFET)</span>'
            f'<span style="font-size:12px;color:{C_MOD};font-weight:600;">14.86 m (drought reduced)</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Max LOA</span>'
            f'<span style="font-size:12px;color:{C_TEXT};font-weight:600;">366 m</span></div>'
            f'</div>'
            f'<div style="font-size:12px;font-weight:600;color:{C_TEXT2};margin-bottom:8px;">Transit Fees by Vessel Class</div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;overflow:hidden;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<tbody>{fees_rows}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"panama_card render error: {e}")
        st.error("Panama Canal card unavailable.")


def _render_suez_card() -> None:
    try:
        suez_data: dict = {}
        if _CANAL_OK:
            try:
                suez_data = fetch_suez_stats() or {}
            except Exception as se:
                logger.warning(f"fetch_suez_stats failed: {se}")

        daily_transits  = suez_data.get("daily_transits", 45)
        rerouted_pct    = suez_data.get("rerouted_via_cape_pct", 65)
        rerouted_vessels = suez_data.get("rerouted_vessels_per_month", 420)
        revenue_impact  = suez_data.get("revenue_loss_usd_m_monthly", 700)

        st.markdown(
            f'<div style="background:{C_CARD};border:1px solid {C_BORDER};border-radius:12px;padding:20px;height:100%;">'
            f'<div style="font-size:14px;font-weight:700;color:{C_TEXT};margin-bottom:16px;">'
            f'Suez Canal — Deep Dive</div>'
            f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:16px;">'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{C_MOD};">{daily_transits}</div>'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">Transits/Day (reduced)</div></div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;text-align:center;">'
            f'<div style="font-size:22px;font-weight:700;color:{C_LOW};">{rerouted_pct}%</div>'
            f'<div style="font-size:11px;color:{C_TEXT3};margin-top:2px;">Rerouted via Cape</div></div>'
            f'</div>'
            f'<div style="background:{C_LOW}11;border:1px solid {C_LOW}44;border-radius:8px;'
            f'padding:12px;margin-bottom:14px;">'
            f'<div style="font-size:12px;font-weight:700;color:{C_LOW};margin-bottom:6px;">Red Sea Security Situation</div>'
            f'<div style="font-size:12px;color:{C_TEXT2};line-height:1.6;">'
            f'Houthi forces continue missile, drone, and naval mine attacks targeting vessels transiting '
            f'Bab-el-Mandeb and the southern Red Sea corridor. Operation Prosperity Guardian (US-led) '
            f'and Operation Aspides (EU) provide partial escort; most major carriers avoid entirely.'
            f'</div></div>'
            f'<div style="font-size:12px;font-weight:600;color:{C_TEXT2};margin-bottom:8px;">Cape of Good Hope Rerouting Impact</div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;margin-bottom:14px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Vessels rerouting/month</span>'
            f'<span style="font-size:12px;color:{C_LOW};font-weight:600;">{rerouted_vessels:,}</span></div>'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Extra transit time</span>'
            f'<span style="font-size:12px;color:{C_MOD};font-weight:600;">+10–14 days</span></div>'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Extra fuel cost per vessel</span>'
            f'<span style="font-size:12px;color:{C_MOD};font-weight:600;">+$2,500–3,500/day</span></div>'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">Extra distance (nm)</span>'
            f'<span style="font-size:12px;color:{C_TEXT};font-weight:600;">+3,500–4,000 nm</span></div>'
            f'<div style="display:flex;justify-content:space-between;">'
            f'<span style="font-size:12px;color:{C_TEXT2};">SCA monthly revenue loss</span>'
            f'<span style="font-size:12px;color:{C_LOW};font-weight:600;">-${revenue_impact}M+</span></div>'
            f'</div>'
            f'<div style="background:{C_SURFACE};border-radius:8px;padding:12px;">'
            f'<div style="font-size:12px;font-weight:600;color:{C_TEXT2};margin-bottom:6px;">Convoy Schedule</div>'
            f'<div style="font-size:12px;color:{C_TEXT2};">2 convoys/day: Northbound 06:00 local, '
            f'Southbound 04:00 local. Average transit: 12–16 hours.</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"suez_card render error: {e}")
        st.error("Suez Canal card unavailable.")


def _render_canal_deep_dives() -> None:
    try:
        col_l, col_r = st.columns(2)
        with col_l:
            _render_panama_card()
        with col_r:
            _render_suez_card()
    except Exception as e:
        logger.error(f"canal_deep_dives render error: {e}")
        st.error("Canal deep dives unavailable.")


# ---------------------------------------------------------------------------
# Section 3: Red Sea Crisis Monitor
# ---------------------------------------------------------------------------

def _render_red_sea_monitor() -> None:
    try:
        st.markdown(
            f'<div style="background:{C_LOW}0d;border:1px solid {C_LOW}44;border-radius:12px;'
            f'padding:20px;margin-bottom:24px;">'
            f'<div style="font-size:15px;font-weight:700;color:{C_LOW};margin-bottom:4px;">'
            f'Red Sea Crisis Monitor</div>'
            f'<div style="font-size:12px;color:{C_TEXT2};margin-bottom:20px;">'
            f'Houthi maritime attacks — Oct 2023 to present. Ongoing disruption to Bab-el-Mandeb / Suez corridor.'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

        tab_timeline, tab_carriers, tab_insurance = st.tabs([
            "Attack Timeline", "Carrier Policies", "Insurance & Rates"
        ])

        with tab_timeline:
            try:
                timeline_rows = ""
                for i, inc in enumerate(_HOUTHI_INCIDENTS):
                    dot_color = C_LOW if "sink" in inc["outcome"].lower() or "kill" in inc["outcome"].lower() else C_MOD
                    timeline_rows += (
                        f'<div style="display:flex;gap:16px;margin-bottom:16px;">'
                        f'<div style="display:flex;flex-direction:column;align-items:center;">'
                        f'<div style="width:12px;height:12px;border-radius:50%;background:{dot_color};'
                        f'flex-shrink:0;margin-top:3px;"></div>'
                        f'{"<div style=width:2px;flex:1;background:" + C_BORDER + ";margin-top:4px;></div>" if i < len(_HOUTHI_INCIDENTS) - 1 else ""}'
                        f'</div>'
                        f'<div style="background:{C_CARD};border-radius:8px;padding:12px;flex:1;">'
                        f'<div style="display:flex;justify-content:space-between;margin-bottom:4px;">'
                        f'<span style="font-size:12px;font-weight:600;color:{C_TEXT};">{inc["vessel"]}</span>'
                        f'<span style="font-size:11px;color:{C_TEXT3};">{inc["date"]}</span>'
                        f'</div>'
                        f'<div style="display:flex;gap:8px;margin-bottom:4px;">'
                        f'<span style="background:{C_MOD}22;color:{C_MOD};padding:2px 8px;border-radius:10px;'
                        f'font-size:10px;font-weight:600;">{inc["type"]}</span>'
                        f'</div>'
                        f'<div style="font-size:12px;color:{C_TEXT2};">{inc["outcome"]}</div>'
                        f'</div>'
                        f'</div>'
                    )

                st.markdown(
                    f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;'
                    f'padding:20px;max-height:480px;overflow-y:auto;">{timeline_rows}</div>',
                    unsafe_allow_html=True,
                )
            except Exception as e:
                logger.error(f"houthi timeline render error: {e}")
                st.error("Timeline unavailable.")

        with tab_carriers:
            try:
                carrier_rows = ""
                for cp in _CARRIER_POLICIES:
                    avoid = cp["policy"] == "Avoiding Red Sea"
                    p_color = C_LOW if avoid else C_MOD
                    carrier_rows += (
                        f'<tr style="border-bottom:1px solid {C_BORDER};">'
                        f'<td style="padding:10px 14px;font-weight:600;color:{C_TEXT};font-size:13px;">{cp["carrier"]}</td>'
                        f'<td style="padding:10px 14px;">'
                        f'<span style="background:{p_color}22;color:{p_color};padding:3px 10px;border-radius:20px;'
                        f'font-size:11px;font-weight:600;">{cp["policy"]}</span></td>'
                        f'<td style="padding:10px 14px;color:{C_TEXT2};font-size:12px;">{cp["since"]}</td>'
                        f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:12px;">{cp["escort"]}</td>'
                        f'</tr>'
                    )

                st.markdown(
                    f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;overflow:hidden;">'
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr style="background:{C_CARD};">'
                    f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
                    f'text-transform:uppercase;letter-spacing:0.08em;">Carrier</th>'
                    f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
                    f'text-transform:uppercase;letter-spacing:0.08em;">Policy</th>'
                    f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
                    f'text-transform:uppercase;letter-spacing:0.08em;">Since</th>'
                    f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
                    f'text-transform:uppercase;letter-spacing:0.08em;">Naval Escort</th>'
                    f'</tr></thead>'
                    f'<tbody>{carrier_rows}</tbody>'
                    f'</table>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            except Exception as e:
                logger.error(f"carrier_policies render error: {e}")
                st.error("Carrier policies unavailable.")

        with tab_insurance:
            try:
                st.markdown(
                    f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:10px;padding:20px;">'
                    f'<div style="font-size:13px;font-weight:600;color:{C_TEXT};margin-bottom:16px;">War Risk Insurance Premiums — Red Sea Transit</div>'
                    f'<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin-bottom:20px;">'
                    f'<div style="background:{C_CARD};border-radius:8px;padding:14px;text-align:center;">'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px;">Pre-Crisis (Oct 2023)</div>'
                    f'<div style="font-size:24px;font-weight:700;color:{C_HIGH};">0.03–0.05%</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">of vessel value</div></div>'
                    f'<div style="background:{C_CARD};border-radius:8px;padding:14px;text-align:center;">'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px;">Peak Crisis (Jan 2024)</div>'
                    f'<div style="font-size:24px;font-weight:700;color:{C_LOW};">0.5–1.0%</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">of vessel value (20–30x increase)</div></div>'
                    f'<div style="background:{C_CARD};border-radius:8px;padding:14px;text-align:center;">'
                    f'<div style="font-size:11px;color:{C_TEXT3};margin-bottom:6px;">Current (2025)</div>'
                    f'<div style="font-size:24px;font-weight:700;color:{C_MOD};">0.3–0.7%</div>'
                    f'<div style="font-size:11px;color:{C_TEXT2};margin-top:4px;">of vessel value per voyage</div></div>'
                    f'</div>'
                    f'<div style="font-size:12px;font-weight:600;color:{C_TEXT2};margin-bottom:10px;">Alternative Routing Impact by Trade Lane</div>'
                    f'<div style="background:{C_CARD};border-radius:8px;overflow:hidden;">'
                    f'<table style="width:100%;border-collapse:collapse;">'
                    f'<thead><tr style="border-bottom:1px solid {C_BORDER};">'
                    f'<th style="padding:8px 12px;text-align:left;font-size:11px;color:{C_TEXT3};">Trade Lane</th>'
                    f'<th style="padding:8px 12px;text-align:left;font-size:11px;color:{C_TEXT3};">Normal Route</th>'
                    f'<th style="padding:8px 12px;text-align:left;font-size:11px;color:{C_TEXT3};">Current Route</th>'
                    f'<th style="padding:8px 12px;text-align:left;font-size:11px;color:{C_TEXT3};">Extra Days</th>'
                    f'<th style="padding:8px 12px;text-align:left;font-size:11px;color:{C_TEXT3};">Extra Cost</th>'
                    f'</tr></thead>'
                    f'<tbody>'
                    f'<tr style="border-bottom:1px solid {C_BORDER};">'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT};">Asia → N. Europe</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">Suez (25–28 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_MOD};">Cape Hope (38–42 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+12–14 days</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+$350K–500K</td>'
                    f'</tr>'
                    f'<tr style="border-bottom:1px solid {C_BORDER};">'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT};">Asia → Mediterranean</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">Suez (22–26 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_MOD};">Cape Hope (35–40 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+10–14 days</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+$300K–450K</td>'
                    f'</tr>'
                    f'<tr>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT};">Middle East → Europe</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_TEXT2};">Suez (18–22 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_MOD};">Cape Hope (30–36 days)</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+12–14 days</td>'
                    f'<td style="padding:8px 12px;font-size:12px;color:{C_LOW};font-weight:600;">+$280K–420K</td>'
                    f'</tr>'
                    f'</tbody>'
                    f'</table>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            except Exception as e:
                logger.error(f"insurance tab render error: {e}")
                st.error("Insurance data unavailable.")

    except Exception as e:
        logger.error(f"red_sea_monitor render error: {e}")
        st.error("Red Sea Crisis Monitor unavailable.")


# ---------------------------------------------------------------------------
# Section 4: Chokepoint Traffic Map
# ---------------------------------------------------------------------------

def _render_traffic_map() -> None:
    try:
        chokepoint_lats  = [30.5, 8.9,  1.35,  12.6,  26.6, 55.5]
        chokepoint_lons  = [32.3, -79.5, 103.8, 43.3,  56.3, 11.8]
        chokepoint_names = [
            "Suez Canal", "Panama Canal", "Strait of Malacca",
            "Bab-el-Mandeb", "Strait of Hormuz", "Danish Straits"
        ]
        chokepoint_risks  = ["CRITICAL", "HIGH", "LOW", "CRITICAL", "HIGH", "LOW"]
        chokepoint_sizes  = [28, 24, 20, 28, 24, 16]
        cp_colors = [_RISK_COLORS.get(r, C_TEXT2) for r in chokepoint_risks]

        fig = go.Figure()

        # Main trade route lines
        routes = [
            # Asia - Europe via Suez
            dict(lats=[1.35, 3.0, 8.0, 12.6, 14.0, 27.0, 30.5, 32.0, 36.0, 43.0, 51.5],
                 lons=[103.8, 100.0, 80.0, 43.3, 42.0, 35.0, 32.3, 28.0, 18.0, 10.0, 0.0],
                 name="Asia–Europe (Suez)", color=C_MOD, dash="dash"),
            # Asia - US West Coast
            dict(lats=[1.35, 15.0, 25.0, 35.0, 37.8],
                 lons=[103.8, 140.0, 170.0, -150.0, -122.4],
                 name="Asia–US West Coast", color=C_HIGH, dash="dash"),
            # Cape of Good Hope rerouting
            dict(lats=[1.35, -10.0, -25.0, -34.4, -20.0, 0.0, 15.0, 36.0, 51.5],
                 lons=[103.8, 60.0, 30.0, 18.5, 5.0, -5.0, -10.0, -6.0, 0.0],
                 name="Cape of Good Hope (alt route)", color=C_ACCENT, dash="dot"),
        ]

        for r in routes:
            fig.add_trace(go.Scattergeo(
                lat=r["lats"], lon=r["lons"],
                mode="lines",
                line=dict(width=2, color=r["color"], dash=r["dash"]),
                name=r["name"],
                hoverinfo="name",
            ))

        # Chokepoint markers
        fig.add_trace(go.Scattergeo(
            lat=chokepoint_lats,
            lon=chokepoint_lons,
            mode="markers+text",
            marker=dict(
                size=chokepoint_sizes,
                color=cp_colors,
                opacity=0.85,
                line=dict(width=1.5, color="white"),
            ),
            text=chokepoint_names,
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT),
            name="Chokepoints",
            hovertemplate="<b>%{text}</b><extra></extra>",
        ))

        fig.update_layout(
            **_dark_layout(
                title=dict(
                    text="Strategic Maritime Chokepoints & Trade Routes",
                    font=dict(color=C_TEXT, size=14),
                    x=0.02,
                ),
                margin=dict(l=0, r=0, t=40, b=0),
                height=440,
            ),
            geo=dict(
                projection_type="natural earth",
                bgcolor=C_BG,
                landcolor="#1e293b",
                oceancolor="#0f172a",
                coastlinecolor=C_BORDER,
                countrycolor=C_BORDER,
                showland=True,
                showocean=True,
                showcoastlines=True,
                showframe=False,
            ),
            legend=dict(
                x=0.01, y=0.02,
                bgcolor="rgba(17,24,39,0.8)",
                bordercolor=C_BORDER,
                font=dict(color=C_TEXT2, size=10),
            ),
        )

        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        logger.error(f"traffic_map render error: {e}")
        st.error("Chokepoint traffic map unavailable.")


# ---------------------------------------------------------------------------
# Section 5: Rate Premium Analysis
# ---------------------------------------------------------------------------

def _render_rate_premiums() -> None:
    try:
        rows_html = ""
        for r in _RATE_PREMIUMS:
            rows_html += (
                f'<tr style="border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;font-weight:600;color:{C_TEXT};font-size:12px;">{r["route"]}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};font-size:12px;">{r["baseline"]}</td>'
                f'<td style="padding:10px 14px;color:{C_MOD};font-size:12px;font-weight:600;">{r["current"]}</td>'
                f'<td style="padding:10px 14px;color:{C_LOW};font-size:12px;font-weight:700;">{r["premium"]}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{r["driver"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            f'<div style="padding:16px 20px;border-bottom:1px solid {C_BORDER};">'
            f'<span style="font-size:14px;font-weight:700;color:{C_TEXT};">Rate Premium by Route — Chokepoint Attribution</span>'
            f'<span style="margin-left:10px;font-size:12px;color:{C_TEXT3};">Estimated surcharges attributable to active disruptions</span>'
            f'</div>'
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_CARD};border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Trade Route</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Baseline Rate</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Current Rate</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Chokepoint Premium</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Primary Driver</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"rate_premiums render error: {e}")
        st.error("Rate premium table unavailable.")


# ---------------------------------------------------------------------------
# Section 6: Historical Disruption Comparison
# ---------------------------------------------------------------------------

def _render_historical_comparison() -> None:
    try:
        rows_html = ""
        for inc in _HISTORICAL_INCIDENTS:
            ongoing = "present" in inc["date"].lower() or "ongoing" in inc["duration"].lower()
            dur_color = C_LOW if ongoing else C_TEXT2
            rows_html += (
                f'<tr style="border-bottom:1px solid {C_BORDER};">'
                f'<td style="padding:10px 14px;font-weight:600;color:{C_TEXT};font-size:12px;">{inc["incident"]}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};font-size:11px;">{inc["date"]}</td>'
                f'<td style="padding:10px 14px;color:{dur_color};font-size:11px;font-weight:600;">{inc["duration"]}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT2};font-size:11px;">{inc["route"]}</td>'
                f'<td style="padding:10px 14px;color:{C_MOD};font-size:11px;">{inc["rate_impact"]}</td>'
                f'<td style="padding:10px 14px;color:{C_TEXT3};font-size:11px;">{inc["vessels_affected"]}</td>'
                f'</tr>'
            )

        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:12px;overflow:hidden;">'
            f'<div style="padding:16px 20px;border-bottom:1px solid {C_BORDER};">'
            f'<span style="font-size:14px;font-weight:700;color:{C_TEXT};">Historical Major Disruptions — Impact Comparison</span>'
            f'</div>'
            f'<div style="overflow-x:auto;">'
            f'<table style="width:100%;border-collapse:collapse;">'
            f'<thead><tr style="background:{C_CARD};border-bottom:1px solid {C_BORDER};">'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Incident</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Date</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Duration</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Route Affected</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Rate Impact</th>'
            f'<th style="padding:10px 14px;text-align:left;font-size:11px;color:{C_TEXT3};'
            f'text-transform:uppercase;letter-spacing:0.08em;">Vessels Affected</th>'
            f'</tr></thead>'
            f'<tbody>{rows_html}</tbody>'
            f'</table>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"historical_comparison render error: {e}")
        st.error("Historical disruption comparison unavailable.")


# ---------------------------------------------------------------------------
# Monthly Transit Chart (Panama)
# ---------------------------------------------------------------------------

def _render_panama_transit_chart() -> None:
    try:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        transits_2022 = [38, 37, 38, 37, 38, 37, 37, 36, 36, 37, 37, 38]
        transits_2023 = [36, 35, 35, 33, 30, 28, 27, 25, 24, 24, 26, 28]
        transits_2024 = [30, 32, 33, 34, 35, 35, 34, 34, 34, 35, 35, 36]

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=months, y=transits_2022,
            name="2022 (normal)", mode="lines+markers",
            line=dict(color=C_HIGH, width=2),
            marker=dict(size=5),
        ))
        fig.add_trace(go.Scatter(
            x=months, y=transits_2023,
            name="2023 (drought)", mode="lines+markers",
            line=dict(color=C_LOW, width=2, dash="dash"),
            marker=dict(size=5),
        ))
        fig.add_trace(go.Scatter(
            x=months, y=transits_2024,
            name="2024 (recovery)", mode="lines+markers",
            line=dict(color=C_MOD, width=2),
            marker=dict(size=5),
        ))

        fig.add_hrect(y0=0, y1=26, fillcolor=C_LOW, opacity=0.07, line_width=0, annotation_text="Critical", annotation_font_color=C_LOW)
        fig.add_hrect(y0=26, y1=30, fillcolor=C_MOD, opacity=0.07, line_width=0, annotation_text="Restricted", annotation_font_color=C_MOD)

        fig.update_layout(
            **_dark_layout(
                title=dict(text="Panama Canal — Monthly Transits (Daily Avg)", font=dict(color=C_TEXT, size=13), x=0.02),
                height=300,
                xaxis=dict(gridcolor=C_BORDER, tickfont=dict(color=C_TEXT2)),
                yaxis=dict(gridcolor=C_BORDER, tickfont=dict(color=C_TEXT2), title="Transits/Day"),
            )
        )
        st.plotly_chart(fig, use_container_width=True)
    except Exception as e:
        logger.error(f"panama_transit_chart render error: {e}")
        st.error("Panama transit chart unavailable.")


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(port_results=None, freight_data=None, insights=None) -> None:
    """Render the Strategic Waterway & Chokepoint Intelligence tab."""
    try:
        # Page header
        st.markdown(
            f'<div style="background:{C_SURFACE};border:1px solid {C_BORDER};border-radius:12px;'
            f'padding:20px 24px;margin-bottom:24px;">'
            f'<div style="font-size:20px;font-weight:700;color:{C_TEXT};margin-bottom:4px;">'
            f'Strategic Waterway & Chokepoint Intelligence</div>'
            f'<div style="font-size:13px;color:{C_TEXT2};">'
            f'Real-time status, disruption analysis, and rate impact for the world\'s critical maritime chokepoints'
            f'</div>'
            f'<div style="display:flex;gap:16px;margin-top:14px;">'
            f'<div style="background:{C_LOW}22;border:1px solid {C_LOW}44;border-radius:8px;padding:8px 16px;">'
            f'<span style="font-size:11px;font-weight:700;color:{C_LOW};">2 CRITICAL CHOKEPOINTS</span>'
            f'<span style="font-size:11px;color:{C_TEXT2};margin-left:8px;">Suez + Bab-el-Mandeb</span>'
            f'</div>'
            f'<div style="background:{C_MOD}22;border:1px solid {C_MOD}44;border-radius:8px;padding:8px 16px;">'
            f'<span style="font-size:11px;font-weight:700;color:{C_MOD};">~65% Suez Rerouted</span>'
            f'<span style="font-size:11px;color:{C_TEXT2};margin-left:8px;">Cape of Good Hope</span>'
            f'</div>'
            f'<div style="background:{C_ACCENT}22;border:1px solid {C_ACCENT}44;border-radius:8px;padding:8px 16px;">'
            f'<span style="font-size:11px;font-weight:700;color:{C_ACCENT};">+$1,800–3,000/TEU</span>'
            f'<span style="font-size:11px;color:{C_TEXT2};margin-left:8px;">Asia–Europe premium</span>'
            f'</div>'
            f'</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception as e:
        logger.error(f"header render error: {e}")

    # Section 1: Status Board
    try:
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:10px;'
            f'text-transform:uppercase;letter-spacing:0.08em;">Chokepoint Status Board</div>',
            unsafe_allow_html=True,
        )
        _render_status_board()
    except Exception as e:
        logger.error(f"section 1 render error: {e}")

    st.divider()

    # Section 2: Canal Deep Dives
    try:
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:10px;'
            f'text-transform:uppercase;letter-spacing:0.08em;">Canal Deep Dives</div>',
            unsafe_allow_html=True,
        )
        _render_canal_deep_dives()
    except Exception as e:
        logger.error(f"section 2 render error: {e}")

    try:
        with st.expander("Panama Canal — Monthly Transit History"):
            _render_panama_transit_chart()
    except Exception as e:
        logger.error(f"panama chart expander error: {e}")

    st.divider()

    # Section 3: Red Sea Crisis Monitor
    try:
        _render_red_sea_monitor()
    except Exception as e:
        logger.error(f"section 3 render error: {e}")

    st.divider()

    # Section 4: Traffic Map
    try:
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:10px;'
            f'text-transform:uppercase;letter-spacing:0.08em;">Chokepoint Traffic Map</div>',
            unsafe_allow_html=True,
        )
        _render_traffic_map()
    except Exception as e:
        logger.error(f"section 4 render error: {e}")

    st.divider()

    # Section 5: Rate Premium Analysis
    try:
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:10px;'
            f'text-transform:uppercase;letter-spacing:0.08em;">Rate Premium Analysis</div>',
            unsafe_allow_html=True,
        )
        _render_rate_premiums()
    except Exception as e:
        logger.error(f"section 5 render error: {e}")

    st.divider()

    # Section 6: Historical Disruption Comparison
    try:
        st.markdown(
            f'<div style="font-size:13px;font-weight:700;color:{C_TEXT};margin-bottom:10px;'
            f'text-transform:uppercase;letter-spacing:0.08em;">Historical Disruption Comparison</div>',
            unsafe_allow_html=True,
        )
        _render_historical_comparison()
    except Exception as e:
        logger.error(f"section 6 render error: {e}")

    # Canal impact summary (if feed available)
    if _CANAL_OK:
        try:
            impact = get_canal_shipping_impact()
            if impact:
                with st.expander("Canal Feed — Live Impact Summary"):
                    st.json(impact)
        except Exception as e:
            logger.warning(f"canal_impact summary error: {e}")
