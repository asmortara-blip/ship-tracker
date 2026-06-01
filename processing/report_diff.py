"""Diff helper for InvestorReport snapshots.

Given two InvestorReport instances (today vs. yesterday, or any pair),
compute a structured diff: which alpha signals are new, which dropped
off the list, which routes saw the biggest 30d-rate delta, how the
composite sentiment shifted, whether the risk-level escalated.

Output is dataclass-shaped so callers can feed it into a daily digest
or render it as a styled HTML snippet via ``format_diff_html``.

Design notes
------------
- The function takes ANY object with the InvestorReport-shaped
  attributes (sentiment.overall_score, alpha.signals, freight.routes,
  market.risk_level, report_date / generated_at). Tests build stand-in
  dataclasses to avoid coupling to the engine's internal shapes.
- All getattr lookups have safe defaults. Bad/missing input returns a
  zeroed ReportDiff rather than raising.
- "Top N" lists are sorted by absolute delta so the biggest moves
  surface first regardless of direction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ─── Dataclasses ───────────────────────────────────────────────────────────

@dataclass
class SignalDelta:
    """One alpha-signal-level delta between two reports."""
    name: str                       # signal title / name
    status: str                     # "new" | "dropped" | "unchanged"
    prev_score: float               # 0 when status == "new"
    curr_score: float               # 0 when status == "dropped"
    delta_score: float              # curr - prev


@dataclass
class RouteDelta:
    """One freight-route-level delta between two reports."""
    name: str                       # route_id or route name
    status: str                     # "new" | "dropped" | "unchanged"
    prev_rate_usd_per_feu: float    # 0 when status == "new"
    curr_rate_usd_per_feu: float    # 0 when status == "dropped"
    delta_pct: float                # (curr - prev) / prev × 100, or 0 when prev == 0


@dataclass
class ReportDiff:
    """The structured diff between two InvestorReport snapshots."""
    prev_date: str                  # ISO or human-readable
    curr_date: str
    sentiment_shift: float          # curr.overall_score - prev.overall_score
    risk_level_change: str          # "LOW -> MODERATE" / "unchanged"
    new_signals: list[str] = field(default_factory=list)
    dropped_signals: list[str] = field(default_factory=list)
    top_signal_score_changes: list[SignalDelta] = field(default_factory=list)
    top_route_rate_changes: list[RouteDelta] = field(default_factory=list)
    summary_narrative: str = ""


# ─── Internal helpers ─────────────────────────────────────────────────────

def _signal_name(sig: Any) -> str:
    """Best-effort name extraction from an AlphaSignal-shaped object.

    Tries (in order): .title, .name, .signal_name, str(sig). Empty
    strings count as 'no name'."""
    for attr in ("title", "name", "signal_name"):
        v = getattr(sig, attr, None)
        if isinstance(v, str) and v:
            return v
        if isinstance(sig, dict):
            v = sig.get(attr)
            if isinstance(v, str) and v:
                return v
    return str(sig) if sig is not None else ""


def _signal_score(sig: Any) -> float:
    """Best-effort score extraction. Tries .score, .strength, .conviction.
    Anything unparseable -> 0.0."""
    for attr in ("score", "strength", "conviction"):
        v = getattr(sig, attr, None)
        if v is None and isinstance(sig, dict):
            v = sig.get(attr)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _route_name(route: Any) -> str:
    """Route id/name extraction from a dict or object."""
    if isinstance(route, dict):
        return str(route.get("route_id") or route.get("route_name") or route.get("name") or "")
    for attr in ("route_id", "route_name", "name"):
        v = getattr(route, attr, None)
        if isinstance(v, str) and v:
            return v
    return ""


def _route_rate(route: Any) -> float:
    """Extract a USD/FEU rate from the route record. Try several common
    field names; return 0.0 when nothing parses."""
    if isinstance(route, dict):
        for k in ("rate_usd_per_feu", "rate", "current_rate", "current_rate_usd_feu"):
            v = route.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    continue
        return 0.0
    for attr in ("rate_usd_per_feu", "rate", "current_rate", "current_rate_usd_feu"):
        v = getattr(route, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return 0.0


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _zero_diff(prev_date: str = "", curr_date: str = "") -> ReportDiff:
    return ReportDiff(
        prev_date=prev_date,
        curr_date=curr_date,
        sentiment_shift=0.0,
        risk_level_change="unchanged",
        summary_narrative="No meaningful changes between reports.",
    )


# ─── Public API ──────────────────────────────────────────────────────────

def compute_report_diff(prev: Any, curr: Any) -> ReportDiff:
    """Return a structured diff between two InvestorReport-shaped objects.

    Never raises — bad input returns a zeroed ReportDiff."""
    if prev is None or curr is None:
        return _zero_diff()

    try:
        prev_date = str(getattr(prev, "report_date", "") or getattr(prev, "generated_at", ""))
        curr_date = str(getattr(curr, "report_date", "") or getattr(curr, "generated_at", ""))

        # ── Sentiment shift ────────────────────────────────────────────────
        prev_sent = getattr(prev, "sentiment", None)
        curr_sent = getattr(curr, "sentiment", None)
        prev_score = _safe_float(getattr(prev_sent, "overall_score", 0.0))
        curr_score = _safe_float(getattr(curr_sent, "overall_score", 0.0))
        sentiment_shift = round(curr_score - prev_score, 4)

        # ── Risk level change ─────────────────────────────────────────────
        prev_risk = str(getattr(getattr(prev, "market", None), "risk_level", "") or "")
        curr_risk = str(getattr(getattr(curr, "market", None), "risk_level", "") or "")
        risk_level_change = (
            f"{prev_risk} -> {curr_risk}" if prev_risk != curr_risk and (prev_risk or curr_risk)
            else "unchanged"
        )

        # ── Signal diffs ──────────────────────────────────────────────────
        prev_signals = list(getattr(getattr(prev, "alpha", None), "signals", None) or [])
        curr_signals = list(getattr(getattr(curr, "alpha", None), "signals", None) or [])
        prev_sig_map = {_signal_name(s): _signal_score(s) for s in prev_signals if _signal_name(s)}
        curr_sig_map = {_signal_name(s): _signal_score(s) for s in curr_signals if _signal_name(s)}

        new_signals = sorted(set(curr_sig_map) - set(prev_sig_map))
        dropped_signals = sorted(set(prev_sig_map) - set(curr_sig_map))

        all_names = set(prev_sig_map) | set(curr_sig_map)
        signal_deltas: list[SignalDelta] = []
        for name in all_names:
            ps = prev_sig_map.get(name, 0.0)
            cs = curr_sig_map.get(name, 0.0)
            if name in new_signals:
                status = "new"
            elif name in dropped_signals:
                status = "dropped"
            else:
                status = "unchanged"
            signal_deltas.append(SignalDelta(
                name=name, status=status,
                prev_score=round(ps, 4), curr_score=round(cs, 4),
                delta_score=round(cs - ps, 4),
            ))
        signal_deltas.sort(key=lambda d: abs(d.delta_score), reverse=True)
        top_signal_score_changes = signal_deltas[:10]

        # ── Route rate diffs ──────────────────────────────────────────────
        prev_routes = list(getattr(getattr(prev, "freight", None), "routes", None) or [])
        curr_routes = list(getattr(getattr(curr, "freight", None), "routes", None) or [])
        prev_route_map = {_route_name(r): _route_rate(r) for r in prev_routes if _route_name(r)}
        curr_route_map = {_route_name(r): _route_rate(r) for r in curr_routes if _route_name(r)}

        all_route_names = set(prev_route_map) | set(curr_route_map)
        route_deltas: list[RouteDelta] = []
        for name in all_route_names:
            pr = prev_route_map.get(name, 0.0)
            cr = curr_route_map.get(name, 0.0)
            if name not in prev_route_map:
                status = "new"
            elif name not in curr_route_map:
                status = "dropped"
            else:
                status = "unchanged"
            delta_pct = ((cr - pr) / pr * 100.0) if pr > 0 else 0.0
            route_deltas.append(RouteDelta(
                name=name, status=status,
                prev_rate_usd_per_feu=round(pr, 2),
                curr_rate_usd_per_feu=round(cr, 2),
                delta_pct=round(delta_pct, 2),
            ))
        route_deltas.sort(key=lambda d: abs(d.delta_pct), reverse=True)
        top_route_rate_changes = route_deltas[:10]

        # ── Narrative ─────────────────────────────────────────────────────
        narrative = _build_narrative(
            sentiment_shift=sentiment_shift,
            risk_change=risk_level_change,
            new_signals=new_signals,
            dropped_signals=dropped_signals,
            top_route_rate_changes=top_route_rate_changes,
        )

        return ReportDiff(
            prev_date=prev_date,
            curr_date=curr_date,
            sentiment_shift=sentiment_shift,
            risk_level_change=risk_level_change,
            new_signals=new_signals,
            dropped_signals=dropped_signals,
            top_signal_score_changes=top_signal_score_changes,
            top_route_rate_changes=top_route_rate_changes,
            summary_narrative=narrative,
        )
    except Exception:
        # Anything we couldn't parse → zeroed diff. Never raise into the
        # caller (this layer feeds a daily digest / email — silent
        # degrade is preferable to crashing the briefing pipeline).
        return _zero_diff()


def _build_narrative(
    *, sentiment_shift: float, risk_change: str,
    new_signals: list[str], dropped_signals: list[str],
    top_route_rate_changes: list[RouteDelta],
) -> str:
    """Build a 2-3 sentence narrative summarizing the diff."""
    sentences: list[str] = []
    if abs(sentiment_shift) >= 0.05:
        direction = "improved" if sentiment_shift > 0 else "softened"
        sentences.append(
            f"Composite sentiment {direction} by {abs(sentiment_shift):.2f} between reports."
        )
    if risk_change != "unchanged":
        sentences.append(f"Risk level shifted: {risk_change}.")
    if new_signals or dropped_signals:
        sentences.append(
            f"{len(new_signals)} new signal(s); {len(dropped_signals)} dropped off."
        )
    if top_route_rate_changes:
        big = top_route_rate_changes[0]
        if abs(big.delta_pct) >= 1.0:
            verb = "rose" if big.delta_pct > 0 else "fell"
            sentences.append(
                f"{big.name} {verb} {abs(big.delta_pct):.1f}%."
            )

    if not sentences:
        return "No meaningful changes between reports."
    return " ".join(sentences)


def format_diff_html(diff: ReportDiff) -> str:
    """Render the diff as an HTML snippet suitable for embedding in
    daily-briefing tabs or email digests.

    Uses inline styles only — no external CSS dependency. Mirrors the
    WSJ-ish palette of the rest of the app (steel-blue accents, muted
    neutrals, severity-coded green/red).
    """
    try:
        _CSS_NEW = "color:#2e9e6e;font-weight:600"
        _CSS_DROP = "color:#c0392b;font-weight:600"
        _CSS_LABEL = "color:#6b6760;font-size:0.7rem;text-transform:uppercase;letter-spacing:0.08em"

        sent_color = "#2e9e6e" if diff.sentiment_shift > 0 else ("#c0392b" if diff.sentiment_shift < 0 else "#6b6760")
        sent_sign = "+" if diff.sentiment_shift > 0 else ""

        # Top signal change rows
        sig_rows: list[str] = []
        for d in diff.top_signal_score_changes[:5]:
            sign = "+" if d.delta_score > 0 else ""
            color = "#2e9e6e" if d.delta_score > 0 else "#c0392b" if d.delta_score < 0 else "#6b6760"
            badge = f'<span style="{_CSS_NEW}">NEW</span>' if d.status == "new" else (
                f'<span style="{_CSS_DROP}">DROPPED</span>' if d.status == "dropped" else ""
            )
            sig_rows.append(
                f'<li style="margin-bottom:4px">'
                f'<span style="color:#e8e6e1">{d.name}</span> '
                f'<span style="color:{color}">{sign}{d.delta_score:.2f}</span> '
                f'{badge}</li>'
            )
        sig_block = (
            f'<ul style="list-style:none;padding-left:0;margin:6px 0">{"".join(sig_rows)}</ul>'
            if sig_rows else
            '<div style="color:#6b6760;font-size:0.8rem">No signal-level changes.</div>'
        )

        # Top route change rows
        route_rows: list[str] = []
        for d in diff.top_route_rate_changes[:5]:
            sign = "+" if d.delta_pct > 0 else ""
            color = "#2e9e6e" if d.delta_pct > 0 else "#c0392b" if d.delta_pct < 0 else "#6b6760"
            route_rows.append(
                f'<li style="margin-bottom:4px">'
                f'<span style="color:#e8e6e1">{d.name}</span> '
                f'<span style="color:{color}">{sign}{d.delta_pct:.1f}%</span> '
                f'<span style="color:#6b6760">${d.curr_rate_usd_per_feu:,.0f}/FEU</span></li>'
            )
        route_block = (
            f'<ul style="list-style:none;padding-left:0;margin:6px 0">{"".join(route_rows)}</ul>'
            if route_rows else
            '<div style="color:#6b6760;font-size:0.8rem">No route-level changes.</div>'
        )

        return (
            f'<div style="background:rgba(53,114,176,0.06);border-left:3px solid #3572b0;'
            f'padding:14px 18px;border-radius:3px;margin:12px 0">'
            f'<div style="{_CSS_LABEL}">What changed</div>'
            f'<div style="font-family:Libre Baskerville,Georgia,serif;font-size:1rem;'
            f'color:#e8e6e1;font-weight:600;margin:6px 0 10px 0">'
            f'{diff.prev_date} → {diff.curr_date}'
            f'</div>'
            f'<div style="margin-bottom:10px;font-size:0.85rem;color:#c5c1b8">'
            f'{diff.summary_narrative}'
            f'</div>'
            f'<div style="display:flex;gap:18px;flex-wrap:wrap;font-size:0.78rem">'
            f'<div><span style="{_CSS_LABEL}">Sentiment</span> '
            f'<span style="color:{sent_color};font-weight:600">{sent_sign}{diff.sentiment_shift:.2f}</span></div>'
            f'<div><span style="{_CSS_LABEL}">Risk</span> '
            f'<span style="color:#e8e6e1">{diff.risk_level_change}</span></div>'
            f'<div><span style="{_CSS_LABEL}">New / dropped</span> '
            f'<span style="color:#e8e6e1">{len(diff.new_signals)} / {len(diff.dropped_signals)}</span></div>'
            f'</div>'
            f'<div style="margin-top:10px"><span style="{_CSS_LABEL}">Top signal moves</span>{sig_block}</div>'
            f'<div><span style="{_CSS_LABEL}">Top route moves</span>{route_block}</div>'
            f'</div>'
        )
    except Exception:
        return '<div style="color:#c0392b">Diff render failed.</div>'
