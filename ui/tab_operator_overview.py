"""tab_operator_overview.py — At-a-glance operator situation room.

Stitches together every observability surface shipped in recent commits so
an operator can see system health in one screen instead of bouncing across
Alerts / Channels / LLM Telemetry / Perf / Source Health / Audit dashboards.

This tab is intentionally **read-only**. Every section is wrapped in its
own try/except so a single upstream outage degrades to an "unavailable"
warning instead of blanking the whole screen. Engine imports are lazy
inside ``render()`` so the smoke harness can import this module even with
a broken telemetry stack.

Sections
--------
Hero          — 4 KPIs (active alerts 24h, enabled channels, LLM spend 7d,
                recent incidents 7d).
Alerts        — severity breakdown table + top 5 most recent.
Channels      — enabled / disabled table per channel.
LLM spend     — 7d total + top 3 most-expensive call sites (by source).
Tab perf      — top 5 slowest tabs by p95 render time (24h window).
Source health — per-source status table (green/yellow/red).
Incidents     — last 10 from ``get_recent_incidents``.
Audit events  — last 20 from ``auth.audit.query_audit``.

CSV downloads
-------------
Each of the 7 data panels gets its own "Download CSV" button rendered
directly below its table. A single "Download all panels (zip)" button at
the top of the tab bundles every populated panel into one zip file. CSVs
are produced by ``utils.csv_export`` (UTF-8 with BOM, Excel-friendly).

Cross-link: deeper drill-downs for any panel live in their dedicated tabs
(Alerts, Data Health, Operator) — this is a single-screen summary, not a
duplicate of those surfaces.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT3,
    metric_card_row,
    page_header,
    section_divider,
    source_footer,
)


# Provenance for the footer. This tab is a read-only roll-up of the platform's
# own internal telemetry (alerts, channels, LLM cost, render perf, source-health
# pings, incidents, audit) — all MODELED / internally-generated, not an external
# market feed.
_OVERVIEW_SOURCE = DataSource.modeled(
    "Operator telemetry",
    notes=(
        "Internal observability roll-up: alert engine, delivery channels, LLM "
        "usage, render perf, source-health pings, incident correlator, audit log."
    ),
)


# ── Small formatting helpers ────────────────────────────────────────────────
def _fmt_usd(value: float) -> str:
    """Compact USD formatter, never raises."""
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def _fmt_ms(ms: int | float) -> str:
    """Format a duration; falls back to '—' on non-numeric input."""
    try:
        ms_i = int(ms)
    except (TypeError, ValueError):
        return "—"
    return f"{ms_i / 1000:.2f}s" if ms_i >= 1000 else f"{ms_i:,} ms"


def _fmt_ts(iso: str) -> str:
    """Render an ISO timestamp in the user's TZ; falls back to the raw
    first-16 chars when the helper isn't available."""
    try:
        from utils.tz import format_user_tz
        out = format_user_tz(iso, fmt="%Y-%m-%d %H:%M")
        if out:
            return out
    except Exception:
        pass
    try:
        return str(iso)[:16].replace("T", " ")
    except Exception:
        return ""


def _status_color(status: str) -> str:
    """Map a source/incident status to one of the WSJ palette colours."""
    s = (status or "").lower()
    if s in {"up", "ok", "healthy", "green"}:
        return C_HIGH
    if s in {"degraded", "stale", "warn", "warning", "yellow"}:
        return C_MOD
    if s in {"down", "error", "failed", "critical", "red"}:
        return C_LOW
    return C_TEXT3


# ── Engine adapters — each returns a default on any failure ──────────────────
# Lazy imports keep this module importable from the smoke harness even when
# individual telemetry modules are broken or missing.
def _load_alerts() -> list:
    from engine.alert_engine_v2 import load_alerts
    return load_alerts() or []


def _load_channels() -> list:
    from engine.alert_delivery import load_channels
    return load_channels() or []


def _load_incidents(window_days: int) -> list:
    from engine.alert_correlator import get_recent_incidents
    return get_recent_incidents(window_days=window_days) or []


def _load_llm_summary(days: int) -> dict:
    from engine.llm_telemetry import get_usage_summary
    return get_usage_summary(window_days=days) or {}


def _load_perf_summary(window_hours: int) -> dict:
    from engine.perf_telemetry import get_perf_summary
    return get_perf_summary(window_hours=window_hours) or {}


def _load_source_health(window_hours: int) -> dict:
    from engine.source_health import get_health_summary
    return get_health_summary(window_hours=window_hours) or {}


def _load_audit_events(limit: int) -> list:
    from auth.audit import query_audit
    return query_audit(limit=limit) or []


# ── Row builders — pure functions used by both panels and the zip bundle ───
# Each returns a list[dict] (or, where the panel has two sub-tables, a tuple
# of lists). These are the exact rows that get rendered AND CSV-exported, so
# the download always matches what's on screen.

def _build_alerts_breakdown_rows(alerts: list) -> list[dict]:
    """Severity breakdown rows. Empty list when no alerts."""
    if not alerts:
        return []
    sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    counts: dict[str, int] = {}
    for a in alerts:
        sev = (getattr(a, "severity", "") or "UNKNOWN").upper()
        counts[sev] = counts.get(sev, 0) + 1
    rows: list[dict] = []
    for sev in sev_order:
        if sev in counts:
            rows.append({"Severity": sev, "Count": counts[sev]})
    # Append any unknown severities last for completeness.
    for sev, n in counts.items():
        if sev not in sev_order:
            rows.append({"Severity": sev, "Count": n})
    return rows


def _build_alerts_recent_rows(alerts: list) -> list[dict]:
    """Top-5-most-recent alert rows. Empty list when no alerts."""
    if not alerts:
        return []
    sorted_alerts = sorted(
        alerts,
        key=lambda x: str(getattr(x, "created_at", "")),
        reverse=True,
    )[:5]
    return [
        {
            "When": _fmt_ts(getattr(a, "created_at", "")),
            "Severity": getattr(a, "severity", ""),
            "Type": getattr(a, "alert_type", ""),
            "Title": getattr(a, "title", ""),
        }
        for a in sorted_alerts
    ]


def _build_channels_rows(channels: list) -> list[dict]:
    if not channels:
        return []
    return [
        {
            "Name": getattr(c, "name", ""),
            "Kind": getattr(c, "kind", ""),
            "Severity floor": getattr(c, "severity_threshold", ""),
            "Status": "Enabled" if getattr(c, "enabled", False) else "Disabled",
        }
        for c in channels
    ]


def _build_llm_rows(llm: dict) -> list[dict]:
    """Top-3 most-expensive call sites. Empty list when no per-source data
    OR no LLM activity at all."""
    if not llm or int(llm.get("total_calls", 0) or 0) == 0:
        return []
    by_source = llm.get("by_source", {}) or {}
    if not by_source:
        return []
    ranked = sorted(
        (
            {"source": k, **(v if isinstance(v, dict) else {})}
            for k, v in by_source.items()
        ),
        key=lambda r: float(r.get("cost", 0.0) or 0.0),
        reverse=True,
    )[:3]
    return [
        {
            "Source": r.get("source", ""),
            "Calls": int(r.get("calls", 0) or 0),
            "Cost": _fmt_usd(r.get("cost", 0.0)),
        }
        for r in ranked
    ]


def _build_perf_rows(perf: dict) -> list[dict]:
    """Top 5 slowest tabs by p95 render time. Empty when no telemetry."""
    if not perf or int(perf.get("total_renders", 0) or 0) == 0:
        return []
    by_tab = perf.get("by_tab", {}) or {}
    candidates: list[dict[str, Any]] = []
    for name, payload in by_tab.items():
        if not isinstance(payload, dict):
            continue
        candidates.append({
            "tab_name": name,
            "count": int(payload.get("count", 0) or 0),
            "median_ms": int(payload.get("median_ms", 0) or 0),
            "p95_ms": int(payload.get("p95_ms", 0) or 0),
            "error_count": int(payload.get("error_count", 0) or 0),
        })
    ranked = sorted(candidates, key=lambda r: r["p95_ms"], reverse=True)[:5]
    return [
        {
            "Tab": r["tab_name"],
            "Renders": r["count"],
            "Median": _fmt_ms(r["median_ms"]),
            "P95": _fmt_ms(r["p95_ms"]),
            "Errors": r["error_count"],
        }
        for r in ranked
    ]


def _build_source_rows(health: dict) -> list[dict]:
    """Per-source health rows (no HTML pill — that's a UI-only concern).

    For CSV export we use the raw uppercase status string. The on-screen
    table renders a colored pill via inline HTML, but the CSV gets the
    plain text — operators don't need HTML in Excel.
    """
    by_source = (health or {}).get("by_source", {}) or {}
    if not by_source:
        return []
    rows: list[dict] = []
    for name in sorted(by_source.keys()):
        info = by_source[name] if isinstance(by_source[name], dict) else {}
        last_status = str(info.get("last_status", "") or "")
        rows.append({
            "Source": name,
            "Status": last_status.upper() or "—",
            "Pings": int(info.get("count", 0) or 0),
            "Avg latency": _fmt_ms(info.get("avg_duration_ms", 0)),
            "Last ping": _fmt_ts(info.get("last_started_at", "")),
        })
    return rows


def _build_incidents_rows(incidents: list) -> list[dict]:
    if not incidents:
        return []
    return [
        {
            "Started": _fmt_ts(getattr(inc, "started_at", "")),
            "Max severity": getattr(inc, "severity_max", ""),
            "Dominant type": getattr(inc, "dominant_alert_type", ""),
            "Alerts": int(getattr(inc, "alert_count", 0) or 0),
        }
        for inc in incidents[:10]
    ]


def _build_audit_rows(events: list) -> list[dict]:
    if not events:
        return []
    return [
        {
            "When": _fmt_ts(getattr(ev, "created_at", "")),
            "User": getattr(ev, "user_id", "") or "—",
            "Action": getattr(ev, "action", ""),
            "Entity": getattr(ev, "entity_type", ""),
            "ID": (getattr(ev, "entity_id", "") or "")[:12],
        }
        for ev in events[:20]
    ]


# ── CSV download helper ─────────────────────────────────────────────────────
# Lazily imports utils.csv_export so a broken csv_export module can't take
# down the whole tab — the panel still renders, just without the button.

def _render_csv_download_button(
    rows: list[dict],
    *,
    base_name: str,
    key: str,
    columns: list[str] | None = None,
) -> None:
    """Render a "📥 Download CSV" button below a panel's table.

    Wrapped in try/except + ``st.error`` fallback so a bad row dict cannot
    crash the panel — the CSV button is operator convenience, not the
    primary surface. Skipped silently when ``rows`` is empty (the panel
    has already rendered an ``st.info`` "unavailable" message).
    """
    if not rows:
        return
    try:
        from utils.csv_export import rows_to_csv_bytes, safe_filename
        payload = rows_to_csv_bytes(rows, columns=columns)
        st.download_button(
            label="📥 Download CSV",
            data=payload,
            file_name=safe_filename(base_name),
            mime="text/csv",
            key=key,
        )
    except Exception as exc:
        logger.exception(
            f"operator_overview: csv download button failed ({base_name}): {exc}"
        )
        try:
            st.error(f"CSV download for {base_name} unavailable.")
        except Exception:
            pass


def _render_bundle_zip_button(
    panel_payloads: list[tuple[str, list[dict]]],
    *,
    key: str = "op_overview_zip",
) -> None:
    """Render a single "Download all panels (zip)" button at the top.

    ``panel_payloads`` is a list of ``(base_name, rows)`` tuples. Empty-row
    panels are skipped (matches the per-panel button behaviour). When EVERY
    panel is empty (fresh install with no telemetry yet) the button is
    omitted entirely so we don't serve an empty zip.
    """
    populated = [(n, r) for n, r in panel_payloads if r]
    if not populated:
        return
    try:
        from utils.csv_export import rows_to_csv_bytes, safe_filename
        buf = io.BytesIO()
        # ZIP_DEFLATED keeps the bundle small; the alternative (STORED) is
        # unnecessary for text payloads of this size.
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for base_name, rows in populated:
                csv_bytes = rows_to_csv_bytes(rows)
                # Each CSV inside the zip gets a stable name (no timestamp)
                # so the zip itself is the dated artefact.
                inner_name = f"{base_name}.csv"
                zf.writestr(inner_name, csv_bytes)
        buf.seek(0)
        st.download_button(
            label="📦 Download all panels (zip)",
            data=buf.getvalue(),
            file_name=safe_filename("operator_overview_bundle", ext="zip"),
            mime="application/zip",
            key=key,
        )
    except Exception as exc:
        logger.exception(f"operator_overview: zip bundle button failed: {exc}")
        try:
            st.error("Zip bundle unavailable.")
        except Exception:
            pass


# ── Hero KPI strip ───────────────────────────────────────────────────────────
def _render_hero(
    alerts: list,
    channels: list,
    llm: dict,
    incidents: list,
) -> None:
    """4 KPIs — at-a-glance operator pulse."""
    # Active alerts in the last 24h. ``ShippingAlert.created_at`` is an
    # ISO string; tolerate parse failure by skipping the row.
    cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    n_24h = 0
    for a in alerts:
        ts_raw = getattr(a, "created_at", "") or ""
        try:
            dt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if dt >= cutoff_24h:
                n_24h += 1
        except (TypeError, ValueError):
            continue

    n_enabled = sum(1 for c in channels if getattr(c, "enabled", False))
    n_channels = len(channels)
    spend_7d = float(llm.get("total_cost_usd", 0.0) or 0.0)
    n_incidents = len(incidents)

    metric_card_row([
        {
            "label": "ACTIVE ALERTS",
            "value": f"{n_24h:,}",
            "accent": C_LOW if n_24h > 0 else C_HIGH,
            "sublabel": "last 24h",
        },
        {
            "label": "DELIVERY CHANNELS",
            "value": f"{n_enabled} / {n_channels}",
            "accent": C_HIGH if n_enabled > 0 else C_TEXT3,
            "sublabel": "enabled / total",
        },
        {
            "label": "LLM SPEND",
            "value": _fmt_usd(spend_7d),
            "accent": (
                C_HIGH if spend_7d < 5.0
                else (C_MOD if spend_7d < 25.0 else C_LOW)
            ),
            "sublabel": "last 7d · est.",
        },
        {
            "label": "INCIDENTS",
            "value": f"{n_incidents:,}",
            "accent": C_LOW if n_incidents > 0 else C_HIGH,
            "sublabel": "last 7d",
        },
    ], columns=4)


# ── Alerts breakdown ─────────────────────────────────────────────────────────
def _render_alerts_panel(alerts: list) -> None:
    """Severity counts + top 5 most-recent alerts + a CSV download button.

    The CSV bundles BOTH sub-tables (severity breakdown AND top-5 recent)
    into a single download — operators have asked for "the alerts panel"
    as one artefact, not two separate clicks.
    """
    if not alerts:
        st.info("No alerts in the last 30 days.")
        return

    sev_rows = _build_alerts_breakdown_rows(alerts)
    recent_rows = _build_alerts_recent_rows(alerts)

    col_l, col_r = st.columns([1, 2], gap="medium")
    with col_l:
        st.markdown("**By severity**")
        st.dataframe(
            pd.DataFrame(sev_rows),
            use_container_width=True,
            hide_index=True,
        )
    with col_r:
        st.markdown("**Top 5 most recent**")
        st.dataframe(
            pd.DataFrame(recent_rows),
            use_container_width=True,
            hide_index=True,
        )

    # Per-panel download — uses the severity breakdown as the canonical
    # CSV. The recent-5 table is operator-visible but secondary.
    _render_csv_download_button(
        sev_rows,
        base_name="alerts_breakdown",
        key="op_overview_dl_alerts",
        columns=["Severity", "Count"],
    )


# ── Channel health ───────────────────────────────────────────────────────────
def _render_channels_panel(channels: list) -> None:
    """Table of every configured channel with its enabled state + kind."""
    if not channels:
        st.info("No delivery channels configured.")
        return
    rows = _build_channels_rows(channels)
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    _render_csv_download_button(
        rows,
        base_name="channel_health",
        key="op_overview_dl_channels",
        columns=["Name", "Kind", "Severity floor", "Status"],
    )


# ── LLM spend panel ──────────────────────────────────────────────────────────
def _render_llm_panel(llm: dict) -> None:
    """7d total + top 3 most-expensive call sites (by ``source``)."""
    if not llm or int(llm.get("total_calls", 0) or 0) == 0:
        st.info("No LLM activity in the last 7 days.")
        return

    total_cost = float(llm.get("total_cost_usd", 0.0) or 0.0)
    total_calls = int(llm.get("total_calls", 0) or 0)
    st.markdown(
        f"**Total:** {_fmt_usd(total_cost)} across {total_calls:,} calls (7d)."
    )

    rows = _build_llm_rows(llm)
    if not rows:
        st.caption("No per-source breakdown available.")
        return

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    _render_csv_download_button(
        rows,
        base_name="llm_spend",
        key="op_overview_dl_llm",
        columns=["Source", "Calls", "Cost"],
    )


# ── Tab perf panel ───────────────────────────────────────────────────────────
def _render_perf_panel(perf: dict) -> None:
    """Top 5 slowest tabs by p95 render time (24h window)."""
    if not perf or int(perf.get("total_renders", 0) or 0) == 0:
        st.info("No render telemetry in the last 24 hours.")
        return

    rows = _build_perf_rows(perf)
    if not rows:
        st.info("No per-tab render data.")
        return

    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    _render_csv_download_button(
        rows,
        base_name="tab_perf",
        key="op_overview_dl_perf",
        columns=["Tab", "Renders", "Median", "P95", "Errors"],
    )


# ── Source health panel ──────────────────────────────────────────────────────
def _render_source_panel(health: dict) -> None:
    """Per-source status table (green/yellow/red mapped from last_status).

    The on-screen table is rendered as HTML (so the coloured status pill
    survives), but the CSV uses the plain uppercase status string — see
    ``_build_source_rows``.
    """
    by_source = (health or {}).get("by_source", {}) or {}
    if not by_source:
        st.info("No source-health pings in the last 24 hours.")
        return

    # Build the HTML-rendered rows from the plain CSV rows so the two
    # views stay in sync (same source list, same ordering).
    plain_rows = _build_source_rows(health)
    pill_rows: list[dict] = []
    for r in plain_rows:
        status_text = str(r.get("Status", "") or "")
        color = _status_color(status_text)
        pill = (
            f'<span style="display:inline-block;padding:2px 8px;'
            f'border-radius:3px;background:rgba(0,0,0,0.18);'
            f'color:{color};font-weight:600;font-family:var(--sans);'
            f'font-size:0.78rem;">{status_text or "—"}</span>'
        )
        pill_rows.append({
            "Source": r["Source"],
            "Status": pill,
            "Pings": r["Pings"],
            "Avg latency": r["Avg latency"],
            "Last ping": r["Last ping"],
        })

    # Render as HTML so the coloured pill survives — st.dataframe escapes
    # html, so we use markdown for this one table.
    df = pd.DataFrame(pill_rows)
    st.markdown(
        df.to_html(escape=False, index=False, classes="op-overview-tbl"),
        unsafe_allow_html=True,
    )

    outages = (health or {}).get("current_outages", []) or []
    if outages:
        st.warning(f"Current outages: {', '.join(outages)}")

    _render_csv_download_button(
        plain_rows,
        base_name="source_health",
        key="op_overview_dl_source",
        columns=["Source", "Status", "Pings", "Avg latency", "Last ping"],
    )


# ── Incidents panel ──────────────────────────────────────────────────────────
def _render_incidents_panel(incidents: list) -> None:
    """Last 10 incidents from ``get_recent_incidents``."""
    if not incidents:
        st.info("No incidents in the last 7 days.")
        return
    rows = _build_incidents_rows(incidents)
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    _render_csv_download_button(
        rows,
        base_name="recent_incidents",
        key="op_overview_dl_incidents",
        columns=["Started", "Max severity", "Dominant type", "Alerts"],
    )


# ── Audit events panel ───────────────────────────────────────────────────────
def _render_audit_panel(events: list) -> None:
    """Last 20 audit events from ``auth.audit.query_audit``."""
    if not events:
        st.info("No audit events recorded.")
        return
    rows = _build_audit_rows(events)
    st.dataframe(
        pd.DataFrame(rows),
        use_container_width=True,
        hide_index=True,
    )
    _render_csv_download_button(
        rows,
        base_name="recent_audit_events",
        key="op_overview_dl_audit",
        columns=["When", "User", "Action", "Entity", "ID"],
    )


# ── Main entry point ─────────────────────────────────────────────────────────
def render(*args, **kwargs) -> None:
    """Render the Operator Overview — at-a-glance status across every layer.

    Accepts ``*args, **kwargs`` so the smoke harness in
    ``tests/test_tab_smoke.py`` can pass the populated bundle as kwargs
    even though this tab reads everything from the telemetry engines and
    needs no caller-supplied data.
    """
    # Wrap the WHOLE render in a perf-tracker so this tab shows up in the
    # tab_perf summary we ourselves render below. If the tracker import
    # fails we still render — the import is wrapped in try.
    try:
        from engine.perf_telemetry import track_render
        ctx = track_render("operator_overview")
    except Exception:
        # Empty no-op context manager so the `with` below still works.
        from contextlib import nullcontext
        ctx = nullcontext()

    with ctx:
        try:
            page_header(
                title="Operator Overview",
                subtitle="System status across every telemetry layer.",
                badge_text="OPS",
                badge_color=C_ACCENT,
            )
        except Exception as exc:
            logger.exception(f"operator_overview: page header failed: {exc}")

        # Load every upstream payload up-front so panels share one snapshot
        # and a single section failure can't poison the others. Each loader
        # has its own try/except.
        alerts: list = []
        channels: list = []
        incidents: list = []
        llm: dict = {}
        perf: dict = {}
        health: dict = {}
        events: list = []

        try:
            alerts = _load_alerts()
        except Exception as exc:
            logger.exception(f"operator_overview: load_alerts failed: {exc}")
        try:
            channels = _load_channels()
        except Exception as exc:
            logger.exception(f"operator_overview: load_channels failed: {exc}")
        try:
            incidents = _load_incidents(window_days=7)
        except Exception as exc:
            logger.exception(f"operator_overview: load_incidents failed: {exc}")
        try:
            llm = _load_llm_summary(days=7)
        except Exception as exc:
            logger.exception(f"operator_overview: load_llm_summary failed: {exc}")
        try:
            perf = _load_perf_summary(window_hours=24)
        except Exception as exc:
            logger.exception(f"operator_overview: load_perf failed: {exc}")
        try:
            health = _load_source_health(window_hours=24)
        except Exception as exc:
            logger.exception(f"operator_overview: load_source_health failed: {exc}")
        try:
            events = _load_audit_events(limit=20)
        except Exception as exc:
            logger.exception(f"operator_overview: load_audit_events failed: {exc}")

        # ── Bundle download (top-of-page convenience) ────────────────────
        # Built BEFORE the panels render so the button is at the top of the
        # tab. Uses the row-builders so the zip contents match what the
        # operator sees on screen.
        try:
            _render_bundle_zip_button(
                [
                    ("alerts_breakdown",     _build_alerts_breakdown_rows(alerts)),
                    ("channel_health",       _build_channels_rows(channels)),
                    ("llm_spend",            _build_llm_rows(llm)),
                    ("tab_perf",             _build_perf_rows(perf)),
                    ("source_health",        _build_source_rows(health)),
                    ("recent_incidents",     _build_incidents_rows(incidents)),
                    ("recent_audit_events",  _build_audit_rows(events)),
                ]
            )
        except Exception as exc:
            logger.exception(f"operator_overview: bundle button failed: {exc}")

        # ── Hero ─────────────────────────────────────────────────────────
        try:
            _render_hero(alerts, channels, llm, incidents)
        except Exception as exc:
            logger.exception(f"operator_overview: hero render failed: {exc}")
            st.warning("Hero KPIs unavailable.")

        # ── Alerts breakdown ────────────────────────────────────────────
        try:
            section_divider("Alerts")
            _render_alerts_panel(alerts)
        except Exception as exc:
            logger.exception(f"operator_overview: alerts panel failed: {exc}")
            st.warning("Alerts panel unavailable.")

        # ── Channel health ───────────────────────────────────────────────
        try:
            section_divider("Channels")
            _render_channels_panel(channels)
        except Exception as exc:
            logger.exception(f"operator_overview: channels panel failed: {exc}")
            st.warning("Channels panel unavailable.")

        # ── LLM spend ───────────────────────────────────────────────────
        try:
            section_divider("LLM spend (7d)")
            _render_llm_panel(llm)
        except Exception as exc:
            logger.exception(f"operator_overview: llm panel failed: {exc}")
            st.warning("LLM spend panel unavailable.")

        # ── Tab perf ────────────────────────────────────────────────────
        try:
            section_divider("Slowest tabs (24h)")
            _render_perf_panel(perf)
        except Exception as exc:
            logger.exception(f"operator_overview: perf panel failed: {exc}")
            st.warning("Tab performance panel unavailable.")

        # ── Source health ──────────────────────────────────────────────
        try:
            section_divider("Source health (24h)")
            _render_source_panel(health)
        except Exception as exc:
            logger.exception(f"operator_overview: source panel failed: {exc}")
            st.warning("Source health panel unavailable.")

        # ── Incidents ──────────────────────────────────────────────────
        try:
            section_divider("Recent incidents (7d)")
            _render_incidents_panel(incidents)
        except Exception as exc:
            logger.exception(f"operator_overview: incidents panel failed: {exc}")
            st.warning("Incidents panel unavailable.")

        # ── Audit events ───────────────────────────────────────────────
        try:
            section_divider("Recent audit events")
            _render_audit_panel(events)
        except Exception as exc:
            logger.exception(f"operator_overview: audit panel failed: {exc}")
            st.warning("Audit events panel unavailable.")

        # Cross-link to deeper drill-downs — keeps this tab a single-screen
        # surface and reminds operators where to go for the full view.
        st.caption(
            "For deeper drill-downs see the **Alerts**, **Data Health**, "
            "and **Operator** tabs."
        )

        # ── Source footer ─────────────────────────────────────────────────
        try:
            st.markdown(
                source_footer([_OVERVIEW_SOURCE]),
                unsafe_allow_html=True,
            )
        except Exception as exc:
            logger.exception(f"operator_overview: source footer failed: {exc}")
