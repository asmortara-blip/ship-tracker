"""Report history — save, load, list, and delete generated investor reports.

Reports are stored as HTML files under cache/reports/. Metadata for every
saved report lives in the SQLite ``report_history`` table (see ``state.db``).
The legacy ``report_index.json`` is no longer written; on first migration
its contents are imported into SQLite and the JSON file is left in place
as a safety net.

The module is intentionally crash-proof: every public function catches all
exceptions and returns a sensible default (empty list, None, False, {}).
"""
from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from typing import Any


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_DIR: Path = Path(__file__).parent.parent / "cache" / "reports"
MAX_REPORTS: int = 30  # keep the last N reports on disk

# Legacy path — kept so state.migrations can find it on first run. Production
# reads/writes go through SQLite.
_INDEX_FILE: Path = REPORT_DIR / "report_index.json"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ReportMeta:
    report_id: str               # UUID
    generated_at: str            # ISO timestamp (UTC)
    report_date: str             # human-readable date shown in the report
    sentiment_label: str         # BULLISH / BEARISH / NEUTRAL / MIXED
    sentiment_score: float       # -1.0 to +1.0
    risk_level: str              # LOW / MODERATE / HIGH / CRITICAL
    signal_count: int            # number of alpha signals included
    data_quality: str            # FULL / PARTIAL / DEGRADED
    file_path: str               # absolute path to the stored HTML file
    file_size_kb: float          # file size in kilobytes
    public_slug: str = ""        # URL-safe base64url token; "" = not shared
    public_expires_at: str = ""  # ISO-8601 UTC; "" or past time = invalid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _row_to_meta(row) -> ReportMeta:
    """Map a sqlite3.Row from the report_history table to a ReportMeta.

    Tolerates rows missing the v5 public-share columns — older databases
    that haven't run the v5 migration yet, or row factories that strip
    columns — by defaulting both fields to the empty string."""
    # sqlite3.Row does not implement .get(); reach for the column by key
    # and fall back if it does not exist on the row.
    try:
        public_slug = row["public_slug"] or ""
    except (IndexError, KeyError):
        public_slug = ""
    try:
        public_expires_at = row["public_expires_at"] or ""
    except (IndexError, KeyError):
        public_expires_at = ""
    return ReportMeta(
        report_id=row["report_id"],
        generated_at=row["generated_at"],
        report_date=row["report_date"],
        sentiment_label=row["sentiment_label"],
        sentiment_score=float(row["sentiment_score"]),
        risk_level=row["risk_level"],
        signal_count=int(row["signal_count"]),
        data_quality=row["data_quality"],
        file_path=row["file_path"],
        file_size_kb=float(row["file_size_kb"]),
        public_slug=public_slug,
        public_expires_at=public_expires_at,
    )


def save_report(html_content: str, report_obj: "Any") -> ReportMeta | None:
    """Persist *html_content* to disk and insert a row in report_history.

    Args:
        html_content: Fully rendered HTML string.
        report_obj:   An InvestorReport (or DailyDigest-compatible) instance
                      whose attributes supply the metadata fields.

    Returns:
        A populated ReportMeta on success, or None if saving fails.

    The row is stamped with the active user's id (resolved via
    ``state.user_scope.current_user_id``) so subsequent
    ``list_reports(user_id=...)`` calls can filter on it. Outside a
    Streamlit session the stamp is ``""`` and the report joins the
    legacy global bucket.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id

        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp_str}_{report_id[:8]}.html"
        file_path = REPORT_DIR / filename

        # Write HTML
        file_path.write_text(html_content, encoding="utf-8")
        file_size_kb = round(file_path.stat().st_size / 1024, 2)

        meta = ReportMeta(
            report_id=report_id,
            generated_at=now.isoformat(),
            report_date=_attr(report_obj, "date", now.strftime("%B %d, %Y")),
            sentiment_label=_attr(report_obj, "market_sentiment", "NEUTRAL"),
            sentiment_score=_safe_float(_attr(report_obj, "sentiment_score", 0.0)),
            risk_level=_attr(report_obj, "risk_level", "MODERATE"),
            signal_count=_safe_int(_attr(report_obj, "signal_count", 0)),
            data_quality=_attr(report_obj, "data_quality", "PARTIAL"),
            file_path=str(file_path.resolve()),
            file_size_kb=file_size_kb,
        )

        uid = current_user_id()
        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO report_history
                  (report_id, generated_at, report_date, sentiment_label,
                   sentiment_score, risk_level, signal_count, data_quality,
                   file_path, file_size_kb, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (meta.report_id, meta.generated_at, meta.report_date,
                 meta.sentiment_label, meta.sentiment_score, meta.risk_level,
                 meta.signal_count, meta.data_quality, meta.file_path,
                 meta.file_size_kb, uid),
            )

        # Prune any rows over MAX_REPORTS, oldest first. Also delete the
        # pruned files from disk.
        _prune_old_reports()

        logger.info(
            f"Report saved: {filename} "
            f"({file_size_kb:.1f} KB, {meta.sentiment_label}, {meta.risk_level})"
        )
        return meta

    except Exception as exc:
        logger.error(f"save_report failed: {exc}")
        return None


def list_reports(*, user_id: str | None = None) -> list[ReportMeta]:
    """Return all saved reports sorted newest-first, skipping missing files.

    If any rows reference files that have been deleted outside this module,
    those rows are removed from the SQLite index before returning.

    Honours per-user scoping with dual-set semantics — when ``user_id``
    resolves to a non-empty string, rows belonging to that user PLUS
    legacy ``user_id=''`` rows are returned. The empty-string case
    returns every row (legacy behaviour).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        rows = conn.execute(
            f"SELECT * FROM report_history WHERE 1=1 {scope_sql} "
            f"ORDER BY generated_at DESC",
            scope_params,
        ).fetchall()
        valid: list[ReportMeta] = []
        stale_ids: list[str] = []
        for r in rows:
            meta = _row_to_meta(r)
            if Path(meta.file_path).exists():
                valid.append(meta)
            else:
                stale_ids.append(meta.report_id)

        if stale_ids:
            with conn:
                conn.executemany(
                    "DELETE FROM report_history WHERE report_id = ?",
                    [(rid,) for rid in stale_ids],
                )
        return valid
    except Exception as exc:
        logger.error(f"list_reports failed: {exc}")
        return []


def load_report_html(report_id: str, *, user_id: str | None = None) -> str | None:
    """Read and return the HTML for the given report_id.

    Honours per-user scoping: a user can only read reports in their
    own scope (their own rows + legacy ``user_id=''`` rows). Crossing
    scope returns ``None`` exactly like an unknown report_id — no
    distinguishable "permission denied" leak. Public-link reads go
    through :func:`load_public_report` instead and bypass scoping by
    design.

    Returns:
        HTML string, or None if the report is not found or unreadable.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT file_path FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"load_report_html: report_id not found: {report_id}")
            return None
        path = Path(row["file_path"])
        if not path.exists():
            logger.warning(f"load_report_html: file missing for {report_id}")
            return None
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error(f"load_report_html failed for {report_id}: {exc}")
        return None


def delete_report(report_id: str, *, user_id: str | None = None) -> bool:
    """Remove a report from the index and delete its file from disk.

    Honours per-user scoping the same way as ``load_report_html`` —
    crossing scope returns ``False`` indistinguishably from an unknown
    report_id, and no row is deleted.

    Returns:
        True if the report was found and removed; False otherwise.
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT file_path FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"delete_report: report_id not found: {report_id}")
            return False

        with conn:
            conn.execute(
                "DELETE FROM report_history WHERE report_id = ?",
                (report_id,),
            )

        path = Path(row["file_path"])
        if path.exists():
            try:
                path.unlink()
                logger.info(f"Deleted report file: {path.name}")
            except Exception as exc:
                logger.warning(f"Could not unlink {path}: {exc}")
        # Audit-log the deletion. Placed AFTER the row was successfully
        # removed so we only emit an event when the delete actually
        # happened — the early-return path on "report not found" does
        # not fire this hook.
        try:
            from auth.audit import record_audit
            record_audit(
                "delete_report",
                entity_type="report",
                entity_id=report_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:
        logger.error(f"delete_report failed for {report_id}: {exc}")
        return False


def make_public(report_id: str, expires_in_days: int = 30, *, user_id: str | None = None) -> str | None:
    """Generate a public-share slug for *report_id* and persist it.

    The slug is a URL-safe base64url token from
    ``secrets.token_urlsafe(12)`` (16-char string) — short enough to be
    pasted into a chat or email, long enough to be unguessable, and
    carrying no information about the internal ``report_id`` or
    ``file_path``. The expiry is stored as an ISO-8601 UTC timestamp
    and enforced on read by :func:`load_public_report`.

    Args:
        report_id:        The internal UUID of the report to share.
        expires_in_days:  Lifetime of the link in days. Must be > 0.

    Returns:
        The newly-generated slug on success, or ``None`` if the report
        cannot be found, ``expires_in_days`` is non-positive, or
        anything else goes wrong (this function never raises).
    """
    try:
        if expires_in_days <= 0:
            return None

        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        # Scope the lookup so only the report's owner (or a legacy
        # report under the empty-string user_id) can publish it. A
        # non-owner gets the same None return as an unknown report_id.
        row = conn.execute(
            f"SELECT report_id FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"make_public: report_id not found: {report_id}")
            return None

        slug = secrets.token_urlsafe(12)
        expires_at = (
            datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        ).isoformat()

        with conn:
            conn.execute(
                """
                UPDATE report_history
                   SET public_slug = ?, public_expires_at = ?
                 WHERE report_id = ?
                """,
                (slug, expires_at, report_id),
            )
        # Audit-log the share-link generation. The slug itself is NOT
        # logged — a stolen audit row should not give an attacker the
        # working URL. expires_in_days is the only payload field; it's
        # what a security review actually wants to see ("user shared
        # report X with a 30-day link").
        try:
            from auth.audit import record_audit
            record_audit(
                "make_public",
                entity_type="report",
                entity_id=report_id,
                detail={"expires_in_days": expires_in_days},
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return slug
    except Exception as exc:
        logger.error(f"make_public failed for {report_id}: {exc}")
        return None


def revoke_public(report_id: str, *, user_id: str | None = None) -> bool:
    """Clear the public-share slug + expiry for *report_id*.

    After revoke, any prior call to :func:`load_public_report` with the
    old slug returns ``None`` (slug not found).

    Honours per-user scoping — only the report's owner (or the legacy
    ``user_id=''`` owner) can revoke its public link. A non-owner gets
    the same ``False`` return as an unknown report_id.

    Returns:
        True if a row was updated, False if the report_id is unknown or
        anything else goes wrong (this function never raises).
    """
    try:
        from state.db import get_connection
        from state.user_scope import current_user_id, scope_filter_sql

        uid = current_user_id() if user_id is None else user_id
        scope_sql, scope_params = scope_filter_sql(uid)

        conn = get_connection()
        row = conn.execute(
            f"SELECT report_id FROM report_history WHERE report_id = ? "
            f"{scope_sql}",
            (report_id, *scope_params),
        ).fetchone()
        if row is None:
            logger.debug(f"revoke_public: report_id not found: {report_id}")
            return False

        with conn:
            conn.execute(
                """
                UPDATE report_history
                   SET public_slug = '', public_expires_at = ''
                 WHERE report_id = ?
                """,
                (report_id,),
            )
        # Audit-log the revoke. Same pattern as make_public —
        # entity_type 'report' so a user can see "I shared X then
        # revoked X" in chronological order during a review.
        try:
            from auth.audit import record_audit
            record_audit(
                "revoke_public",
                entity_type="report",
                entity_id=report_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception as exc:
        logger.error(f"revoke_public failed for {report_id}: {exc}")
        return False


def load_public_report(slug: str) -> str | None:
    """Return the HTML for the report identified by *slug*, if the link is valid.

    Validity requires all of:
      * ``slug`` is non-empty.
      * A ``report_history`` row exists with that ``public_slug``.
      * ``public_expires_at`` parses as a future ISO-8601 UTC timestamp.
      * The on-disk HTML file referenced by ``file_path`` still exists.

    Returns:
        The HTML string on success, or ``None`` for any failure
        condition (slug unknown / empty / expired / file deleted /
        unreadable). Never raises.
    """
    try:
        if not slug:
            return None

        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            """
            SELECT file_path, public_slug, public_expires_at
              FROM report_history
             WHERE public_slug = ?
            """,
            (slug,),
        ).fetchone()
        if row is None:
            logger.debug(f"load_public_report: slug not found: {slug}")
            return None

        # Defensive — a row where the slug column happened to be empty
        # would have been matched by ``WHERE public_slug = ''``. Treat
        # that as "not shared" and refuse.
        if not row["public_slug"]:
            return None

        expires_at_raw = row["public_expires_at"] or ""
        try:
            expires_at = datetime.fromisoformat(expires_at_raw)
        except ValueError:
            logger.debug(
                f"load_public_report: bad expires_at for slug {slug}: "
                f"{expires_at_raw!r}"
            )
            return None
        # Normalise to UTC-aware so we can compare against a
        # timezone-aware "now" without raising.
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            logger.debug(f"load_public_report: slug expired: {slug}")
            return None

        path = Path(row["file_path"])
        if not path.exists():
            logger.debug(f"load_public_report: file missing for slug {slug}")
            return None
        return path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.error(f"load_public_report failed for slug {slug!r}: {exc}")
        return None


def get_report_stats(*, user_id: str | None = None) -> dict:
    """Return aggregate statistics across all saved reports.

    Honours per-user scoping via the underlying ``list_reports`` call —
    when ``user_id`` is non-empty, the stats only cover that user's
    rows plus legacy ``user_id=''`` rows.

    Returns a dict with keys:
        total_reports, total_size_mb, oldest_date, newest_date,
        avg_sentiment_score, sentiment_distribution
    """
    try:
        # Forward the scope parameter so the stats match whatever
        # list_reports would have surfaced for the same user.
        entries = list_reports(user_id=user_id)  # already filtered + sorted newest-first
        if not entries:
            return {
                "total_reports": 0,
                "total_size_mb": 0.0,
                "oldest_date": None,
                "newest_date": None,
                "avg_sentiment_score": 0.0,
                "sentiment_distribution": {},
            }

        total_size_mb = round(sum(e.file_size_kb for e in entries) / 1024, 3)
        dates = sorted(e.generated_at for e in entries)
        avg_score = round(
            sum(e.sentiment_score for e in entries) / len(entries), 4
        )
        distribution: dict[str, int] = {}
        for e in entries:
            distribution[e.sentiment_label] = (
                distribution.get(e.sentiment_label, 0) + 1
            )

        return {
            "total_reports": len(entries),
            "total_size_mb": total_size_mb,
            "oldest_date": dates[0],
            "newest_date": dates[-1],
            "avg_sentiment_score": avg_score,
            "sentiment_distribution": distribution,
        }
    except Exception as exc:
        logger.error(f"get_report_stats failed: {exc}")
        return {
            "total_reports": 0,
            "total_size_mb": 0.0,
            "oldest_date": None,
            "newest_date": None,
            "avg_sentiment_score": 0.0,
            "sentiment_distribution": {},
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prune_old_reports() -> None:
    """Keep only the MAX_REPORTS most recent rows; delete pruned files."""
    try:
        from state.db import get_connection

        conn = get_connection()
        # Find rows beyond the MAX_REPORTS newest
        rows = conn.execute(
            """
            SELECT report_id, file_path FROM report_history
            ORDER BY generated_at DESC
            LIMIT -1 OFFSET ?
            """,
            (MAX_REPORTS,),
        ).fetchall()

        if not rows:
            return

        with conn:
            conn.executemany(
                "DELETE FROM report_history WHERE report_id = ?",
                [(r["report_id"],) for r in rows],
            )

        # Delete pruned files from disk (best-effort).
        for r in rows:
            try:
                path = Path(r["file_path"])
                if path.exists():
                    path.unlink()
                    logger.debug(f"Pruned old report: {path.name}")
            except Exception as exc:
                logger.warning(f"Could not delete pruned report {r['file_path']}: {exc}")
    except Exception as exc:
        logger.error(f"_prune_old_reports failed: {exc}")


# ---------------------------------------------------------------------------
# Attribute extraction helpers
# ---------------------------------------------------------------------------

def _attr(obj: object, name: str, default: "Any") -> "Any":
    """Return getattr(obj, name, default), also checking dict-style access."""
    try:
        if hasattr(obj, name):
            val = getattr(obj, name)
            return val if val is not None else default
        if isinstance(obj, dict):
            return obj.get(name, default)
    except Exception:
        pass
    return default


def _safe_float(val: "Any", default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: "Any", default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default
