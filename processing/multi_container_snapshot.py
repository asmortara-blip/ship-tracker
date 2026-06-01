"""processing/multi_container_snapshot.py — per-container snapshot fan-out.

The single-container daily snapshot job
(``processing.port_supply_history.run_daily_snapshot_job``) persists exactly
one container type per run. Operators want a *cross-container* view —
how the 40FT_DRY surplus compares to the 40FT_REEFER deficit on the same
day, for instance — which requires a snapshot for each container type to
exist under the same date directory.

This module is the coordinator that fans the single-container job out
across the canonical container-type list. It is intentionally a thin
shim around ``run_daily_snapshot_job`` so the single-container helper
remains the authoritative implementation:

  * Other code that only needs one container type keeps calling the
    underlying helper directly — nothing changes for that path.
  * Callers that want the full cross-container set call
    :func:`run_multi_container_snapshot_job`, which loops, collects per
    container results, and reports an aggregate.

Defensive contract — failures are *isolated per container*. If the
reefer snapshot blows up (e.g. upstream data hiccup), the dry + 20ft
snapshots still land on disk and surface their results. That matches
the contract of every other worker job in the codebase (never raise out
of the worker pool; surface the failure in ``ok=False`` + ``error_msg``).

Container-type list — :data:`DEFAULT_CONTAINER_TYPES` covers the three
slices the operator dashboard relies on. ``equipment_tracker`` tracks
five (20FT_DRY, 40FT_DRY, 40FT_HC, 40FT_REEFER, 20FT_TANK); the HC and
tank slices aren't yet wired into the dashboard, so the daily fan-out
skips them by default. Callers can pass an explicit list to include them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Optional


__all__ = [
    "DEFAULT_CONTAINER_TYPES",
    "MultiContainerResult",
    "run_multi_container_snapshot_job",
    "summarize_multi_container_health",
]


# Default container-type fan-out. The dashboard renders these three
# slices side-by-side; equipment_tracker tracks two more (40FT_HC,
# 20FT_TANK) but those aren't wired into the daily operator view yet.
DEFAULT_CONTAINER_TYPES: list[str] = [
    "40FT_DRY",
    "40FT_REEFER",
    "20FT_DRY",
]


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class MultiContainerResult:
    """Outcome of one :func:`run_multi_container_snapshot_job` call.

    Mirrors the shape of ``SnapshotJobResult`` from
    ``processing.port_supply_history`` — never raises, always returns
    populated. Per-container results are keyed by container-type string
    so the operator can pick the slice they want without iterating.

    ``any_failed`` is True iff at least one per-container
    ``SnapshotJobResult.ok`` came back False. ``total_bytes_written`` is
    the sum across all per-container successful saves (regional rollups
    not counted — same accounting convention as the single-container job).
    """

    today_iso: str = ""
    per_container_results: dict = field(default_factory=dict)
    total_bytes_written: int = 0
    any_failed: bool = False


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


def run_multi_container_snapshot_job(
    *,
    container_types: Optional[Iterable[str]] = None,
    root: Path | None = None,
    today: date | None = None,
    min_diff_delta_days: float = 1.0,
) -> MultiContainerResult:
    """Run the daily snapshot job once per container type, collect results.

    Defaults to :data:`DEFAULT_CONTAINER_TYPES` when ``container_types``
    is ``None``. Each per-container call is wrapped in its own
    try/except so a failure on one container does NOT abort the loop —
    the other containers still get their snapshot written + returned.

    The aggregate result reflects the union of per-container outcomes:
    ``any_failed`` flips True the first time a per-container result
    comes back ``ok=False``, and ``total_bytes_written`` is the sum
    across all containers (zero bytes counted for failed runs).

    Parameters
    ----------
    container_types:
        Iterable of container-type strings (e.g. ``"40FT_DRY"``).
        ``None`` (default) uses :data:`DEFAULT_CONTAINER_TYPES`. An
        empty iterable yields a result with no per-container entries
        (still ``any_failed=False`` since nothing was attempted).
    root:
        Snapshot tree root, passed through to the underlying job. Tests
        pass a ``tmp_path``; production leaves it ``None`` so the
        default ``SNAPSHOT_ROOT`` is used.
    today:
        Snapshot date, passed through. ``None`` (default) means UTC
        today — tests pin a fixed date for determinism.
    min_diff_delta_days:
        Forwarded to the underlying job's ``min_diff_delta_days``
        comparator knob. The default (1.0d) matches the operator's
        overnight-change reporting threshold.
    """
    today = today or datetime.now(timezone.utc).date()
    if container_types is None:
        container_types = DEFAULT_CONTAINER_TYPES

    result = MultiContainerResult(today_iso=today.isoformat())

    # Lazy import — keeps this module cheap to import for tests that
    # monkeypatch ``run_daily_snapshot_job`` (they import the function
    # *symbol* via the port_supply_history module path, not via this
    # module's bound reference).
    from processing import port_supply_history

    for ct in container_types:
        try:
            single = port_supply_history.run_daily_snapshot_job(
                container_type=ct,
                today=today,
                root=root,
                min_diff_delta_days=min_diff_delta_days,
            )
        except Exception as exc:
            # The underlying job is defensive and shouldn't raise — but
            # if a monkeypatched test (or a future refactor) does raise,
            # we still keep going with the other containers. Synthesize
            # a failed SnapshotJobResult so the per-container view is
            # populated for every requested type.
            single = port_supply_history.SnapshotJobResult(
                ok=False,
                today=today.isoformat(),
                container_type=ct,
                error_msg=(
                    f"run_daily_snapshot_job raised: "
                    f"{type(exc).__name__}: {exc}"
                ),
            )

        result.per_container_results[ct] = single
        if single.ok:
            result.total_bytes_written += int(single.bytes_written or 0)
        else:
            result.any_failed = True

    return result


# ---------------------------------------------------------------------------
# Log-friendly summarizer
# ---------------------------------------------------------------------------


def summarize_multi_container_health(result: MultiContainerResult) -> str:
    """One-line-per-container summary suitable for logger.info output.

    Lists each container with its ok/fail flag + bytes saved, then a
    final aggregate line. Designed to be readable inline in a log tail
    without parsing JSON — the JSON formatter on the CLI side handles
    the machine-readable case.
    """
    lines: list[str] = []
    lines.append(
        f"multi-container snapshot {result.today_iso}: "
        f"{len(result.per_container_results)} container types, "
        f"total {result.total_bytes_written:,}B "
        f"{'(with failures)' if result.any_failed else '(all ok)'}"
    )
    for ct, single in result.per_container_results.items():
        if single.ok:
            lines.append(
                f"  {ct}: ok, {int(single.bytes_written or 0):,}B"
                + (
                    f", diff vs {single.prior_snapshot_date}"
                    if getattr(single, "prior_snapshot_date", "")
                    else ""
                )
            )
        else:
            lines.append(f"  {ct}: FAILED — {single.error_msg}")
    return "\n".join(lines)
