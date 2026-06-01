"""engine/weekly_digest.py — automated weekly summary delivery.

Once a week (Monday 14:00 UTC by default, configurable per user) the
worker assembles a digest covering the prior 7 days of alerts, top
routes / tickers, incident counts, source-feed health and per-channel
budget usage, and ships it through every enabled delivery channel of
the user's choosing.

Design
------
* Read-only consumer of ``engine.alert_engine_v2`` (alerts),
  ``engine.alert_correlator`` (incidents), ``engine.source_health``
  (feed status) and ``engine.alert_delivery`` (channels + budget
  usage). No new SQLite tables — everything fans in from existing
  surfaces.
* Per-user config + dispatch lock live in ``kv_state`` under the
  ``weekly_digest_config:<user_id>`` and
  ``weekly_digest_last_dispatched_at:<user_id>`` row-key conventions —
  zero-migration extension that mirrors how every other per-user
  knob (filters / settings) is stored.
* Dispatch fans the digest back through ``alert_delivery.deliver_alert``
  with a synthesized ``ShippingAlert`` (``alert_type='WEEKLY_DIGEST'``,
  severity LOW) so the existing per-channel severity / quiet-hours /
  budget gates all apply — but escalation / cooldown / dedup do NOT
  because the synthetic alert is never persisted via ``save_alerts``.
* Self-gating in ``run_weekly_digest_job``: the worker's main loop calls
  this hourly, the helper itself checks ``day_of_week`` + ``hour_utc``
  + the per-user idempotency lock and silently no-ops when not due.
* NEVER raises at the orchestrator level — a single user's dispatch
  failure can never block any other user's digest.
"""
from __future__ import annotations

import html
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
#  Module constants
# ─────────────────────────────────────────────────────────────────────────────

# Per-user kv_state row keys. Single source of truth so the UI / CLI /
# worker stay in sync. The two suffixes never overlap so a config and a
# lock for the same user are independent rows.
_CONFIG_KEY_PREFIX = "weekly_digest_config:"
_LOCK_KEY_PREFIX = "weekly_digest_last_dispatched_at:"

# The synthetic alert_type / severity used by ``dispatch_digest`` so the
# digest rides ``alert_delivery.deliver_alert`` without colliding with
# any real alert in the engine's dedup / cooldown / escalation tables.
# alert_type is documented in the module docstring above.
_DIGEST_ALERT_TYPE = "WEEKLY_DIGEST"
_DIGEST_ALERT_SEVERITY = "LOW"

# Day-of-week → Python weekday() integer (Monday=0).
_WEEKDAY_LOOKUP = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}

# Channel kinds that can carry the digest payload. SMS gets dropped
# automatically because a 1600-char markdown blob blows past every
# carrier's segment limit; PagerDuty's incident model is wrong for a
# "here's your weekly recap" message.
_COMPATIBLE_CHANNEL_KINDS = {"email", "slack", "webhook", "discord"}

# Look-back window — every renderable summary spans exactly 7 days
# (Mon 00:00 UTC through Sun 23:59:59 UTC). Defined as a constant so
# tests can import it without hard-coding the literal everywhere.
_WEEK_LENGTH_DAYS = 7

# Top-N caps for the renderer-facing aggregates. Kept small enough to
# fit in an email without scrolling but large enough to show genuine
# variety.
_TOP_N = 5


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class WeeklyDigest:
    """Snapshot of one user's last 7 days, ready to render.

    Field shapes:
      * ``user_id`` — owner the digest was computed for ("" for legacy /
        single-user mode).
      * ``week_start`` / ``week_end`` — ISO dates (YYYY-MM-DD) for the
        Monday and Sunday bracketing the digest window.
      * ``summary`` — dict carrying every count + ranked list the
        renderers need. The keys are stable so the markdown / HTML
        renderers don't need conditional shape checks:

            {
              "alerts_total":          int,
              "alerts_by_severity":    {"CRITICAL": int, ...},
              "incidents_total":       int,
              "top_alert_types":       [{"alert_type": str, "count": int}, ...],
              "top_routes":            [{"route_id": str, "count": int}, ...],
              "top_tickers":           [{"ticker": str, "count": int}, ...],
              "source_health":         {"window_hours": int, ...},
              "channel_usage":         [{...}, ...],
              "ack_rate":              float (0.0..1.0),
              "budget_suppressed":     int,  # cumulative counter snapshot
            }

      * ``generated_at`` — ISO timestamp of the build call (NOT the
        window start).
    """
    user_id: str
    week_start: str
    week_end: str
    summary: dict = field(default_factory=dict)
    generated_at: str = ""


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    """Single source of truth for "now" — tests pin via monkeypatch."""
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now_utc().isoformat()


def _monday_of(dt: datetime) -> datetime:
    """Return the Monday 00:00 UTC of the week containing ``dt``."""
    # weekday(): Mon=0, Sun=6 — subtract that many days to land on Monday.
    day = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    monday = day - timedelta(days=day.weekday())
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_iso(s: str) -> Optional[datetime]:
    """Tolerant ISO 8601 parser (accepts trailing Z). Returns None on
    failure so callers can skip without crashing the aggregation.
    """
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _resolve_week_window(week_start: Optional[str]) -> tuple[datetime, datetime]:
    """Return ``(week_start_dt, week_end_dt)`` UTC, both timezone-aware.

    ``week_start=None`` → Monday 00:00 UTC of the CURRENT week.
    Explicit ``week_start`` strings parse to that date's Monday so a
    mid-week date still snaps cleanly. ``week_end`` is always
    week_start + 7 days (exclusive upper bound). Malformed input falls
    back to "this week" rather than raising — callers expect a window.
    """
    base = _now_utc() if week_start is None else _parse_iso(week_start)
    if base is None:
        base = _now_utc()
    start = _monday_of(base)
    end = start + timedelta(days=_WEEK_LENGTH_DAYS)
    return start, end


def _iso_date(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).date().isoformat()


def _row_key_config(user_id: str) -> str:
    return f"{_CONFIG_KEY_PREFIX}{user_id}"


def _row_key_lock(user_id: str) -> str:
    return f"{_LOCK_KEY_PREFIX}{user_id}"


def _default_config() -> dict:
    """The shape returned for a user that has never opted in. All other
    config branches conform to this same shape so the UI / CLI don't
    need to guard against missing keys."""
    return {
        "enabled": False,
        "day_of_week": "monday",
        "hour_utc": 14,
        "channel_ids": [],
    }


def _normalize_config(raw: Any) -> dict:
    """Coerce a kv_state-blob payload into the canonical config dict.

    Bad input (None / non-dict / typo'd day name / negative hour) falls
    back to the corresponding default field so the worker can always
    rely on the shape.
    """
    cfg = _default_config()
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", False))
    dow = str(raw.get("day_of_week", "monday")).strip().lower()
    if dow in _WEEKDAY_LOOKUP:
        cfg["day_of_week"] = dow
    try:
        hour = int(raw.get("hour_utc", 14))
        if 0 <= hour <= 23:
            cfg["hour_utc"] = hour
    except (TypeError, ValueError):
        pass
    chs = raw.get("channel_ids") or []
    if isinstance(chs, list):
        cfg["channel_ids"] = [str(c) for c in chs if c]
    return cfg


def _read_kv(key: str) -> Optional[str]:
    """Best-effort kv_state read. Returns None on any internal failure
    so callers can fall through to defaults without try/except gymnastics.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return row["value"] if hasattr(row, "keys") else row[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._read_kv({key!r}) failed: {exc}")
        return None


def _write_kv(key: str, value: str) -> bool:
    """Best-effort kv_state write. Returns True on success, False
    otherwise. Caller decides whether to surface the failure.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (key, value, _now_iso()),
            )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._write_kv({key!r}) failed: {exc}")
        return False


def _delete_kv(key: str) -> bool:
    """Best-effort kv_state delete. Returns True on success."""
    try:
        from state.db import get_connection

        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM kv_state WHERE key = ?", (key,))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._delete_kv({key!r}) failed: {exc}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
#  Config persistence
# ─────────────────────────────────────────────────────────────────────────────

def get_digest_config(*, user_id: str) -> dict:
    """Return the user's weekly-digest config, defaulting to disabled.

    NEVER raises. Missing row → ``_default_config()``. Malformed blob →
    canonicalized via ``_normalize_config`` (bad fields drop back to
    defaults silently).
    """
    raw = _read_kv(_row_key_config(user_id))
    if raw is None:
        return _default_config()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return _default_config()
    return _normalize_config(parsed)


def save_digest_config(cfg: dict, *, user_id: str) -> bool:
    """Persist the user's config blob. Normalizes the input first so
    even a hand-edited dict can't poison the kv_state row.
    """
    normalized = _normalize_config(cfg)
    try:
        payload = json.dumps(normalized, default=str)
    except (TypeError, ValueError):
        return False
    return _write_kv(_row_key_config(user_id), payload)


def enable_digest(
    *,
    user_id: str,
    channel_ids: list[str],
    day_of_week: str = "monday",
    hour_utc: int = 14,
) -> bool:
    """Opt the user in. Convenience wrapper around ``save_digest_config``
    that pre-fills the four required fields. Returns the underlying
    save result so callers can surface failures.
    """
    cfg = {
        "enabled": True,
        "day_of_week": day_of_week,
        "hour_utc": hour_utc,
        "channel_ids": list(channel_ids or []),
    }
    return save_digest_config(cfg, user_id=user_id)


def disable_digest(*, user_id: str) -> bool:
    """Opt the user out. Wipes the config row entirely so the next
    ``get_digest_config`` call returns the disabled default. Also
    clears the per-user dispatch lock so re-enabling later does not
    inherit a stale "already sent" stamp from the previous run.
    """
    cfg_ok = _delete_kv(_row_key_config(user_id))
    # Best-effort lock cleanup — failure does not invalidate the disable.
    _delete_kv(_row_key_lock(user_id))
    return cfg_ok


# ─────────────────────────────────────────────────────────────────────────────
#  Public API: compute
# ─────────────────────────────────────────────────────────────────────────────

def _load_alerts_in_window(
    *, user_id: str, week_start: datetime, week_end: datetime
) -> list:
    """Load alerts overlapping the week window for ``user_id``.

    The engine helper takes a max-age-days window relative to "now" — we
    pull a slightly wider window (week_length + 1 days) and then filter
    in Python so a future-dated ``week_start`` request still works
    (e.g. tests pinning the clock). Defensive — returns ``[]`` on any
    failure so the digest never crashes on a broken alerts table.
    """
    try:
        from engine.alert_engine_v2 import load_alerts

        # Pad so a windowed query against "now-7d" returns rows that
        # fall inside an explicit (week_start, week_end) bracket even
        # when ``week_end`` is yesterday.
        days_back = max(_WEEK_LENGTH_DAYS + 1,
                        int((_now_utc() - week_start).total_seconds() / 86400) + 2)
        rows = load_alerts(max_age_days=days_back, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._load_alerts_in_window failed: {exc}")
        return []

    out = []
    for alert in rows:
        ts = _parse_iso(getattr(alert, "created_at", "") or "")
        if ts is None:
            continue
        # Normalize tz so the comparison works for naive timestamps
        # (legacy rows occasionally lack the offset).
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if week_start <= ts < week_end:
            out.append(alert)
    return out


def _aggregate_alerts(alerts: list) -> dict:
    """Build the alert-shaped slice of the summary dict.

    Returns shape:
      {
        "alerts_total": int,
        "alerts_by_severity": {"CRITICAL": n, "HIGH": n, ...},
        "top_alert_types": [{"alert_type": str, "count": int}, ...],
        "top_routes":      [{"route_id": str,   "count": int}, ...],
        "top_tickers":     [{"ticker": str,     "count": int}, ...],
        "ack_rate": float,
      }
    """
    if not alerts:
        return {
            "alerts_total": 0,
            "alerts_by_severity": {},
            "top_alert_types": [],
            "top_routes": [],
            "top_tickers": [],
            "ack_rate": 0.0,
        }

    sev_counts: dict[str, int] = {}
    type_counts: dict[str, int] = {}
    route_counts: dict[str, int] = {}
    ticker_counts: dict[str, int] = {}
    acked = 0
    for a in alerts:
        sev_counts[a.severity] = sev_counts.get(a.severity, 0) + 1
        type_counts[a.alert_type] = type_counts.get(a.alert_type, 0) + 1
        if a.route_id:
            route_counts[a.route_id] = route_counts.get(a.route_id, 0) + 1
        if a.ticker:
            ticker_counts[a.ticker] = ticker_counts.get(a.ticker, 0) + 1
        if getattr(a, "acknowledged", False):
            acked += 1

    def _top(counts: dict[str, int], key_name: str) -> list[dict]:
        # Sort by (count desc, name asc) so ties have a deterministic
        # winner — matches the tie-break convention used by
        # ``alert_correlator``'s dominant_alert_type.
        return [
            {key_name: name, "count": int(cnt)}
            for name, cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:_TOP_N]
        ]

    return {
        "alerts_total": len(alerts),
        "alerts_by_severity": dict(sorted(sev_counts.items())),
        "top_alert_types": _top(type_counts, "alert_type"),
        "top_routes": _top(route_counts, "route_id"),
        "top_tickers": _top(ticker_counts, "ticker"),
        "ack_rate": round(acked / len(alerts), 6),
    }


def _aggregate_incidents(alerts: list) -> int:
    """Run alerts through the correlator and return the incident count.

    Defensive — a correlator failure degrades to 0 rather than crashing
    the digest.
    """
    if not alerts:
        return 0
    try:
        from engine.alert_correlator import correlate_alerts

        return len(correlate_alerts(alerts))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._aggregate_incidents failed: {exc}")
        return 0


def _aggregate_source_health() -> dict:
    """Fetch the 7-day source-health window. Defensive — empty shape
    on any error so the digest still renders.
    """
    try:
        from engine.source_health import get_health_summary

        return dict(get_health_summary(window_hours=_WEEK_LENGTH_DAYS * 24))
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._aggregate_source_health failed: {exc}")
        return {"window_hours": _WEEK_LENGTH_DAYS * 24,
                "total_pings": 0, "by_source": {}, "current_outages": []}


def _aggregate_channel_usage(*, user_id: str) -> list[dict]:
    """Fetch per-channel budget usage rows. Defensive — empty list on
    any error.
    """
    try:
        from engine.alert_delivery import get_all_channel_usage

        return list(get_all_channel_usage(user_id=user_id) or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._aggregate_channel_usage failed: {exc}")
        return []


def _aggregate_budget_suppressed() -> int:
    """Snapshot the cumulative budget-suppressed counter."""
    try:
        from engine.alert_delivery import get_budget_suppressed_count

        return int(get_budget_suppressed_count() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._aggregate_budget_suppressed failed: {exc}")
        return 0


def _empty_summary() -> dict:
    """Sentinel summary shape — returned when an aggregator failure
    leaves us without real data. Same keys as the populated path so
    the renderers never need to guard against missing keys."""
    return {
        "alerts_total": 0,
        "alerts_by_severity": {},
        "incidents_total": 0,
        "top_alert_types": [],
        "top_routes": [],
        "top_tickers": [],
        "source_health": {"window_hours": _WEEK_LENGTH_DAYS * 24,
                          "total_pings": 0, "by_source": {},
                          "current_outages": []},
        "channel_usage": [],
        "ack_rate": 0.0,
        "budget_suppressed": 0,
    }


def compute_digest(
    *,
    user_id: str,
    week_start: Optional[str] = None,
) -> WeeklyDigest:
    """Build a populated :class:`WeeklyDigest` for ``user_id``.

    ``week_start`` defaults to the Monday 00:00 UTC of the current
    week. Any explicit value is snapped to that week's Monday so a
    mid-week input still produces a Mon→Sun bracket.

    NEVER raises. Every aggregator wraps its engine call in try/except
    and degrades to a sentinel shape on failure — the digest always
    ships with the same key set. A top-level try/except around the
    composition is the belt-and-braces guard for the (rare) case where
    one of the helpers is monkeypatched to raise, or a downstream
    refactor accidentally removes an inner guard.
    """
    try:
        start_dt, end_dt = _resolve_week_window(week_start)
        week_start_iso = _iso_date(start_dt)
        # week_end is the LAST day of the window (Sunday) — exclusive upper
        # bound on the alert query, inclusive in the human-facing field.
        week_end_iso = _iso_date(end_dt - timedelta(days=1))

        alerts = _load_alerts_in_window(
            user_id=user_id, week_start=start_dt, week_end=end_dt
        )

        summary: dict = {}
        summary.update(_aggregate_alerts(alerts))
        summary["incidents_total"] = _aggregate_incidents(alerts)
        summary["source_health"] = _aggregate_source_health()
        summary["channel_usage"] = _aggregate_channel_usage(user_id=user_id)
        summary["budget_suppressed"] = _aggregate_budget_suppressed()

        return WeeklyDigest(
            user_id=str(user_id or ""),
            week_start=week_start_iso,
            week_end=week_end_iso,
            summary=summary,
            generated_at=_now_iso(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"compute_digest: top-level failure: {exc}")
        # Best-effort fallback shape so the caller still gets a usable
        # object (the contract: "NEVER raises").
        try:
            start_dt, end_dt = _resolve_week_window(week_start)
            ws_iso = _iso_date(start_dt)
            we_iso = _iso_date(end_dt - timedelta(days=1))
        except Exception:
            ws_iso = ""
            we_iso = ""
        return WeeklyDigest(
            user_id=str(user_id or ""),
            week_start=ws_iso,
            week_end=we_iso,
            summary=_empty_summary(),
            generated_at=_now_iso(),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Public API: rendering
# ─────────────────────────────────────────────────────────────────────────────

def render_digest_markdown(digest: WeeklyDigest) -> str:
    """Compose a self-contained markdown summary.

    The layout mirrors the operator-digest text fallback — a header
    with the week bracket, a top-of-page KPI block, then a sequence
    of section tables. Designed to render cleanly in Slack code blocks
    AND a generic markdown viewer.
    """
    summary = digest.summary or {}
    lines: list[str] = []
    lines.append(f"# Weekly Digest · {digest.week_start} → {digest.week_end}")
    lines.append("")
    lines.append(f"_Generated {digest.generated_at}_")
    lines.append("")

    # Headline block — alerts / incidents / ack rate. Padded to a fixed
    # width so the values line up vertically in monospace renderers.
    alerts_total = int(summary.get("alerts_total", 0) or 0)
    incidents_total = int(summary.get("incidents_total", 0) or 0)
    ack_rate = float(summary.get("ack_rate", 0.0) or 0.0)
    budget_suppressed = int(summary.get("budget_suppressed", 0) or 0)
    lines.append("## Headline")
    lines.append("")
    lines.append(f"- **Alerts this week:** {alerts_total}")
    lines.append(f"- **Incidents:** {incidents_total}")
    lines.append(f"- **Ack rate:** {ack_rate * 100:.1f}%")
    lines.append(f"- **Budget-suppressed deliveries (cumulative):** "
                 f"{budget_suppressed}")
    lines.append("")

    # By-severity table.
    sev = summary.get("alerts_by_severity") or {}
    if sev:
        lines.append("## Alerts by Severity")
        lines.append("")
        lines.append("| Severity | Count |")
        lines.append("|---|---|")
        for k in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
            if k in sev:
                lines.append(f"| {k} | {int(sev[k])} |")
        lines.append("")

    # Top alert types.
    top_types = summary.get("top_alert_types") or []
    if top_types:
        lines.append("## Top Alert Types")
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("|---|---|")
        for row in top_types:
            lines.append(f"| {row.get('alert_type', '')} | {int(row.get('count', 0))} |")
        lines.append("")

    # Top routes.
    top_routes = summary.get("top_routes") or []
    if top_routes:
        lines.append("## Top Routes")
        lines.append("")
        lines.append("| Route | Alerts |")
        lines.append("|---|---|")
        for row in top_routes:
            lines.append(f"| {row.get('route_id', '')} | {int(row.get('count', 0))} |")
        lines.append("")

    # Top tickers.
    top_tickers = summary.get("top_tickers") or []
    if top_tickers:
        lines.append("## Top Tickers")
        lines.append("")
        lines.append("| Ticker | Alerts |")
        lines.append("|---|---|")
        for row in top_tickers:
            lines.append(f"| {row.get('ticker', '')} | {int(row.get('count', 0))} |")
        lines.append("")

    # Source-feed health — only the current outages list, no
    # ping-by-ping breakdown (that's a Data Health tab thing).
    health = summary.get("source_health") or {}
    outages = list(health.get("current_outages") or [])
    lines.append("## Source-Feed Health")
    lines.append("")
    if outages:
        lines.append("Current outages:")
        for src in outages:
            lines.append(f"- {src}")
    else:
        lines.append("All sources healthy.")
    lines.append("")

    # Channel usage — only flag channels with a positive budget.
    usage_rows = summary.get("channel_usage") or []
    interesting = [r for r in usage_rows if int(r.get("budget", 0) or 0) > 0]
    if interesting:
        lines.append("## Channel Budgets")
        lines.append("")
        lines.append("| Channel | Kind | Usage | Budget | % | Status |")
        lines.append("|---|---|---|---|---|---|")
        for r in interesting:
            pct = r.get("pct")
            pct_s = f"{float(pct):.1f}%" if pct is not None else "—"
            status = "OVER" if r.get("over_budget") else "OK"
            lines.append(
                f"| {r.get('name', '')} | {r.get('kind', '')} | "
                f"{int(r.get('usage', 0))} | {int(r.get('budget', 0))} | "
                f"{pct_s} | {status} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_digest_email_html(digest: WeeklyDigest) -> str:
    """Email-safe HTML — table-based layout, every attribute inline,
    no external CSS, no <script>, no <style>.

    Every payload-derived value passes through ``html.escape`` so a
    malicious channel name / ticker symbol can't smuggle markup into
    the recipient's inbox. The whole output is wrapped in <html><body>
    so it stands on its own when an email client renders the raw HTML
    part directly.
    """
    summary = digest.summary or {}
    esc = html.escape

    def _row_tile(label: str, value: str) -> str:
        return (
            f"<td style='padding:8px 12px;vertical-align:top;width:25%;'>"
            f"<div style='border-left:3px solid #2a3b4d;padding:6px 10px;'>"
            f"<div style='font-size:10px;font-weight:700;letter-spacing:0.08em;"
            f"text-transform:uppercase;color:#586069;'>{esc(label)}</div>"
            f"<div style='font-size:18px;font-weight:700;color:#24292e;"
            f"margin-top:2px;'>{esc(value)}</div>"
            f"</div></td>"
        )

    alerts_total = int(summary.get("alerts_total", 0) or 0)
    incidents_total = int(summary.get("incidents_total", 0) or 0)
    ack_rate = float(summary.get("ack_rate", 0.0) or 0.0)
    budget_suppressed = int(summary.get("budget_suppressed", 0) or 0)

    header = (
        f"<div style='border-left:6px solid #2a3b4d;padding:14px 18px;"
        f"background:#fafbfc;'>"
        f"<div style='font-size:11px;font-weight:700;letter-spacing:0.12em;"
        f"text-transform:uppercase;color:#2a3b4d;'>Weekly Digest</div>"
        f"<h2 style='margin:4px 0 0 0;font-size:20px;color:#24292e;'>"
        f"{esc(digest.week_start)} &rarr; {esc(digest.week_end)}</h2>"
        f"<div style='font-size:12px;color:#586069;margin-top:4px;'>"
        f"Generated {esc(digest.generated_at)}</div>"
        f"</div>"
    )

    kpi_row = (
        "<table style='width:100%;border-collapse:collapse;margin-top:16px;"
        "font-family:Helvetica,Arial,sans-serif;'><tr>"
        + _row_tile("Alerts", f"{alerts_total:,}")
        + _row_tile("Incidents", f"{incidents_total:,}")
        + _row_tile("Ack rate", f"{ack_rate * 100:.1f}%")
        + _row_tile("Budget suppressed", f"{budget_suppressed:,}")
        + "</tr></table>"
    )

    # Severity table.
    sev = summary.get("alerts_by_severity") or {}
    sev_html = ""
    if sev:
        rows = "".join(
            f"<tr><td style='padding:4px 10px;'>{esc(k)}</td>"
            f"<td style='padding:4px 10px;'>{int(v)}</td></tr>"
            for k, v in sev.items()
        )
        sev_html = (
            "<h3 style='margin:20px 0 6px 0;font-size:14px;color:#24292e;'>"
            "Alerts by severity</h3>"
            "<table style='border-collapse:collapse;font-size:13px;"
            "font-family:Helvetica,Arial,sans-serif;'>"
            f"{rows}</table>"
        )

    def _list_table(title: str, rows: list[dict], val_key: str, count_label: str) -> str:
        if not rows:
            return ""
        body = "".join(
            f"<tr><td style='padding:4px 10px;'>{esc(str(r.get(val_key, '')))}</td>"
            f"<td style='padding:4px 10px;'>{int(r.get('count', 0))}</td></tr>"
            for r in rows
        )
        return (
            f"<h3 style='margin:20px 0 6px 0;font-size:14px;color:#24292e;'>"
            f"{esc(title)}</h3>"
            f"<table style='border-collapse:collapse;font-size:13px;"
            f"font-family:Helvetica,Arial,sans-serif;'>"
            f"<tr><th style='padding:4px 10px;text-align:left;color:#586069;'>"
            f"{esc(val_key.replace('_', ' ').title())}</th>"
            f"<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            f"{esc(count_label)}</th></tr>"
            f"{body}</table>"
        )

    types_html = _list_table(
        "Top alert types", summary.get("top_alert_types") or [],
        "alert_type", "Count",
    )
    routes_html = _list_table(
        "Top routes", summary.get("top_routes") or [],
        "route_id", "Alerts",
    )
    tickers_html = _list_table(
        "Top tickers", summary.get("top_tickers") or [],
        "ticker", "Alerts",
    )

    # Health block.
    health = summary.get("source_health") or {}
    outages = list(health.get("current_outages") or [])
    if outages:
        outage_items = "".join(f"<li>{esc(str(s))}</li>" for s in outages)
        health_html = (
            "<h3 style='margin:20px 0 6px 0;font-size:14px;color:#24292e;'>"
            "Current outages</h3>"
            f"<ul style='font-family:Helvetica,Arial,sans-serif;font-size:13px;'>"
            f"{outage_items}</ul>"
        )
    else:
        health_html = (
            "<h3 style='margin:20px 0 6px 0;font-size:14px;color:#24292e;'>"
            "Source-feed health</h3>"
            "<p style='font-family:Helvetica,Arial,sans-serif;font-size:13px;"
            "color:#22863a;'>All sources healthy.</p>"
        )

    # Channel usage table.
    usage_rows = summary.get("channel_usage") or []
    interesting = [r for r in usage_rows if int(r.get("budget", 0) or 0) > 0]
    usage_html = ""
    if interesting:
        rows_html = ""
        for r in interesting:
            pct_val = r.get("pct")
            pct_s = f"{float(pct_val):.1f}%" if pct_val is not None else "&mdash;"
            status = "OVER" if r.get("over_budget") else "OK"
            rows_html += (
                f"<tr><td style='padding:4px 10px;'>{esc(str(r.get('name', '')))}</td>"
                f"<td style='padding:4px 10px;'>{esc(str(r.get('kind', '')))}</td>"
                f"<td style='padding:4px 10px;'>{int(r.get('usage', 0))}</td>"
                f"<td style='padding:4px 10px;'>{int(r.get('budget', 0))}</td>"
                f"<td style='padding:4px 10px;'>{pct_s}</td>"
                f"<td style='padding:4px 10px;'>{esc(status)}</td></tr>"
            )
        usage_html = (
            "<h3 style='margin:20px 0 6px 0;font-size:14px;color:#24292e;'>"
            "Channel budgets</h3>"
            "<table style='border-collapse:collapse;font-size:13px;"
            "font-family:Helvetica,Arial,sans-serif;'>"
            "<tr><th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "Channel</th>"
            "<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "Kind</th>"
            "<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "Usage</th>"
            "<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "Budget</th>"
            "<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "%</th>"
            "<th style='padding:4px 10px;text-align:left;color:#586069;'>"
            "Status</th></tr>"
            f"{rows_html}</table>"
        )

    body = (
        "<html><body style='font-family:Helvetica,Arial,sans-serif;color:#24292e;"
        "background:#ffffff;margin:0;padding:0;'>"
        "<div style='max-width:680px;margin:0 auto;padding:24px;'>"
        f"{header}{kpi_row}{sev_html}{types_html}{routes_html}{tickers_html}"
        f"{health_html}{usage_html}"
        "</div></body></html>"
    )
    return body


# ─────────────────────────────────────────────────────────────────────────────
#  Public API: dispatch
# ─────────────────────────────────────────────────────────────────────────────

def _build_digest_alert(digest: WeeklyDigest) -> Any:
    """Synthesize a ShippingAlert that carries the rendered digest as
    title + body. Used by ``dispatch_digest`` so the existing
    ``alert_delivery.deliver_alert`` transport (with all its budget /
    quiet-hours / per-user-prefs gates) can ship the digest WITHOUT
    persisting it through ``save_alerts``.

    Lazy import so this module can be imported even when the alert
    engine is partially loaded.
    """
    from engine.alert_engine_v2 import _make as _make_alert

    title = f"Weekly Digest · {digest.week_start} → {digest.week_end}"
    body = render_digest_markdown(digest)
    return _make_alert(
        alert_type=_DIGEST_ALERT_TYPE,
        severity=_DIGEST_ALERT_SEVERITY,
        title=title,
        body=body,
    )


def dispatch_digest(
    digest: WeeklyDigest,
    *,
    channel_ids: Optional[list[str]] = None,
) -> list[dict]:
    """Dispatch the digest through every compatible enabled channel.

    ``channel_ids`` (when provided) filters the user's channels to just
    those ids. ``None`` (the default) fans out to every enabled channel
    of a compatible kind. SMS / PagerDuty are dropped automatically
    because the digest payload is too long for SMS and the wrong shape
    for PagerDuty's incident model.

    Returns a list of per-channel result dicts:
      ``{"channel_id": str, "name": str, "kind": str,
         "success": bool, "status_code": int, "error_msg": str}``

    NEVER raises — every dispatch is wrapped in try/except so one bad
    channel can never break the rest.
    """
    try:
        from engine.alert_delivery import deliver_alert, load_channels
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"dispatch_digest: failed to import delivery surface: {exc}")
        return []

    try:
        channels = load_channels(user_id=digest.user_id) or []
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"dispatch_digest: load_channels failed: {exc}")
        return []

    # Filter to compatible kinds + enabled channels + (optionally) the
    # operator-supplied id list. Membership preserves the order on disk.
    id_filter = set(channel_ids) if channel_ids else None
    eligible = []
    for ch in channels:
        if not ch.enabled:
            continue
        if ch.kind not in _COMPATIBLE_CHANNEL_KINDS:
            continue
        if id_filter is not None and ch.channel_id not in id_filter:
            continue
        eligible.append(ch)

    if not eligible:
        return []

    alert = _build_digest_alert(digest)
    out: list[dict] = []
    for ch in eligible:
        try:
            result = deliver_alert(alert, ch)
            out.append({
                "channel_id": ch.channel_id,
                "name": ch.name,
                "kind": ch.kind,
                "success": bool(getattr(result, "success", False)),
                "status_code": int(getattr(result, "status_code", 0) or 0),
                "error_msg": str(getattr(result, "error_msg", "") or ""),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                f"dispatch_digest: channel={ch.name!r} kind={ch.kind} "
                f"failed: {exc}"
            )
            out.append({
                "channel_id": ch.channel_id,
                "name": ch.name,
                "kind": ch.kind,
                "success": False,
                "status_code": 0,
                "error_msg": str(exc),
            })
    return out


# ─────────────────────────────────────────────────────────────────────────────
#  Public API: worker entrypoint
# ─────────────────────────────────────────────────────────────────────────────

def _list_users_with_digest() -> list[str]:
    """Enumerate user_ids that have a weekly-digest config row.

    Returns ``[]`` on any error. The list contains EVERY user that has
    ever called ``save_digest_config`` (enabled or not) — the caller
    is expected to drop disabled rows via ``get_digest_config``.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT key FROM kv_state WHERE key LIKE ?",
            (f"{_CONFIG_KEY_PREFIX}%",),
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"weekly_digest._list_users_with_digest failed: {exc}")
        return []
    prefix_len = len(_CONFIG_KEY_PREFIX)
    return [(r["key"] if hasattr(r, "keys") else r[0])[prefix_len:] for r in rows]


def _is_due(cfg: dict, now: datetime) -> bool:
    """Return True iff the user's config matches the current
    day_of_week + hour_utc. The check is hour-level — the worker fires
    hourly, so a 14:00 trigger fires sometime in the 14:xx range.
    """
    if not cfg.get("enabled", False):
        return False
    weekday = _WEEKDAY_LOOKUP.get(str(cfg.get("day_of_week", "")).lower(), -1)
    hour = int(cfg.get("hour_utc", -1))
    if weekday < 0 or hour < 0:
        return False
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return now_utc.weekday() == weekday and now_utc.hour == hour


def _lock_is_fresh(user_id: str, now: datetime, *, ttl_minutes: int = 90) -> bool:
    """Return True iff the per-user dispatch lock was bumped within
    the last ``ttl_minutes``. Used by ``run_weekly_digest_job`` to
    short-circuit a double-fire inside the same scheduled hour. 90
    minutes covers the worst-case 60-min hour + a 30-min grace for
    clock skew / slow workers.
    """
    raw = _read_kv(_row_key_lock(user_id))
    if raw is None:
        return False
    ts = _parse_iso(raw)
    if ts is None:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (now_utc - ts) <= timedelta(minutes=ttl_minutes)


def _stamp_lock(user_id: str, now: datetime) -> None:
    """Persist the current dispatch timestamp so the next hourly tick
    inside the same window skips the user.
    """
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    _write_kv(_row_key_lock(user_id), now_utc.isoformat())


def run_weekly_digest_job(*, now: Optional[datetime] = None) -> dict:
    """Worker entrypoint. Iterates every user with a digest config,
    self-gates on day-of-week + hour, and dispatches the digest for
    every user that is due AND has not already been processed in this
    scheduled hour.

    Returns a summary dict:
      ``{"checked": N, "fired": N, "skipped": N, "failed": N}``

    NEVER raises — a per-user dispatch failure increments ``failed``
    and the loop continues. A top-level failure (e.g. kv_state cannot
    be read at all) returns the all-zeros shape.
    """
    summary = {"checked": 0, "fired": 0, "skipped": 0, "failed": 0}
    try:
        clock = now or _now_utc()
        user_ids = _list_users_with_digest()
        summary["checked"] = len(user_ids)
        for uid in user_ids:
            try:
                cfg = get_digest_config(user_id=uid)
                if not _is_due(cfg, clock):
                    summary["skipped"] += 1
                    continue
                if _lock_is_fresh(uid, clock):
                    summary["skipped"] += 1
                    continue
                digest = compute_digest(user_id=uid)
                results = dispatch_digest(
                    digest, channel_ids=cfg.get("channel_ids") or None
                )
                # Stamp the lock REGARDLESS of dispatch success — the
                # operator's preference is "don't burn the recipient
                # with retries inside the scheduled hour"; a failed
                # transport will surface on the next configured slot.
                _stamp_lock(uid, clock)
                if any(r.get("success") for r in results):
                    summary["fired"] += 1
                else:
                    summary["failed"] += 1
                logger.info(
                    f"run_weekly_digest_job: user_id={uid!r} "
                    f"dispatched={len(results)} "
                    f"successes={sum(1 for r in results if r.get('success'))}"
                )
            except Exception as exc:  # noqa: BLE001
                summary["failed"] += 1
                logger.warning(
                    f"run_weekly_digest_job: per-user dispatch failed for "
                    f"{uid!r}: {exc}"
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"run_weekly_digest_job: top-level failure: {exc}")
    return summary


__all__ = [
    "WeeklyDigest",
    "compute_digest",
    "render_digest_markdown",
    "render_digest_email_html",
    "dispatch_digest",
    "run_weekly_digest_job",
    "get_digest_config",
    "save_digest_config",
    "enable_digest",
    "disable_digest",
]
