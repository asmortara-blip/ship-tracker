"""processing/port_supply_history.py — daily snapshot persistence.

Persists per-day per-port-summary CSV snapshots to ``cache/port_supply_snapshots/<date>/``
and provides a worker job that runs the full save+diff cycle so the
operator wakes up to "here's what changed overnight" without having to
run the CLI manually.

The snapshot file layout:

    cache/port_supply_snapshots/
        2026-05-25/
            port_supply_summary_40ft_dry.csv
            port_supply_summary_40ft_reefer.csv
        2026-05-26/
            port_supply_summary_40ft_dry.csv
            ...

Filenames intentionally drop the date stamp (date lives in the parent
directory) so the diff helper can pair across days deterministically
without parsing dates out of filenames.

The save side is a thin wrapper around
``utils.port_supply_csv.chains_to_summary_csv`` — same exporter the UI
+ CLI use. The diff side delegates to
``tools.port_supply_diff.compare_snapshots`` — same comparator the diff
CLI uses. This module is glue + I/O.

Worker job (``run_daily_snapshot_job``):
  1. Builds today's chains via ``build_port_supply_chains``
  2. Writes the snapshot under today's dir
  3. Looks back N days for the previous snapshot (default 1 = yesterday)
  4. If found, diffs against it and returns the diff report
  5. Failure modes log + return ``ok=False`` rather than raising — same
     contract as the other worker jobs in ``worker/scheduler.py``
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


__all__ = [
    "SNAPSHOT_ROOT",
    "SnapshotJobResult",
    "snapshot_dir_for",
    "save_snapshot",
    "load_snapshot",
    "list_snapshot_dates",
    "find_prior_snapshot_date",
    "run_daily_snapshot_job",
]


# Default root for persisted snapshots — under the project's existing
# ``cache/`` tree so the bulk_export job picks them up alongside
# everything else in the daily backup.
SNAPSHOT_ROOT: Path = Path(__file__).parent.parent / "cache" / "port_supply_snapshots"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class SnapshotJobResult:
    """Outcome of one ``run_daily_snapshot_job`` call.

    Mirrors the shape of ``worker/scheduler.ReportJobResult`` — never
    raises, always returns populated. ``diff`` is the
    ``tools.port_supply_diff.DiffReport`` produced when a prior snapshot
    was available + diffed against, else None.
    """

    ok: bool = False
    today: str = ""
    container_type: str = ""
    snapshot_path: str = ""
    bytes_written: int = 0
    prior_snapshot_date: str = ""    # ISO date of the prior snapshot used
    diff: object | None = None       # DiffReport | None
    error_msg: str = ""


# ---------------------------------------------------------------------------
# Path helpers — pure, easy to test
# ---------------------------------------------------------------------------


def snapshot_dir_for(
    snapshot_date: date,
    *,
    root: Path | None = None,
) -> Path:
    """Return the directory path for a given snapshot date.

    ``root`` defaults to ``SNAPSHOT_ROOT`` but tests pass a ``tmp_path``
    so the on-disk state stays local to the test run.
    """
    base = Path(root) if root is not None else SNAPSHOT_ROOT
    return base / snapshot_date.isoformat()


def _snapshot_filename(container_type: str) -> str:
    """Canonical per-container filename inside a snapshot dir.

    Date intentionally omitted — the parent dir carries it. This keeps
    the diff helper deterministic when pairing across days."""
    return f"port_supply_summary_{container_type.lower()}.csv"


# ---------------------------------------------------------------------------
# Save / load — thin wrappers around the existing exporters + parsers
# ---------------------------------------------------------------------------


def save_snapshot(
    *,
    snapshot_date: date | None = None,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
) -> tuple[Path, int]:
    """Build today's chains + write the per-port-summary CSV.

    Returns ``(path_written, bytes_written)``. Creates the date-stamped
    parent dir if missing. Raises only on a genuinely unrecoverable
    state (filesystem permission, dataset import); callers should wrap
    in try/except.
    """
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_summary_csv

    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    out_dir = snapshot_dir_for(snapshot_date, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _snapshot_filename(container_type)

    chains = build_port_supply_chains(container_type=container_type)
    body = chains_to_summary_csv(chains, container_type=container_type)
    target.write_text(body, encoding="utf-8")
    return target, len(body.encode("utf-8"))


def load_snapshot(
    snapshot_date: date,
    *,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
) -> list:
    """Load a previously-saved snapshot. Returns the parsed PortRow list
    (delegates to ``tools.port_supply_diff.parse_summary_csv``).

    Raises ``FileNotFoundError`` if the snapshot doesn't exist for the
    given date + container type."""
    from tools.port_supply_diff import parse_summary_csv

    path = snapshot_dir_for(snapshot_date, root=root) / _snapshot_filename(
        container_type,
    )
    if not path.exists():
        raise FileNotFoundError(
            f"no snapshot for {snapshot_date.isoformat()} "
            f"({container_type}) at {path}"
        )
    return parse_summary_csv(path)


def list_snapshot_dates(
    *,
    root: Path | None = None,
) -> list[date]:
    """Return all snapshot dates present under ``root``, sorted oldest-first.

    A snapshot dir counts when it has a name that parses as ISO YYYY-MM-DD
    — directories with other names (stray manual artefacts) are skipped."""
    base = Path(root) if root is not None else SNAPSHOT_ROOT
    if not base.exists():
        return []
    dates: list[date] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            dates.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    dates.sort()
    return dates


def find_prior_snapshot_date(
    today: date,
    *,
    container_type: str = "40FT_DRY",
    max_lookback_days: int = 14,
    root: Path | None = None,
) -> Optional[date]:
    """Find the most recent snapshot date BEFORE ``today`` for the given
    container_type, looking back at most ``max_lookback_days``.

    Skips dates that have a directory but no CSV for the requested
    container type (e.g. operator captured only 40FT_REEFER on that day)."""
    base = Path(root) if root is not None else SNAPSHOT_ROOT
    for delta in range(1, max(1, int(max_lookback_days)) + 1):
        candidate = today - timedelta(days=delta)
        path = snapshot_dir_for(candidate, root=base) / _snapshot_filename(
            container_type,
        )
        if path.exists():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Worker job — save today + diff vs prior
# ---------------------------------------------------------------------------


def run_daily_snapshot_job(
    *,
    container_type: str = "40FT_DRY",
    today: date | None = None,
    root: Path | None = None,
    max_lookback_days: int = 14,
    min_diff_delta_days: float = 1.0,
) -> SnapshotJobResult:
    """End-to-end daily snapshot: save today + diff vs prior.

    Defensive — every step is wrapped so a failure surfaces in
    ``SnapshotJobResult.ok=False`` + ``error_msg`` rather than raising
    out of the worker pool.

    Parameters mirror the underlying helpers; defaults match what the
    scheduler would use:
      * ``container_type`` — 40FT_DRY (the most operationally relevant
        slice on the platform's modeled fleet)
      * ``today`` — UTC today; tests inject a fixed date for determinism
      * ``root`` — snapshots dir (defaults to SNAPSHOT_ROOT)
      * ``max_lookback_days`` — how far back to look for the prior
        snapshot (default 14 so weekend gaps are tolerated)
      * ``min_diff_delta_days`` — passed straight through to the
        comparator's ``--min-delta`` knob (default 1.0d)
    """
    today = today or datetime.now(timezone.utc).date()
    result = SnapshotJobResult(
        today=today.isoformat(),
        container_type=container_type,
    )

    # ── Save today's snapshot ─────────────────────────────────────────
    try:
        path, byte_count = save_snapshot(
            snapshot_date=today,
            container_type=container_type,
            root=root,
        )
    except Exception as exc:
        result.error_msg = f"save_snapshot failed: {type(exc).__name__}: {exc}"
        return result

    result.snapshot_path = str(path)
    result.bytes_written = byte_count

    # ── Find + diff against prior ─────────────────────────────────────
    try:
        prior = find_prior_snapshot_date(
            today,
            container_type=container_type,
            max_lookback_days=max_lookback_days,
            root=root,
        )
        if prior is not None:
            from tools.port_supply_diff import (
                compare_snapshots, parse_summary_csv,
            )
            before_rows = load_snapshot(
                prior, container_type=container_type, root=root,
            )
            after_rows = parse_summary_csv(path)
            result.diff = compare_snapshots(
                before_rows, after_rows,
                min_delta_days=min_diff_delta_days,
            )
            result.prior_snapshot_date = prior.isoformat()
    except Exception as exc:
        # Save succeeded — diff failure is non-fatal. Record the error
        # for telemetry but mark the run ok=True since the snapshot
        # itself landed on disk.
        result.error_msg = f"diff failed (snapshot still saved): {exc}"

    result.ok = True
    return result
