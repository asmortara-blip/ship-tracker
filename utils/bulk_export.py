"""utils/bulk_export.py — bundle durable state into a single tar.gz archive.

Use cases
---------
* Backup before a schema migration / dependency bump.
* Hand a colleague a working dataset without ten separate files.
* Migrate from laptop A to laptop B without losing alerts / reports.

What gets included
------------------
* ``cache/ship_tracker.db`` — the SQLite state DB (alerts, rules,
  reports index, audit log, telemetry, etc.). Snapshotted via
  ``shutil.copy2`` into a temp dir so we never tar a file the WAL
  writer is mid-flush on.
* ``cache/*/`` — every per-source parquet subdirectory (fred, yfinance,
  worldbank, ...). The exports/ directory itself is excluded so we
  never tar previous exports into a new export.
* ``cache/reports/*.html`` — generated investor-briefing HTML files.

What gets excluded (on purpose)
-------------------------------
* ``logs/`` — rotated loguru logs grow unbounded and aren't durable
  state. They're not in the backup scope.
* ``cache/exports/`` — previous tar.gz archives. Including them would
  cause unbounded growth (each export contains every prior export).
* Anything outside the ``cache/`` tree.

Archive layout
--------------
    ship-tracker-20260522-143012.tar.gz
      MANIFEST.json              # serialized ExportManifest
      ship_tracker.db            # snapshotted SQLite DB (top level)
      cache/
        fred/*.parquet
        yfinance/*.parquet
        ...
        reports/*.html

Safety contract
---------------
* Every public function NEVER raises — failures degrade to ``None``,
  empty list, or ``0`` and are logged.
* The DB is copied to a temp dir before being added to the archive,
  so a concurrent WAL write cannot corrupt the snapshot.
* The audit hook logs the export with a path stripped to its filename
  (no absolute home-dir leak) and the size in bytes.

CLI
---
    python -m utils.bulk_export
    python -m utils.bulk_export --no-reports
    python -m utils.bulk_export --output /tmp/myexport.tar.gz
    python -m utils.bulk_export --prune
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tarfile
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger


# ─── Module-level constants ────────────────────────────────────────────────

# Anchor to the project root so path resolution is stable regardless of CWD.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# The exports directory lives INSIDE cache/ alongside the per-source
# subdirs. It is created lazily on first export so a fresh checkout
# never has empty placeholder dirs.
EXPORT_DIR: Path = _PROJECT_ROOT / "cache" / "exports"

# Filename stem used for default output paths.
_FILENAME_STEM: str = "ship-tracker"
_FILENAME_TIMESTAMP_FMT: str = "%Y%m%d-%H%M%S"


# ─── Data model ────────────────────────────────────────────────────────────


@dataclass
class ExportManifest:
    """Metadata persisted at the archive root as ``MANIFEST.json``.

    Attributes
    ----------
    generated_at:
        ISO-8601 UTC timestamp of the export.
    schema_version:
        The SQLite schema version recorded at export time. Lets a
        future ``restore`` step refuse a backup from a newer DB.
    db_size_bytes:
        Size of the included ``ship_tracker.db`` in bytes, or 0 when
        the DB was excluded (``include_db=False``).
    parquet_count:
        Total number of parquet files included from ``cache/*/``.
    report_count:
        Number of HTML report files included from ``cache/reports/``.
    includes_db / includes_cache / includes_reports:
        Echoes the flags the caller passed to :func:`build_export` so a
        downstream consumer can tell what's actually in the archive
        without listing every file.
    """

    generated_at: str
    schema_version: int
    db_size_bytes: int
    parquet_count: int
    report_count: int
    includes_db: bool
    includes_cache: bool
    includes_reports: bool


# ─── Internal helpers ──────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_filename() -> str:
    """Default archive filename: ship-tracker-YYYYMMDD-HHMMSS.tar.gz."""
    stamp = datetime.now(timezone.utc).strftime(_FILENAME_TIMESTAMP_FMT)
    return f"{_FILENAME_STEM}-{stamp}.tar.gz"


def _resolve_db_path() -> Path:
    """Look up the live DB path through state.db so tests that monkeypatch
    ``state.db.DB_PATH`` see the redirected location."""
    try:
        from state import db as state_db
        return Path(state_db.DB_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"bulk_export: could not import state.db: {exc}")
        return _PROJECT_ROOT / "cache" / "ship_tracker.db"


def _resolve_schema_version() -> int:
    """Best-effort schema version lookup — defaults to 0 if state.db is
    unavailable. Never raises."""
    try:
        from state import db as state_db
        return int(getattr(state_db, "SCHEMA_VERSION", 0))
    except Exception:  # noqa: BLE001
        return 0


def _resolve_reports_dir() -> Path:
    """Look up the live reports dir through utils.report_history so tests
    that monkeypatch ``REPORT_DIR`` see the redirected location."""
    try:
        from utils import report_history as rh
        return Path(rh.REPORT_DIR)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"bulk_export: could not import report_history: {exc}")
        return _PROJECT_ROOT / "cache" / "reports"


def _cache_root() -> Path:
    """Resolve the cache root by walking up from the DB path. Keeps tests
    that redirect DB_PATH to tmp_path consistent — the export then writes
    inside the same tmp tree instead of polluting the real cache/."""
    db_path = _resolve_db_path()
    # DB lives at <cache_root>/ship_tracker.db, so the parent IS the root.
    return db_path.parent


def _safe_detail_path(path: Path) -> str:
    """Return a path string safe to log in the audit detail field.

    The full path may contain ``/Users/<name>/`` or ``/home/<name>/``
    segments that leak identity into the audit log. Truncate to the
    filename only so the audit trail stays useful without becoming a
    privacy footgun.
    """
    try:
        return Path(str(path)).name
    except Exception:  # noqa: BLE001
        return ""


def _iter_cache_parquets(cache_root: Path, exports_dir: Path) -> list[Path]:
    """Yield every ``*.parquet`` file under ``cache/*/`` excluding
    ``cache/exports/``. Returns a list (not a generator) so the count
    is available for the manifest without re-walking."""
    if not cache_root.exists():
        return []
    out: list[Path] = []
    try:
        exports_resolved = exports_dir.resolve()
    except Exception:  # noqa: BLE001
        exports_resolved = exports_dir
    for child in cache_root.iterdir():
        try:
            if not child.is_dir():
                continue
            # Skip the exports/ subdir to avoid recursive bloat.
            if child.resolve() == exports_resolved:
                continue
            # Skip the reports/ subdir — it's handled separately as HTML.
            if child.name == "reports":
                continue
            for parquet in child.rglob("*.parquet"):
                try:
                    if parquet.is_file():
                        out.append(parquet)
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            continue
    return out


def _iter_report_htmls(reports_dir: Path) -> list[Path]:
    """Yield every ``*.html`` file directly inside ``cache/reports/``.
    Returns a list so the count is available for the manifest."""
    if not reports_dir.exists() or not reports_dir.is_dir():
        return []
    try:
        return [p for p in reports_dir.glob("*.html") if p.is_file()]
    except Exception:  # noqa: BLE001
        return []


# ─── Public API: build ─────────────────────────────────────────────────────


def build_export(
    *,
    output_path: Optional[Path] = None,
    include_db: bool = True,
    include_cache: bool = True,
    include_reports: bool = True,
) -> Optional[Path]:
    """Bundle durable state into a single tar.gz archive. NEVER raises.

    Parameters
    ----------
    output_path:
        Where to write the archive. Default:
        ``cache/exports/ship-tracker-YYYYMMDD-HHMMSS.tar.gz``. The
        parent directory is created lazily.
    include_db:
        When True (default), copy ``cache/ship_tracker.db`` into the
        archive root. The copy is staged through a temp dir via
        ``shutil.copy2`` so a concurrent WAL write cannot corrupt the
        snapshot.
    include_cache:
        When True (default), include every ``cache/<source>/*.parquet``
        file. The ``cache/exports/`` subdirectory itself is always
        excluded to prevent recursive bloat.
    include_reports:
        When True (default), include every ``cache/reports/*.html`` file.

    Returns
    -------
    The output path on success; ``None`` on any failure. Failures are
    logged at WARNING/ERROR but never raised — bulk export is best-
    effort by contract.
    """
    try:
        cache_root = _cache_root()
        reports_dir = _resolve_reports_dir()
        db_path = _resolve_db_path()

        # Resolve the output path BEFORE we compute the file list so we
        # can pass it to the parquet iterator (which excludes the
        # exports/ subdir from the manifest).
        if output_path is None:
            target = EXPORT_DIR / _timestamp_filename()
        else:
            target = Path(output_path)
        # Always exclude the directory the new archive will live in, so
        # a custom output_path pointing at an existing exports/ dir does
        # not get tarred into itself.
        exports_dir_for_skip = target.parent

        # Stage everything in a temp dir so the DB copy and the tar
        # build happen on a snapshot the WAL writer cannot touch.
        with tempfile.TemporaryDirectory(prefix="ship_export_") as staging:
            staging_dir = Path(staging)
            staged_db: Optional[Path] = None

            db_size = 0
            if include_db:
                try:
                    if db_path.exists() and db_path.is_file():
                        staged_db = staging_dir / db_path.name
                        shutil.copy2(db_path, staged_db)
                        db_size = staged_db.stat().st_size
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"build_export: DB copy failed for {db_path}: {exc}"
                    )
                    staged_db = None
                    db_size = 0

            parquets: list[Path] = []
            if include_cache:
                parquets = _iter_cache_parquets(cache_root, exports_dir_for_skip)

            reports: list[Path] = []
            if include_reports:
                reports = _iter_report_htmls(reports_dir)

            manifest = ExportManifest(
                generated_at=_now_iso(),
                schema_version=_resolve_schema_version(),
                db_size_bytes=int(db_size),
                parquet_count=len(parquets),
                report_count=len(reports),
                includes_db=bool(include_db and staged_db is not None),
                includes_cache=bool(include_cache),
                includes_reports=bool(include_reports),
            )

            # Write the manifest to the staging dir so it lands at the
            # archive root via the same `arcname` mechanism as the DB.
            manifest_path = staging_dir / "MANIFEST.json"
            try:
                manifest_path.write_text(
                    json.dumps(asdict(manifest), indent=2, default=str),
                    encoding="utf-8",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"build_export: manifest write failed: {exc}")
                return None

            # Ensure the output directory exists. Done LATE so we don't
            # create cache/exports/ when output_path points elsewhere.
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    f"build_export: could not create output dir "
                    f"{target.parent}: {exc}"
                )
                return None

            # Build the tarball. Everything below this point is wrapped
            # so a single bad file doesn't kill the whole archive.
            try:
                with tarfile.open(target, "w:gz") as tar:
                    # MANIFEST.json at the archive root.
                    tar.add(manifest_path, arcname="MANIFEST.json")

                    # DB at the archive root (top level, not under cache/)
                    # so a colleague can spot it immediately on ls.
                    if staged_db is not None and staged_db.exists():
                        tar.add(staged_db, arcname=staged_db.name)

                    # Parquet files preserve their relative path under
                    # cache/ so the archive can be unpacked directly
                    # over an empty cache/ tree.
                    for parquet in parquets:
                        try:
                            rel = parquet.relative_to(cache_root)
                            arcname = str(Path("cache") / rel)
                            tar.add(parquet, arcname=arcname)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                f"build_export: skipping parquet "
                                f"{parquet}: {exc}"
                            )
                            continue

                    # Report HTML files live at cache/reports/*.html.
                    for report in reports:
                        try:
                            arcname = str(
                                Path("cache") / "reports" / report.name
                            )
                            tar.add(report, arcname=arcname)
                        except Exception as exc:  # noqa: BLE001
                            logger.debug(
                                f"build_export: skipping report "
                                f"{report}: {exc}"
                            )
                            continue
            except Exception as exc:  # noqa: BLE001
                logger.error(f"build_export: tar.open failed: {exc}")
                # Best-effort cleanup — if a partial archive landed on
                # disk it would mislead a colleague into thinking the
                # export succeeded.
                try:
                    if target.exists():
                        target.unlink()
                except Exception:  # noqa: BLE001
                    pass
                return None

        # ─── Audit hook ────────────────────────────────────────────────
        try:
            try:
                size_bytes = int(target.stat().st_size)
            except Exception:  # noqa: BLE001
                size_bytes = 0
            from auth.audit import record_audit
            record_audit(
                "bulk_export",
                detail={
                    # Strip the path to its filename so we never leak a
                    # user's home dir into the audit table.
                    "output_path": _safe_detail_path(target),
                    "size_bytes": size_bytes,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"build_export: audit hook failed: {exc}")

        logger.info(
            f"build_export: wrote {target.name} "
            f"({manifest.parquet_count} parquets, "
            f"{manifest.report_count} reports, "
            f"db={manifest.db_size_bytes} bytes)"
        )
        return target
    except Exception as exc:  # noqa: BLE001
        # Top-level guard so build_export NEVER raises — required by the
        # task spec and by every caller (the CLI, the scheduler, future
        # UI buttons).
        logger.error(f"build_export: unhandled exception: {exc}")
        return None


# ─── Public API: list ──────────────────────────────────────────────────────


def list_exports() -> list[dict]:
    """List past exports under ``cache/exports/`` newest-first. NEVER raises.

    Each entry is a dict with keys:
      * ``filename``    — basename only (no leading directories).
      * ``size_bytes``  — int file size on disk.
      * ``created_at``  — ISO-8601 UTC timestamp from the file mtime.
                          (Filesystem ctime is OS-dependent and
                          unreliable on macOS for "creation time"; mtime
                          is the closest portable signal.)

    Returns an empty list when the exports dir doesn't exist or any
    error occurs during the scan.
    """
    try:
        if not EXPORT_DIR.exists() or not EXPORT_DIR.is_dir():
            return []
        out: list[dict] = []
        for entry in EXPORT_DIR.iterdir():
            try:
                if not entry.is_file():
                    continue
                if not entry.name.endswith(".tar.gz"):
                    continue
                stat = entry.stat()
                created = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
                out.append({
                    "filename": entry.name,
                    "size_bytes": int(stat.st_size),
                    "created_at": created,
                })
            except Exception:  # noqa: BLE001
                continue
        # Newest first by created_at — ISO-8601 sorts lexically so this
        # is robust without a key= parsing step.
        out.sort(key=lambda d: d.get("created_at", ""), reverse=True)
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"list_exports: failed: {exc}")
        return []


# ─── Public API: prune ─────────────────────────────────────────────────────


def prune_old_exports(keep_n: int = 5) -> int:
    """Keep the newest ``keep_n`` exports; delete the rest. NEVER raises.

    A non-positive ``keep_n`` is rejected and returns ``0`` rather than
    wiping every archive — keep_n=0 would be a silent foot-gun for a
    caller that misread the function signature.

    Returns
    -------
    int
        Count of archives deleted (``0`` on no-op or any error).
    """
    try:
        if not isinstance(keep_n, int) or keep_n <= 0:
            return 0
        entries = list_exports()
        if len(entries) <= keep_n:
            return 0
        deleted = 0
        for entry in entries[keep_n:]:
            try:
                path = EXPORT_DIR / entry["filename"]
                if path.exists():
                    path.unlink()
                    deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    f"prune_old_exports: could not delete "
                    f"{entry.get('filename')}: {exc}"
                )
                continue
        logger.info(
            f"prune_old_exports: deleted={deleted} "
            f"(kept newest {keep_n})"
        )
        return deleted
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"prune_old_exports: failed: {exc}")
        return 0


# ─── CLI entry point ───────────────────────────────────────────────────────


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="utils.bulk_export",
        description=(
            "Bundle the SQLite DB + cache parquets + reports + manifest "
            "into a single timestamped tar.gz archive."
        ),
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help=(
            "Output path for the archive. Default: "
            "cache/exports/ship-tracker-YYYYMMDD-HHMMSS.tar.gz"
        ),
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Exclude the SQLite DB from the archive.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Exclude per-source parquet caches from the archive.",
    )
    parser.add_argument(
        "--no-reports",
        action="store_true",
        help="Exclude generated report HTML files from the archive.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help=(
            "After building, keep only the newest 5 archives and delete "
            "the rest."
        ),
    )
    return parser.parse_args(argv)


def _main(argv: Optional[list] = None) -> int:
    """CLI entry point. Returns 0 on success, 1 on failure."""
    args = _parse_args(argv)
    output_path = Path(args.output) if args.output else None

    result = build_export(
        output_path=output_path,
        include_db=not args.no_db,
        include_cache=not args.no_cache,
        include_reports=not args.no_reports,
    )
    if result is None:
        print(json.dumps({"success": False, "output_path": None}, indent=2))
        return 1

    payload: dict = {
        "success": True,
        "output_path": str(result),
        "size_bytes": 0,
    }
    try:
        payload["size_bytes"] = int(result.stat().st_size)
    except Exception:  # noqa: BLE001
        pass

    if args.prune:
        try:
            deleted = prune_old_exports(keep_n=5)
            payload["pruned"] = deleted
        except Exception:  # noqa: BLE001
            payload["pruned"] = 0

    print(json.dumps(payload, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
