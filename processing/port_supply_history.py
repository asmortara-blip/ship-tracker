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
            port_supply_regional_rollup_40ft_dry.csv
            port_supply_regional_rollup_40ft_reefer.csv
        2026-05-26/
            port_supply_summary_40ft_dry.csv
            port_supply_regional_rollup_40ft_dry.csv
            ...

Filenames intentionally drop the date stamp (date lives in the parent
directory) so the diff helper can pair across days deterministically
without parsing dates out of filenames.

The save side is a thin wrapper around
``utils.port_supply_csv.chains_to_summary_csv`` — same exporter the UI
+ CLI use. The diff side delegates to
``tools.port_supply_diff.compare_snapshots`` — same comparator the diff
CLI uses. This module is glue + I/O.

The regional rollup artifact uses
``utils.port_supply_csv.chains_to_regional_rollup_csv`` — same chains
as the per-port summary, just a different cross-section. The per-port
diff is the authoritative overnight-change signal; regional is saved
but not diffed (it's a rollup of the rows the per-port diff already
covers).

Worker job (``run_daily_snapshot_job``):
  1. Builds today's chains via ``build_port_supply_chains``
  2. Writes the per-port snapshot under today's dir
  3. Writes the regional rollup alongside the per-port snapshot
  4. Looks back N days for the previous per-port snapshot (default 1)
  5. If found, diffs against it and returns the diff report
  6. Failure modes log + return ``ok=False`` rather than raising — same
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
    "RetentionPolicy",
    "snapshot_dir_for",
    "save_snapshot",
    "load_snapshot",
    "save_regional_snapshot",
    "load_regional_snapshot",
    "list_snapshot_dates",
    "find_prior_snapshot_date",
    "run_daily_snapshot_job",
    "gc_old_snapshots",
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

    ``regional_snapshot_path`` + ``regional_bytes_written`` reflect the
    parallel regional-rollup save. Default empty / 0 so a regional save
    failure (which is non-fatal — the per-port save is the authoritative
    artifact) leaves the rest of the result intact.
    """

    ok: bool = False
    today: str = ""
    container_type: str = ""
    snapshot_path: str = ""
    bytes_written: int = 0
    regional_snapshot_path: str = ""
    regional_bytes_written: int = 0
    prior_snapshot_date: str = ""    # ISO date of the prior snapshot used
    diff: object | None = None       # DiffReport | None
    error_msg: str = ""


@dataclass
class RetentionPolicy:
    """Retention rules applied by :func:`gc_old_snapshots`.

    Snapshots older than ``keep_days`` are candidates for deletion. The
    first-of-month / first-of-year flags preserve specific dates beyond
    the rolling window so the operator retains long-tail historical
    anchors without paying the full daily-snapshot disk cost.

    Defaults: 90 days hot retention + monthly + yearly anchors. That
    yields ~90 daily rows + ~12 monthly anchors per year + 1 yearly
    anchor per pre-window year — a small bounded footprint that still
    supports year-over-year comparisons.
    """

    keep_days: int = 90
    keep_first_of_month: bool = True
    keep_first_of_year: bool = True


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


def _regional_snapshot_filename(container_type: str) -> str:
    """Canonical per-container regional-rollup filename inside a snapshot dir.

    Mirrors ``_snapshot_filename`` — date lives in the parent dir,
    container type is lowercased. The two filenames live side-by-side
    under the same date directory so operators get both cross-sections
    in one place."""
    return f"port_supply_regional_rollup_{container_type.lower()}.csv"


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


def save_regional_snapshot(
    snapshot_date: date | None = None,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
) -> tuple[Path, int]:
    """Build today's chains + write the regional-rollup CSV.

    Returns ``(path_written, bytes_written)``. Lands the file alongside
    the per-port summary under the same date-stamped parent dir so
    operators get both cross-sections in one place. Mirrors
    ``save_snapshot`` — creates the parent dir if missing, raises only
    on a genuinely unrecoverable state; callers should wrap in
    try/except.
    """
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_regional_rollup_csv

    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    out_dir = snapshot_dir_for(snapshot_date, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _regional_snapshot_filename(container_type)

    chains = build_port_supply_chains(container_type=container_type)
    body = chains_to_regional_rollup_csv(chains, container_type=container_type)
    target.write_text(body, encoding="utf-8")
    return target, len(body.encode("utf-8"))


def load_regional_snapshot(
    snapshot_date: date,
    *,
    container_type: str = "40FT_DRY",
    root: Path | None = None,
) -> list[dict]:
    """Load a previously-saved regional-rollup snapshot.

    The regional rollup has a different schema than the per-port summary
    (one row per region with aggregate stats), so we use a lightweight
    parser here — mirroring the BOM + comment-header handling of
    ``tools.port_supply_diff.parse_summary_csv`` but emitting a plain
    list of dict rows since there's no downstream comparator to feed.

    Raises ``FileNotFoundError`` if the file doesn't exist for the
    given date + container type.
    """
    import csv
    import io

    path = snapshot_dir_for(snapshot_date, root=root) / (
        _regional_snapshot_filename(container_type)
    )
    if not path.exists():
        raise FileNotFoundError(
            f"no regional snapshot for {snapshot_date.isoformat()} "
            f"({container_type}) at {path}"
        )

    text = path.read_text(encoding="utf-8")
    # Strip BOM if present (mirrors parse_summary_csv).
    if text.startswith("﻿"):
        text = text[1:]
    # Drop comment-header lines before the CSV body.
    body_lines = [
        line for line in text.split("\n")
        if line and not line.startswith("# ")
    ]
    if not body_lines:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(body_lines)))
    return [dict(row) for row in reader if row.get("region")]


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

    # ── Save today's regional rollup ─────────────────────────────────
    # Wrapped in its own try block so a regional save failure does NOT
    # affect the per-port save (which already landed above) or the
    # downstream diff. The per-port diff is the authoritative overnight
    # signal; the regional rollup is a parallel cross-section.
    try:
        regional_path, regional_bytes = save_regional_snapshot(
            snapshot_date=today,
            container_type=container_type,
            root=root,
        )
        result.regional_snapshot_path = str(regional_path)
        result.regional_bytes_written = regional_bytes
    except Exception as exc:
        # Non-fatal — the per-port snapshot is the authoritative artifact.
        # Surface the failure in error_msg so telemetry sees it, but
        # leave the regional fields at their dataclass defaults.
        result.error_msg = (
            f"regional save failed (per-port still saved): "
            f"{type(exc).__name__}: {exc}"
        )

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


# ---------------------------------------------------------------------------
# Garbage collection — bounded disk usage for the snapshot tree
# ---------------------------------------------------------------------------


# Filenames produced by save_snapshot / save_regional_snapshot. Any other
# file inside a snapshot dir is treated as a manual operator artefact and
# the dir is skipped (we never delete operator data without consent).
_KNOWN_SNAPSHOT_FILE_PREFIXES = (
    "port_supply_summary_",
    "port_supply_regional_rollup_",
)


def _is_known_snapshot_file(name: str) -> bool:
    """True if ``name`` matches a file we know we created.

    Used to detect operator-placed manual artefacts in a snapshot dir so
    the GC skips that dir rather than nuking the operator's data.
    """
    return name.endswith(".csv") and any(
        name.startswith(prefix) for prefix in _KNOWN_SNAPSHOT_FILE_PREFIXES
    )


def _dir_size_bytes(path: Path) -> int:
    """Sum file sizes (recursive) under ``path``. Returns 0 on any error."""
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0
    return total


def gc_old_snapshots(
    *,
    policy: RetentionPolicy | None = None,
    root: Path | None = None,
    today: date | None = None,
) -> dict:
    """Garbage-collect snapshot dirs older than the retention window.

    Walks every ISO-named subdirectory under ``root`` (defaults to
    ``SNAPSHOT_ROOT``) and deletes any whose date is older than
    ``today - policy.keep_days``, EXCEPT:

      * If ``policy.keep_first_of_month`` is True and the date's day is 1,
        the dir is preserved (logged in ``preserved_anchors``).
      * If ``policy.keep_first_of_year`` is True and the date is January
        1st, the dir is preserved (logged in ``preserved_anchors``).
      * If the dir contains a file other than the known snapshot CSVs
        (operator placed a manual artefact there), the dir is preserved
        regardless of date — we never delete operator data.

    Defensive — same contract as the other helpers in this file. Never
    raises; a per-dir failure is swallowed + does not abort the run.

    Returns a count dict:
        ``{"n_dirs_scanned": int, "n_dirs_deleted": int,
           "n_bytes_freed": int, "preserved_anchors": list[str]}``
    """
    import shutil

    policy = policy or RetentionPolicy()
    base = Path(root) if root is not None else SNAPSHOT_ROOT
    today = today or datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=max(0, int(policy.keep_days)))

    result = {
        "n_dirs_scanned": 0,
        "n_dirs_deleted": 0,
        "n_bytes_freed": 0,
        "preserved_anchors": [],
    }

    if not base.exists():
        return result

    try:
        children = list(base.iterdir())
    except OSError:
        return result

    for child in children:
        if not child.is_dir():
            continue
        try:
            snap_date = date.fromisoformat(child.name)
        except ValueError:
            # Non-date dir (manual artefacts) — skip, do not count.
            continue

        result["n_dirs_scanned"] += 1

        # Inside the keep window — preserve.
        if snap_date >= cutoff:
            continue

        # Anchor preservation — check newest-takes-precedence-but-both-log.
        is_anchor = False
        if policy.keep_first_of_year and snap_date.month == 1 and snap_date.day == 1:
            is_anchor = True
        elif policy.keep_first_of_month and snap_date.day == 1:
            is_anchor = True
        if is_anchor:
            result["preserved_anchors"].append(snap_date.isoformat())
            continue

        # Operator-artefact guard: if there's any non-snapshot file in
        # the dir, skip + log a warning. Do not delete operator data.
        try:
            has_unknown_file = False
            for entry in child.iterdir():
                if entry.is_file() and not _is_known_snapshot_file(entry.name):
                    has_unknown_file = True
                    break
            if has_unknown_file:
                # Importing loguru lazily so this module stays import-cheap
                # for test paths that don't trigger GC.
                try:
                    from loguru import logger
                    logger.warning(
                        f"gc_old_snapshots: skipping {child} — "
                        f"contains non-snapshot file(s)"
                    )
                except Exception:
                    pass
                continue
        except OSError:
            continue

        # Size the dir BEFORE deletion so the freed-bytes count is accurate.
        size = _dir_size_bytes(child)

        try:
            shutil.rmtree(child)
        except OSError:
            # One bad dir doesn't kill the whole run. Skip + continue.
            continue

        result["n_dirs_deleted"] += 1
        result["n_bytes_freed"] += size

    return result
