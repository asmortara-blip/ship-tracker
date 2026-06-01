"""Replay historical alerts to a delivery channel without consuming budget.

An operator who just rewired a Slack webhook, swapped a PagerDuty key, or
added an email recipient wants to verify the channel works against REAL
alerts they remember (last week's BDI spike, last month's port shutdown)
— not the synthetic ``send_test_ping`` payload. This module re-dispatches
an existing :class:`ShippingAlert` from the historical store to one
:class:`DeliveryChannel`, with three deliberate differences from the
production fire path:

1. The dispatched title is prefixed with ``"[REPLAY] "`` so the recipient
   immediately sees this is a re-send, not a live event.
2. The per-channel monthly delivery budget is NEITHER checked NOR
   incremented — replay traffic is operator-driven test traffic and must
   not exhaust the cap that's there to silence noisy real fires.
3. The dispatch is recorded in the audit log as ``action='alert_replay'``
   (carrying ``{alert_id, channel_id, success}``) so a security review
   can distinguish replays from real fires + from synthetic test pings.

Per-user scoping
----------------
Both the alert_id AND the channel_id must belong to the caller's
``user_id``. We rely on the dual-set scoping already enforced by
``load_alerts`` and ``load_channels``: bob cannot replay alice's alert
because alice's exclusive rows never appear in ``load_alerts(user_id=
'bob')`` to begin with. The replay function fails closed —
``ReplayResult(success=False, message="alert not found or not owned")``
— when either id misses the user's scope. This mirrors the security
posture of ``acknowledge_alert``: a cross-user attempt produces the
same observable outcome as querying an unknown id, so an attacker
cannot enumerate other users' ids by probing.

Never raises
------------
Every public helper is wrapped in ``try/except Exception`` and surfaces
failures via the ``ReplayResult.message`` field. The caller can iterate
over many replays without one transient outage breaking the whole batch.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from loguru import logger

from engine.alert_engine_v2 import ShippingAlert, load_alerts
from engine.alert_delivery import (
    DeliveryChannel,
    _dispatch_alert,
    load_channels,
)


# ─────────────────────────────────────────────────────────────────────────────
#  Constants
# ─────────────────────────────────────────────────────────────────────────────

# Prefix injected into the dispatched alert's title (and surfaced via the
# channel-specific formatters: format_slack_payload, format_email_payload,
# format_sms_payload, format_webhook_payload, format_discord_payload,
# format_pagerduty_payload — they all key off ShippingAlert.title). The
# leading bracket pair makes this visually distinct from "[TEST]" which
# ``send_test_ping`` uses; an operator scanning Slack history can tell at
# a glance whether a message was a synthetic test, a real fire, or a
# replay.
REPLAY_TITLE_PREFIX: str = "[REPLAY] "

# Default upper bound on the bulk-by-filter convenience helper. Holds the
# blast radius if an operator forgets to narrow their filter — replaying
# every alert in a 30-day window to PagerDuty would be a very bad day.
# The hard cap is enforced even when the caller passes a larger value
# (see ``replay_alerts_by_filter``).
DEFAULT_REPLAY_LIMIT: int = 50
MAX_REPLAY_LIMIT: int = 200


# ─────────────────────────────────────────────────────────────────────────────
#  Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ReplayResult:
    """Outcome of a single ``replay_alert`` call.

    ``success`` is ``True`` iff the underlying ``deliver_alert`` /
    ``_dispatch_alert`` returned ``DeliveryResult.success=True`` AND no
    pre-flight check (alert lookup, channel lookup, per-user scoping)
    rejected the request. ``message`` is the human-readable summary —
    ``'delivered'`` on the happy path, an error string otherwise. Callers
    iterating over a bulk replay can sum truthiness on the ``success``
    field for a quick "12/15 succeeded" headline.
    """

    alert_id: str
    channel_id: str
    success: bool
    message: str


# ─────────────────────────────────────────────────────────────────────────────
#  Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _result(alert_id: str, channel_id: str, success: bool, message: str) -> ReplayResult:
    """Construct a ``ReplayResult`` with the standard field shape.

    Tiny helper that lets the call sites read as one-liners — the
    explicit kwargs version of the dataclass constructor would dominate
    the line width on every return statement in ``replay_alert``.
    """
    return ReplayResult(
        alert_id=alert_id or "",
        channel_id=channel_id or "",
        success=bool(success),
        message=message or "",
    )


def _find_alert(alert_id: str, *, user_id: str) -> Optional[ShippingAlert]:
    """Return the user-scoped ``ShippingAlert`` for ``alert_id``, or None.

    Goes through ``load_alerts(user_id=user_id)`` so the dual-set
    scoping is enforced exactly as everywhere else in the codebase:
    the user sees their own rows + legacy ``user_id=''`` rows. A
    cross-user probe (bob asking for alice's exclusive alert) returns
    ``None`` — same observable outcome as an unknown id, so callers
    cannot enumerate by probing.

    Uses a 30-day lookback as a balance between "remembering last
    month's port shutdown" (the headline use case) and not scanning the
    whole ``alerts`` table for every probe. Operators that need a
    longer window can extend the lookback via the filter helper.
    """
    try:
        # 90 days picks up "last month's disruption" comfortably without
        # blowing past the implicit _MAX_STORED=500 retention cap.
        for a in load_alerts(max_age_days=90, user_id=user_id):
            if a.alert_id == alert_id:
                return a
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"alert_replay._find_alert: load_alerts failed: {exc}")
        return None
    return None


def _find_channel(channel_id: str, *, user_id: str) -> Optional[DeliveryChannel]:
    """Return the user-scoped ``DeliveryChannel`` for ``channel_id``, or None.

    Same dual-set scoping pattern as ``_find_alert``: bob cannot replay
    to alice's exclusive channel because alice's row never appears in
    ``load_channels(user_id='bob')``. Legacy ``user_id=''`` channels
    remain visible to authenticated users so pre-multi-user data does
    not vanish on first login (matches the rest of the codebase).
    """
    try:
        for c in load_channels(user_id=user_id):
            if c.channel_id == channel_id:
                return c
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"alert_replay._find_channel: load_channels failed: {exc}")
        return None
    return None


def _with_replay_prefix(alert: ShippingAlert) -> ShippingAlert:
    """Return a shallow copy of ``alert`` whose title carries the
    ``REPLAY_TITLE_PREFIX``.

    ``copy.copy`` is sufficient — every field on ``ShippingAlert`` is
    a primitive (str / float / bool), so a shallow copy is a full
    duplicate for the purposes of the downstream payload formatters.
    The ORIGINAL alert in the database is NOT mutated — replay must not
    re-write the historical title.

    If the title already starts with the prefix (e.g. the operator is
    replaying an already-prefixed payload from a previous round of
    testing), we leave it alone — double-prefix ``[REPLAY] [REPLAY] …``
    is needless noise and might cause an alert-router downstream to
    treat the two prefixes differently.
    """
    out = copy.copy(alert)
    title = out.title or ""
    if not title.startswith(REPLAY_TITLE_PREFIX):
        out.title = f"{REPLAY_TITLE_PREFIX}{title}"
    return out


def _record_replay_audit(
    *,
    alert_id: str,
    channel_id: str,
    success: bool,
    message: str,
    user_id: str,
) -> None:
    """Best-effort audit hook. NEVER raises.

    Writes one ``action='alert_replay'`` row carrying the alert_id +
    channel_id + outcome in the ``detail`` payload. We do NOT log the
    channel's target (webhook URL / email / phone number) — it's the
    secret we are protecting and matches the redaction policy already
    enforced in ``save_channel`` + ``send_test_ping``.

    The audit write is the ONLY persistent side-effect of a replay (no
    alert insert, no kv_state bump) — it's the differentiator that lets
    a security review tell a replay apart from a real fire and a
    ``send_test_ping``.
    """
    try:
        from auth.audit import record_audit

        record_audit(
            "alert_replay",
            entity_type="alert",
            entity_id=alert_id or "",
            detail={
                "alert_id":   alert_id or "",
                "channel_id": channel_id or "",
                "success":    bool(success),
                "message":    message or "",
            },
            user_id=user_id,
        )
    except Exception:  # noqa: BLE001 — audit must never break replay
        pass


# ─────────────────────────────────────────────────────────────────────────────
#  Public API
# ─────────────────────────────────────────────────────────────────────────────

def replay_alert(alert_id: str, channel_id: str, *, user_id: str) -> ReplayResult:
    """Re-dispatch one historical alert to one delivery channel. NEVER raises.

    Workflow:
      1. Look up the alert by ``alert_id``, scoped to ``user_id``. A
         miss (unknown id OR cross-user) returns
         ``ReplayResult(success=False, message='alert not found or not owned')``.
      2. Look up the channel by ``channel_id``, scoped to ``user_id``.
         Same failure shape on miss.
      3. Build a shallow copy of the alert with ``"[REPLAY] "`` prefixed
         on the title. The original DB row is unchanged.
      4. Dispatch via ``_dispatch_alert`` (NOT ``deliver_alert``) so
         the per-channel monthly budget check + bump are bypassed.
         Replay traffic must not consume operator-visible budget.
      5. Record an audit row with ``action='alert_replay'`` carrying
         the outcome.

    Returns:
        :class:`ReplayResult` — never raises, even when the downstream
        dispatch helper itself raises. A monkeypatched ``deliver_alert``
        that throws an exception (an unrealistic but possible scenario)
        collapses to ``success=False`` with the exception text in
        ``message``.

    Per-user scoping is the security boundary — see the module docstring
    for the threat model. The caller MUST pass ``user_id`` explicitly;
    we do NOT fall back to ``current_user_id()`` from a Streamlit
    session here because every call site (UI button handler, CLI
    subcommand) already has the user id in hand and the explicit
    parameter makes the auth check unmissable in a security review.
    """
    aid = alert_id or ""
    cid = channel_id or ""
    uid = user_id or ""

    # ── Look up the alert + verify per-user ownership ────────────────────
    alert = _find_alert(aid, user_id=uid)
    if alert is None:
        msg = "alert not found or not owned"
        _record_replay_audit(
            alert_id=aid, channel_id=cid,
            success=False, message=msg, user_id=uid,
        )
        return _result(aid, cid, False, msg)

    # ── Look up the channel + verify per-user ownership ──────────────────
    channel = _find_channel(cid, user_id=uid)
    if channel is None:
        msg = "channel not found or not owned"
        _record_replay_audit(
            alert_id=aid, channel_id=cid,
            success=False, message=msg, user_id=uid,
        )
        return _result(aid, cid, False, msg)

    # ── Dispatch with [REPLAY] prefix, bypassing the budget gate ─────────
    # We call ``_dispatch_alert`` directly (rather than ``deliver_alert``)
    # because ``deliver_alert`` runs the per-channel monthly budget check
    # + post-success increment. Replay traffic should not consume budget.
    # ``_dispatch_alert`` still honours channel.kind dispatch correctly —
    # see its docstring; the only thing it skips is the budget envelope
    # that wraps it inside ``deliver_alert``.
    #
    # Note: ``_dispatch_alert`` does NOT check ``channel.enabled`` or
    # ``severity_threshold`` or quiet-hours — those are upstream of it in
    # ``deliver_alert``. For replay we WANT to bypass enabled/severity/
    # quiet checks because the whole point is to test the wire even
    # against a channel an operator has intentionally muted (matches the
    # ``send_test_ping`` philosophy: "I want to see the wire light up
    # regardless of the channel's normal gating").
    replay_alert_obj = _with_replay_prefix(alert)
    try:
        result = _dispatch_alert(channel, replay_alert_obj)
    except Exception as exc:  # noqa: BLE001 — never raise out of replay
        msg = f"dispatch error: {exc}"
        _record_replay_audit(
            alert_id=aid, channel_id=cid,
            success=False, message=msg, user_id=uid,
        )
        return _result(aid, cid, False, msg)

    success = bool(getattr(result, "success", False))
    if success:
        message = "delivered"
    else:
        message = getattr(result, "error_msg", "") or "delivery failed"

    _record_replay_audit(
        alert_id=aid, channel_id=cid,
        success=success, message=message, user_id=uid,
    )
    return _result(aid, cid, success, message)


def replay_alerts(
    alert_ids: Sequence[str],
    channel_id: str,
    *,
    user_id: str,
) -> list[ReplayResult]:
    """Bulk replay — one :class:`ReplayResult` per id in ``alert_ids``.

    Does NOT stop at the first failure: every id is attempted, and a
    failure on id #3 does not prevent ids #4 and onwards from being
    dispatched. This matches the philosophy of the existing
    ``deliver_pending`` batch path — the caller wants a per-id report
    of "what happened to each one", not a single binary success/fail
    for the whole batch.

    Order is preserved — the returned list aligns positionally with the
    input ``alert_ids``. Duplicate ids in the input are replayed twice
    (and produce two results). An empty input returns an empty list
    without touching the network or the audit log.

    NEVER raises. A pathological input where the iteration itself
    raises (e.g. ``alert_ids`` is None passed in untyped) collapses to
    an empty list with a logger.warning.
    """
    try:
        ids = list(alert_ids or [])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"alert_replay.replay_alerts: input iteration failed: {exc}")
        return []
    if not ids:
        return []
    return [replay_alert(aid, channel_id, user_id=user_id) for aid in ids]


def replay_alerts_by_filter(
    *,
    channel_id: str,
    user_id: str,
    severity: Optional[str] = None,
    alert_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = DEFAULT_REPLAY_LIMIT,
) -> list[ReplayResult]:
    """Filter the user's alerts, then replay every match to ``channel_id``.

    Filters (all optional, ANDed together):
      * ``severity`` — one of ``'CRITICAL'``, ``'HIGH'``, ``'MEDIUM'``,
        ``'LOW'``. Exact match (case-sensitive — matches the rest of the
        codebase). ``None`` = no severity filter.
      * ``alert_type`` — exact match against ``ShippingAlert.alert_type``
        (e.g. ``'BDI_MOVE'``, ``'CONGESTION'``). ``None`` = no filter.
      * ``since`` / ``until`` — ISO-8601 UTC strings (e.g.
        ``'2026-05-01T00:00:00+00:00'``). Filters on ``created_at``.
        ``None`` on either side = no bound on that side. Strings that
        fail comparison are tolerated (the malformed bound is treated
        as "no filter").
      * ``limit`` — caps the number of alerts replayed. Defaults to
        :data:`DEFAULT_REPLAY_LIMIT` (50); values exceeding
        :data:`MAX_REPLAY_LIMIT` (200) are silently clamped down so a
        typo can't accidentally fire a 1000-alert blast. ``limit <= 0``
        returns the empty list (matches "no work requested").

    Per-user scoping is enforced by ``load_alerts(user_id=user_id)`` —
    a user only sees their own alerts + legacy ``user_id=''`` rows.

    Returns the per-alert :class:`ReplayResult` list, in the same order
    ``load_alerts`` returned them (newest first). NEVER raises.
    """
    # Normalise limit before doing any DB work so a degenerate input
    # short-circuits immediately.
    try:
        n = int(limit)
    except (TypeError, ValueError):
        n = DEFAULT_REPLAY_LIMIT
    if n <= 0:
        return []
    n = min(n, MAX_REPLAY_LIMIT)

    # Load everything in the user's scope, then filter in Python — the
    # alerts table has _MAX_STORED=500 rows max so this is cheap and
    # avoids hand-rolling a SQL builder that has to match the dual-set
    # scoping semantics from state.user_scope.
    try:
        alerts = load_alerts(max_age_days=90, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            f"alert_replay.replay_alerts_by_filter: load_alerts failed: {exc}"
        )
        return []

    def _matches(a: ShippingAlert) -> bool:
        try:
            if severity is not None and a.severity != severity:
                return False
            if alert_type is not None and a.alert_type != alert_type:
                return False
            if since is not None and (a.created_at or "") < since:
                return False
            if until is not None and (a.created_at or "") > until:
                return False
        except Exception:  # noqa: BLE001 — never raise from filter
            return False
        return True

    filtered = [a for a in alerts if _matches(a)][:n]
    if not filtered:
        return []
    return [replay_alert(a.alert_id, channel_id, user_id=user_id) for a in filtered]


# ─────────────────────────────────────────────────────────────────────────────
#  Time helpers (re-exported for the CLI's --since "7d" parsing)
# ─────────────────────────────────────────────────────────────────────────────

def parse_relative_since(spec: str) -> Optional[str]:
    """Convert an operator-friendly ``"7d"`` / ``"24h"`` / ``"30m"`` spec
    into an ISO-8601 UTC timestamp suitable for the ``since`` parameter
    of :func:`replay_alerts_by_filter`.

    Accepted suffixes:
      * ``d`` — days
      * ``h`` — hours
      * ``m`` — minutes

    Returns ``None`` for any unparseable input (empty string, missing
    suffix, non-numeric prefix, negative duration). The CLI surfaces the
    ``None`` as "ignoring malformed --since" rather than aborting — we
    want operators to be able to mistype and retry without losing other
    arguments.

    The ISO string is returned in UTC with a ``+00:00`` offset so it
    sorts lexicographically against ``ShippingAlert.created_at`` (also
    a UTC ISO string).
    """
    if not isinstance(spec, str) or not spec:
        return None
    s = spec.strip().lower()
    if not s or len(s) < 2:
        return None
    suffix = s[-1]
    if suffix not in ("d", "h", "m"):
        return None
    try:
        n = int(s[:-1])
    except ValueError:
        return None
    if n <= 0:
        return None
    from datetime import timedelta
    if suffix == "d":
        delta = timedelta(days=n)
    elif suffix == "h":
        delta = timedelta(hours=n)
    else:
        delta = timedelta(minutes=n)
    return (datetime.now(timezone.utc) - delta).isoformat()
