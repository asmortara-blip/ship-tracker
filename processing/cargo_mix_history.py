"""processing/cargo_mix_history.py — daily per-route cargo-mix persistence.

Persists ``cache/cargo_mix_history/<YYYY-MM-DD>/cargo_mix.jsonl`` — one
record per route per day. The cargo-flow anomaly detector reads this
trailing window to score today's mix vs the trailing median.

File layout:

    cache/cargo_mix_history/
        2026-05-25/
            cargo_mix.jsonl     # one route per line
        2026-05-26/
            cargo_mix.jsonl
        ...

Each line is JSON: ``{"route_id": str, "mix": {category: share}}``.

The save side is a thin wrapper around
``processing.cargo_analyzer.get_route_cargo_mix`` called once per route
in ``routes.route_registry.ROUTES``. The load side returns the
trailing N days of mixes for a given route, oldest-first — the exact
shape ``processing.cargo_flow_anomaly.compute_cargo_flow_anomaly``
consumes.

Worker job (``run_daily_cargo_mix_snapshot_job``):
  1. Builds today's per-route mix
  2. Writes one JSONL line per route under today's dir
  3. For each route, loads the trailing 14 days, scores today's mix
     against them, and returns a count of how many routes crossed the
     anomaly threshold (logged inline so operators see drift the same
     tick it appears).

Defensive — every step is wrapped so a failure surfaces in
``ok=False`` + ``error_msg`` rather than raising out of the worker pool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


__all__ = [
    "CARGO_MIX_ROOT",
    "CargoMixHistoryJobResult",
    "cargo_mix_dir_for",
    "save_cargo_mix_snapshot",
    "load_cargo_mix_for_route",
    "list_cargo_mix_dates",
    "gc_old_cargo_mix_snapshots",
    "run_daily_cargo_mix_snapshot_job",
]


# Default persistence root — under cache/ alongside the other daily
# snapshot trees so the bulk-export job picks it up automatically.
CARGO_MIX_ROOT: Path = (
    Path(__file__).parent.parent / "cache" / "cargo_mix_history"
)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CargoMixHistoryJobResult:
    """Outcome of one ``run_daily_cargo_mix_snapshot_job`` call.

    Mirrors the shape of the other ``*JobResult`` dataclasses in the
    project — never raises, always returns populated. ``anomaly_routes``
    is the list of route ids that crossed the JSD threshold for today.
    """

    ok: bool = False
    today: str = ""
    n_routes_saved: int = 0
    bytes_written: int = 0
    snapshot_path: str = ""
    anomaly_routes: list[str] = field(default_factory=list)
    n_anomaly_routes: int = 0
    error_msg: str = ""


# ---------------------------------------------------------------------------
# Path helpers — pure, easy to test
# ---------------------------------------------------------------------------


def cargo_mix_dir_for(
    snapshot_date: date,
    *,
    root: Path | None = None,
) -> Path:
    """Return the directory path for a given snapshot date."""
    base = Path(root) if root is not None else CARGO_MIX_ROOT
    return base / snapshot_date.isoformat()


_FILENAME = "cargo_mix.jsonl"


# ---------------------------------------------------------------------------
# Save / load — JSONL one record per route
# ---------------------------------------------------------------------------


def save_cargo_mix_snapshot(
    *,
    snapshot_date: date | None = None,
    root: Path | None = None,
    routes: list | None = None,
    trade_data: dict | None = None,
) -> tuple[Path, int]:
    """Build today's per-route mixes + write the JSONL file.

    ``routes`` defaults to ``routes.route_registry.ROUTES``; tests
    inject a stub list. ``trade_data`` defaults to ``{}`` (the
    cargo-analyzer falls back to its illustrative weights when no real
    trade data is supplied).

    Returns ``(path_written, bytes_written)``. Creates the date-stamped
    parent dir if missing. Raises only on a truly unrecoverable state
    (filesystem permission); callers wrap in try/except.
    """
    from processing.cargo_analyzer import get_route_cargo_mix

    if routes is None:
        from routes.route_registry import ROUTES
        routes = ROUTES
    if trade_data is None:
        trade_data = {}

    snapshot_date = snapshot_date or datetime.now(timezone.utc).date()
    out_dir = cargo_mix_dir_for(snapshot_date, root=root)
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / _FILENAME

    lines: list[str] = []
    for r in routes:
        route_id = getattr(r, "id", None) or getattr(r, "route_id", None)
        if not route_id:
            continue
        try:
            mix = get_route_cargo_mix(route_id, trade_data)
        except Exception:
            # One bad route shouldn't kill the snapshot — write an
            # empty mix so the row count still equals the route count.
            mix = {}
        lines.append(json.dumps(
            {"route_id": route_id, "mix": dict(mix)},
            sort_keys=True,
        ))
    body = "\n".join(lines) + ("\n" if lines else "")
    target.write_text(body, encoding="utf-8")
    return target, len(body.encode("utf-8"))


def load_cargo_mix_for_route(
    route_id: str,
    *,
    window_days: int = 14,
    today: date | None = None,
    root: Path | None = None,
) -> list[dict[str, float]]:
    """Return the trailing N days of mix snapshots for ``route_id``.

    Walks ``cargo_mix_dir_for(d)`` for each d in [today-window_days, today),
    extracts the matching route's mix, and returns the list oldest-first.
    Missing dates / missing routes are skipped silently so the caller
    gets a clean list even when history is incomplete.
    """
    today = today or datetime.now(timezone.utc).date()
    window = max(1, int(window_days))
    base = Path(root) if root is not None else CARGO_MIX_ROOT
    out: list[dict[str, float]] = []
    for delta in range(window, 0, -1):
        d = today - timedelta(days=delta)
        path = cargo_mix_dir_for(d, root=base) / _FILENAME
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(blob.get("route_id", "")) != route_id:
                    continue
                mix = blob.get("mix", {})
                if isinstance(mix, dict):
                    out.append(
                        {str(k): float(v) for k, v in mix.items() if v is not None}
                    )
        except Exception:
            # A corrupt file shouldn't abort the load — log nothing
            # here (defensive read) and continue with what we have.
            continue
    return out


def list_cargo_mix_dates(
    *,
    root: Path | None = None,
) -> list[date]:
    """Every ISO date present under ``root`` with a cargo_mix.jsonl file."""
    base = Path(root) if root is not None else CARGO_MIX_ROOT
    if not base.exists():
        return []
    dates: list[date] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        if not (child / _FILENAME).exists():
            continue
        try:
            dates.append(date.fromisoformat(child.name))
        except ValueError:
            continue
    dates.sort()
    return dates


def gc_old_cargo_mix_snapshots(
    *,
    keep_days: int = 90,
    root: Path | None = None,
    today: date | None = None,
) -> dict:
    """Garbage-collect cargo-mix snapshot dirs older than ``keep_days``.

    Mirrors :func:`processing.port_supply_history.gc_old_snapshots` — the
    cargo-mix tree was added alongside the port-supply tree but never got
    a GC job, so it grew one dated dir per day forever. Walks every
    ISO-named subdir under ``root`` (default :data:`CARGO_MIX_ROOT`) and
    deletes those older than ``today - keep_days``. An operator-artefact
    guard preserves any dir holding a file other than ``_FILENAME`` — we
    never delete data we did not write. Never raises; a per-dir failure
    is swallowed so one bad dir can't abort the sweep.

    Returns ``{"n_dirs_scanned", "n_dirs_deleted", "n_bytes_freed",
    "preserved_artefacts"}``.
    """
    import shutil

    base = Path(root) if root is not None else CARGO_MIX_ROOT
    anchor = today or datetime.now(timezone.utc).date()
    cutoff = anchor - timedelta(days=max(0, int(keep_days)))
    result: dict = {
        "n_dirs_scanned": 0,
        "n_dirs_deleted": 0,
        "n_bytes_freed": 0,
        "preserved_artefacts": [],
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
            continue                      # non-date dir — not ours, skip
        result["n_dirs_scanned"] += 1
        if snap_date >= cutoff:
            continue                      # inside the keep window
        try:
            files = [e for e in child.iterdir() if e.is_file()]
            extra = [e for e in files if e.name != _FILENAME]
            size = sum(e.stat().st_size for e in files)
        except OSError:
            continue
        if extra:                         # operator artefact — never delete
            result["preserved_artefacts"].append(snap_date.isoformat())
            continue
        try:
            shutil.rmtree(child)
        except OSError:
            continue                      # one bad dir doesn't kill the run
        result["n_dirs_deleted"] += 1
        result["n_bytes_freed"] += size
    return result


# ---------------------------------------------------------------------------
# Worker job — save today + score each route's anomaly
# ---------------------------------------------------------------------------


def run_daily_cargo_mix_snapshot_job(
    *,
    today: date | None = None,
    root: Path | None = None,
    window_days: int = 14,
    jsd_anomaly_threshold: float = 0.15,
    jump_threshold_pp: float = 10.0,
) -> CargoMixHistoryJobResult:
    """Save today's per-route mixes + identify anomalous routes.

    Defensive — every step is wrapped so a failure surfaces in
    ``CargoMixHistoryJobResult.ok=False`` + ``error_msg`` rather than
    raising out of the worker pool.

    ``jsd_anomaly_threshold`` matches the "anomalous" band lower bound
    in ``processing.cargo_flow_anomaly.CARGO_DRIFT_BANDS`` (0.15) — a
    route only lands in the anomaly list when its JSD vs the trailing
    median crosses this threshold.
    """
    from processing.cargo_flow_anomaly import compute_cargo_flow_anomaly
    from routes.route_registry import ROUTES

    today = today or datetime.now(timezone.utc).date()
    result = CargoMixHistoryJobResult(today=today.isoformat())

    # ── Save today's mixes ────────────────────────────────────────────
    try:
        path, byte_count = save_cargo_mix_snapshot(
            snapshot_date=today, root=root,
        )
    except Exception as exc:
        result.error_msg = f"save_cargo_mix_snapshot failed: {type(exc).__name__}: {exc}"
        return result

    result.snapshot_path = str(path)
    result.bytes_written = byte_count
    result.n_routes_saved = len(ROUTES)

    # ── Score each route against its trailing window ──────────────────
    # Defensive: a single route's score failure must not kill the rest.
    try:
        from processing.cargo_analyzer import get_route_cargo_mix
        anomaly_routes: list[str] = []
        for r in ROUTES:
            route_id = getattr(r, "id", None)
            if not route_id:
                continue
            try:
                today_mix = get_route_cargo_mix(route_id, {})
                history = load_cargo_mix_for_route(
                    route_id, window_days=window_days,
                    today=today, root=root,
                )
                report = compute_cargo_flow_anomaly(
                    route_id=route_id,
                    today_mix=today_mix,
                    history=history,
                    jump_threshold_pp=jump_threshold_pp,
                    jsd_elevated_threshold=jsd_anomaly_threshold,
                    trailing_window=window_days,
                )
                if report.is_anomaly and report.jsd >= jsd_anomaly_threshold:
                    anomaly_routes.append(route_id)
            except Exception:
                continue
        result.anomaly_routes = anomaly_routes
        result.n_anomaly_routes = len(anomaly_routes)
    except Exception as exc:
        result.error_msg = f"scoring failed (snapshot still saved): {exc}"

    result.ok = True
    return result
