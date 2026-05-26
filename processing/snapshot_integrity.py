"""processing/snapshot_integrity.py — port-supply snapshot integrity checker.

Confirms that each daily snapshot directory under
``cache/port_supply_snapshots/<YYYY-MM-DD>/`` contains the files the
pipeline is expected to persist + that those files parse cleanly. Built
for two operators:

  * **CI gating** — a freshly-saved snapshot that's missing the per-port
    summary or has a truncated body should fail the build before it
    poisons downstream diffs / dashboards.
  * **Ops monitoring** — a daily cron sweep over every retained snapshot
    catches silent corruption (disk full, hand-edit gone wrong, partial
    write from a crashed worker) so the operator finds out from a
    dashboard rather than from a stale digest.

The expected-files list adapts to what
``processing.port_supply_history`` actually writes today: the per-port
summary CSV (authoritative) plus the regional-rollup CSV (cross-section
shipped alongside since 2026-05). The checker reads the file-naming
helpers directly off the history module so when that module ships a new
artifact, this checker picks it up by changing only one constant here.

Defensive contract — a corrupted file produces a clean warning string in
``parse_warnings``; it never raises out of the checker. Operators care
about WHICH file is broken, not about the worker pool dying.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional


__all__ = [
    "SnapshotIntegrityReport",
    "expected_filenames",
    "check_snapshot_integrity",
    "check_all_snapshots",
    "summarize_integrity_run",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class SnapshotIntegrityReport:
    """Outcome of one ``check_snapshot_integrity`` call.

    Mirrors the shape of the other ops/result dataclasses in the
    project — populated whether or not the snapshot exists. ``ok`` is a
    convenience: True iff nothing is missing + nothing failed to parse.
    """

    date_iso: str = ""
    container_type: str = ""
    files_expected: list[str] = field(default_factory=list)
    files_present: list[str] = field(default_factory=list)
    files_missing: list[str] = field(default_factory=list)
    files_corrupted: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    ok: bool = False


# ---------------------------------------------------------------------------
# Expected-files lookup — adapts to whatever the pipeline writes today
# ---------------------------------------------------------------------------


def expected_filenames(container_type: str) -> list[str]:
    """Return the canonical filenames the snapshot pipeline persists for
    ``container_type``.

    Reads the filename helpers off ``processing.port_supply_history``
    so this checker stays in lock-step with what the pipeline actually
    writes. When that module adds another artifact, extend this list
    by importing its new helper — don't hard-code filenames.
    """
    from processing.port_supply_history import (
        _regional_snapshot_filename,
        _snapshot_filename,
    )
    return [
        _snapshot_filename(container_type),
        _regional_snapshot_filename(container_type),
    ]


# ---------------------------------------------------------------------------
# Single-date check
# ---------------------------------------------------------------------------


def check_snapshot_integrity(
    snapshot_date: date,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
) -> SnapshotIntegrityReport:
    """Check one snapshot directory for missing / corrupted files.

    Returns a populated ``SnapshotIntegrityReport`` even when the dir
    doesn't exist — in that case every expected file lands in
    ``files_missing`` and ``ok=False``.

    Per-port summary parse path: attempts
    ``tools.port_supply_diff.parse_summary_csv`` and emits a parse
    warning for each row with a blank locode or an unparseable
    ``supply_deficit_days``. Any exception during parse marks the file
    as corrupted (operator-visible) rather than propagating.
    """
    from processing.port_supply_history import (
        _snapshot_filename,
        snapshot_dir_for,
    )

    expected = expected_filenames(container_type)
    report = SnapshotIntegrityReport(
        date_iso=snapshot_date.isoformat(),
        container_type=container_type,
        files_expected=list(expected),
    )

    snap_dir = snapshot_dir_for(snapshot_date, root=root)

    # ── Existence sweep ──────────────────────────────────────────────
    for fname in expected:
        target = snap_dir / fname
        if target.exists() and target.is_file():
            report.files_present.append(fname)
        else:
            report.files_missing.append(fname)

    # ── Parse the per-port summary if present ────────────────────────
    summary_fname = _snapshot_filename(container_type)
    summary_path = snap_dir / summary_fname
    if summary_fname in report.files_present:
        try:
            from tools.port_supply_diff import parse_summary_csv
            rows = parse_summary_csv(summary_path)
        except Exception as exc:
            report.files_corrupted.append(summary_fname)
            report.parse_warnings.append(
                f"{summary_fname}: parse failed: "
                f"{type(exc).__name__}: {exc}"
            )
            rows = []

        # Even on a successful parse, defensively flag rows that look
        # wrong. parse_summary_csv silently skips blank-locode rows so
        # we re-check by reading the raw CSV body — a row with a blank
        # locode passes parse but is a real-world red flag.
        if rows is not None and summary_fname not in report.files_corrupted:
            try:
                _detect_row_warnings(summary_path, summary_fname, report)
            except Exception as exc:
                # Re-reading the body should never raise but stay
                # defensive — flag it but don't kill the run.
                report.files_corrupted.append(summary_fname)
                report.parse_warnings.append(
                    f"{summary_fname}: row sweep failed: "
                    f"{type(exc).__name__}: {exc}"
                )

    # ── ok is the AND of "nothing missing" + "no parse warnings" ────
    report.ok = (
        not report.files_missing
        and not report.files_corrupted
        and not report.parse_warnings
    )
    return report


def _detect_row_warnings(
    path: Path,
    fname: str,
    report: SnapshotIntegrityReport,
) -> None:
    """Sweep the raw CSV body for missing-locode or unparseable-deficit rows.

    Appends one human-readable line per offending row into
    ``report.parse_warnings``. Mirrors the BOM + comment-line tolerance
    of ``tools.port_supply_diff.parse_summary_csv`` so we don't
    misreport metadata rows as bad data."""
    import csv
    import io

    text = path.read_text(encoding="utf-8")
    if text.startswith("﻿"):
        text = text[1:]
    body_lines = [
        line for line in text.split("\n")
        if line and not line.startswith("# ")
    ]
    if not body_lines:
        # An empty body (header-only or fully blank) is a structural
        # problem worth flagging — a real snapshot has rows.
        report.files_corrupted.append(fname)
        report.parse_warnings.append(
            f"{fname}: empty body (no data rows after header)"
        )
        return

    reader = csv.DictReader(io.StringIO("\n".join(body_lines)))
    if reader.fieldnames is None:
        report.files_corrupted.append(fname)
        report.parse_warnings.append(
            f"{fname}: missing CSV header"
        )
        return

    for line_no, row in enumerate(reader, start=2):
        locode = (row.get("locode") or "").strip()
        if not locode:
            report.parse_warnings.append(
                f"{fname}:row {line_no}: missing locode"
            )
            continue
        raw_deficit = (row.get("supply_deficit_days") or "").strip()
        # An empty deficit string is itself unparseable.
        if raw_deficit == "":
            report.parse_warnings.append(
                f"{fname}:row {line_no} ({locode}): "
                f"unparseable deficit_days (empty)"
            )
            continue
        try:
            float(raw_deficit)
        except ValueError:
            report.parse_warnings.append(
                f"{fname}:row {line_no} ({locode}): "
                f"unparseable deficit_days ({raw_deficit!r})"
            )


# ---------------------------------------------------------------------------
# Bulk sweep across every retained snapshot
# ---------------------------------------------------------------------------


def check_all_snapshots(
    container_type: str = "40FT_DRY",
    root: Path | None = None,
    since: date | None = None,
) -> list[SnapshotIntegrityReport]:
    """Run ``check_snapshot_integrity`` over every snapshot date present
    under ``root``, oldest-first.

    ``since`` filters to dates strictly on-or-after the given date so
    the daily cron can scope its sweep to "the retention window" rather
    than checking every snapshot ever produced.
    """
    from processing.port_supply_history import list_snapshot_dates

    dates = list_snapshot_dates(root=root)
    if since is not None:
        dates = [d for d in dates if d >= since]
    return [
        check_snapshot_integrity(d, container_type=container_type, root=root)
        for d in dates
    ]


# ---------------------------------------------------------------------------
# Aggregate summary for dashboards / CI gates
# ---------------------------------------------------------------------------


def summarize_integrity_run(
    reports: list[SnapshotIntegrityReport],
) -> dict:
    """Reduce a list of reports to a dashboard-friendly counter dict.

    Keys:
      * ``n_dates_checked`` — count of input reports
      * ``n_ok``            — count of reports with ``ok=True``
      * ``n_corrupted``     — count of reports with at least one
                              corrupted file
      * ``n_missing``       — count of reports with at least one
                              missing file
      * ``oldest_problem_date`` — ISO date of the earliest report with
                                  ``ok=False`` (None if all clean)
    """
    n_ok = sum(1 for r in reports if r.ok)
    n_corrupted = sum(1 for r in reports if r.files_corrupted)
    n_missing = sum(1 for r in reports if r.files_missing)
    problem_dates = sorted(r.date_iso for r in reports if not r.ok)
    oldest_problem: Optional[str] = (
        problem_dates[0] if problem_dates else None
    )
    return {
        "n_dates_checked": len(reports),
        "n_ok": n_ok,
        "n_corrupted": n_corrupted,
        "n_missing": n_missing,
        "oldest_problem_date": oldest_problem,
    }
