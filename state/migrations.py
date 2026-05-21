"""One-time migration helpers for the SQLite state layer.

When the SQLite database is created for the first time, ``state.db._init_schema``
calls ``migrate_legacy_json_files()`` to import any existing JSON-file
persistence into the new tables. The legacy JSON files are NOT deleted —
they remain on disk as a safety net during the transition.

Each migration function:
  - Is idempotent (running it twice has no extra effect — INSERT OR
    IGNORE / INSERT OR REPLACE).
  - Catches its own exceptions and logs them rather than raising, so a
    malformed legacy file doesn't block the schema initialization.

This module imports lazily from ``engine.alert_engine_v2`` and
``utils.report_history`` for their legacy path constants, so it must
not be imported at module-load time of those modules (avoiding cycles).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Iterable

from loguru import logger


_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def migrate_legacy_json_files(conn: sqlite3.Connection) -> None:
    """Best-effort import of legacy JSON persistence into the SQLite DB.

    Idempotent — uses INSERT OR IGNORE so re-running on the same DB
    (which shouldn't happen, but defensively) does not duplicate rows."""
    _migrate_alerts_json(conn)
    _migrate_rules_json(conn)
    _migrate_report_history_index(conn)


# ─── Alerts ────────────────────────────────────────────────────────────────

_ALERTS_JSON = _PROJECT_ROOT / "cache" / "alerts" / "alerts.json"


def _migrate_alerts_json(conn: sqlite3.Connection) -> None:
    if not _ALERTS_JSON.exists():
        return
    try:
        with _ALERTS_JSON.open("r", encoding="utf-8") as fh:
            records = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_ALERTS_JSON}: {exc}")
        return
    if not isinstance(records, list):
        return

    rows = []
    for rec in records:
        if not isinstance(rec, dict) or "alert_id" not in rec:
            continue
        rows.append((
            rec.get("alert_id"),
            rec.get("created_at", ""),
            rec.get("alert_type", "MACRO"),
            rec.get("severity", "LOW"),
            rec.get("title", ""),
            rec.get("body", ""),
            rec.get("ticker", "") or "",
            rec.get("route_id", "") or "",
            rec.get("port_locode", "") or "",
            float(rec.get("value", 0.0) or 0.0),
            float(rec.get("threshold", 0.0) or 0.0),
            float(rec.get("change_pct", 0.0) or 0.0),
            1 if rec.get("acknowledged") else 0,
        ))

    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO alerts
              (alert_id, created_at, alert_type, severity, title, body,
               ticker, route_id, port_locode, value, threshold, change_pct,
               acknowledged)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info(f"state.migrations: imported {len(rows)} alerts from {_ALERTS_JSON}")


# ─── Alert rules ──────────────────────────────────────────────────────────

_RULES_JSON = _PROJECT_ROOT / "cache" / "alerts" / "rules.json"


def _migrate_rules_json(conn: sqlite3.Connection) -> None:
    if not _RULES_JSON.exists():
        return
    try:
        with _RULES_JSON.open("r", encoding="utf-8") as fh:
            rules = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_RULES_JSON}: {exc}")
        return
    if not isinstance(rules, list):
        return

    rows = []
    for r in rules:
        if not isinstance(r, dict):
            continue
        rule_id = r.get("rule_id") or r.get("id")
        if not rule_id:
            continue
        rows.append((str(rule_id), json.dumps(r, default=str)))

    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO alert_rules (rule_id, data) VALUES (?, ?)",
            rows,
        )
        logger.info(f"state.migrations: imported {len(rows)} alert rules from {_RULES_JSON}")


# ─── Report history index ─────────────────────────────────────────────────

_REPORT_INDEX_JSON = _PROJECT_ROOT / "cache" / "reports" / "report_index.json"


def _migrate_report_history_index(conn: sqlite3.Connection) -> None:
    if not _REPORT_INDEX_JSON.exists():
        return
    try:
        with _REPORT_INDEX_JSON.open("r", encoding="utf-8") as fh:
            records = json.load(fh)
    except Exception as exc:
        logger.warning(f"state.migrations: failed to read {_REPORT_INDEX_JSON}: {exc}")
        return
    if not isinstance(records, list):
        return

    rows = []
    for rec in records:
        if not isinstance(rec, dict) or "report_id" not in rec:
            continue
        rows.append((
            rec.get("report_id"),
            rec.get("generated_at", ""),
            rec.get("report_date", "") or "",
            rec.get("sentiment_label", "") or "",
            float(rec.get("sentiment_score", 0.0) or 0.0),
            rec.get("risk_level", "") or "",
            int(rec.get("signal_count", 0) or 0),
            rec.get("data_quality", "") or "",
            rec.get("file_path", ""),
            float(rec.get("file_size_kb", 0.0) or 0.0),
        ))

    if rows:
        conn.executemany(
            """
            INSERT OR IGNORE INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        logger.info(
            f"state.migrations: imported {len(rows)} reports from {_REPORT_INDEX_JSON}"
        )
