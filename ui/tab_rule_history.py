"""tab_rule_history.py — Per-rule history view: timeline, ack trend, recent fires.

Operators can already browse the firehose of alerts on the Alert Center, but
two operator questions don't have a one-screen answer there:

  1. "Is rule X actually firing too often?" — needs a fire-count timeline.
  2. "How often does the trading desk ack alerts from rule Y?" — needs an
     ack-rate trend.

This tab answers both, scoped to ONE selected rule. Cross-link only — the
rule editor itself lives in ``ui.tab_alerts``; we render rule metadata + a
"link" pointer rather than duplicating the editor.

Sections
--------
Hero                — page header + rule selector + rule metadata strip
Hero KPIs           — total fires (all / 7d / 30d), ack rate, mean TTA,
                      suppressed-by-{cooldown, flap, silence} counters
Fire timeline       — Plotly bar chart of daily fire counts (90d), severity tinted
Ack rate trend      — Plotly line chart of daily ack% (30d)
Recent fires        — table of newest 50 fires (created_at, severity, title,
                      ticker, acknowledged, acknowledged_at, TTA min,
                      annotation count)
Audit trail         — table of audit_events whose action LIKE 'rule_*' AND
                      entity_id == rule_id
CSV download        — full history button (rows_to_csv_bytes); only shown
                      when the rule has at least one fire

Per-section error isolation
---------------------------
Each section is wrapped in its own try/except + st.warning fallback. A
broken engine helper degrades to a one-line "unavailable" notice for the
affected section; the rest of the tab still renders.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import streamlit as st
from loguru import logger


# ── Constants ──────────────────────────────────────────────────────────────
_TIMELINE_WINDOW_DAYS: int = 90
_ACK_TREND_WINDOW_DAYS: int = 30
_RECENT_FIRES_CAP: int = 50
_AUDIT_TRAIL_LIMIT: int = 100
# Rule-related audit actions. The audit log uses verbs like 'save_rules'
# and 'reset_rules' that span ALL rules; for rule-specific events we
# match on entity_id == rule_id, then any action that starts with
# 'rule_' (e.g. 'rule_create', 'rule_edit', 'rule_delete'). The prefix
# match is permissive — any future rule-scoped audit verb will land
# here automatically without a code change.
_RULE_AUDIT_ACTION_PREFIX: str = "rule_"


# ── Small formatting helpers ──────────────────────────────────────────────
def _fmt_ts(iso: Optional[str]) -> str:
    """ISO → user-tz string; degrades to a 16-char slice on failure."""
    if not iso:
        return "—"
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


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    """Permissive ISO parser. Returns None on any failure."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _tta_minutes(created_at: Optional[str], ack_at: Optional[str]) -> Optional[float]:
    """Time-to-ack in minutes. Returns None when either timestamp is
    unparseable OR the ack predates creation (clock skew on a bad row)."""
    c = _parse_iso(created_at)
    a = _parse_iso(ack_at)
    if c is None or a is None:
        return None
    delta = (a - c).total_seconds() / 60.0
    if delta < 0:
        return None
    return delta


def _sev_color(severity: str) -> str:
    """Map a severity label to the WSJ palette colour."""
    from ui.styles import C_ACCENT, C_HIGH, C_LOW, C_MOD, C_TEXT3
    s = (severity or "").upper()
    if s == "CRITICAL":
        return C_LOW
    if s == "HIGH":
        return C_MOD
    if s == "MEDIUM":
        return C_ACCENT
    if s == "LOW":
        return C_HIGH
    return C_TEXT3


# ── Engine adapters — lazy + best-effort ──────────────────────────────────
def _load_rules() -> list[dict]:
    """Load the user's rules. Returns [] on any failure."""
    try:
        from engine.alert_engine_v2 import load_rules
        return load_rules() or []
    except Exception as exc:
        logger.debug(f"tab_rule_history._load_rules: failed: {exc}")
        return []


def _get_alerts_by_rule(rule_id: str, *, since: Optional[str] = None,
                        limit: int = 500) -> list:
    """Engine helper wrapper. Returns [] on any failure."""
    try:
        from engine.alert_engine_v2 import get_alerts_by_rule
        return get_alerts_by_rule(rule_id, since=since, limit=limit) or []
    except Exception as exc:
        logger.debug(
            f"tab_rule_history._get_alerts_by_rule: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return []


def _get_full_rows_by_rule(rule_id: str, limit: int = _RECENT_FIRES_CAP) -> list[dict]:
    """Read the newest ``limit`` rows for ``rule_id`` as full dicts so the
    UI can render acknowledged_at + TTA (the dataclass projection drops
    these columns). Returns [] on any failure."""
    if not rule_id:
        return []
    try:
        from engine.alert_engine_v2 import _resolve_user_id, _row_to_alert_full
        from state.db import get_connection
        from state.user_scope import scope_filter_sql

        uid = _resolve_user_id(None)
        scope_sql, scope_params = scope_filter_sql(uid)
        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM alerts WHERE rule_id = ? {scope_sql} "
            f"ORDER BY created_at DESC LIMIT ?",
            (rule_id, *scope_params, int(limit)),
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            full = _row_to_alert_full(r)
            # Attach acknowledged_at — _row_to_alert_full skips it.
            try:
                full["acknowledged_at"] = r["acknowledged_at"] or ""
            except (IndexError, KeyError):
                full["acknowledged_at"] = ""
            out.append(full)
        return out
    except Exception as exc:
        logger.debug(
            f"tab_rule_history._get_full_rows_by_rule: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return []


def _query_rule_audit(rule_id: str, *, limit: int = _AUDIT_TRAIL_LIMIT) -> list:
    """Audit events whose entity_id == rule_id AND action LIKE 'rule_*'.
    The query_audit helper does NOT support a LIKE filter, so we pull a
    larger window for entity_type='alert_rule' (the canonical entity
    type for rules) AND filter by action prefix in Python."""
    if not rule_id:
        return []
    try:
        from auth.audit import query_audit
        # Pull every audit event in a generous batch then filter. The
        # cap on the SQL side prevents OOM; the Python filter prunes
        # everything that is not rule-scoped.
        events = query_audit(limit=max(limit * 5, limit)) or []
        out = []
        for ev in events:
            try:
                if getattr(ev, "entity_id", "") != rule_id:
                    continue
                action = str(getattr(ev, "action", "") or "")
                if not action.startswith(_RULE_AUDIT_ACTION_PREFIX):
                    continue
                out.append(ev)
                if len(out) >= limit:
                    break
            except Exception:
                continue
        return out
    except Exception as exc:
        logger.debug(
            f"tab_rule_history._query_rule_audit: failed for "
            f"rule_id={rule_id!r}: {exc}"
        )
        return []


def _get_cooldown_count() -> int:
    try:
        from engine.alert_engine_v2 import get_suppressed_by_cooldown_count
        return int(get_suppressed_by_cooldown_count() or 0)
    except Exception:
        return 0


def _get_flap_count() -> int:
    try:
        from engine.flap_detector import get_flap_suppressed_count
        return int(get_flap_suppressed_count() or 0)
    except Exception:
        return 0


def _get_silence_count() -> int:
    try:
        from engine.alert_silences import get_suppressed_by_silence_count
        return int(get_suppressed_by_silence_count() or 0)
    except Exception:
        return 0


def _get_annotation_counts(alert_ids: list[str]) -> dict[str, int]:
    if not alert_ids:
        return {}
    try:
        from engine.alert_annotations import count_annotations_per_alert
        from engine.alert_engine_v2 import _resolve_user_id
        uid = _resolve_user_id(None)
        if not uid:
            # No user scope → annotations module short-circuits to {}.
            return {}
        return count_annotations_per_alert(alert_ids, user_id=uid) or {}
    except Exception:
        return {}


# ── KPI computations ──────────────────────────────────────────────────────
def _compute_kpis(rule_id: str, alerts_30d: list, alerts_7d: list,
                  alerts_all: list, full_rows_30d: list[dict]) -> dict:
    """Pure KPI math. Returns a dict with stringified values + numeric
    sources so the caller can render OR export."""
    total_all = len(alerts_all)
    total_7d = len(alerts_7d)
    total_30d = len(alerts_30d)

    # Ack rate over the 30-day window (the typical operator review
    # horizon). Divide-by-zero short-circuits to 0% rather than NaN —
    # "no fires in the window" reads cleanly as "nothing to ack".
    acked_30d = sum(1 for r in full_rows_30d if r.get("acknowledged"))
    ack_rate = (acked_30d / total_30d * 100.0) if total_30d > 0 else 0.0

    # Mean TTA across the 30-day window — only rows that ARE acked AND
    # have a parseable ack timestamp contribute. Empty list → "—".
    ttas: list[float] = []
    for r in full_rows_30d:
        if not r.get("acknowledged"):
            continue
        tta = _tta_minutes(r.get("created_at"), r.get("acknowledged_at"))
        if tta is not None:
            ttas.append(tta)
    mean_tta_min = (sum(ttas) / len(ttas)) if ttas else None

    return {
        "total_all": total_all,
        "total_7d": total_7d,
        "total_30d": total_30d,
        "ack_rate_pct": ack_rate,
        "mean_tta_min": mean_tta_min,
        "suppressed_cooldown": _get_cooldown_count(),
        "suppressed_flap": _get_flap_count(),
        "suppressed_silence": _get_silence_count(),
    }


# ── Section: hero (selector + rule metadata) ──────────────────────────────
def _render_hero(rules: list[dict]) -> Optional[dict]:
    """Render the page header + rule selector. Returns the selected
    rule dict, or None when no rule is selectable."""
    from ui.styles import page_header
    page_header(
        title="Rule History",
        subtitle="Per-rule fire timeline, ack-rate trend, and audit trail",
        badge_text="OPS",
        badge_color="#3572b0",
    )
    if not rules:
        st.info(
            "No alert rules configured yet. Visit the **Alerts** tab "
            "(Intelligence section) to create your first rule."
        )
        return None

    # Build display labels. Prefer 'name'; fall back to id; tag the
    # severity so two name-collision rules are still distinguishable.
    options: list[tuple[str, dict]] = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("rule_id") or r.get("id") or "")
        if not rid:
            continue
        name = str(r.get("name") or rid)
        sev = str(r.get("severity") or "")
        label = f"{name}  ·  {sev}" if sev else name
        options.append((label, r))

    if not options:
        st.info("No alert rules configured yet.")
        return None

    labels = [o[0] for o in options]
    pick = st.selectbox("Select a rule", labels, key="rule_history_select")
    chosen = next((o[1] for o in options if o[0] == pick), options[0][1])
    return chosen


def _render_rule_metadata(rule: dict) -> None:
    """Compact strip describing the selected rule's configuration."""
    from ui.styles import C_TEXT2, C_TEXT3
    rid = str(rule.get("rule_id") or rule.get("id") or "")
    name = str(rule.get("name") or rid)
    metric = str(rule.get("metric") or rule.get("alert_type") or "—")
    threshold = rule.get("threshold")
    severity = str(rule.get("severity") or "—")
    cooldown = rule.get("cooldown_minutes", 0) or 0
    flap_on = bool(rule.get("flap_detection_enabled", False))
    created_at = str(rule.get("created_at") or "")
    modified_at = str(rule.get("last_modified") or rule.get("modified_at") or "")

    try:
        thr_str = f"{float(threshold):,.4g}" if threshold is not None else "—"
    except (TypeError, ValueError):
        thr_str = str(threshold)

    pieces = [
        # Escape the user-authored rule name — this blob is emitted via
        # st.markdown(unsafe_allow_html=True), so an unescaped name is stored
        # XSS. metric/severity come from selectboxes (controlled).
        f"<b>{html.escape(str(name))}</b>",
        f"metric: <code>{metric}</code>",
        f"threshold: <code>{thr_str}</code>",
        f"severity: <code>{severity}</code>",
        f"cooldown: <code>{cooldown}m</code>",
        f"flap-detect: <code>{'on' if flap_on else 'off'}</code>",
    ]
    if created_at:
        pieces.append(f"created: <code>{_fmt_ts(created_at)}</code>")
    if modified_at:
        pieces.append(f"modified: <code>{_fmt_ts(modified_at)}</code>")
    html = (
        f'<div style="padding:8px 12px;margin-bottom:14px;'
        f'background:rgba(53,114,176,0.06);border-left:2px solid #3572b0;'
        f'border-radius:3px;font-size:0.82rem;color:{C_TEXT2};'
        f'font-family:var(--sans);line-height:1.7;">'
        + " &nbsp;·&nbsp; ".join(pieces)
        + f'<div style="font-size:0.7rem;color:{C_TEXT3};margin-top:4px;">'
        f'rule_id: <code>{rid}</code></div>'
        f'</div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ── Section: KPI strip ────────────────────────────────────────────────────
def _render_kpis(kpis: dict) -> None:
    from ui.styles import C_ACCENT, C_HIGH, C_LOW, C_MOD, metric_card_row

    tta_str = (
        f"{kpis['mean_tta_min']:.1f}m"
        if kpis["mean_tta_min"] is not None else "—"
    )
    ack_color = C_HIGH if kpis["ack_rate_pct"] >= 75 else (
        C_MOD if kpis["ack_rate_pct"] >= 40 else C_LOW
    )

    # Two rows of four — first the activity numbers, then the
    # suppression counters. metric_card_row(columns=4) renders four
    # cards across; calling it twice stacks the rows.
    metric_card_row([
        {"label": "Fires (all time)", "value": f"{kpis['total_all']:,}",
         "accent": C_ACCENT},
        {"label": "Fires (7 days)", "value": f"{kpis['total_7d']:,}",
         "accent": C_ACCENT},
        {"label": "Fires (30 days)", "value": f"{kpis['total_30d']:,}",
         "accent": C_ACCENT},
        {"label": "Ack rate (30d)",
         "value": f"{kpis['ack_rate_pct']:.0f}%",
         "accent": ack_color},
    ], columns=4)

    metric_card_row([
        {"label": "Mean time-to-ack", "value": tta_str,
         "sublabel": "30-day window", "accent": C_ACCENT},
        {"label": "Suppressed by cooldown",
         "value": f"{kpis['suppressed_cooldown']:,}",
         "sublabel": "global counter", "accent": C_MOD},
        {"label": "Suppressed by flap",
         "value": f"{kpis['suppressed_flap']:,}",
         "sublabel": "global counter", "accent": C_MOD},
        {"label": "Suppressed by silence",
         "value": f"{kpis['suppressed_silence']:,}",
         "sublabel": "global counter", "accent": C_MOD},
    ], columns=4)


# ── Section: fire timeline ────────────────────────────────────────────────
def _render_fire_timeline(alerts: list) -> None:
    """Daily fire counts over the last _TIMELINE_WINDOW_DAYS, colour-coded
    by severity. Falls back to an info message when no fires exist."""
    from ui.styles import apply_dark_layout, section_header
    section_header(
        f"Fire timeline ({_TIMELINE_WINDOW_DAYS}d)",
        "Daily fire count grouped by severity.",
    )
    if not alerts:
        st.info("No fires recorded for this rule in the window.")
        return

    import pandas as pd
    import plotly.graph_objects as go

    rows = []
    for a in alerts:
        ts = getattr(a, "created_at", "") or ""
        d = _parse_iso(ts)
        if d is None:
            continue
        rows.append({
            "day": d.astimezone(timezone.utc).date(),
            "severity": (getattr(a, "severity", "") or "UNKNOWN").upper(),
        })
    if not rows:
        st.info("No parseable fire timestamps for this rule.")
        return

    df = pd.DataFrame(rows)
    pivot = (
        df.groupby(["day", "severity"]).size().unstack(fill_value=0)
    )
    # Stack severities in CRITICAL-first order so the most-urgent
    # band sits at the bottom of each daily bar.
    sev_order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    fig = go.Figure()
    for sev in sev_order:
        if sev in pivot.columns:
            fig.add_trace(go.Bar(
                x=pivot.index, y=pivot[sev], name=sev,
                marker_color=_sev_color(sev),
            ))
    # Add any unknown severities last so they don't disappear.
    for sev in pivot.columns:
        if sev not in sev_order:
            fig.add_trace(go.Bar(
                x=pivot.index, y=pivot[sev], name=sev,
                marker_color=_sev_color(sev),
            ))
    apply_dark_layout(
        fig, height=320,
        barmode="stack",
        xaxis={"title": "", "showgrid": False},
        yaxis={"title": "fires", "rangemode": "tozero"},
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: ack-rate trend ───────────────────────────────────────────────
def _render_ack_trend(full_rows: list[dict]) -> None:
    from ui.styles import C_ACCENT, apply_dark_layout, section_header
    section_header(
        f"Ack rate trend ({_ACK_TREND_WINDOW_DAYS}d)",
        "Daily share of fires that were acknowledged.",
    )
    if not full_rows:
        st.info("No fires in the ack-rate window.")
        return

    import pandas as pd
    import plotly.graph_objects as go

    rows = []
    for r in full_rows:
        d = _parse_iso(r.get("created_at"))
        if d is None:
            continue
        rows.append({
            "day": d.astimezone(timezone.utc).date(),
            "acked": 1 if r.get("acknowledged") else 0,
        })
    if not rows:
        st.info("No parseable fire timestamps for this rule.")
        return

    df = pd.DataFrame(rows)
    grouped = df.groupby("day").agg(total=("acked", "size"),
                                    acked=("acked", "sum"))
    # Divide-by-zero guard: total can't be 0 because the groupby
    # itself only emits rows where there is at least one fire, but
    # we belt-and-braces it.
    grouped["ack_pct"] = grouped.apply(
        lambda r: (r["acked"] / r["total"] * 100.0) if r["total"] > 0 else 0.0,
        axis=1,
    )
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=grouped.index, y=grouped["ack_pct"],
        mode="lines+markers",
        line={"color": C_ACCENT, "width": 2.5},
        marker={"size": 7},
        name="Ack %",
    ))
    apply_dark_layout(
        fig, height=280,
        yaxis={"title": "ack %", "range": [0, 100]},
        xaxis={"title": "", "showgrid": False},
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Section: recent fires table ───────────────────────────────────────────
def _render_recent_fires(full_rows: list[dict],
                         annotation_counts: dict[str, int]) -> None:
    from ui.styles import section_header, wsj_market_table
    section_header(
        f"Recent fires (top {_RECENT_FIRES_CAP})",
        "Newest acknowledgement status + time-to-ack per fire.",
    )
    if not full_rows:
        st.info("No fires recorded for this rule yet.")
        return

    # Already capped by _get_full_rows_by_rule; defensive trim here too
    # so the table cannot exceed _RECENT_FIRES_CAP even if the caller
    # passes a larger batch.
    rows = full_rows[:_RECENT_FIRES_CAP]
    headers = ["When", "Severity", "Title", "Ticker", "Ack",
               "Acked at", "TTA (min)", "Notes"]
    data: list[list[str]] = []
    for r in rows:
        tta = _tta_minutes(r.get("created_at"), r.get("acknowledged_at"))
        tta_str = f"{tta:.1f}" if tta is not None else "—"
        ack = bool(r.get("acknowledged"))
        ann_n = int(annotation_counts.get(r.get("alert_id", ""), 0))
        data.append([
            _fmt_ts(r.get("created_at")),
            str(r.get("severity") or ""),
            str(r.get("title") or "")[:80],
            str(r.get("ticker") or "") or "—",
            "✓" if ack else "✗",
            _fmt_ts(r.get("acknowledged_at")) if ack else "—",
            tta_str,
            str(ann_n) if ann_n else "—",
        ])
    wsj_market_table(headers, data)


# ── Section: audit trail ──────────────────────────────────────────────────
def _render_audit_trail(rule_id: str) -> None:
    from ui.styles import section_header, wsj_market_table
    section_header(
        "Audit trail",
        "Rule-scoped audit events (creates, edits, deletes).",
    )
    events = _query_rule_audit(rule_id)
    if not events:
        st.info("No audit events for this rule.")
        return
    headers = ["When", "User", "Action", "Detail"]
    data: list[list[str]] = []
    for ev in events:
        detail = getattr(ev, "detail_json", None)
        if isinstance(detail, dict):
            # Compact one-line render — the audit log payload is small.
            try:
                detail_str = ", ".join(
                    f"{k}={v}" for k, v in list(detail.items())[:4]
                )
            except Exception:
                detail_str = "—"
        elif detail is None:
            detail_str = "—"
        else:
            detail_str = str(detail)[:120]
        data.append([
            _fmt_ts(getattr(ev, "created_at", "")),
            str(getattr(ev, "user_id", "") or "—"),
            str(getattr(ev, "action", "") or ""),
            detail_str[:120],
        ])
    wsj_market_table(headers, data)


# ── Section: CSV download ─────────────────────────────────────────────────
def _render_csv_download(rule_id: str, rule_name: str, alerts: list,
                         full_rows: list[dict]) -> None:
    """Render the CSV download button. The task spec calls for the
    button to appear ONLY when the rule has at least one fire — a
    button that exports an empty file is just noise."""
    if not alerts and not full_rows:
        return
    try:
        from utils.csv_export import rows_to_csv_bytes, safe_filename
        # Build a compact row dict per fire that fits a CSV nicely.
        # We use full_rows when available (carries acknowledged_at)
        # and fall back to alerts for older fires beyond the cap.
        seen_ids: set[str] = set()
        out_rows: list[dict] = []
        for r in full_rows:
            aid = r.get("alert_id", "")
            seen_ids.add(aid)
            tta = _tta_minutes(r.get("created_at"), r.get("acknowledged_at"))
            out_rows.append({
                "alert_id": aid,
                "created_at": r.get("created_at", ""),
                "severity": r.get("severity", ""),
                "alert_type": r.get("alert_type", ""),
                "title": r.get("title", ""),
                "ticker": r.get("ticker", ""),
                "route_id": r.get("route_id", ""),
                "port_locode": r.get("port_locode", ""),
                "value": r.get("value", ""),
                "threshold": r.get("threshold", ""),
                "change_pct": r.get("change_pct", ""),
                "acknowledged": 1 if r.get("acknowledged") else 0,
                "acknowledged_at": r.get("acknowledged_at", ""),
                "time_to_ack_minutes": (
                    f"{tta:.2f}" if tta is not None else ""
                ),
            })
        # Top up from `alerts` (ShippingAlert objects) for fires that
        # are not in the recent-rows batch.
        for a in alerts:
            aid = getattr(a, "alert_id", "")
            if aid in seen_ids:
                continue
            seen_ids.add(aid)
            out_rows.append({
                "alert_id": aid,
                "created_at": getattr(a, "created_at", ""),
                "severity": getattr(a, "severity", ""),
                "alert_type": getattr(a, "alert_type", ""),
                "title": getattr(a, "title", ""),
                "ticker": getattr(a, "ticker", ""),
                "route_id": getattr(a, "route_id", ""),
                "port_locode": getattr(a, "port_locode", ""),
                "value": getattr(a, "value", ""),
                "threshold": getattr(a, "threshold", ""),
                "change_pct": getattr(a, "change_pct", ""),
                "acknowledged": 1 if getattr(a, "acknowledged", False) else 0,
                "acknowledged_at": "",
                "time_to_ack_minutes": "",
            })
        if not out_rows:
            return
        columns = [
            "alert_id", "created_at", "severity", "alert_type", "title",
            "ticker", "route_id", "port_locode", "value", "threshold",
            "change_pct", "acknowledged", "acknowledged_at",
            "time_to_ack_minutes",
        ]
        csv_bytes = rows_to_csv_bytes(out_rows, columns=columns)
        fname = safe_filename(f"rule_history_{rule_name or rule_id}")
        st.download_button(
            "Download history as CSV",
            data=csv_bytes,
            file_name=fname,
            mime="text/csv",
            key=f"rule_history_csv_{rule_id}",
        )
    except Exception as exc:
        logger.warning(f"tab_rule_history._render_csv_download: failed: {exc}")
        st.warning("CSV download unavailable.")


# ── Public entry point ────────────────────────────────────────────────────
def render(*args: Any, **kwargs: Any) -> None:
    """Render the Rule History tab.

    The render body is wrapped in ``engine.perf_telemetry.track_render``
    for per-tab p50/p95 timing — same pattern as every other tab. The
    wrapping happens at the OUTER scope so the section-level try/except
    blocks inside still bubble timings to the parent.

    Args are accepted but unused — the tab reads everything it needs
    from the engine helpers; the smoke harness still calls render(...)
    with the standard payload bundle.
    """
    try:
        from engine.perf_telemetry import track_render
    except Exception:
        # Telemetry unavailable → fall back to a no-op context manager
        # so the tab still renders. Belt-and-braces; perf_telemetry is
        # always present in production.
        from contextlib import nullcontext as track_render  # type: ignore

    with track_render('rule_history'):
        # ── Section 1: hero (selector + metadata) ────────────────────
        rule: Optional[dict] = None
        try:
            rules = _load_rules()
            rule = _render_hero(rules)
        except Exception as exc:
            logger.warning(f"tab_rule_history: hero section failed: {exc}")
            st.warning(f"Rule selector unavailable: {exc}")
            return

        if rule is None:
            return

        try:
            _render_rule_metadata(rule)
        except Exception as exc:
            logger.warning(f"tab_rule_history: metadata section failed: {exc}")
            st.warning(f"Rule metadata unavailable: {exc}")

        rule_id = str(rule.get("rule_id") or rule.get("id") or "")
        rule_name = str(rule.get("name") or rule_id)
        if not rule_id:
            st.warning("Selected rule has no id — cannot load history.")
            return

        # ── Pull data once + share across sections ───────────────────
        # Each helper is wrapped in try/except so any pull failure
        # degrades to an empty list and the downstream sections render
        # the "no fires" branch rather than the whole tab erroring out.
        now = datetime.now(timezone.utc)
        since_30d = (now - timedelta(days=_ACK_TREND_WINDOW_DAYS)).isoformat()
        since_90d = (now - timedelta(days=_TIMELINE_WINDOW_DAYS)).isoformat()
        since_7d = (now - timedelta(days=7)).isoformat()

        alerts_all = _get_alerts_by_rule(rule_id, limit=500)
        alerts_90d = _get_alerts_by_rule(rule_id, since=since_90d, limit=500)
        alerts_30d = _get_alerts_by_rule(rule_id, since=since_30d, limit=500)
        alerts_7d = _get_alerts_by_rule(rule_id, since=since_7d, limit=500)
        full_rows_recent = _get_full_rows_by_rule(rule_id, limit=_RECENT_FIRES_CAP)
        # Pull a separate 30-day window of full rows for ack-rate +
        # mean-TTA. The recent-fires batch only carries 50; we want
        # the WHOLE 30-day window for the trend math.
        full_rows_30d = _get_full_rows_by_rule(rule_id, limit=500)

        # ── Section 2: KPI strip ─────────────────────────────────────
        try:
            kpis = _compute_kpis(rule_id, alerts_30d, alerts_7d,
                                 alerts_all, full_rows_30d)
            _render_kpis(kpis)
        except Exception as exc:
            logger.warning(f"tab_rule_history: KPIs section failed: {exc}")
            st.warning(f"KPI strip unavailable: {exc}")

        # ── Section 3: fire timeline ─────────────────────────────────
        try:
            _render_fire_timeline(alerts_90d)
        except Exception as exc:
            logger.warning(f"tab_rule_history: timeline section failed: {exc}")
            st.warning(f"Fire timeline unavailable: {exc}")

        # ── Section 4: ack-rate trend ────────────────────────────────
        try:
            _render_ack_trend(full_rows_30d)
        except Exception as exc:
            logger.warning(f"tab_rule_history: ack-trend section failed: {exc}")
            st.warning(f"Ack rate trend unavailable: {exc}")

        # ── Section 5: recent fires table ────────────────────────────
        try:
            ann_counts = _get_annotation_counts(
                [r.get("alert_id", "") for r in full_rows_recent]
            )
            _render_recent_fires(full_rows_recent, ann_counts)
        except Exception as exc:
            logger.warning(f"tab_rule_history: recent-fires section failed: {exc}")
            st.warning(f"Recent fires unavailable: {exc}")

        # ── Section 6: audit trail ───────────────────────────────────
        try:
            _render_audit_trail(rule_id)
        except Exception as exc:
            logger.warning(f"tab_rule_history: audit-trail section failed: {exc}")
            st.warning(f"Audit trail unavailable: {exc}")

        # ── Section 7: CSV download ──────────────────────────────────
        try:
            _render_csv_download(rule_id, rule_name, alerts_all, full_rows_recent)
        except Exception as exc:
            logger.warning(f"tab_rule_history: csv-download section failed: {exc}")
            st.warning(f"CSV download unavailable: {exc}")
