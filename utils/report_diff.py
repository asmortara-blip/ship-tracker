"""Report-to-report structured diff.

Comparing a current investor report against a prior one and surfacing
"what changed" — new alpha signals, signals that flipped direction,
confidence shifts, route value moves, sentiment / risk drift, schema
upgrades.

Today operators can only eyeball a side-by-side by opening two browser
tabs; this module produces a structured, render-friendly entry list a
CLI / API / UI can all consume identically.

Design
------
* All diff helpers are **pure functions** — no I/O, no DB calls. They
  accept any object that quacks like the InvestorReport-shaped payload
  (the engine's ``InvestorReport`` dataclass, a
  ``processing.report_snapshot.ReportSnapshot``, a dict, or a
  hand-rolled stand-in). Every getattr has a safe fallback so missing
  fields never raise.
* Each meaningful change becomes one :class:`DiffEntry` carrying enough
  metadata to render in any surface (category badge, before/after
  values, human description).
* The :func:`render_diff_html` renderer escapes every user-supplied
  string via :func:`html.escape` — signal titles can come from
  external news feeds and any HTML in those titles must not break out
  of the diff container.
* Numeric thresholds are tunable per call (``confidence_thresh``,
  ``route_pct_thresh``) so a UI can offer a "show all changes" toggle
  without reshipping logic.

This module never compares the raw rendered HTML of the two reports —
that's brittle and surfaces formatting noise. The contract is: the
structured payload is the source of truth, the HTML is for humans.

Public surface
--------------
* :class:`DiffEntry`, :class:`ReportDiff`
* :func:`diff_reports(report_a, report_b, *, report_a_id, report_b_id)
  -> ReportDiff`
* :func:`diff_report_metadata`, :func:`diff_signals`,
  :func:`diff_routes`, :func:`diff_risk`, :func:`diff_sentiment`
* :func:`render_diff_markdown(diff) -> str`
* :func:`render_diff_html(diff) -> str`
* :func:`load_report_payload(report_id, *, user_id) -> Any`

The loader is the only function that touches I/O. It pulls the
``ReportMeta`` row and the closest-in-time ``ReportSnapshot`` and
synthesises a single duck-typed payload object. Per-user scoping is
enforced via the underlying ``list_reports(user_id=...)`` call so a
user can never load another user's report by id.
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

# Category constants — small, exhaustive, used by both the diff functions
# and the rendering helpers so a typo turns into an immediate import or
# test failure rather than a silently-misclassified entry.
CATEGORY_SIGNAL = "signal"
CATEGORY_ROUTE = "route"
CATEGORY_SENTIMENT = "sentiment"
CATEGORY_RISK = "risk"
CATEGORY_METADATA = "metadata"

CHANGE_ADDED = "added"
CHANGE_REMOVED = "removed"
CHANGE_CHANGED = "changed"


@dataclass
class DiffEntry:
    """One row of "what changed" between two reports.

    Attributes
    ----------
    category : str
        One of ``signal`` / ``route`` / ``sentiment`` / ``risk`` /
        ``metadata`` — used by the renderers to group + color rows.
    change_type : str
        One of ``added`` / ``removed`` / ``changed``.
    key : str
        The thing that changed — a signal name, a route id, a metadata
        field name. Used as the row's stable identifier in the UI.
    before : Any
        Value in report A (or ``None`` when ``change_type == 'added'``).
    after : Any
        Value in report B (or ``None`` when ``change_type == 'removed'``).
    description : str
        One-line human summary suitable for direct rendering. The
        renderer escapes it before injecting into HTML, so writers
        can use any character here freely.
    """

    category: str
    change_type: str
    key: str
    before: Any
    after: Any
    description: str


@dataclass
class ReportDiff:
    """The structured diff between two report payloads.

    The summary counts are derived from ``entries`` at construction
    time but exposed as a separate field so a consumer can render the
    headline without re-counting. Mutating ``entries`` after the fact
    leaves ``summary`` stale — callers should treat the object as
    immutable, or rebuild the summary via :func:`_compute_summary`.
    """

    report_a_id: str
    report_b_id: str
    entries: list[DiffEntry] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers — duck-typed field extraction with safe fallbacks
# ---------------------------------------------------------------------------


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Return getattr(obj, name) or obj[name], else default. Never raises."""
    if obj is None:
        return default
    try:
        if hasattr(obj, name):
            val = getattr(obj, name)
            return default if val is None else val
        if isinstance(obj, dict):
            val = obj.get(name)
            return default if val is None else val
    except Exception:
        pass
    return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce to float; on TypeError / ValueError return *default*."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_str(val: Any, default: str = "") -> str:
    """Coerce to str; ``None`` becomes *default*."""
    if val is None:
        return default
    try:
        return str(val)
    except Exception:
        return default


def _changed_significantly(
    before: Any, after: Any, *, abs_thresh: float = 0.05,
) -> bool:
    """Return True iff ``|after - before| > abs_thresh`` numerically.

    Non-numeric inputs degrade to ``before != after`` — this is the
    fallback that catches label flips (e.g. ``"LONG" -> "SHORT"``) when
    a caller passes string-shaped values by accident.
    """
    try:
        return abs(float(after) - float(before)) > float(abs_thresh)
    except (TypeError, ValueError):
        return before != after


def _signal_name(sig: Any) -> str:
    """Best-effort signal-name extraction.

    Tries ``signal_name`` (the real AlphaSignal field) first, then
    ``title`` / ``name`` so dict-shaped or snapshot-view inputs all
    resolve. Empty strings count as "no name" so a malformed row is
    skipped rather than collapsing into the empty-key bucket.
    """
    for attr in ("signal_name", "title", "name"):
        v = _attr(sig, attr, None)
        if isinstance(v, str) and v:
            return v
    return ""


def _signal_direction(sig: Any) -> str:
    """Best-effort direction extraction. Empty string when unknown."""
    return _safe_str(_attr(sig, "direction", ""), default="")


def _signal_confidence(sig: Any) -> float:
    """Best-effort confidence extraction.

    Real AlphaSignal uses ``strength`` (0-1) — the closest analogue to
    confidence. Snapshots expose ``score``. Some hand-rolled inputs use
    ``confidence`` directly. Try each in order; first parseable wins.
    """
    for attr in ("confidence", "strength", "score"):
        v = _attr(sig, attr, None)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _route_name(route: Any) -> str:
    """Route-name extraction — same pattern as ``_signal_name``."""
    for attr in ("route_id", "route_name", "name", "route", "lane"):
        v = _attr(route, attr, None)
        if isinstance(v, str) and v:
            return v
    return ""


def _route_value(route: Any) -> float:
    """Best-effort latest route rate / value extraction."""
    for attr in ("rate_usd_per_feu", "rate", "current_rate", "value", "latest"):
        v = _attr(route, attr, None)
        if v is None:
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


def _route_status(route: Any) -> str:
    """Route status / trend label — used to detect status flips."""
    for attr in ("status", "trend", "label", "momentum_label"):
        v = _attr(route, attr, None)
        if isinstance(v, str) and v:
            return v
    return ""


def _sentiment_score(report: Any) -> float:
    """Pull the composite sentiment score from a duck-typed report.

    The engine puts it on ``report.sentiment.overall_score``; the slim
    snapshot proxies through a ``_SentimentView`` so the same path
    works. As a final fallback we look at the top-level
    ``sentiment_score`` field on the metadata-only path
    (``ReportMeta``) so a metadata-only payload still produces a
    sensible delta.
    """
    sent = _attr(report, "sentiment", None)
    if sent is not None:
        v = _attr(sent, "overall_score", None)
        if v is not None:
            return _safe_float(v)
    return _safe_float(_attr(report, "sentiment_score", 0.0))


def _sentiment_label(report: Any) -> str:
    """Pull the sentiment label — mirrors :func:`_sentiment_score`."""
    sent = _attr(report, "sentiment", None)
    if sent is not None:
        for attr in ("overall_label", "label", "sentiment_label"):
            v = _attr(sent, attr, None)
            if isinstance(v, str) and v:
                return v
    return _safe_str(_attr(report, "sentiment_label", ""), default="")


def _risk_level(report: Any) -> str:
    """Pull the risk level from a duck-typed report."""
    market = _attr(report, "market", None)
    if market is not None:
        v = _attr(market, "risk_level", None)
        if isinstance(v, str) and v:
            return v
    return _safe_str(_attr(report, "risk_level", ""), default="")


def _signals_iter(report: Any) -> Iterable[Any]:
    """Return the iterable of signal-shaped objects on *report*.

    Tries ``report.alpha.signals`` (engine + snapshot) then
    ``report.signals`` (flat dict / stand-in). Always returns an
    iterable — empty list on miss.
    """
    alpha = _attr(report, "alpha", None)
    if alpha is not None:
        sigs = _attr(alpha, "signals", None)
        if sigs is not None:
            return list(sigs)
    sigs = _attr(report, "signals", None)
    if sigs is not None:
        return list(sigs)
    return []


def _routes_iter(report: Any) -> Iterable[Any]:
    """Return the iterable of route-shaped objects on *report*.

    Tries ``report.freight.routes`` (engine + snapshot) then
    ``report.routes`` (flat). Always returns an iterable — empty list
    on miss.
    """
    freight = _attr(report, "freight", None)
    if freight is not None:
        routes = _attr(freight, "routes", None)
        if routes is not None:
            return list(routes)
    routes = _attr(report, "routes", None)
    if routes is not None:
        return list(routes)
    return []


def _schema_version(report: Any) -> str:
    """Pull a schema-version stamp if the payload carries one.

    The engine doesn't stamp one today (we DON'T bump SCHEMA_VERSION as
    part of this feature), but a future payload could. Empty string
    when unknown.
    """
    return _safe_str(_attr(report, "schema_version", ""), default="")


def _generated_at(report: Any) -> str:
    """Pull the generated_at ISO stamp."""
    return _safe_str(_attr(report, "generated_at", ""), default="")


# ---------------------------------------------------------------------------
# Per-category diff helpers
# ---------------------------------------------------------------------------


def diff_report_metadata(meta_a: Any, meta_b: Any) -> list[DiffEntry]:
    """Compare top-level metadata fields and return one entry per change.

    Surfaces:
      * ``generated_at`` — for visibility, not necessarily a "change"
        per se; only flagged when both are non-empty AND differ.
      * ``sentiment_score`` — significant when ``|delta| > 0.05``.
      * ``sentiment_label`` — flagged on any string flip.
      * ``risk_level`` — flagged on any string flip.
      * ``schema_version`` — flagged when both stamps are present and
        differ (or when one is empty and the other isn't).

    The function never raises — missing fields collapse to empty
    strings / zero and simply produce no entry.
    """
    entries: list[DiffEntry] = []

    # generated_at — visibility only (not really a "change", but
    # operators want to see the two timestamps side by side).
    gen_a = _generated_at(meta_a)
    gen_b = _generated_at(meta_b)
    if gen_a and gen_b and gen_a != gen_b:
        entries.append(DiffEntry(
            category=CATEGORY_METADATA,
            change_type=CHANGE_CHANGED,
            key="generated_at",
            before=gen_a,
            after=gen_b,
            description=f"Generated at: {gen_a} -> {gen_b}",
        ))

    # sentiment_score
    score_a = _sentiment_score(meta_a)
    score_b = _sentiment_score(meta_b)
    if _changed_significantly(score_a, score_b, abs_thresh=0.05):
        delta = score_b - score_a
        sign = "+" if delta > 0 else ""
        entries.append(DiffEntry(
            category=CATEGORY_SENTIMENT,
            change_type=CHANGE_CHANGED,
            key="sentiment_score",
            before=round(score_a, 4),
            after=round(score_b, 4),
            description=(
                f"Sentiment score: {score_a:.2f} -> {score_b:.2f} "
                f"({sign}{delta:.2f})"
            ),
        ))

    # sentiment_label — string flip is always significant
    label_a = _sentiment_label(meta_a)
    label_b = _sentiment_label(meta_b)
    if label_a != label_b and (label_a or label_b):
        entries.append(DiffEntry(
            category=CATEGORY_SENTIMENT,
            change_type=CHANGE_CHANGED,
            key="sentiment_label",
            before=label_a,
            after=label_b,
            description=f"Sentiment label: {label_a or '(none)'} -> {label_b or '(none)'}",
        ))

    # risk_level
    risk_a = _risk_level(meta_a)
    risk_b = _risk_level(meta_b)
    if risk_a != risk_b and (risk_a or risk_b):
        entries.append(DiffEntry(
            category=CATEGORY_RISK,
            change_type=CHANGE_CHANGED,
            key="risk_level",
            before=risk_a,
            after=risk_b,
            description=f"Risk level: {risk_a or '(none)'} -> {risk_b or '(none)'}",
        ))

    # schema_version — note when payloads were generated by different
    # schemas so a consumer can decide whether the rest of the diff is
    # apples-to-apples or apples-to-oranges.
    sv_a = _schema_version(meta_a)
    sv_b = _schema_version(meta_b)
    if sv_a != sv_b and (sv_a or sv_b):
        entries.append(DiffEntry(
            category=CATEGORY_METADATA,
            change_type=CHANGE_CHANGED,
            key="schema_version",
            before=sv_a,
            after=sv_b,
            description=(
                f"Schema version differs: {sv_a or '(none)'} -> "
                f"{sv_b or '(none)'}; subsequent entries may not be "
                "directly comparable."
            ),
        ))

    return entries


def diff_signals(
    sigs_a: Iterable[Any],
    sigs_b: Iterable[Any],
    *,
    confidence_thresh: float = 0.10,
) -> list[DiffEntry]:
    """Compare two signal lists and emit one entry per meaningful change.

    Semantics:
      * **Added**: signal name present in B but not A.
      * **Removed**: signal name present in A but not B.
      * **Changed (direction)**: same name in both, but direction
        differs (LONG -> SHORT, NEUTRAL -> LONG, etc.).
      * **Changed (confidence)**: same name, same direction, but
        ``|confidence_b - confidence_a| > confidence_thresh``.

    Signals with no resolvable name (no ``signal_name`` / ``title`` /
    ``name``) are silently skipped — a malformed entry shouldn't show
    up in the diff as a duplicate empty-key row.
    """
    entries: list[DiffEntry] = []

    map_a: dict[str, Any] = {}
    for sig in (sigs_a or []):
        n = _signal_name(sig)
        if n:
            map_a[n] = sig
    map_b: dict[str, Any] = {}
    for sig in (sigs_b or []):
        n = _signal_name(sig)
        if n:
            map_b[n] = sig

    # Added — sorted so output is deterministic across runs.
    for name in sorted(set(map_b) - set(map_a)):
        sig = map_b[name]
        direction = _signal_direction(sig)
        conf = _signal_confidence(sig)
        suffix = f" ({direction}, conf {conf:.2f})" if direction else ""
        entries.append(DiffEntry(
            category=CATEGORY_SIGNAL,
            change_type=CHANGE_ADDED,
            key=name,
            before=None,
            after={"direction": direction, "confidence": round(conf, 4)},
            description=f"New signal: {name}{suffix}",
        ))

    # Removed
    for name in sorted(set(map_a) - set(map_b)):
        sig = map_a[name]
        direction = _signal_direction(sig)
        conf = _signal_confidence(sig)
        suffix = f" ({direction}, conf {conf:.2f})" if direction else ""
        entries.append(DiffEntry(
            category=CATEGORY_SIGNAL,
            change_type=CHANGE_REMOVED,
            key=name,
            before={"direction": direction, "confidence": round(conf, 4)},
            after=None,
            description=f"Removed signal: {name}{suffix}",
        ))

    # Changed — direction flip or confidence shift
    for name in sorted(set(map_a) & set(map_b)):
        sig_a = map_a[name]
        sig_b = map_b[name]
        dir_a = _signal_direction(sig_a)
        dir_b = _signal_direction(sig_b)
        conf_a = _signal_confidence(sig_a)
        conf_b = _signal_confidence(sig_b)

        if dir_a != dir_b and (dir_a or dir_b):
            entries.append(DiffEntry(
                category=CATEGORY_SIGNAL,
                change_type=CHANGE_CHANGED,
                key=name,
                before={"direction": dir_a, "confidence": round(conf_a, 4)},
                after={"direction": dir_b, "confidence": round(conf_b, 4)},
                description=(
                    f"{name}: direction flipped {dir_a or '(none)'} -> "
                    f"{dir_b or '(none)'}"
                ),
            ))
            continue

        if abs(conf_b - conf_a) > confidence_thresh:
            delta = conf_b - conf_a
            sign = "+" if delta > 0 else ""
            entries.append(DiffEntry(
                category=CATEGORY_SIGNAL,
                change_type=CHANGE_CHANGED,
                key=name,
                before={"direction": dir_a, "confidence": round(conf_a, 4)},
                after={"direction": dir_b, "confidence": round(conf_b, 4)},
                description=(
                    f"{name}: confidence {conf_a:.2f} -> {conf_b:.2f} "
                    f"({sign}{delta:.2f})"
                ),
            ))

    return entries


def diff_routes(
    routes_a: Iterable[Any],
    routes_b: Iterable[Any],
    *,
    pct_thresh: float = 5.0,
) -> list[DiffEntry]:
    """Compare two route lists and emit one entry per meaningful change.

    Semantics:
      * **Added**: route key present in B but not A.
      * **Removed**: route key present in A but not B.
      * **Changed (value)**: same key, latest value moved by more than
        ``pct_thresh`` percent (default 5%).
      * **Changed (status)**: same key, status / trend label flipped.

    Both numeric and status changes can fire for the same route — a
    rate jump that also flipped the trend label produces two entries.
    Routes with no resolvable name are silently skipped.
    """
    entries: list[DiffEntry] = []

    map_a: dict[str, Any] = {}
    for r in (routes_a or []):
        n = _route_name(r)
        if n:
            map_a[n] = r
    map_b: dict[str, Any] = {}
    for r in (routes_b or []):
        n = _route_name(r)
        if n:
            map_b[n] = r

    for name in sorted(set(map_b) - set(map_a)):
        r = map_b[name]
        val = _route_value(r)
        status = _route_status(r)
        suffix = f" (status: {status})" if status else ""
        entries.append(DiffEntry(
            category=CATEGORY_ROUTE,
            change_type=CHANGE_ADDED,
            key=name,
            before=None,
            after={"value": round(val, 4), "status": status},
            description=f"New route: {name}{suffix}",
        ))

    for name in sorted(set(map_a) - set(map_b)):
        r = map_a[name]
        val = _route_value(r)
        status = _route_status(r)
        suffix = f" (status: {status})" if status else ""
        entries.append(DiffEntry(
            category=CATEGORY_ROUTE,
            change_type=CHANGE_REMOVED,
            key=name,
            before={"value": round(val, 4), "status": status},
            after=None,
            description=f"Removed route: {name}{suffix}",
        ))

    for name in sorted(set(map_a) & set(map_b)):
        r_a = map_a[name]
        r_b = map_b[name]
        val_a = _route_value(r_a)
        val_b = _route_value(r_b)
        status_a = _route_status(r_a)
        status_b = _route_status(r_b)

        # Numeric change > pct_thresh. Use abs(val_a) so a negative
        # baseline (rare for rates, but defensive) still produces a
        # sane denominator and we never divide by zero.
        if val_a != 0:
            pct = (val_b - val_a) / abs(val_a) * 100.0
        elif val_b != 0:
            # Going from zero to non-zero is by definition a 100%+
            # change — flag it as "changed" so the entry surfaces.
            pct = float("inf")
        else:
            pct = 0.0

        if abs(pct) > pct_thresh:
            if pct == float("inf"):
                pct_str = "zero -> non-zero"
            else:
                sign = "+" if pct > 0 else ""
                pct_str = f"{sign}{pct:.1f}%"
            entries.append(DiffEntry(
                category=CATEGORY_ROUTE,
                change_type=CHANGE_CHANGED,
                key=name,
                before={"value": round(val_a, 4), "status": status_a},
                after={"value": round(val_b, 4), "status": status_b},
                description=(
                    f"{name}: value {val_a:.2f} -> {val_b:.2f} ({pct_str})"
                ),
            ))

        # Status flip — separate entry so a rate move AND a status
        # flip both surface (a consumer can suppress duplicates if it
        # wants, but we shouldn't lose information at the diff layer).
        if status_a != status_b and (status_a or status_b):
            entries.append(DiffEntry(
                category=CATEGORY_ROUTE,
                change_type=CHANGE_CHANGED,
                key=name,
                before={"value": round(val_a, 4), "status": status_a},
                after={"value": round(val_b, 4), "status": status_b},
                description=(
                    f"{name}: status {status_a or '(none)'} -> "
                    f"{status_b or '(none)'}"
                ),
            ))

    return entries


# ---------------------------------------------------------------------------
# Top-level diff
# ---------------------------------------------------------------------------


def _compute_summary(entries: list[DiffEntry]) -> dict:
    """Tally add / remove / change counts off an entry list."""
    summary = {"added": 0, "removed": 0, "changed": 0}
    for e in entries:
        if e.change_type == CHANGE_ADDED:
            summary["added"] += 1
        elif e.change_type == CHANGE_REMOVED:
            summary["removed"] += 1
        elif e.change_type == CHANGE_CHANGED:
            summary["changed"] += 1
    return summary


def diff_reports(
    report_a: Any,
    report_b: Any,
    *,
    report_a_id: str = "",
    report_b_id: str = "",
    confidence_thresh: float = 0.10,
    route_pct_thresh: float = 5.0,
) -> ReportDiff:
    """Compare two report payloads. Returns a structured :class:`ReportDiff`.

    Order:
      1. Metadata (sentiment, risk, schema_version, generated_at)
      2. Signals (added / removed / direction-flipped / confidence)
      3. Routes (added / removed / value moves / status flips)

    Never raises. ``None`` inputs collapse to an empty side — passing
    a ``None`` A with a populated B yields all ``added`` entries; the
    reverse yields all ``removed``.
    """
    if report_a is None and report_b is None:
        diff = ReportDiff(
            report_a_id=report_a_id or "",
            report_b_id=report_b_id or "",
            entries=[],
        )
        diff.summary = _compute_summary(diff.entries)
        return diff

    entries: list[DiffEntry] = []

    try:
        entries.extend(diff_report_metadata(report_a, report_b))
    except Exception:
        pass

    try:
        entries.extend(diff_signals(
            _signals_iter(report_a),
            _signals_iter(report_b),
            confidence_thresh=confidence_thresh,
        ))
    except Exception:
        pass

    try:
        entries.extend(diff_routes(
            _routes_iter(report_a),
            _routes_iter(report_b),
            pct_thresh=route_pct_thresh,
        ))
    except Exception:
        pass

    diff = ReportDiff(
        report_a_id=report_a_id or "",
        report_b_id=report_b_id or "",
        entries=entries,
        summary={},
    )
    diff.summary = _compute_summary(diff.entries)
    return diff


# ---------------------------------------------------------------------------
# Renderers — markdown + HTML
# ---------------------------------------------------------------------------


def _group_entries_by_category(entries: list[DiffEntry]) -> dict[str, list[DiffEntry]]:
    """Bucket entries by category, preserving insertion order."""
    grouped: dict[str, list[DiffEntry]] = {
        CATEGORY_METADATA: [],
        CATEGORY_SENTIMENT: [],
        CATEGORY_RISK: [],
        CATEGORY_SIGNAL: [],
        CATEGORY_ROUTE: [],
    }
    for e in entries:
        grouped.setdefault(e.category, []).append(e)
    return grouped


_CATEGORY_LABELS = {
    CATEGORY_METADATA: "Metadata",
    CATEGORY_SENTIMENT: "Sentiment",
    CATEGORY_RISK: "Risk",
    CATEGORY_SIGNAL: "Signals",
    CATEGORY_ROUTE: "Routes",
}


def render_diff_markdown(diff: ReportDiff) -> str:
    """Render *diff* as Markdown with one section per category.

    The output is utf-8 safe — descriptions can contain accented
    characters / emoji / quotes without escaping. The Markdown is
    deliberately plain (no HTML tags) so it round-trips through
    Slack / GitHub / Notion identically.

    Returns a string; never raises. An empty diff yields a one-line
    "no changes" notice rather than an empty string so a download
    button always has content.
    """
    a = diff.report_a_id or "(unknown)"
    b = diff.report_b_id or "(unknown)"
    summary = diff.summary or _compute_summary(diff.entries)

    lines: list[str] = []
    lines.append(f"# Report Diff: {a} -> {b}")
    lines.append("")
    lines.append(
        f"**Summary:** {summary.get('added', 0)} added, "
        f"{summary.get('removed', 0)} removed, "
        f"{summary.get('changed', 0)} changed"
    )
    lines.append("")

    if not diff.entries:
        lines.append("_No meaningful differences detected between the two reports._")
        return "\n".join(lines)

    grouped = _group_entries_by_category(diff.entries)
    for cat in (
        CATEGORY_METADATA, CATEGORY_SENTIMENT, CATEGORY_RISK,
        CATEGORY_SIGNAL, CATEGORY_ROUTE,
    ):
        bucket = grouped.get(cat) or []
        if not bucket:
            continue
        label = _CATEGORY_LABELS.get(cat, cat.title())
        lines.append(f"## {label}")
        lines.append("")
        for e in bucket:
            badge = e.change_type.upper()
            lines.append(f"- **[{badge}]** {e.description}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# Colour palette for HTML rendering — kept consistent with the rest of
# the app's WSJ-ish steel-blue / muted-neutral identity. Inline so the
# diff snippet has no external CSS dependency.
_HTML_COLOR_ADDED = "#2e9e6e"
_HTML_COLOR_REMOVED = "#c0392b"
_HTML_COLOR_CHANGED = "#c9962b"
_HTML_COLOR_TEXT = "#e8e6e1"
_HTML_COLOR_MUTED = "#6b6760"
_HTML_COLOR_BORDER = "rgba(232,230,225,0.10)"


def _change_color(change_type: str) -> str:
    return {
        CHANGE_ADDED: _HTML_COLOR_ADDED,
        CHANGE_REMOVED: _HTML_COLOR_REMOVED,
        CHANGE_CHANGED: _HTML_COLOR_CHANGED,
    }.get(change_type, _HTML_COLOR_MUTED)


def render_diff_html(diff: ReportDiff) -> str:
    """Render *diff* as an HTML snippet suitable for embedding in the UI.

    Security: every user-supplied string (report ids, descriptions,
    keys) passes through :func:`html.escape` BEFORE concatenation, so
    a signal title scraped from a news feed containing
    ``<script>alert(1)</script>`` renders as inert text.

    Returns a string; never raises. An empty diff yields a tidy
    one-line "no changes" panel so the UI doesn't render an empty box.
    """
    a = html.escape(diff.report_a_id or "(unknown)")
    b = html.escape(diff.report_b_id or "(unknown)")
    summary = diff.summary or _compute_summary(diff.entries)

    header = (
        f'<div style="font-family:Libre Baskerville,Georgia,serif;'
        f'font-size:1rem;color:{_HTML_COLOR_TEXT};font-weight:600;'
        f'margin:6px 0 10px 0">'
        f'Report diff: {a} &rarr; {b}'
        f'</div>'
    )
    summary_line = (
        f'<div style="color:{_HTML_COLOR_MUTED};font-size:0.78rem;'
        f'margin-bottom:14px">'
        f'<span style="color:{_HTML_COLOR_ADDED};font-weight:600">'
        f'+{summary.get("added", 0)} added</span> &middot; '
        f'<span style="color:{_HTML_COLOR_REMOVED};font-weight:600">'
        f'-{summary.get("removed", 0)} removed</span> &middot; '
        f'<span style="color:{_HTML_COLOR_CHANGED};font-weight:600">'
        f'~{summary.get("changed", 0)} changed</span>'
        f'</div>'
    )

    if not diff.entries:
        body = (
            f'<div style="color:{_HTML_COLOR_MUTED};font-size:0.85rem">'
            f'No meaningful differences detected.</div>'
        )
        return _wrap_panel(header + summary_line + body)

    grouped = _group_entries_by_category(diff.entries)
    sections: list[str] = []
    for cat in (
        CATEGORY_METADATA, CATEGORY_SENTIMENT, CATEGORY_RISK,
        CATEGORY_SIGNAL, CATEGORY_ROUTE,
    ):
        bucket = grouped.get(cat) or []
        if not bucket:
            continue
        label = html.escape(_CATEGORY_LABELS.get(cat, cat.title()))
        rows: list[str] = []
        for e in bucket:
            color = _change_color(e.change_type)
            badge = html.escape(e.change_type.upper())
            # description carries the bulk of the info; escape it
            # ALL so a malicious signal title in the source data
            # cannot inject markup. The badge already comes from a
            # constant string but is escaped belt-and-braces.
            desc = html.escape(e.description)
            rows.append(
                f'<li style="margin-bottom:6px;font-size:0.83rem;'
                f'color:{_HTML_COLOR_TEXT}">'
                f'<span style="display:inline-block;min-width:80px;'
                f'color:{color};font-weight:600;font-size:0.72rem;'
                f'letter-spacing:0.06em">{badge}</span>'
                f'{desc}'
                f'</li>'
            )
        sections.append(
            f'<div style="margin-top:12px">'
            f'<div style="color:{_HTML_COLOR_MUTED};font-size:0.72rem;'
            f'text-transform:uppercase;letter-spacing:0.08em;'
            f'margin-bottom:6px">{label}</div>'
            f'<ul style="list-style:none;padding-left:0;margin:0">'
            f'{"".join(rows)}'
            f'</ul>'
            f'</div>'
        )

    return _wrap_panel(header + summary_line + "".join(sections))


def _wrap_panel(inner_html: str) -> str:
    """Wrap *inner_html* in the standard diff-panel container."""
    return (
        f'<div style="background:rgba(53,114,176,0.06);'
        f'border-left:3px solid #3572b0;border-radius:3px;'
        f'padding:14px 18px;margin:12px 0;'
        f'border:1px solid {_HTML_COLOR_BORDER};border-left-width:3px">'
        f'{inner_html}'
        f'</div>'
    )


# ---------------------------------------------------------------------------
# Loader — synthesise a payload object from ReportMeta + snapshot
# ---------------------------------------------------------------------------


@dataclass
class _LoadedReportPayload:
    """Duck-typed payload built from ReportMeta + closest matching snapshot.

    Exposes ALL attribute paths the diff functions read so a
    ``diff_reports(payload_a, payload_b)`` call works against the
    same shape as the real :class:`InvestorReport`. Fields default
    to safe blanks so a metadata-only load (no snapshot available)
    still produces a sane partial diff (metadata + sentiment + risk).
    """

    report_id: str
    generated_at: str = ""
    report_date: str = ""
    sentiment_score: float = 0.0
    sentiment_label: str = ""
    risk_level: str = ""
    signals: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    schema_version: str = ""

    @property
    def sentiment(self) -> "_SentimentBag":
        return _SentimentBag(
            overall_score=self.sentiment_score,
            overall_label=self.sentiment_label,
        )

    @property
    def market(self) -> "_MarketBag":
        return _MarketBag(risk_level=self.risk_level)

    @property
    def alpha(self) -> "_AlphaBag":
        return _AlphaBag(signals=self.signals)

    @property
    def freight(self) -> "_FreightBag":
        return _FreightBag(routes=self.routes)


@dataclass
class _SentimentBag:
    overall_score: float = 0.0
    overall_label: str = ""


@dataclass
class _MarketBag:
    risk_level: str = ""


@dataclass
class _AlphaBag:
    signals: list = field(default_factory=list)


@dataclass
class _FreightBag:
    routes: list = field(default_factory=list)


def load_report_payload(
    report_id: str,
    *,
    user_id: str | None = None,
    snapshot_match_window_seconds: float = 60.0,
) -> _LoadedReportPayload | None:
    """Build a diff-ready payload for *report_id* in the caller's scope.

    Loads the :class:`ReportMeta` row via ``list_reports`` (so per-user
    scoping is honoured — crossing scope returns ``None`` exactly like
    an unknown id, no permission-denied leak). Then looks for the
    closest matching :class:`ReportSnapshot` within
    ``snapshot_match_window_seconds`` of ``meta.generated_at`` and
    layers its signals + routes onto the metadata.

    When no snapshot is found, the payload still carries metadata
    (sentiment / risk / sentiment_label) so the diff has SOMETHING to
    surface — the entries list will only contain metadata / sentiment
    / risk rows, never signal or route rows.

    Returns ``None`` only when the report id is unknown in the
    caller's scope. Every other failure (snapshot load, DB blip)
    degrades to "metadata only" silently. Never raises.
    """
    try:
        from utils.report_history import list_reports
    except Exception:
        return None

    try:
        rows = list_reports(user_id=user_id)
    except Exception:
        return None

    target = next((r for r in (rows or []) if r.report_id == report_id), None)
    if target is None:
        return None

    payload = _LoadedReportPayload(
        report_id=target.report_id,
        generated_at=_safe_str(getattr(target, "generated_at", ""), default=""),
        report_date=_safe_str(getattr(target, "report_date", ""), default=""),
        sentiment_score=_safe_float(getattr(target, "sentiment_score", 0.0)),
        sentiment_label=_safe_str(getattr(target, "sentiment_label", ""), default=""),
        risk_level=_safe_str(getattr(target, "risk_level", ""), default=""),
    )

    # Best-effort snapshot pull. Match by closest generated_at within
    # ``snapshot_match_window_seconds``; snapshots stamped in the same
    # save_report() call will be within milliseconds, so the default
    # 60s window is generous. If no snapshot is within range, we
    # leave signals + routes empty.
    try:
        snap = _closest_snapshot(
            payload.generated_at,
            user_id=user_id,
            match_window_seconds=snapshot_match_window_seconds,
        )
        if snap is not None:
            payload.signals = list(getattr(snap, "signals", None) or [])
            payload.routes = list(getattr(snap, "routes", None) or [])
            # If the meta sentiment was zero / blank (older rows
            # sometimes lack it), fall back to the snapshot's stamp.
            if not payload.sentiment_score:
                payload.sentiment_score = _safe_float(
                    getattr(snap, "sentiment_overall_score", 0.0),
                )
            if not payload.sentiment_label:
                payload.sentiment_label = _safe_str(
                    getattr(snap, "sentiment_label", ""), default="",
                )
            if not payload.risk_level:
                payload.risk_level = _safe_str(
                    getattr(snap, "risk_level", ""), default="",
                )
    except Exception:
        # Snapshot integration is best-effort — silent fallback to
        # metadata-only is the correct behaviour.
        pass

    return payload


def _closest_snapshot(
    generated_at_iso: str,
    *,
    user_id: str | None,
    match_window_seconds: float,
) -> Any:
    """Return the snapshot whose ``generated_at`` is closest to *target*.

    Loads up to 50 recent snapshots in the caller's scope, computes the
    absolute time delta to ``generated_at_iso``, and returns the one
    inside ``match_window_seconds``. ``None`` when no snapshot
    qualifies, or when anything goes wrong (no raise).
    """
    if not generated_at_iso:
        return None
    try:
        from datetime import datetime, timezone

        from processing.report_snapshot import load_latest_snapshots

        try:
            target = datetime.fromisoformat(generated_at_iso)
        except ValueError:
            return None
        if target.tzinfo is None:
            target = target.replace(tzinfo=timezone.utc)

        snaps = load_latest_snapshots(n=50, user_id=user_id or "")
        best = None
        best_delta = None
        for s in snaps or []:
            ts = _safe_str(getattr(s, "generated_at", ""), default="")
            if not ts:
                continue
            try:
                cand = datetime.fromisoformat(ts)
            except ValueError:
                continue
            if cand.tzinfo is None:
                cand = cand.replace(tzinfo=timezone.utc)
            delta = abs((cand - target).total_seconds())
            if best_delta is None or delta < best_delta:
                best = s
                best_delta = delta
        if best is not None and best_delta is not None and best_delta <= match_window_seconds:
            return best
        return None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Convenience: ReportDiff -> JSON-friendly dict (used by API + CLI)
# ---------------------------------------------------------------------------


def diff_to_dict(diff: ReportDiff) -> dict:
    """Serialise a :class:`ReportDiff` to a plain JSON-friendly dict.

    The CLI uses this for ``--format json`` and the API endpoint uses
    it for the response body. We avoid :func:`dataclasses.asdict` here
    because the entries' ``before`` / ``after`` payloads may contain
    nested dicts that asdict mishandles (mutates) — straight dict
    construction is safer + more explicit.
    """
    return {
        "report_a_id": diff.report_a_id,
        "report_b_id": diff.report_b_id,
        "summary": dict(diff.summary or {}),
        "entries": [
            {
                "category": e.category,
                "change_type": e.change_type,
                "key": e.key,
                "before": e.before,
                "after": e.after,
                "description": e.description,
            }
            for e in (diff.entries or [])
        ],
    }
