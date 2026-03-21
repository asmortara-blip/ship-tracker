"""Report history — save, load, list, and delete generated investor reports.

Reports are stored as HTML files under cache/reports/.  A JSON sidecar index
(report_index.json) tracks metadata for every saved report so the UI can
display history without re-reading every HTML file.

The module is intentionally crash-proof: every public function catches all
exceptions and returns a sensible default (empty list, None, False, {}).
"""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass
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

def save_report(html_content: str, report_obj: "Any") -> ReportMeta | None:
    """Persist *html_content* to disk and update the report index.

    Args:
        html_content: Fully rendered HTML string.
        report_obj:   An InvestorReport (or DailyDigest-compatible) instance
                      whose attributes supply the metadata fields.

    Returns:
        A populated ReportMeta on success, or None if saving fails.
    """
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)

        report_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"report_{timestamp_str}_{report_id[:8]}.html"
        file_path = REPORT_DIR / filename

        # Write HTML
        file_path.write_text(html_content, encoding="utf-8")
        file_size_kb = round(file_path.stat().st_size / 1024, 2)

        # Extract metadata from report_obj with safe fallbacks
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

        # Update persistent index
        existing = _load_index()
        existing.append(meta)
        existing = _prune_old_reports(existing)
        _save_index(existing)

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

    Returns:
        List of ReportMeta; empty list on any error.
    """
    try:
        entries = _load_index()
        # Filter entries whose HTML file has been deleted outside this module
        valid = [e for e in entries if Path(e.file_path).exists()]
        if len(valid) != len(entries):
            # Persist the cleaned index
            _save_index(valid)
        return sorted(valid, key=lambda m: m.generated_at, reverse=True)
    except Exception as exc:
        logger.error(f"list_reports failed: {exc}")
        return []


def load_report_html(report_id: str) -> str | None:
    """Read and return the HTML for the given report_id.

    Returns:
        HTML string, or None if the report is not found or unreadable.
    """
    try:
        entries = _load_index()
        for meta in entries:
            if meta.report_id == report_id:
                path = Path(meta.file_path)
                if not path.exists():
                    logger.warning(f"load_report_html: file missing for {report_id}")
                    return None
                return path.read_text(encoding="utf-8")
        logger.debug(f"load_report_html: report_id not found: {report_id}")
        return None
    except Exception as exc:
        logger.error(f"load_report_html failed for {report_id}: {exc}")
        return None


def delete_report(report_id: str) -> bool:
    """Remove a report from the index and delete its file from disk.

    Returns:
        True if the report was found and removed; False otherwise.
    """
    try:
        entries = _load_index()
        to_delete = [e for e in entries if e.report_id == report_id]
        if not to_delete:
            logger.debug(f"delete_report: report_id not found: {report_id}")
            return False

        remaining = [e for e in entries if e.report_id != report_id]

        for meta in to_delete:
            path = Path(meta.file_path)
            if path.exists():
                path.unlink()
                logger.info(f"Deleted report file: {path.name}")

        _save_index(remaining)
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
        entries = list_reports()  # already filtered and sorted newest-first
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

def _prune_old_reports(meta_list: list[ReportMeta]) -> list[ReportMeta]:
    """Keep only the MAX_REPORTS most recent entries; delete pruned files."""
    if len(meta_list) <= MAX_REPORTS:
        return meta_list

    # Sort newest-first so we can slice off the tail
    sorted_list = sorted(meta_list, key=lambda m: m.generated_at, reverse=True)
    keep = sorted_list[:MAX_REPORTS]
    prune = sorted_list[MAX_REPORTS:]

    for meta in prune:
        try:
            path = Path(meta.file_path)
            if path.exists():
                path.unlink()
                logger.debug(f"Pruned old report: {path.name}")
        except Exception as exc:
            logger.warning(f"Could not delete pruned report {meta.file_path}: {exc}")

    return keep


def _save_index(meta_list: list[ReportMeta]) -> None:
    """Serialize the metadata list to report_index.json."""
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        payload = [asdict(m) for m in meta_list]
        _INDEX_FILE.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.error(f"_save_index failed: {exc}")


def _load_index() -> list[ReportMeta]:
    """Deserialize report_index.json into a list of ReportMeta.

    Returns an empty list if the file is absent or corrupted.
    """
    try:
        if not _INDEX_FILE.exists():
            return []
        raw = _INDEX_FILE.read_text(encoding="utf-8")
        payload = json.loads(raw)
        result: list[ReportMeta] = []
        for item in payload:
            try:
                result.append(ReportMeta(**item))
            except Exception as item_exc:
                logger.warning(f"Skipping malformed index entry: {item_exc}")
        return result
    except Exception as exc:
        logger.error(f"_load_index failed: {exc}")
        return []


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
