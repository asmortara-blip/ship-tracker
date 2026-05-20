"""tab_voyage_tracker.py — Voyage Tracker (Disruption Alpha · stage 1).

Search any modeled voyage in the synthetic fleet, then inspect it: a great-circle
route map (origin + destination port markers, the lane line, and the vessel's
current position), a progress gauge, nominal-vs-weather-adjusted ETA, and a
delay banner when the voyage is materially behind schedule. A full-fleet table
sits below.

Canonical tab pattern (mirrors ``ui/tab_rate_analytics.py``):
  * palette + components imported from ``ui/styles.py`` — never redeclared;
  * ``render(...)`` ends with ``**kwargs`` for argument safety;
  * every section wrapped in try/except + ``logger.exception``;
  * ``source_footer`` at the bottom.

The voyage fleet itself is modeled — see ``data/voyage_dataset.py``.

Sections
--------
A. Page header (badge "MODELED")
B. Fleet KPI strip — metric_card_row
C. Vessel search — text_input + selectbox over search_voyages
D. Selected-voyage detail — Scattergeo route map, progress gauge, ETA, delay banner
E. Full-fleet table — wsj_market_table
"""
from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st
from loguru import logger

from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MACRO,
    C_MOD,
    C_RULE,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    alert_banner,
    apply_dark_layout,
    badge,
    gauge_ring,
    metric_card_row,
    page_header,
    section_divider,
    section_header,
    source_footer,
    wsj_market_table,
)

# ── Domain-specific color mappings ──────────────────────────────────────────
# Semantic status -> palette color. Palette constants themselves live in
# ui/styles.py and are imported above, never redeclared here.

_STATUS_COLOR: dict[str, str] = {
    "On Schedule": C_HIGH,
    "Minor Delay": C_MOD,
    "Major Delay": C_LOW,
    "Arrived":     C_ACCENT,
}

_VESSEL_TYPE_COLOR: dict[str, str] = {
    "Container":    C_ACCENT,
    "Bulk Carrier": C_MOD,
    "Tanker":       C_LOW,
}


def _status_color(status: str) -> str:
    return _STATUS_COLOR.get(status, C_TEXT2)


def _delay_color(delay_days: float) -> str:
    if delay_days > 3.0:
        return C_LOW
    if delay_days > 1.0:
        return C_MOD
    if delay_days < -0.5:
        return C_HIGH
    return C_TEXT2


# ── Cell formatters for the WSJ market table ────────────────────────────────
# wsj_market_table renders each cell string as raw HTML inside a <td>.

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


def _metric_label(value: str) -> str:
    """First-column metric label — restrained uppercase eyebrow styling."""
    return (
        f'<span style="font-family:var(--sans);color:{C_TEXT3};'
        f'font-size:0.74rem;text-transform:uppercase;letter-spacing:0.05em;">'
        f'{value}</span>'
    )


def _group_label(value: str) -> str:
    """A spanning group divider row inside the fact table."""
    return (
        f'<span style="font-family:var(--sans);color:{C_ACCENT};'
        f'font-size:0.66rem;font-weight:700;text-transform:uppercase;'
        f'letter-spacing:0.1em;">{value}</span>'
    )


# ── Fleet loading (cached) ──────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def _load_fleet() -> list:
    """Build (and cache) the modeled voyage fleet.

    The fleet is seeded from the current date inside ``build_voyage_fleet``,
    so it is stable within a session and refreshes day to day.
    """
    from data.voyage_dataset import build_voyage_fleet
    return build_voyage_fleet()


# ── Section B': fleet utilization ───────────────────────────────────────────
def _classification_color(label: str) -> str:
    """Map a utilization classification to the palette."""
    return {"Tight": C_HIGH, "Slack": C_LOW}.get(label, C_MOD)


def _synth_util_history(current_score: float, *, n: int = 180, seed: int = 20260520):
    """Build a 180-day synthetic utilization history anchored on the *current*
    fleet score. Used because the platform only ingests a current voyage
    snapshot — there's no persisted per-day utilization series yet. Endpoint
    matches the live score so the snapshot/history are visually consistent.
    """
    import numpy as np
    import pandas as pd
    from datetime import date, timedelta

    rng = np.random.default_rng(seed)
    dates = [date.today() - timedelta(days=(n - 1 - i)) for i in range(n)]
    walk = rng.normal(0.0, 0.012, size=n).cumsum()
    walk -= walk[-1]  # land exactly on current_score at t = n-1
    path = np.clip(current_score + walk, 0.05, 0.95)
    return pd.Series(path, index=pd.to_datetime(dates))


def _synth_rate_history_for_util(seed_key: str = "fleet_util", *, n: int = 180):
    """Companion synthetic rate series for the walk-forward backtest panel."""
    import numpy as np
    import pandas as pd
    from datetime import date, timedelta

    # Use stable_hash so the seed is process-stable (Python's hash() is salted).
    try:
        from utils.helpers import stable_hash
        seed = stable_hash(seed_key)
    except Exception:
        seed = 4242
    rng = np.random.default_rng(seed % (2**31))
    dates = [date.today() - timedelta(days=(n - 1 - i)) for i in range(n)]
    rates = 2000.0 * np.exp(rng.normal(0.0, 0.012, size=n).cumsum())
    return pd.Series(rates, index=pd.to_datetime(dates))


def _render_fleet_utilization(fleet: list, freight_data=None) -> None:
    """Snapshot of fleet utilization + per-route ranking + backtest panel.

    Pulls compute_fleet_utilization on the current fleet, surfaces the four
    component metrics per route in a wsj_market_table, and runs the
    walk-forward backtest on synthetic history so the model's predictive
    power is visible in the UI (per docs/ROADMAP.md principle 5).
    """
    try:
        from engine.fleet_utilization import (
            compute_fleet_utilization,
            walk_forward_backtest,
        )

        section_header(
            "Fleet Utilization",
            "Composite score of active share, capacity lock-in, delay intensity, "
            "and forward destination congestion. Higher = tighter capacity = bullish for rates.",
        )

        report = compute_fleet_utilization(fleet)

        # ── Headline metric strip ──────────────────────────────────────────
        cls_color = _classification_color(report.fleet_classification)
        metric_card_row(
            [
                {"label": "Fleet Utilization",
                 "value": f"{report.fleet_utilization * 100:.1f}%",
                 "accent": cls_color,
                 "sublabel": report.fleet_classification},
                {"label": "Active Voyages",
                 "value": f"{report.voyages_active}",
                 "accent": C_ACCENT,
                 "sublabel": f"of {report.voyages_total} total"},
                {"label": "Routes Tracked",
                 "value": f"{len(report.routes)}",
                 "accent": C_TEXT2,
                 "sublabel": "with at least one voyage"},
                {"label": "Tight / Balanced / Slack",
                 "value": (
                     f"{sum(1 for r in report.routes if r.classification == 'Tight')} / "
                     f"{sum(1 for r in report.routes if r.classification == 'Balanced')} / "
                     f"{sum(1 for r in report.routes if r.classification == 'Slack')}"
                 ),
                 "accent": cls_color,
                 "sublabel": "Route distribution"},
            ],
            columns=4,
        )

        if not report.routes:
            st.info("No routes carry active voyages — nothing to score.")
            return

        # ── Per-route ranking table, sorted by score descending ────────────
        sorted_routes = sorted(
            report.routes,
            key=lambda rm: rm.utilization_score,
            reverse=True,
        )
        headers = [
            "Route", "Score", "Class", "Active",
            "Total", "Progress", "Delay (d)", "Fwd Cong.",
        ]
        rows: list[list[str]] = []
        for rm in sorted_routes:
            rows.append([
                _mono(rm.route_id, color=C_TEXT),
                _mono(f"{rm.utilization_score * 100:5.1f}%",
                      color=_classification_color(rm.classification)),
                _sans(
                    badge(rm.classification, color=_classification_color(rm.classification)),
                    color=C_TEXT2,
                ),
                _mono(f"{rm.voyages_active}", color=C_TEXT),
                _mono(f"{rm.voyages_total}", color=C_TEXT3),
                _mono(f"{rm.mean_progress_pct * 100:4.0f}%", color=C_TEXT2),
                _mono(
                    f"{rm.mean_delay_days:+5.1f}",
                    color=(C_LOW if rm.mean_delay_days > 5 else (
                        C_MOD if rm.mean_delay_days > 1 else C_TEXT2
                    )),
                ),
                _mono(
                    f"{rm.mean_dest_congestion * 100:4.0f}%",
                    color=(C_LOW if rm.mean_dest_congestion > 0.6 else (
                        C_MOD if rm.mean_dest_congestion > 0.35 else C_TEXT2
                    )),
                ),
            ])
        wsj_market_table(headers, rows)

        # ── Walk-forward backtest on synthetic utilization vs rate ─────────
        try:
            util_series = _synth_util_history(report.fleet_utilization)
            rate_series = _synth_rate_history_for_util("fleet_util")
            bt = walk_forward_backtest(
                util_series, rate_series,
                train_window=60, test_window=10, step=10,
            )
            hit_color = (
                C_HIGH if bt.hit_rate >= 0.55
                else (C_MOD if bt.hit_rate >= 0.45 else C_LOW)
            )
            out_color = C_HIGH if bt.avg_r_out_of_sample > 0.1 else C_TEXT2
            metric_card_row(
                [
                    {"label": "Backtest Windows",
                     "value": f"{bt.n_windows}",
                     "accent": C_TEXT2,
                     "sublabel": "Train 60d / test 10d / step 10d"},
                    {"label": "Modal Lag",
                     "value": f"{bt.best_lag_days}d",
                     "accent": C_ACCENT,
                     "sublabel": "Most-picked across windows"},
                    {"label": "Direction Hit Rate",
                     "value": f"{bt.hit_rate * 100:.0f}%",
                     "accent": hit_color,
                     "sublabel": "Sign of next-window Δrate"},
                    {"label": "Out-of-Sample r̄",
                     "value": f"{bt.avg_r_out_of_sample:+.2f}",
                     "accent": out_color,
                     "sublabel": f"In-sample r̄={bt.avg_r_in_sample:+.2f}"},
                ],
                columns=4,
            )
        except Exception as exc:
            logger.debug(f"tab_voyage_tracker: utilization backtest skipped: {exc}")

        # ── Provenance footer ──────────────────────────────────────────────
        try:
            from data.quality import DataSource
            st.markdown(
                source_footer([
                    DataSource.modeled(
                        "Fleet Utilization Composite",
                        notes=(
                            "Snapshot from current voyage fleet. Backtest panel "
                            "uses a synthetic 180-day utilization history "
                            "anchored on the current score — the platform does "
                            "not yet persist a per-day utilization series."
                        ),
                    ),
                ]),
                unsafe_allow_html=True,
            )
        except Exception:
            pass

    except Exception:
        logger.exception("Voyage Tracker — fleet utilization render failed")


# ── Section B: fleet KPI strip ──────────────────────────────────────────────

def _render_kpi_strip(fleet: list) -> None:
    from data.voyage_dataset import voyage_fleet_summary

    summary = voyage_fleet_summary(fleet)
    delayed_pct = summary["delayed_pct"]
    delayed_accent = (
        C_LOW if delayed_pct >= 35 else (C_MOD if delayed_pct >= 15 else C_HIGH)
    )
    on_time_pct = 100.0 - delayed_pct

    metric_card_row(
        [
            {
                "label":  "Voyages Tracked",
                "value":  str(summary["total"]),
                "accent": C_ACCENT,
                "sublabel": f"{summary['in_transit']} in transit · {summary['arrived']} arrived",
            },
            {
                "label":  "On Schedule",
                "value":  f"{on_time_pct:.0f}%",
                "accent": (C_HIGH if on_time_pct >= 65 else C_MOD),
                "delta":  f"{summary['on_schedule']} of {summary['total']} vessels",
                "delta_color": C_TEXT3,
                "sublabel": "running at or ahead of plan",
            },
            {
                "label":  "Delayed",
                "value":  f"{delayed_pct:.0f}%",
                "accent": delayed_accent,
                "sublabel": f"{summary['major_delay']} major · {summary['minor_delay']} minor",
            },
            {
                "label":  "Avg Delay",
                "value":  f"{summary['avg_delay_days']:+.1f}d",
                "accent": _delay_color(summary["avg_delay_days"]),
                "sublabel": f"fleet avg progress {summary['avg_progress_pct']:.0f}%",
            },
            {
                "label":  "Disrupted Lanes",
                "value":  str(summary["disrupted_routes"]),
                "accent": C_MOD if summary["disrupted_routes"] else C_HIGH,
                "sublabel": f"chokepoint-touched · {summary['avg_speed_kts']:.1f} kts avg",
            },
        ],
        columns=5,
    )


# ── Section D: route map ────────────────────────────────────────────────────

def _render_route_map(voyage) -> None:
    """Render a Scattergeo map: origin + dest port markers, lane line, position."""
    from data.voyage_dataset import _great_circle_point
    from ports.port_registry import PORTS_BY_LOCODE

    origin = PORTS_BY_LOCODE.get(voyage.origin_locode)
    dest = PORTS_BY_LOCODE.get(voyage.dest_locode)
    if origin is None or dest is None:
        st.info("Port geometry is unavailable for this lane — map cannot be drawn.")
        return

    fig = go.Figure()

    # ─ Great-circle lane line (≈24 interpolated waypoints so it follows the
    #   true shortest path instead of cutting through land) ─
    line_lats, line_lons = [], []
    for i in range(25):
        frac = i / 24.0
        lat, lon = _great_circle_point(
            origin.lat, origin.lon, dest.lat, dest.lon, frac
        )
        line_lats.append(lat)
        line_lons.append(lon)

    # Sailed portion of the lane (origin -> current position) drawn solid so
    # the eye reads "covered ground vs ground remaining" at a glance.
    sailed = max(2, min(25, int(round(voyage.progress_pct * 24)) + 1))
    fig.add_trace(go.Scattergeo(
        lat=line_lats[:sailed],
        lon=line_lons[:sailed],
        mode="lines",
        line=dict(width=2.4, color="rgba(53,114,176,0.85)"),
        hoverinfo="skip",
        showlegend=False,
        name="Sailed",
    ))

    # Remaining portion — dotted, recessive.
    fig.add_trace(go.Scattergeo(
        lat=line_lats,
        lon=line_lons,
        mode="lines",
        line=dict(width=1.4, color="rgba(53,114,176,0.32)", dash="dot"),
        hoverinfo="skip",
        showlegend=False,
        name="Route",
    ))

    # ─ Origin + destination port markers ─
    fig.add_trace(go.Scattergeo(
        lat=[origin.lat, dest.lat],
        lon=[origin.lon, dest.lon],
        mode="markers+text",
        marker=dict(
            size=[12, 12],
            color=[C_TEXT2, C_HIGH],
            symbol="circle",
            opacity=0.95,
            line=dict(width=1.4, color="rgba(255,255,255,0.4)"),
        ),
        text=[f"{origin.name} ({origin.locode})", f"{dest.name} ({dest.locode})"],
        textposition="top center",
        textfont=dict(
            color=C_TEXT2,
            size=10,
            family="'Libre Franklin', 'Inter', system-ui, sans-serif",
        ),
        hovertext=[
            f"<b>Origin · {origin.name}</b><br>{origin.locode}",
            f"<b>Destination · {dest.name}</b><br>{dest.locode}",
        ],
        hoverinfo="text",
        showlegend=False,
        name="Ports",
    ))

    # ─ Current vessel position — soft halo + crisp marker ─
    pos_color = _status_color(voyage.status)
    fig.add_trace(go.Scattergeo(
        lat=[voyage.current_lat],
        lon=[voyage.current_lon],
        mode="markers",
        marker=dict(
            size=26,
            color=pos_color,
            symbol="circle",
            opacity=0.16,
        ),
        hoverinfo="skip",
        showlegend=False,
        name="Vessel halo",
    ))
    fig.add_trace(go.Scattergeo(
        lat=[voyage.current_lat],
        lon=[voyage.current_lon],
        mode="markers",
        marker=dict(
            size=14,
            color=pos_color,
            symbol="triangle-up",
            opacity=1.0,
            line=dict(width=1.6, color="rgba(255,255,255,0.65)"),
        ),
        hovertext=[
            f"<b>{voyage.vessel_name}</b><br>"
            f"{voyage.status}<br>"
            f"Progress: {voyage.progress_pct * 100:.0f}%<br>"
            f"Speed: {voyage.speed_kts:.1f} kts"
        ],
        hoverinfo="text",
        showlegend=False,
        name="Vessel",
    ))

    apply_dark_layout(
        fig,
        height=420,
        showlegend=False,
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        geo=dict(
            showframe=False,
            showcoastlines=True,
            coastlinecolor="rgba(100,130,180,0.4)",
            showland=True,
            landcolor="rgba(18,26,46,1)",
            showocean=True,
            oceancolor="rgba(8,16,36,1)",
            showlakes=False,
            showrivers=False,
            showcountries=True,
            countrycolor="rgba(60,80,120,0.3)",
            projection_type="natural earth",
            bgcolor="rgba(10,15,26,1)",
        ),
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        config={"displayModeBar": False},
        key="voyage_tracker_map",
    )


def _render_lane_legend(voyage) -> None:
    """A compact, captioned legend strip beneath the route map."""
    items = [
        ("rgba(53,114,176,0.85)", "Sailed"),
        ("rgba(53,114,176,0.45)", "Remaining"),
        (C_HIGH, "Destination"),
        (_status_color(voyage.status), "Vessel position"),
    ]
    chips = "".join(
        f'<span style="display:inline-flex;align-items:center;gap:6px;'
        f'font-family:var(--sans);font-size:0.7rem;color:{C_TEXT3};">'
        f'<span style="width:9px;height:9px;border-radius:2px;'
        f'background:{color};display:inline-block;"></span>{label}</span>'
        for color, label in items
    )
    st.markdown(
        f'<div style="display:flex;flex-wrap:wrap;gap:18px;'
        f'margin-top:6px;padding-top:8px;border-top:1px solid {C_RULE};">'
        f'{chips}</div>',
        unsafe_allow_html=True,
    )


# ── Section D: selected-voyage detail ───────────────────────────────────────

def _render_voyage_detail(voyage) -> None:
    """Render the full detail block for one selected voyage."""
    from routes.route_registry import ROUTES_BY_ID

    route = ROUTES_BY_ID.get(voyage.route_id)
    route_name = route.name if route else voyage.route_id

    section_header(
        f"{voyage.vessel_name}",
        f"{voyage.voyage_id} · {route_name} · {voyage.vessel_type} · MMSI {voyage.mmsi}",
    )

    # ─ Delay banner on a material delay ─
    if voyage.status == "Major Delay":
        choke_note = ""
        if voyage.chokepoints_on_route:
            choke_note = (
                f" Lane is affected by an active disruption at "
                f"{', '.join(voyage.chokepoints_on_route)}."
            )
        alert_banner(
            f"<b>{voyage.vessel_name}</b> is running "
            f"<b>{voyage.delay_days:+.1f} days</b> behind schedule on the "
            f"{route_name} lane.{choke_note}",
            level="critical",
        )
    elif voyage.status == "Minor Delay":
        alert_banner(
            f"<b>{voyage.vessel_name}</b> is modestly behind schedule "
            f"({voyage.delay_days:+.1f} days) on the {route_name} lane.",
            level="warning",
        )

    col_map, col_side = st.columns([3, 2], gap="large")

    # ─ Map ─
    with col_map:
        try:
            # Lane brief: a single editorial line framing the map below it.
            st.markdown(
                f'<div style="font-family:var(--sans);font-size:0.78rem;'
                f'color:{C_TEXT2};margin-bottom:6px;">'
                f'<span style="color:{C_TEXT3};text-transform:uppercase;'
                f'letter-spacing:0.05em;font-size:0.68rem;font-weight:700;">'
                f'Lane</span>&nbsp;&nbsp;'
                f'{voyage.origin_locode}&nbsp;&rarr;&nbsp;{voyage.dest_locode}'
                f'&nbsp;&nbsp;<span style="color:{C_TEXT3};">·</span>&nbsp;&nbsp;'
                f'{voyage.progress_pct * 100:.0f}% sailed at '
                f'{voyage.speed_kts:.1f} kts</div>',
                unsafe_allow_html=True,
            )
            _render_route_map(voyage)
            _render_lane_legend(voyage)
        except Exception:
            logger.exception("Voyage Tracker — route map failed")
            st.error("Route map unavailable for this voyage.")

    # ─ Gauge + ETA + facts ─
    with col_side:
        try:
            gauge_color = _status_color(voyage.status)
            fig = gauge_ring(
                voyage.progress_pct,
                "Voyage Progress",
                color=gauge_color,
                size=190,
            )
            st.plotly_chart(
                fig,
                use_container_width=True,
                config={"displayModeBar": False},
                key="voyage_tracker_gauge",
            )

            # Nominal vs weather-adjusted ETA via the real weather model.
            try:
                from processing.weather_risk import compute_weather_adjusted_eta
                wx_expected, wx_worst = compute_weather_adjusted_eta(
                    voyage.route_id, float(voyage.nominal_transit_days)
                )
            except Exception:
                logger.exception("Voyage Tracker — weather ETA failed")
                wx_expected = wx_worst = float(voyage.nominal_transit_days)

            congestion_pct = voyage.congestion_at_dest * 100
            choke_text = (
                ", ".join(voyage.chokepoints_on_route)
                if voyage.chokepoints_on_route
                else "none on lane"
            )
            choke_color = C_MOD if voyage.chokepoints_on_route else C_HIGH

            # Grouped fact table — schedule block then risk block, with thin
            # spanning group rows so the panel reads as a structured brief.
            eta_rows = [
                [_group_label("Schedule"), ""],
                [
                    _metric_label("Status"),
                    badge(voyage.status, color=_status_color(voyage.status)),
                ],
                [
                    _metric_label("Delay"),
                    _mono(f"{voyage.delay_days:+.1f} d",
                          color=_delay_color(voyage.delay_days)),
                ],
                [
                    _metric_label("Departed"),
                    _mono(voyage.departed_at.strftime("%Y-%m-%d"), color=C_TEXT2),
                ],
                [
                    _metric_label("ETA · nominal"),
                    _mono(voyage.eta_nominal.strftime("%Y-%m-%d"), color=C_TEXT),
                ],
                [
                    _metric_label("ETA · adjusted"),
                    _mono(voyage.eta_adjusted.strftime("%Y-%m-%d"),
                          color=_delay_color(voyage.delay_days)),
                ],
                [_group_label("Transit & Risk"), ""],
                [
                    _metric_label("Weather-adj transit"),
                    _mono(f"{wx_expected:.1f} d · wc {wx_worst:.1f}", color=C_MOD),
                ],
                [
                    _metric_label("Speed"),
                    _mono(f"{voyage.speed_kts:.1f} kts", color=C_MACRO),
                ],
                [
                    _metric_label("Dest congestion"),
                    _mono(f"{congestion_pct:.0f}%",
                          color=_delay_color(voyage.congestion_at_dest * 6)),
                ],
                [
                    _metric_label("Chokepoints"),
                    _sans(choke_text, color=choke_color, weight=600),
                ],
            ]
            wsj_market_table(headers=["Metric", "Value"], rows=eta_rows)
        except Exception:
            logger.exception("Voyage Tracker — voyage facts failed")
            st.error("Voyage detail panel unavailable.")


# ── Section C: search empty state ───────────────────────────────────────────

def _render_search_hint() -> None:
    """A quiet prompt shown before any voyage is selected."""
    st.markdown(
        f'<div style="border:1px dashed {C_RULE};border-radius:8px;'
        f'padding:18px 22px;margin:4px 0 4px 0;background:rgba(53,114,176,0.025);">'
        f'<div style="font-family:var(--serif);font-size:0.98rem;'
        f'color:{C_TEXT};margin-bottom:4px;">No vessel selected</div>'
        f'<div style="font-family:var(--sans);font-size:0.8rem;'
        f'color:{C_TEXT2};line-height:1.55;">'
        f'Pick a voyage above to open its route map, progress gauge and '
        f'weather-adjusted ETA &mdash; or scan the full fleet below, '
        f'sorted by delay.</div></div>',
        unsafe_allow_html=True,
    )


# ── Section E: full-fleet table ─────────────────────────────────────────────

def _render_fleet_table(fleet: list) -> None:
    from routes.route_registry import ROUTES_BY_ID

    section_header(
        "Full Fleet",
        f"All {len(fleet)} modeled voyages — sorted by delay, most-delayed first",
    )

    rows = []
    for v in sorted(fleet, key=lambda x: x.delay_days, reverse=True):
        route = ROUTES_BY_ID.get(v.route_id)
        route_name = route.name if route else v.route_id
        rows.append([
            _sans(v.vessel_name[:22], color=C_TEXT, weight=700),
            badge(v.vessel_type, color=_VESSEL_TYPE_COLOR.get(v.vessel_type, C_TEXT2)),
            _sans(str(route_name)[:24], color=C_TEXT2),
            _mono(f"{v.origin_locode} → {v.dest_locode}", color=C_TEXT3),
            _mono(f"{v.progress_pct * 100:.0f}%", color=C_TEXT),
            _mono(v.eta_adjusted.strftime("%m-%d"), color=C_TEXT2),
            _mono(f"{v.delay_days:+.1f}d", color=_delay_color(v.delay_days)),
            badge(v.status, color=_status_color(v.status)),
        ])

    wsj_market_table(
        headers=[
            "Vessel", "Type", "Route", "Lane",
            "Progress", "ETA", "Delay", "Status",
        ],
        rows=rows,
    )


# ── Public entry point ──────────────────────────────────────────────────────

def render(freight_data=None, route_results=None, **kwargs) -> None:
    """Render the Voyage Tracker tab.

    Parameters
    ----------
    freight_data, route_results:
        Accepted for call-site symmetry with the rest of the platform; the
        voyage fleet is self-contained (modeled), so they are not required.
    """
    # ── A. Page header ──────────────────────────────────────────────────────
    page_header(
        title="Voyage Tracker",
        subtitle="Search and inspect any vessel in the modeled fleet — "
        "route, progress, ETA, congestion and disruption exposure",
        badge_text="MODELED",
        badge_color=C_ACCENT,
    )

    # ── Load the modeled fleet ──────────────────────────────────────────────
    try:
        fleet = _load_fleet()
    except Exception:
        logger.exception("Voyage Tracker — fleet build failed")
        st.error("Could not build the modeled voyage fleet.")
        return

    if not fleet:
        st.info("No voyages in the modeled fleet.")
        return

    # ── B. Fleet KPI strip ──────────────────────────────────────────────────
    try:
        _render_kpi_strip(fleet)
    except Exception:
        logger.exception("Voyage Tracker — KPI strip failed")
        st.error("Fleet KPI strip unavailable.")

    # ── B'. Fleet utilization composite + backtest panel ───────────────────
    section_divider("Utilization")
    _render_fleet_utilization(fleet, freight_data=freight_data)

    section_divider("Vessel Search")

    # ── C. Vessel search ────────────────────────────────────────────────────
    selected_voyage = None
    matched = True
    try:
        from data.voyage_dataset import search_voyages

        section_header(
            "Find a Vessel",
            "Search by vessel name, voyage ID, MMSI, route or port code",
        )
        query = st.text_input(
            "Search vessels",
            value="",
            placeholder="e.g. EVER, asia_europe, CNSHA, VY-ASIA…",
            key="voyage_tracker_search",
        )
        matches = search_voyages(query, fleet)

        if not matches:
            matched = False
            alert_banner(
                f"No voyages match <b>'{query}'</b> — clear the search to "
                f"browse the full fleet below.",
                level="info",
            )
        else:
            # Build a stable, human-readable label per voyage.
            def _label(v) -> str:
                return (
                    f"{v.vessel_name} · {v.voyage_id} · "
                    f"{v.origin_locode}→{v.dest_locode} · {v.status}"
                )

            label_map = {_label(v): v for v in matches}
            n = len(matches)
            chosen_label = st.selectbox(
                f"Select a voyage — {n} match{'' if n == 1 else 'es'}",
                options=list(label_map.keys()),
                key="voyage_tracker_select",
            )
            selected_voyage = label_map.get(chosen_label)
    except Exception:
        logger.exception("Voyage Tracker — vessel search failed")
        st.error("Vessel search unavailable.")

    # ── D. Selected-voyage detail ───────────────────────────────────────────
    if selected_voyage is not None:
        section_divider("Voyage Detail")
        try:
            _render_voyage_detail(selected_voyage)
        except Exception:
            logger.exception("Voyage Tracker — voyage detail failed")
            st.error("Voyage detail unavailable.")
    elif matched:
        # Matches exist but none resolved to a voyage — quiet prompt.
        try:
            _render_search_hint()
        except Exception:
            logger.exception("Voyage Tracker — search hint failed")

    section_divider("Fleet Roster")

    # ── E. Full-fleet table ─────────────────────────────────────────────────
    try:
        _render_fleet_table(fleet)
    except Exception:
        logger.exception("Voyage Tracker — fleet table failed")
        st.error("Fleet table unavailable.")

    # ── Provenance footer ───────────────────────────────────────────────────
    try:
        from data.voyage_dataset import VOYAGE_DATA_SOURCE
        st.markdown(source_footer([VOYAGE_DATA_SOURCE]), unsafe_allow_html=True)
    except Exception:
        logger.exception("Voyage Tracker — source footer failed")
