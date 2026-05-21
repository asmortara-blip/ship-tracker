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

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
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
    report_id: str          # UUID
    generated_at: str       # ISO timestamp (UTC)
    report_date: str        # human-readable date shown in the report
    sentiment_label: str    # BULLISH / BEARISH / NEUTRAL / MIXED
    sentiment_score: float  # -1.0 to +1.0
    risk_level: str         # LOW / MODERATE / HIGH / CRITICAL
    signal_count: int       # number of alpha signals included
    data_quality: str       # FULL / PARTIAL / DEGRADED
    file_path: str          # absolute path to the stored HTML file
    file_size_kb: float     # file size in kilobytes


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _row_to_meta(row) -> ReportMeta:
    """Map a sqlite3.Row from the report_history table to a ReportMeta."""
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
    )


def save_report(html_content: str, report_obj: "Any") -> ReportMeta | None:
    """Persist *html_content* to disk and insert a row in report_history.

    Args:
        html_content: Fully rendered HTML string.
        report_obj:   An InvestorReport (or DailyDigest-compatible) instance
                      whose attributes supply the metadata fields.

    Returns:
        A populated ReportMeta on success, or None if saving fails.
    """
    try:
        from state.db import get_connection

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

        conn = get_connection()
        with conn:
            conn.execute(
                """
                INSERT INTO report_history
                  (report_id, generated_at, report_date, sentiment_label,
                   sentiment_score, risk_level, signal_count, data_quality,
                   file_path, file_size_kb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (meta.report_id, meta.generated_at, meta.report_date,
                 meta.sentiment_label, meta.sentiment_score, meta.risk_level,
                 meta.signal_count, meta.data_quality, meta.file_path,
                 meta.file_size_kb),
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


def list_reports() -> list[ReportMeta]:
    """Return all saved reports sorted newest-first, skipping missing files.

    If any rows reference files that have been deleted outside this module,
    those rows are removed from the SQLite index before returning.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT * FROM report_history ORDER BY generated_at DESC"
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


def load_report_html(report_id: str) -> str | None:
    """Read and return the HTML for the given report_id.

    Returns:
        HTML string, or None if the report is not found or unreadable.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT file_path FROM report_history WHERE report_id = ?",
            (report_id,),
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


def delete_report(report_id: str) -> bool:
    """Remove a report from the index and delete its file from disk.

    Returns:
        True if the report was found and removed; False otherwise.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT file_path FROM report_history WHERE report_id = ?",
            (report_id,),
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
        return True
    except Exception as exc:
        logger.error(f"delete_report failed for {report_id}: {exc}")
        return False


def get_report_stats() -> dict:
    """Return aggregate statistics across all saved reports.

    Returns a dict with keys:
        total_reports, total_size_mb, oldest_date, newest_date,
        avg_sentiment_score, sentiment_distribution
    """
    try:
        entries = list_reports()  # already filtered + sorted newest-first
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
