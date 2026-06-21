"""
Strategic Waterway & Chokepoint Intelligence Tab — refactored to ui/styles.

Sections:
  1. Chokepoint Status Board
  2. Canal Deep Dives (Panama + Suez)
  3. Red Sea Crisis Monitor (timeline / carriers / insurance)
  4. Chokepoint Traffic Map
  5. Rate Premium Analysis
  6. Historical Disruption Comparison
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_BG,
    C_BORDER,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    apply_dark_layout,
    badge,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

try:
    from data.canal_feed import fetch_panama_stats, fetch_suez_stats, get_canal_shipping_impact
    _CANAL_OK = True
except Exception as _ce:
    _CANAL_OK = False
    logger.warning(f"canal_feed unavailable: {_ce}")


# ── Provenance / data sources ───────────────────────────────────────────────
_SRC_CANAL_FEED = DataSource.modeled(
    "Panama Canal Authority · Suez Canal Authority composite"
)
_SRC_HOUTHI = DataSource.modeled(
    "USCENTCOM bulletins · UKMTO incident database"
)
_SRC_CARRIER_POL = DataSource.modeled(
    "Carrier route advisories (Maersk · MSC · CMA CGM · Hapag-Lloyd · ONE)"
)
_SRC_INSURANCE = DataSource.modeled(
    "Lloyd's of London · marine war risk underwriters"
)
_SRC_RATE_PREMIUM = DataSource.modeled(
    "SCFI · Drewry WCI · Freightos Baltic Index composite"
)
_SRC_HISTORICAL = DataSource.modeled(
    "Maritime incident archive · academic + trade-press"
)
_SRC_ESCALATION = DataSource.modeled(
    "Modeled escalation ladder (R034) — probabilistic forward-escalation "
    "refinement of the deterministic chokepoint risk",
    notes=(
        "Conservative, published per-state Markov transition probabilities — "
        "NOT a fitted hazard model and NOT an observed feed."
    ),
)


# ── Static data ─────────────────────────────────────────────────────────────

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

# HIGH-risk tier — orange, sits between MODERATE amber and CRITICAL red
_C_ORANGE = "#f97316"

_STATUS_COLORS = {
    "CRITICAL":  C_LOW,
    "HIGH":      _C_ORANGE,
    "ELEVATED":  C_MOD,
    "RESTRICTED": C_MOD,
    "NORMAL":    C_HIGH,
    "HIGH RISK": C_LOW,
}

_RISK_COLORS = {
    "CRITICAL":  C_LOW,
    "HIGH":      _C_ORANGE,
    "MODERATE":  C_MOD,
    "LOW":       C_HIGH,
}

# Ladder-state badge colors (low → high severity), aligned to the risk palette.
_LADDER_STATE_COLORS = {
    "DE_ESCALATING": C_HIGH,
    "TENSION":       C_MOD,
    "INCIDENT":      _C_ORANGE,
    "PARTIAL":       C_LOW,
    "CLOSURE":       C_LOW,
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


# ── Cell formatters ─────────────────────────────────────────────────────────

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


# ── Section 1: Status Board ─────────────────────────────────────────────────

def _render_status_board() -> None:
    try:
        section_header(
            "Global Chokepoint Status",
            subtitle="Real-time operational status across 6 strategic maritime waterways",
        )
        rows = []
        for cp in _CHOKEPOINTS:
            sc = _STATUS_COLORS.get(cp["status"],    C_TEXT2)
            rc = _RISK_COLORS.get(cp["risk_level"], C_TEXT2)
            rows.append([
                _sans(cp["name"], color=C_TEXT, weight=700),
                _sans(cp["daily_transits"], color=C_TEXT2),
                badge(cp["status"], color=sc),
                _sans(cp["wait_time"], color=C_TEXT2),
                _mono(cp["rate_premium"], color=C_MOD),
                badge(cp["risk_level"], color=rc),
                _sans(cp["note"], color=C_TEXT3),
            ])
        wsj_market_table(
            headers=[
                "Chokepoint", "Daily Transits", "Status",
                "Wait Time", "Rate Premium", "Risk Level", "Intel Note",
            ],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_CANAL_FEED]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"status_board render error: {e}")
        st.error("Chokepoint status board unavailable.")


# ── Section 1b: Forward Escalation Outlook (R034) ───────────────────────────
#
# Surfaces processing/escalation_ladder.py: a MODELED probabilistic refinement
# of the deterministic chokepoint risk. We show, per chokepoint, the current
# ladder state, the deterministic "current" score, the modeled forward expected
# score (the Markov ladder rolled `horizon` steps), and the delta. This is a
# modeled overlay — NOT a feed and NOT a fitted model — and is labelled as such.

def _render_escalation_outlook() -> None:
    try:
        from processing.chokepoint_analyzer import (
            CHOKEPOINTS,
            compute_chokepoint_risk_score,
        )
        from processing.escalation_ladder import (
            PROVENANCE_NOTE,
            escalation_alert_signals,
            ladder_expected_scores,
        )

        section_header(
            "Forward Escalation Outlook",
            subtitle="Modeled probability-weighted forward risk per chokepoint — "
                     "a Markov escalation ladder priced over the next step(s)",
        )

        # Horizon selector (1–4 steps; each step ≈ one weekly review). Keyed
        # uniquely so it never collides with another widget on the page.
        horizon = st.slider(
            "Forward horizon (steps)",
            min_value=1,
            max_value=4,
            value=1,
            key="chokepoint_escalation_horizon",
            help="Number of ~weekly review steps to roll the modeled "
                 "escalation ladder forward.",
        )

        # Deterministic "current" score, the forward-blended score, and the
        # raw ladder results — all keyed identically to CHOKEPOINTS.
        current = compute_chokepoint_risk_score()
        forward = compute_chokepoint_risk_score(
            escalation_ladder=True, ladder_horizon=horizon
        )
        ladder = ladder_expected_scores(CHOKEPOINTS, horizon=horizon)

        # Short modeled-provenance caption (trimmed PROVENANCE_NOTE).
        st.markdown(
            f'<div class="wsj-body" style="color:{C_TEXT3};">'
            f'{PROVENANCE_NOTE}</div>',
            unsafe_allow_html=True,
        )

        # Early-warning callout: ELEVATED passages whose modeled forward path
        # prices real escalation ABOVE today's deterministic risk — the
        # closure-is-coming signal before the closure. Calm and already-hot
        # passages are excluded by the gate (see escalation_alert_signals).
        signals = escalation_alert_signals(current, ladder)
        if signals:
            names = ", ".join(
                f"{getattr(CHOKEPOINTS.get(s.key), 'name', s.key)} "
                f"({s.current_state.replace('_', ' ').title()}, "
                f"{s.forward_score:.2f} vs {s.current_score:.2f})"
                for s in signals
            )
            st.warning(
                f"**Forward-escalation early warning** — {len(signals)} elevated "
                f"passage(s) on a modeled path above current risk: {names}. "
                "Modeled (Markov ladder), not a feed."
            )

        # Expected closure cost (R258) — fuse P(reach CLOSURE within horizon)
        # with the CONDITIONAL closure impact: expected = probability × severity.
        # This is the decision-grade number the bare risk score never gives.
        try:
            from processing.closure_scenario import expected_closure_impacts

            cost = expected_closure_impacts(CHOKEPOINTS, horizon=horizon)
            ranked = sorted(
                cost.values(),
                key=lambda c: abs(getattr(c, "expected_rate_impact_pct", 0.0)),
                reverse=True,
            )[:3]
            shown = [c for c in ranked
                     if abs(getattr(c, "expected_rate_impact_pct", 0.0)) > 1e-9]
            if shown:
                parts = "; ".join(
                    f"{getattr(CHOKEPOINTS.get(c.chokepoint_key), 'name', c.chokepoint_key)}: "
                    f"P(closure) {c.p_closure * 100:.0f}% × {c.conditional_rate_impact_pct:+.0f}% "
                    f"→ {c.expected_rate_impact_pct:+.1f}% expected freight-rate impact"
                    for c in shown
                )
                st.markdown(
                    f'<div class="wsj-body" style="color:{C_TEXT3};">'
                    f'<b>Expected closure cost (modeled)</b> — {parts}.</div>',
                    unsafe_allow_html=True,
                )
        except Exception as ce:
            logger.debug(f"expected closure cost readout skipped: {ce}")

        # One row per chokepoint, sorted hottest-first by forward score.
        rows = []
        chart_names: list[str] = []
        chart_scores: list[float] = []
        for key, cp in sorted(
            CHOKEPOINTS.items(),
            key=lambda kv: forward.get(kv[0], 0.0),
            reverse=True,
        ):
            res = ladder.get(key)
            state = res.current_state if res is not None else "DE_ESCALATING"
            cur_score = current.get(key, 0.0)
            fwd_score = forward.get(key, 0.0)
            delta = fwd_score - cur_score
            state_color = _LADDER_STATE_COLORS.get(state, C_TEXT2)
            delta_color = C_LOW if delta > 1e-6 else C_TEXT3
            rows.append([
                _sans(cp.name, color=C_TEXT, weight=700),
                badge(state, color=state_color),
                _mono(f"{cur_score:.3f}", color=C_TEXT2),
                _mono(f"{fwd_score:.3f}", color=C_MOD),
                _mono(f"{delta:+.3f}", color=delta_color),
            ])
            chart_names.append(cp.name)
            chart_scores.append(fwd_score)

        wsj_market_table(
            headers=[
                "Chokepoint", "Current Ladder State",
                "Current Risk (det.)", "Forward Expected (modeled)", "Δ (fwd − cur)",
            ],
            rows=rows,
        )

        # Horizontal bar: modeled forward expected score per chokepoint
        # (hottest on top — reverse the descending lists for a top-down read).
        try:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=list(reversed(chart_scores)),
                y=list(reversed(chart_names)),
                orientation="h",
                marker=dict(color=C_MOD, line=dict(width=0)),
                hovertemplate="<b>%{y}</b><br>forward expected: "
                              "%{x:.3f}<extra></extra>",
            ))
            apply_dark_layout(fig, height=320)
            fig.update_layout(
                title=dict(
                    text=f"Modeled Forward Expected Risk (horizon {horizon})",
                    font=dict(color=C_TEXT, size=13), x=0.02,
                ),
                xaxis=dict(
                    range=[0, 1], gridcolor=C_BORDER,
                    tickfont=dict(color=C_TEXT2), title="Expected severity [0–1]",
                ),
                yaxis=dict(tickfont=dict(color=C_TEXT2)),
                margin=dict(l=0, r=10, t=40, b=0),
            )
            st.plotly_chart(
                fig, use_container_width=True,
                key="chokepoint_escalation_bar",
            )
        except Exception as ce:
            logger.warning(f"escalation_outlook chart skipped: {ce}")

        st.markdown(source_footer([_SRC_ESCALATION]), unsafe_allow_html=True)
    except Exception as e:
        logger.warning(f"escalation_outlook render error: {e}")
        st.error("Forward escalation outlook unavailable.")


# ── Section 2: Canal Deep Dives ─────────────────────────────────────────────

def _gatun_label(level_m: float) -> tuple[str, str]:
    """Return (color, label) for Gatun Lake water level."""
    if level_m < 25.9:
        return C_LOW, "CRITICAL"
    if level_m < 27.0:
        return C_MOD, "LOW"
    return C_HIGH, "NORMAL"


def _render_panama_card() -> None:
    try:
        panama_data = None
        if _CANAL_OK:
            try:
                panama_data = fetch_panama_stats()
            except Exception as pe:
                logger.warning(f"fetch_panama_stats failed: {pe}")

        # fetch_panama_stats() returns a CanalStats dataclass — access by attr.
        daily_transits = getattr(panama_data, "daily_transits", 34)
        nb_wait        = getattr(panama_data, "northbound_wait_days", 8)
        sb_wait        = getattr(panama_data, "southbound_wait_days", 6)
        lake_level     = getattr(panama_data, "water_level_m", 26.4)
        lake_color, lake_label = _gatun_label(lake_level)

        section_header(
            "Panama Canal — Deep Dive",
            subtitle="Transit throughput, queue depth, and Gatun Lake water levels",
        )

        metric_card_row(
            [
                {"label": "Transits / Day", "value": f"{daily_transits}",
                 "accent": C_MOD, "sublabel": "vs ~36 normal"},
                {"label": "Northbound Wait", "value": f"{nb_wait}d",
                 "accent": C_LOW, "sublabel": "queue at Cristóbal"},
                {"label": "Southbound Wait", "value": f"{sb_wait}d",
                 "accent": C_LOW, "sublabel": "queue at Balboa"},
                {"label": "Gatun Lake Level", "value": f"{lake_level:.1f} m",
                 "accent": lake_color, "sublabel": f"{lake_label} (22 m floor / 30 m full)"},
            ],
            columns=4,
        )

        # Vessel-size / draft restrictions table
        size_rows = [
            [_sans("Neopanamax max beam", color=C_TEXT2),
             _mono("49 m", color=C_TEXT)],
            [_sans("Max draft (DFET)", color=C_TEXT2),
             _mono("14.86 m (drought reduced)", color=C_MOD)],
            [_sans("Max LOA", color=C_TEXT2),
             _mono("366 m", color=C_TEXT)],
        ]
        wsj_market_table(["Vessel Size Restriction", "Limit"], size_rows)

        # Transit fees table
        fees_rows = [
            [_sans("Neopanamax Container", color=C_TEXT2),
             _mono("$800,000–1.2M", color=C_MOD)],
            [_sans("Panamax Container", color=C_TEXT2),
             _mono("$350,000–500,000", color=C_MOD)],
            [_sans("LNG Carrier", color=C_TEXT2),
             _mono("$600,000–900,000", color=C_MOD)],
            [_sans("Bulk Carrier (Panamax)", color=C_TEXT2),
             _mono("$200,000–320,000", color=C_MOD)],
        ]
        wsj_market_table(["Transit Fee Class", "Fee Range"], fees_rows)
        st.markdown(source_footer([_SRC_CANAL_FEED]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"panama_card render error: {e}")
        st.error("Panama Canal card unavailable.")


def _render_suez_card() -> None:
    try:
        suez_data = None
        if _CANAL_OK:
            try:
                suez_data = fetch_suez_stats()
            except Exception as se:
                logger.warning(f"fetch_suez_stats failed: {se}")

        # fetch_suez_stats() returns a CanalStats dataclass — access by attr.
        # The rerouting/revenue figures are not part of CanalStats; keep the
        # established baseline estimates for those.
        daily_transits   = getattr(suez_data, "daily_transits", 45)
        rerouted_pct     = 65
        rerouted_vessels = 420
        revenue_impact   = 700

        section_header(
            "Suez Canal — Deep Dive",
            subtitle="Throughput, Cape rerouting, and SCA revenue impact under Red Sea threat",
        )

        metric_card_row(
            [
                {"label": "Transits / Day", "value": f"{daily_transits}",
                 "accent": C_MOD, "sublabel": "reduced from ~75 normal"},
                {"label": "Rerouted via Cape", "value": f"{rerouted_pct}%",
                 "accent": C_LOW, "sublabel": "of pre-crisis Suez volume"},
                {"label": "Vessels Rerouting / Month", "value": f"{rerouted_vessels:,}",
                 "accent": C_LOW, "sublabel": "transiting Cape of Good Hope"},
                {"label": "SCA Monthly Revenue Loss", "value": f"-${revenue_impact}M+",
                 "accent": C_LOW, "sublabel": "vs. baseline"},
            ],
            columns=4,
        )

        # Cape rerouting impact table
        impact_rows = [
            [_sans("Extra transit time", color=C_TEXT2),
             _mono("+10–14 days", color=C_MOD)],
            [_sans("Extra fuel cost per vessel", color=C_TEXT2),
             _mono("+$2,500–3,500/day", color=C_MOD)],
            [_sans("Extra distance", color=C_TEXT2),
             _mono("+3,500–4,000 nm", color=C_TEXT)],
            [_sans("Convoy schedule", color=C_TEXT2),
             _mono("2/day (NB 06:00 / SB 04:00)", color=C_TEXT)],
            [_sans("Average transit duration", color=C_TEXT2),
             _mono("12–16 hours", color=C_TEXT)],
        ]
        wsj_market_table(["Cape Rerouting Metric", "Value"], impact_rows)

        # Brief security note
        st.markdown(
            '<div class="sub-section-header">Red Sea Security Situation</div>'
            '<div class="wsj-body">'
            'Houthi forces continue missile, drone, and naval mine attacks targeting vessels '
            'transiting Bab-el-Mandeb and the southern Red Sea corridor. Operation Prosperity '
            'Guardian (US-led) and Operation Aspides (EU) provide partial escort; most major '
            'carriers avoid entirely.</div>',
            unsafe_allow_html=True,
        )
        st.markdown(source_footer([_SRC_CANAL_FEED]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"suez_card render error: {e}")
        st.error("Suez Canal card unavailable.")


def _render_canal_deep_dives() -> None:
    try:
        _render_panama_card()
        _render_suez_card()
    except Exception as e:
        logger.error(f"canal_deep_dives render error: {e}")
        st.error("Canal deep dives unavailable.")


# ── Section 3: Red Sea Crisis Monitor ───────────────────────────────────────

def _render_red_sea_timeline() -> None:
    try:
        rows = []
        for inc in _HOUTHI_INCIDENTS:
            severe = (
                "sink" in inc["outcome"].lower()
                or "kill" in inc["outcome"].lower()
            )
            type_color = C_LOW if severe else C_MOD
            rows.append([
                _mono(inc["date"], color=C_TEXT3),
                _sans(inc["vessel"], color=C_TEXT, weight=600),
                badge(inc["type"], color=type_color),
                _sans(inc["outcome"], color=C_TEXT2),
            ])
        wsj_market_table(
            headers=["Date", "Vessel", "Attack Type", "Outcome"],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_HOUTHI]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"houthi timeline render error: {e}")
        st.error("Timeline unavailable.")


def _render_carrier_policies() -> None:
    try:
        rows = []
        for cp in _CARRIER_POLICIES:
            avoid = cp["policy"] == "Avoiding Red Sea"
            p_color = C_LOW if avoid else C_MOD
            rows.append([
                _sans(cp["carrier"], color=C_TEXT, weight=700),
                badge(cp["policy"], color=p_color),
                _sans(cp["since"], color=C_TEXT2),
                _sans(cp["escort"], color=C_TEXT3),
            ])
        wsj_market_table(
            headers=["Carrier", "Policy", "Since", "Naval Escort"],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_CARRIER_POL]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"carrier_policies render error: {e}")
        st.error("Carrier policies unavailable.")


def _render_insurance_panel() -> None:
    try:
        metric_card_row(
            [
                {
                    "label":  "Pre-Crisis (Oct 2023)",
                    "value":  "0.03–0.05%",
                    "accent": C_HIGH,
                    "sublabel": "of vessel value",
                },
                {
                    "label":  "Peak Crisis (Jan 2024)",
                    "value":  "0.5–1.0%",
                    "accent": C_LOW,
                    "sublabel": "of vessel value (20–30x increase)",
                },
                {
                    "label":  "Current (2025)",
                    "value":  "0.3–0.7%",
                    "accent": C_MOD,
                    "sublabel": "of vessel value per voyage",
                },
            ],
            columns=3,
        )

        routing = [
            {"lane": "Asia → N. Europe",      "normal": "Suez (25–28 days)",  "current": "Cape Hope (38–42 days)", "days": "+12–14 days", "cost": "+$350K–500K"},
            {"lane": "Asia → Mediterranean", "normal": "Suez (22–26 days)",  "current": "Cape Hope (35–40 days)", "days": "+10–14 days", "cost": "+$300K–450K"},
            {"lane": "Middle East → Europe", "normal": "Suez (18–22 days)",  "current": "Cape Hope (30–36 days)", "days": "+12–14 days", "cost": "+$280K–420K"},
        ]
        rows = []
        for r in routing:
            rows.append([
                _sans(r["lane"], color=C_TEXT, weight=600),
                _sans(r["normal"], color=C_TEXT2),
                _sans(r["current"], color=C_MOD),
                _mono(r["days"], color=C_LOW),
                _mono(r["cost"], color=C_LOW),
            ])
        wsj_market_table(
            headers=["Trade Lane", "Normal Route", "Current Route", "Extra Days", "Extra Cost"],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_INSURANCE]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"insurance tab render error: {e}")
        st.error("Insurance data unavailable.")


def _render_red_sea_monitor() -> None:
    try:
        section_header(
            "Red Sea Crisis Monitor",
            subtitle="Houthi maritime attacks — Oct 2023 to present; ongoing Bab-el-Mandeb / Suez disruption",
        )
        tab_timeline, tab_carriers, tab_insurance = st.tabs([
            "Attack Timeline", "Carrier Policies", "Insurance & Rates",
        ])
        with tab_timeline:
            _render_red_sea_timeline()
        with tab_carriers:
            _render_carrier_policies()
        with tab_insurance:
            _render_insurance_panel()
    except Exception as e:
        logger.error(f"red_sea_monitor render error: {e}")
        st.error("Red Sea Crisis Monitor unavailable.")


# ── Section 4: Chokepoint Traffic Map ───────────────────────────────────────

def _render_traffic_map() -> None:
    try:
        cp_lats  = [30.5, 8.9,  1.35,  12.6,  26.6, 55.5]
        cp_lons  = [32.3, -79.5, 103.8, 43.3,  56.3, 11.8]
        cp_names = [
            "Suez Canal", "Panama Canal", "Strait of Malacca",
            "Bab-el-Mandeb", "Strait of Hormuz", "Danish Straits",
        ]
        cp_risks  = ["CRITICAL", "HIGH", "LOW", "CRITICAL", "HIGH", "LOW"]
        cp_sizes  = [28, 24, 20, 28, 24, 16]
        cp_colors = [_RISK_COLORS.get(r, C_TEXT2) for r in cp_risks]

        fig = go.Figure()

        routes = [
            dict(lats=[1.35, 3.0, 8.0, 12.6, 14.0, 27.0, 30.5, 32.0, 36.0, 43.0, 51.5],
                 lons=[103.8, 100.0, 80.0, 43.3, 42.0, 35.0, 32.3, 28.0, 18.0, 10.0, 0.0],
                 name="Asia–Europe (Suez)", color=C_MOD, dash="dash"),
            dict(lats=[1.35, 15.0, 25.0, 35.0, 37.8],
                 lons=[103.8, 140.0, 170.0, -150.0, -122.4],
                 name="Asia–US West Coast", color=C_HIGH, dash="dash"),
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

        fig.add_trace(go.Scattergeo(
            lat=cp_lats, lon=cp_lons,
            mode="markers+text",
            marker=dict(
                size=cp_sizes, color=cp_colors, opacity=0.85,
                line=dict(width=1.5, color="white"),
            ),
            text=cp_names,
            textposition="top center",
            textfont=dict(size=9, color=C_TEXT),
            name="Chokepoints",
            hovertemplate="<b>%{text}</b><extra></extra>",
        ))

        apply_dark_layout(fig, height=440)
        fig.update_layout(
            title=dict(text="Strategic Maritime Chokepoints & Trade Routes",
                       font=dict(color=C_TEXT, size=14), x=0.02),
            margin=dict(l=0, r=0, t=40, b=0),
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
        st.markdown(source_footer([_SRC_CANAL_FEED]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"traffic_map render error: {e}")
        st.error("Chokepoint traffic map unavailable.")


# ── Section 5: Rate Premium Analysis ────────────────────────────────────────

def _render_rate_premiums() -> None:
    try:
        section_header(
            "Rate Premium by Route — Chokepoint Attribution",
            subtitle="Estimated surcharges attributable to active disruptions",
        )
        rows = []
        for r in _RATE_PREMIUMS:
            rows.append([
                _sans(r["route"], color=C_TEXT, weight=700),
                _mono(r["baseline"], color=C_TEXT2),
                _mono(r["current"],  color=C_MOD),
                _mono(r["premium"],  color=C_LOW),
                _sans(r["driver"],   color=C_TEXT3),
            ])
        wsj_market_table(
            headers=["Trade Route", "Baseline Rate", "Current Rate", "Chokepoint Premium", "Primary Driver"],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_RATE_PREMIUM]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"rate_premiums render error: {e}")
        st.error("Rate premium table unavailable.")


# ── Section 6: Historical Disruption Comparison ─────────────────────────────

def _render_historical_comparison() -> None:
    try:
        section_header("Historical Major Disruptions — Impact Comparison")
        rows = []
        for inc in _HISTORICAL_INCIDENTS:
            ongoing = "present" in inc["date"].lower() or "ongoing" in inc["duration"].lower()
            dur_color = C_LOW if ongoing else C_TEXT2
            rows.append([
                _sans(inc["incident"], color=C_TEXT, weight=700),
                _sans(inc["date"],     color=C_TEXT2),
                _sans(inc["duration"], color=dur_color, weight=600),
                _sans(inc["route"],    color=C_TEXT2),
                _sans(inc["rate_impact"], color=C_MOD),
                _sans(inc["vessels"],  color=C_TEXT3),
            ])
        wsj_market_table(
            headers=["Incident", "Date", "Duration", "Route Affected", "Rate Impact", "Vessels Affected"],
            rows=rows,
        )
        st.markdown(source_footer([_SRC_HISTORICAL]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"historical_comparison render error: {e}")
        st.error("Historical disruption comparison unavailable.")


# ── Panama monthly transit chart (expander) ─────────────────────────────────

def _render_panama_transit_chart() -> None:
    try:
        months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        transits_2022 = [38, 37, 38, 37, 38, 37, 37, 36, 36, 37, 37, 38]
        transits_2023 = [36, 35, 35, 33, 30, 28, 27, 25, 24, 24, 26, 28]
        transits_2024 = [30, 32, 33, 34, 35, 35, 34, 34, 34, 35, 35, 36]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=months, y=transits_2022, name="2022 (normal)",   mode="lines+markers",
                                 line=dict(color=C_HIGH, width=2), marker=dict(size=5)))
        fig.add_trace(go.Scatter(x=months, y=transits_2023, name="2023 (drought)",  mode="lines+markers",
                                 line=dict(color=C_LOW,  width=2, dash="dash"), marker=dict(size=5)))
        fig.add_trace(go.Scatter(x=months, y=transits_2024, name="2024 (recovery)", mode="lines+markers",
                                 line=dict(color=C_MOD,  width=2), marker=dict(size=5)))
        fig.add_hrect(y0=0,  y1=26, fillcolor=C_LOW, opacity=0.07, line_width=0, annotation_text="Critical",   annotation_font_color=C_LOW)
        fig.add_hrect(y0=26, y1=30, fillcolor=C_MOD, opacity=0.07, line_width=0, annotation_text="Restricted", annotation_font_color=C_MOD)

        apply_dark_layout(fig, height=300)
        fig.update_layout(
            title=dict(text="Panama Canal — Monthly Transits (Daily Avg)",
                       font=dict(color=C_TEXT, size=13), x=0.02),
            xaxis=dict(gridcolor=C_BORDER, tickfont=dict(color=C_TEXT2)),
            yaxis=dict(gridcolor=C_BORDER, tickfont=dict(color=C_TEXT2), title="Transits/Day"),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.markdown(source_footer([_SRC_CANAL_FEED]), unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"panama_transit_chart render error: {e}")
        st.error("Panama transit chart unavailable.")


# ── Main render ─────────────────────────────────────────────────────────────

def render(port_results=None, freight_data=None, insights=None) -> None:
    """Render the Strategic Waterway & Chokepoint Intelligence tab."""
    # Lazy import keeps perf_telemetry off the tab-load critical path.
    from engine.perf_telemetry import track_render
    
    with track_render('chokepoints'):
        # R007 per-session UI overlay: mirror worker.run_canal_sync_job so the
        # UI process's CHOKEPOINTS registry reflects live Suez/Panama transit.
        # apply_live_canal_state is REAL-only (synthetic stats NO-OP via
        # CanalStats.is_synthetic) and idempotent, so tests (synthetic fallback)
        # stay baseline. Runs once per render; the canal feed has a 12h cache.
        try:
            from data.canal_feed import fetch_panama_stats, fetch_suez_stats
            from processing.canal_chokepoint_sync import apply_live_canal_state
            apply_live_canal_state([fetch_suez_stats(), fetch_panama_stats()])
        except Exception as _exc:
            logger.debug(f"canal->chokepoint UI overlay skipped: {_exc}")

        # S1 per-session UI overlay: escalate the NON-canal straits from the REAL
        # IMF PortWatch transit feed (mirrors worker.run_portwatch_transit_job).
        # Offline-safe + escalate-only: an unavailable/empty feed is a no-op so
        # the baseline stands; Suez/Panama stay owned by the canal overlay above.
        transit_marker: dict = {}
        try:
            from data.portwatch_feed import fetch_chokepoint_transits
            from processing.canal_chokepoint_sync import apply_live_chokepoint_transits
            transit_marker = apply_live_chokepoint_transits(
                fetch_chokepoint_transits()) or {}
        except Exception as _exc:
            logger.debug(f"portwatch->chokepoint UI overlay skipped: {_exc}")

        try:
            page_header(
                title="Strategic Waterway & Chokepoint Intelligence",
                subtitle="Real-time status, disruption analysis, and rate impact for the world's critical maritime chokepoints",
                badge_text="CHOKEPOINTS",
                badge_color=C_ACCENT,
            )

            section_header(
                "Network at a Glance",
                subtitle="Headline disruption read across the world's critical maritime chokepoints",
            )
            metric_card_row(
                [
                    {"label": "Critical Chokepoints", "value": "2",      "accent": C_LOW,
                     "sublabel": "Suez + Bab-el-Mandeb"},
                    {"label": "Suez Traffic Rerouted", "value": "~65%",  "accent": C_MOD,
                     "sublabel": "via Cape of Good Hope"},
                    {"label": "Asia–Europe Premium",   "value": "+$1.8–3.0K", "accent": C_ACCENT,
                     "sublabel": "per TEU surcharge"},
                ],
                columns=3,
            )

            # S1 provenance: which straits read REAL IMF PortWatch transit data
            # (and any escalated by a real transit collapse), stated honestly.
            _names = {"bab_el_mandeb": "Bab-el-Mandeb", "hormuz": "Hormuz",
                      "malacca": "Malacca", "gibraltar": "Gibraltar", "dover": "Dover",
                      "danish_straits": "Danish Straits", "lombok_sunda": "Lombok/Sunda"}
            if transit_marker:
                live = sorted(_names.get(k, k) for k in transit_marker)
                escalated = sorted(
                    (k for k, m in transit_marker.items()
                     if (m.get("transit_drop") or 0.0) >= 0.15),
                    key=lambda k: -(transit_marker[k].get("transit_drop") or 0.0))
                if escalated:
                    esc = "; ".join(
                        f"{_names.get(k, k)} −{transit_marker[k]['transit_drop'] * 100:.0f}% "
                        f"transits → {transit_marker[k]['risk_level']}"
                        for k in escalated)
                    st.caption(
                        f"🛰 **Live transit telemetry (IMF PortWatch)** — {len(live)} "
                        f"straits on real data. **Escalated by real transit collapse:** "
                        f"{esc}. (Suez/Panama via the canal feed.)")
                else:
                    st.caption(
                        f"🛰 **Live transit telemetry (IMF PortWatch)** — {len(live)} "
                        f"straits on real data ({', '.join(live)}); all near baseline "
                        f"flow. (Suez/Panama via the canal feed.)")
            else:
                st.caption(
                    "🛰 IMF PortWatch transit feed unavailable this load — chokepoint "
                    "risk on the modeled baseline (no real transit signal fabricated).")
        except Exception as e:
            logger.error(f"header render error: {e}")

        # Section 1
        try:
            section_divider("Chokepoint Status Board")
            _render_status_board()
        except Exception as e:
            logger.error(f"section 1 render error: {e}")

        # Section 2
        try:
            section_divider("Canal Deep Dives")
            _render_canal_deep_dives()
        except Exception as e:
            logger.error(f"section 2 render error: {e}")

        try:
            with st.expander("Panama Canal — Monthly Transit History"):
                _render_panama_transit_chart()
        except Exception as e:
            logger.error(f"panama chart expander error: {e}")

        # Section 3
        try:
            section_divider("Red Sea Crisis Monitor")
            _render_red_sea_monitor()
        except Exception as e:
            logger.error(f"section 3 render error: {e}")

        # Section 4
        try:
            section_divider("Chokepoint Traffic Map")
            _render_traffic_map()
        except Exception as e:
            logger.error(f"section 4 render error: {e}")

        # Section 5
        try:
            section_divider("Rate Premium Analysis")
            _render_rate_premiums()
        except Exception as e:
            logger.error(f"section 5 render error: {e}")

        # Section 6
        try:
            section_divider("Historical Disruption Comparison")
            _render_historical_comparison()
        except Exception as e:
            logger.error(f"section 6 render error: {e}")

        if _CANAL_OK:
            try:
                impact = get_canal_shipping_impact(fetch_panama_stats(), fetch_suez_stats())
                if impact:
                    with st.expander("Canal Feed — Live Impact Summary"):
                        st.json(impact)
            except Exception as e:
                logger.warning(f"canal_impact summary error: {e}")
