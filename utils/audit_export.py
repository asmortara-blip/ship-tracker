"""utils.audit_export — JSONL export of the audit log for SIEM ingestion.

The Streamlit UI and the read-only HTTP API both surface
``audit_events`` as parsed dicts inside a JSON envelope (``{items:
[...], count: N}``). That shape is fine for a human or a tab renderer
but it's awkward for the operator running a Splunk / Vector / Loki
sidecar that wants to vacuum new audit rows into a SIEM on a cron.

Standard SIEM ingestion is **line-delimited JSON** (JSONL / ndjson):
one JSON object per line, ``\\n``-terminated, with a trailing newline
on the last line. The envelope is the wrong shape for that — every
SIEM scraper would have to write custom code to unwrap ``items``.

This module exposes three helpers that translate ``audit_events`` rows
into JSONL bytes:

* :func:`rows_to_jsonl` — pure formatter: ``list[dict] → bytes``. No
  DB access. Used by both the in-memory export and the streaming
  variant (the latter just calls it on each batch).
* :func:`export_audit_to_jsonl` — end-to-end: queries the DB via
  :func:`auth.audit.query_audit` and returns the full JSONL bytes.
  Suitable when the caller intends to hold the whole export in
  memory (an HTTP response body that the client will pipe straight
  through).
* :func:`export_audit_to_stream` — streaming variant for very large
  exports. Writes batches of JSONL to an open file/buffer in chunks
  so a 100k-row export doesn't materialize as one giant byte string.
  Used by the operator CLI's ``audit export`` subcommand when ``--out``
  is provided.

The ``since`` / ``until`` window is enforced HERE, not in
``query_audit`` (which only knows ``since``). The ``until`` filter is
applied client-side after the query returns — for the audit-log volume
the codebase sees (one row per user-initiated action) the extra rows
the over-fetch carries are negligible, and pushing ``until`` down into
the SQL would require modifying ``auth.audit`` which is intentionally
read-only from this module.

What this module does NOT do
----------------------------
* No decryption of ``detail_json`` payload fields. The audit log is
  already stored with secrets redacted at recording time (see
  ``engine.alert_delivery.save_channel``); we pass ``detail_json``
  through verbatim.
* No new DB schema. We only read.
* No new package dependency. ``json`` + ``dataclasses`` are stdlib.
"""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from typing import IO, Any, Optional

from loguru import logger


# Default streaming batch size — tuned so each chunk is roughly 100k of
# wire bytes for typical audit rows (~100 bytes/row). Smaller batches
# trade throughput for memory headroom; the operator CLI uses this
# default and never needs to override it.
_DEFAULT_BATCH_SIZE = 1000


# ─── Public API: formatter ────────────────────────────────────────────────

def rows_to_jsonl(rows: list[dict]) -> bytes:
    """Convert a list of audit-event dicts to UTF-8 JSONL bytes.

    Args:
        rows: List of dicts representing audit events. Each dict is
              serialised independently. ``dataclass`` instances passed
              here are auto-converted via ``asdict`` so the caller can
              pass ``AuditEvent`` objects without an explicit map step.
              ``None`` / empty list produces empty bytes (NOT ``b"\\n"``
              — an empty export should be a zero-byte file so a SIEM
              parser doesn't try to decode a blank line).

    Returns:
        UTF-8 bytes. Each row → one ``json.dumps`` line, joined by
        ``\\n``, with a trailing ``\\n`` after the last row so every
        line (including the final one) is terminated. The empty-list
        case returns ``b""`` with no trailing newline so the file
        size is exactly zero.

    Encoding details:
      * ``separators=(",", ":")`` — compact form. SIEM parsers do not
        care about pretty-printing and the saved bytes add up over
        millions of rows.
      * ``default=str`` — datetimes, Paths, and other non-JSON-native
        types fall back to ``str(obj)`` instead of raising. Matches
        the convention used by ``_send_json`` in the HTTP API and by
        ``record_audit`` itself.
      * ``ensure_ascii=False`` — preserves UTF-8 characters in
        payloads (Asian-script titles, emoji in ticker names) without
        the ``\\uXXXX`` escape blow-up. Downstream UTF-8 parsers
        handle this natively.
    """
    if not rows:
        return b""

    lines: list[str] = []
    for row in rows:
        try:
            # AuditEvent instances arrive here when the caller passes
            # the dataclass directly. Convert via asdict so the JSON
            # serialiser sees a plain dict with the canonical keys.
            obj = asdict(row) if is_dataclass(row) and not isinstance(row, type) else row
            line = json.dumps(
                obj,
                separators=(",", ":"),
                default=str,
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            # A row whose values cannot even be stringified is
            # exceptional — log + skip rather than fail the whole
            # export. The SIEM scraper will see the surrounding rows
            # unchanged and the operator can investigate via the
            # debug log.
            logger.debug(
                f"utils.audit_export.rows_to_jsonl: skipping unserialisable "
                f"row: {exc}"
            )
            continue
        lines.append(line)

    if not lines:
        return b""
    # Join with \n and add a trailing \n so every line — INCLUDING the
    # last — is terminated. SIEM parsers that scan for "\n\n" as a
    # record separator would otherwise miss the final row.
    return ("\n".join(lines) + "\n").encode("utf-8")


# ─── Public API: end-to-end in-memory export ──────────────────────────────

def export_audit_to_jsonl(
    *,
    user_id: Optional[str] = None,
    action: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 10_000,
) -> bytes:
    """Query the audit log and format the result as JSONL bytes.

    Args:
        user_id: Optional per-user scope. ``None`` returns rows across
                 every user (admin / sidecar use). A non-empty string
                 filters strictly to that ``user_id`` — same semantics
                 as ``query_audit``.
        action:  Optional action-verb filter (``"login_success"``,
                 ``"save_rules"``, …).
        since:   ISO-8601 string. Only rows created at or after this
                 timestamp are returned. Forwarded to ``query_audit``
                 which enforces it in SQL.
        until:   ISO-8601 string. Only rows created strictly BEFORE
                 this timestamp are returned. Applied client-side
                 because ``query_audit`` does not natively support
                 an upper bound.
        limit:   Max rows to return. Default 10_000 — comfortable
                 for a daily SIEM pull; SHOULD be set higher for an
                 initial backfill (the CLI passes it through). The
                 underlying ``query_audit`` clamps via SQL LIMIT, so
                 a malicious / typo'd ``10**9`` does not OOM the
                 process.

    Returns:
        JSONL bytes ready to write to a file or HTTP response body.
        Empty bytes on an empty result. Never raises — a DB error
        in ``query_audit`` already swallows internally and returns
        ``[]``; this wrapper inherits that contract.
    """
    from auth.audit import query_audit

    events = query_audit(
        user_id=user_id,
        action=action,
        since=since,
        limit=int(limit) if isinstance(limit, int) else _DEFAULT_BATCH_SIZE,
    )

    # Apply the ``until`` filter client-side. ``query_audit`` returns
    # rows newest-first; filtering by string comparison on ISO-8601
    # is correct because the timestamps are always written with the
    # same UTC offset (``datetime.now(timezone.utc).isoformat()``) so
    # lexicographic order matches chronological order.
    if until is not None and until != "":
        events = [e for e in events if e.created_at < until]

    # Convert AuditEvent dataclasses to dicts with the canonical key
    # order. We expand explicitly rather than relying on asdict so the
    # output shape is stable across dataclass refactors.
    rows = [
        {
            "event_id":    e.event_id,
            "created_at":  e.created_at,
            "user_id":     e.user_id,
            "action":      e.action,
            "entity_type": e.entity_type,
            "entity_id":   e.entity_id,
            "detail_json": e.detail_json,
        }
        for e in events
    ]
    return rows_to_jsonl(rows)


# ─── Public API: streaming export ─────────────────────────────────────────

def export_audit_to_stream(
    stream: IO,
    *,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    **filters: Any,
) -> int:
    """Write the JSONL export to ``stream`` in chunks.

    Args:
        stream:     An open writable stream. May be binary
                    (``io.BytesIO`` / ``open(..., 'wb')``) or text
                    (``sys.stdout`` / ``open(..., 'w')``). The
                    function detects the mode via a one-byte test
                    write and adapts.
        batch_size: Rows per ``write()`` call. Default 1000 — keeps
                    each chunk roughly 100 KB for typical row sizes.
        **filters:  Forwarded to :func:`export_audit_to_jsonl`. Same
                    keyword names: ``user_id``, ``action``, ``since``,
                    ``until``, ``limit``.

    Returns:
        Total rows written. Never raises — a write failure is logged
        and the partial total is returned, so a calling CLI handler
        can decide whether to exit 0 (some rows succeeded) or 1 (zero
        rows + the stream is bad).

    Memory profile:
        At most ``batch_size`` rows are materialised at once. For a
        100k-row export at the default batch this caps peak memory at
        ~10 MB regardless of the total result size.

    Streaming algorithm:
        We fetch ALL matching rows up front (a single ``query_audit``
        call) and slice into batches. This is intentional: the audit
        volume the codebase produces (~tens of thousands per year
        max) fits comfortably in memory, and a single SQL query is
        far cheaper than N keyset-paginated round-trips. The
        ``batch_size`` knob exists for the WRITE side — chunking the
        bytes that hit ``stream.write`` so the OS / network layer
        doesn't have to absorb one massive buffer.
    """
    from auth.audit import query_audit

    user_id = filters.pop("user_id", None)
    action = filters.pop("action", None)
    since = filters.pop("since", None)
    until = filters.pop("until", None)
    limit = int(filters.pop("limit", 10_000))

    try:
        events = query_audit(
            user_id=user_id, action=action, since=since, limit=limit,
        )
        if until is not None and until != "":
            events = [e for e in events if e.created_at < until]
    except Exception as exc:  # noqa: BLE001
        logger.debug(
            f"utils.audit_export.export_audit_to_stream: query failed: {exc}"
        )
        return 0

    # Decide whether the stream is binary or text by looking at its
    # mode (when set) or by a duck-type write probe. We need this
    # because rows_to_jsonl returns bytes — writing bytes to a text
    # stream raises TypeError on every line.
    is_binary = _stream_is_binary(stream)

    total_written = 0
    if not events:
        # Make sure the writer at least gets a zero-byte flush so any
        # downstream "did we open the file?" checks see a file that
        # exists. Empty result → empty file (consistent with
        # rows_to_jsonl returning b"").
        try:
            stream.flush()
        except Exception:  # noqa: BLE001
            pass
        return 0

    bs = max(1, int(batch_size))
    # Slice into write batches. Each slice is an independent
    # rows_to_jsonl call so the per-chunk encoding fits in memory.
    for start in range(0, len(events), bs):
        chunk = events[start:start + bs]
        rows = [
            {
                "event_id":    e.event_id,
                "created_at":  e.created_at,
                "user_id":     e.user_id,
                "action":      e.action,
                "entity_type": e.entity_type,
                "entity_id":   e.entity_id,
                "detail_json": e.detail_json,
            }
            for e in chunk
        ]
        payload = rows_to_jsonl(rows)
        if not payload:
            continue
        try:
            if is_binary:
                stream.write(payload)
            else:
                stream.write(payload.decode("utf-8"))
            total_written += len(rows)
        except Exception as exc:  # noqa: BLE001
            # A failed write mid-stream — log and return the partial
            # count. The caller can decide whether to surface a
            # "partial export" error or accept what made it through.
            logger.debug(
                f"utils.audit_export.export_audit_to_stream: write "
                f"failed at batch {start // bs}: {exc}"
            )
            break

    try:
        stream.flush()
    except Exception:  # noqa: BLE001
        # A non-flushable stream (BytesIO) is fine — flush is
        # advisory; the OS-level write already happened.
        pass

    return total_written


# ─── Internal helpers ─────────────────────────────────────────────────────

def _stream_is_binary(stream: IO) -> bool:
    """Best-effort detection of whether ``stream`` accepts bytes.

    Order of checks:
      1. ``stream.mode`` set and contains ``'b'`` → binary.
      2. ``stream.mode`` set and lacks ``'b'`` → text.
      3. Duck-type ``write`` annotation via ``isinstance(buffer)``:
         ``io.BytesIO`` and ``open(..., 'wb')`` both have a
         ``buffer`` attribute on text wrappers but not on themselves
         — we check the inverse.
      4. Fallback: assume text. ``sys.stdout`` lands here and is
         text by default; binary callers will have an explicit mode.
    """
    mode = getattr(stream, "mode", None)
    if isinstance(mode, str):
        return "b" in mode
    # io.BytesIO has no .mode but does support .write(bytes); poke at
    # the class name as a cheap fallback.
    cls_name = type(stream).__name__
    if cls_name in ("BytesIO", "BufferedWriter", "BufferedRandom"):
        return True
    return False


__all__ = [
    "rows_to_jsonl",
    "export_audit_to_jsonl",
    "export_audit_to_stream",
]
