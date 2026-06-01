"""``python -m tools.backup_cli`` — backup + restore CLI for the SQLite
state DB and the generated-reports tree.

Operators occasionally need to snapshot the platform's durable state
before a risky change (a schema migration, a hand-edit of the rules
table, a bulk-acknowledge run) and roll back if something goes wrong.
Today they'd have to know the file paths and tar them by hand. This
module gives them four subcommands instead:

    python -m tools.backup_cli create   [--out PATH]
    python -m tools.backup_cli list     [--dir PATH]
    python -m tools.backup_cli restore  --from PATH  --confirm
    python -m tools.backup_cli verify   --from PATH

The CLI lives in its own module — separate from ``tools.ops_cli`` —
because the surface is large enough (four subcommands, tarfile I/O, a
manifest schema) that bolting it onto ``ops_cli`` would noticeably
bloat the argparse tree and make the operator help text harder to
scan.

What lands in a backup
----------------------
A backup tar.gz contains:

* ``ship_tracker.db`` — the SQLite DB snapshotted via the live
  connection's :meth:`sqlite3.Connection.backup` API, NOT a raw
  ``shutil.copy``. The online-backup API copies the database file at
  a consistent point — it acquires the right locks internally so the
  snapshot survives concurrent WAL writes, which a plain file copy
  cannot guarantee.
* ``cache/reports/*.html`` — the generated investor-briefing HTML
  files, if the reports dir exists.
* ``manifest.json`` — a small JSON document at the archive root
  describing the snapshot. Used by ``list`` (to pretty-print the
  table), by ``verify`` (to compare counts against the live DB), and
  by ``restore`` (to refuse a restore-forward when the backup's
  schema_version is higher than the running code).

What does NOT land in a backup
------------------------------
* Logs (``logs/``).
* Secrets (``.env``, vault-key material).
* Per-source parquet caches (``cache/<source>/*.parquet``) — those
  are derived state, re-derivable from the live API feeds. Adding
  them would make backups much larger without adding recoverability.
* The previous backups directory itself — the default backups dir is
  treated as out-of-scope to avoid recursive bloat.

Exit codes
----------
* ``0`` — handler ran cleanly.
* ``1`` — handler raised, OR a guard fired (missing ``--confirm``,
  schema-version forward, corrupted manifest). The exception/guard
  message went to stderr.
* ``2`` — argparse rejected the invocation.

The CLI must NEVER bubble an exception out to the shell; the top-level
``main`` wraps every handler in try/except → exit-1 + stderr message.
Tests rely on this contract.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger


# ─── Module-level constants ────────────────────────────────────────────────

# Anchor to the project root so the default backups dir is stable
# regardless of CWD. The same anchoring trick state.db uses.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# Default location for created backups + the default search dir for
# ``list``. Lives alongside ``cache/`` so an operator who tars the
# entire project still picks them up.
DEFAULT_BACKUP_DIR: Path = _PROJECT_ROOT / "backups"

# Filename stem used for default output paths. Date-stamped so an
# operator can tell at a glance when each archive was taken.
_FILENAME_STEM: str = "ship_tracker"
_FILENAME_TIMESTAMP_FMT: str = "%Y%m%dT%H%M%SZ"

# Tool version string baked into every manifest so future maintainers
# can spot manifests written by an old format. Bump on any breaking
# manifest-schema change. Currently "1" — keep it short, the manifest
# is human-readable on purpose.
_TOOL_VERSION: str = "1"

# Tables whose row counts get persisted into the manifest. The list is
# deliberately bounded — these are the five tables that matter for
# "did the restore round-trip the data I expected?". Adding more would
# bloat the manifest without changing what the operator actually
# checks. If the live DB doesn't have a table (e.g. a fresh checkout
# pre-v7), the count comes back as 0 rather than raising.
_MANIFEST_TABLES: tuple[str, ...] = (
    "users",
    "alerts",
    "alert_rules",
    "delivery_channels",
    "report_history",
)


# ─── Internal helpers — paths + manifests ──────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp_filename() -> str:
    """Default backup filename: ship_tracker_YYYYMMDDTHHMMSSZ.tar.gz."""
    stamp = datetime.now(timezone.utc).strftime(_FILENAME_TIMESTAMP_FMT)
    return f"{_FILENAME_STEM}_{stamp}.tar.gz"


def _resolve_db_path() -> Path:
    """Look up the live DB path through ``state.db`` so tests that
    monkeypatch ``state.db.DB_PATH`` see the redirected location.
    Falls back to the hard-coded default path when state.db is not
    importable (which only happens in pathological broken installs)."""
    try:
        from state import db as state_db
        return Path(state_db.DB_PATH)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"backup_cli: could not import state.db: {exc}")
        return _PROJECT_ROOT / "cache" / "ship_tracker.db"


def _resolve_reports_dir() -> Path:
    """Look up the live reports dir through ``utils.report_history``
    so tests that monkeypatch ``REPORT_DIR`` see the redirected
    location."""
    try:
        from utils import report_history as rh
        return Path(rh.REPORT_DIR)
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"backup_cli: could not import report_history: {exc}")
        return _PROJECT_ROOT / "cache" / "reports"


def _live_schema_version() -> int:
    """Best-effort lookup of the schema version the running code knows
    about. Defaults to 0 when state.db is unavailable so the caller
    can still compare against a manifest value (the schema-forward
    check below will refuse the restore in that degenerate case)."""
    try:
        from state import db as state_db
        return int(getattr(state_db, "SCHEMA_VERSION", 0))
    except Exception:  # noqa: BLE001
        return 0


def _count_table_rows(conn: sqlite3.Connection, table: str) -> int:
    """Return ``SELECT COUNT(*)`` for one table. Returns 0 when the
    table doesn't exist (fresh checkout, partial migration), never
    raises — the manifest is best-effort metadata, not a contract."""
    try:
        cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
        row = cur.fetchone()
        # Row may be a tuple or sqlite3.Row depending on row_factory;
        # ``row[0]`` works for both shapes.
        return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def _read_db_schema_version(conn: sqlite3.Connection) -> int:
    """Read the schema_version row out of the ``kv_state`` table.
    Returns 0 when the row (or even the table) doesn't exist — same
    semantics as ``_init_schema`` in ``state.db`` for the legacy
    pre-v1 case."""
    try:
        cur = conn.execute(
            "SELECT value FROM kv_state WHERE key = 'schema_version'"
        )
        row = cur.fetchone()
        if row is None:
            return 0
        return int(row[0])
    except Exception:  # noqa: BLE001
        return 0


# ─── SQLite online-backup helpers ──────────────────────────────────────────


def _snapshot_db_to(src_path: Path, dest_path: Path) -> None:
    """Use SQLite's online-backup API to copy ``src_path`` into a brand-
    new file at ``dest_path``.

    ``Connection.backup`` is the only safe way to snapshot a live DB
    while the WAL writer may be mid-flush — a plain ``shutil.copy`` of
    the .db file races against the -wal / -shm sidecars. The backup
    API copies the database file at a transactionally-consistent
    point and writes a fully self-contained .db at the destination
    (no WAL sidecar required to read it).

    Both connections are closed before the function returns so the
    caller can immediately tar the dest file without worrying about
    dangling file handles on Windows.
    """
    src_conn = sqlite3.connect(str(src_path))
    try:
        # New file at dest — the backup API materializes it on first
        # write. We open it on a fresh connection (NOT the source's
        # connection) per the SQLite docs.
        dest_conn = sqlite3.connect(str(dest_path))
        try:
            # pages=-1 means "copy everything in one call" — for a few
            # MB of DB this is faster than the chunked variant and
            # there's no UI/progress indicator to keep responsive in
            # the CLI case anyway.
            src_conn.backup(dest_conn, pages=-1)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


# ─── Internal helpers — output formatting ──────────────────────────────────


def _print_kv(payload: dict) -> None:
    """Render a flat dict as ``key: value`` lines. Used by ``create``
    and ``restore`` to summarize what happened."""
    if not payload:
        print("(empty)")
        return
    width = max(len(str(k)) for k in payload.keys())
    for k, v in payload.items():
        print(f"{str(k).ljust(width)} : {v}")


def _print_table(rows: list[dict], columns: Optional[list[str]] = None) -> None:
    """Fixed-width ASCII table. Same shape as ``tools.ops_cli._print_table``
    so the output style stays consistent across the two CLIs."""
    if not rows:
        print("(no rows)")
        return
    cols = columns if columns is not None else list(rows[0].keys())
    widths: dict[str, int] = {}
    for c in cols:
        max_val_len = max((len(str(r.get(c, ""))) for r in rows), default=0)
        widths[c] = max(len(str(c)), max_val_len)

    def _fmt_row(values: list[str]) -> str:
        return "  ".join(values[i].ljust(widths[cols[i]]) for i in range(len(cols)))

    header = _fmt_row([str(c) for c in cols])
    sep = "  ".join("-" * widths[c] for c in cols)
    print(header)
    print(sep)
    for r in rows:
        print(_fmt_row([str(r.get(c, "")) for c in cols]))


def _human_size(size_bytes: int) -> str:
    """Format a byte count as KB/MB so the operator can eyeball the
    archive size without counting digits."""
    try:
        n = float(size_bytes)
    except Exception:  # noqa: BLE001
        return str(size_bytes)
    if n < 1024:
        return f"{int(n)}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    return f"{n / (1024 * 1024):.1f}MB"


# ─── Manifest read/write ───────────────────────────────────────────────────


@dataclass
class Manifest:
    """In-memory view of a backup's ``manifest.json``.

    Stored as a dataclass for type-clarity, serialized as a plain dict
    (``to_dict``) when we write it back to the tarball — keeping the
    on-disk shape JSON-only means a human can ``tar xzf …
    manifest.json`` and read it without our code present.
    """

    schema_version: int
    created_at: str
    tables: dict[str, int]
    hostname: str
    tool_version: str

    def to_dict(self) -> dict:
        return {
            "schema_version": int(self.schema_version),
            "created_at": str(self.created_at),
            "tables": {str(k): int(v) for k, v in self.tables.items()},
            "hostname": str(self.hostname),
            "tool_version": str(self.tool_version),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        # Defensive: cast each field. A corrupted manifest gets caught
        # here and raises ValueError, which ``verify`` reports as a
        # failed check rather than crashing the CLI.
        try:
            tables_raw = data.get("tables", {}) or {}
            if not isinstance(tables_raw, dict):
                raise ValueError("manifest.tables is not a dict")
            return cls(
                schema_version=int(data["schema_version"]),
                created_at=str(data["created_at"]),
                tables={str(k): int(v) for k, v in tables_raw.items()},
                hostname=str(data.get("hostname", "")),
                tool_version=str(data.get("tool_version", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"corrupted manifest: {exc}") from exc


def _build_manifest_for_db(db_path: Path) -> Manifest:
    """Open ``db_path`` read-only-ish (autocommit, no schema init) and
    materialize a Manifest from it. Used by ``create`` right before
    tar.

    We deliberately open a FRESH connection here rather than going
    through ``state.db.get_connection`` — that helper runs
    ``_init_schema`` on every open, which would mutate a snapshot DB
    we just took. Manifest construction must be read-only.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        schema_v = _read_db_schema_version(conn)
        tables = {t: _count_table_rows(conn, t) for t in _MANIFEST_TABLES}
    finally:
        conn.close()

    return Manifest(
        schema_version=int(schema_v),
        created_at=_now_iso(),
        tables=tables,
        # gethostname can technically raise on weird systems; fall back
        # to empty string so the manifest is still well-formed.
        hostname=_safe_hostname(),
        tool_version=_TOOL_VERSION,
    )


def _safe_hostname() -> str:
    try:
        return socket.gethostname() or ""
    except Exception:  # noqa: BLE001
        return ""


# ─── Tar helpers ───────────────────────────────────────────────────────────


def _safe_extract_member(tar: tarfile.TarFile, member: tarfile.TarInfo,
                         dest_root: Path) -> None:
    """Extract one tar member into ``dest_root`` after guarding against
    a path-traversal payload (member name starts with ``/`` or contains
    ``..``). Refuses anything that would escape ``dest_root``.

    Python's tarfile API will happily extract a member with an
    absolute path or one that contains ``..`` segments, which a
    malicious archive could use to write outside the intended dir.
    We're the only producer of these archives in practice, but the
    CLI may be pointed at a hand-rolled file via ``--from`` so the
    check is cheap insurance.
    """
    # Strip leading slashes / drive letters and resolve the join.
    member_path = (dest_root / member.name).resolve()
    if not str(member_path).startswith(str(dest_root.resolve())):
        raise ValueError(
            f"unsafe tar member path: {member.name!r} "
            f"would escape extraction dir"
        )
    tar.extract(member, dest_root)


def _extract_all(tar_path: Path, dest_root: Path) -> None:
    """Extract every member of the tar at ``tar_path`` into ``dest_root``,
    refusing path-traversal payloads via ``_safe_extract_member``."""
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            _safe_extract_member(tar, member, dest_root)


def _read_manifest_from_tar(tar_path: Path) -> Manifest:
    """Open ``tar_path``, find ``manifest.json``, decode it. Raises
    ValueError if missing or malformed — the caller maps that to an
    exit-1 with a clean stderr message."""
    with tarfile.open(tar_path, "r:gz") as tar:
        try:
            member = tar.getmember("manifest.json")
        except KeyError as exc:
            raise ValueError("backup archive missing manifest.json") from exc
        f = tar.extractfile(member)
        if f is None:
            raise ValueError("manifest.json could not be opened")
        try:
            data = json.loads(f.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"manifest.json not valid JSON: {exc}") from exc
    return Manifest.from_dict(data)


# ─── Subcommand: create ────────────────────────────────────────────────────


def _cmd_create(args: argparse.Namespace) -> None:
    """Snapshot the live DB + the reports dir into a fresh tar.gz.

    Steps:
      1. Resolve the live DB path + reports dir (honouring monkeypatched
         test paths).
      2. Stage a snapshot of the DB into a tempdir via the online-
         backup API.
      3. Build the manifest from the snapshot (NOT the live DB — the
         row counts inside the snapshot must match what a later
         ``verify`` will see).
      4. tarfile.open(target, 'w:gz') and add manifest + DB + reports.
      5. Print the output path + size.
    """
    db_path = _resolve_db_path()
    reports_dir = _resolve_reports_dir()

    if not db_path.exists():
        raise RuntimeError(
            f"DB file not found at {db_path} — nothing to back up. "
            "Run the app once or initialize the schema first."
        )

    # Resolve the output path. Default lives under ./backups/ so a
    # plain `python -m tools.backup_cli create` is self-contained.
    if args.out:
        target = Path(args.out)
    else:
        target = DEFAULT_BACKUP_DIR / _timestamp_filename()
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="ship_backup_") as staging:
        staging_dir = Path(staging)
        staged_db = staging_dir / "ship_tracker.db"

        # Use the online-backup API so concurrent WAL writes don't
        # corrupt the snapshot.
        _snapshot_db_to(db_path, staged_db)

        # Build the manifest from the SNAPSHOT — its row counts are
        # the ones a later verify/restore will see, not necessarily
        # what the live DB has now.
        manifest = _build_manifest_for_db(staged_db)
        manifest_path = staging_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True),
            encoding="utf-8",
        )

        # Now build the tarball. Everything below this point is
        # one-shot; a partial archive on failure is unlinked so an
        # operator does not mistake it for a good backup.
        try:
            with tarfile.open(target, "w:gz") as tar:
                # Manifest first so `tar tzf …` shows it at the top.
                tar.add(manifest_path, arcname="manifest.json")
                tar.add(staged_db, arcname="ship_tracker.db")

                # Reports — only HTML files directly under the reports
                # dir. Skipped silently when the dir does not exist
                # (a fresh checkout). The arcname keeps the relative
                # path so an operator browsing the archive sees the
                # familiar `cache/reports/<file>` layout.
                if reports_dir.exists() and reports_dir.is_dir():
                    for report in sorted(reports_dir.glob("*.html")):
                        if not report.is_file():
                            continue
                        arcname = str(
                            Path("cache") / "reports" / report.name
                        )
                        tar.add(report, arcname=arcname)
        except Exception:
            # Clean up the partial tar so a retry starts from a blank
            # slate and a later `list` does not show a corrupted entry.
            try:
                if target.exists():
                    target.unlink()
            except Exception:  # noqa: BLE001
                pass
            raise

    size_bytes = int(target.stat().st_size) if target.exists() else 0
    _print_kv({
        "output_path": str(target),
        "size":        _human_size(size_bytes),
        "size_bytes":  size_bytes,
        "schema_version": int(manifest.schema_version),
        "users":       int(manifest.tables.get("users", 0)),
        "alerts":      int(manifest.tables.get("alerts", 0)),
        "alert_rules": int(manifest.tables.get("alert_rules", 0)),
        "delivery_channels": int(manifest.tables.get("delivery_channels", 0)),
        "report_history":    int(manifest.tables.get("report_history", 0)),
    })


# ─── Subcommand: list ──────────────────────────────────────────────────────


def _cmd_list(args: argparse.Namespace) -> None:
    """Glob the backups dir for ``ship_tracker_*.tar.gz``, peek at each
    manifest, print a table sorted oldest-first.

    A backup whose manifest is corrupted or missing shows up with
    ``schema_version=?`` and empty counts rather than crashing the
    whole listing — a single bad archive should not hide the rest.
    """
    backup_dir = Path(args.dir) if args.dir else DEFAULT_BACKUP_DIR
    if not backup_dir.exists() or not backup_dir.is_dir():
        print("(no backups)")
        return

    rows: list[dict] = []
    for entry in sorted(backup_dir.glob(f"{_FILENAME_STEM}_*.tar.gz")):
        if not entry.is_file():
            continue
        try:
            stat = entry.stat()
            size_bytes = int(stat.st_size)
            try:
                manifest = _read_manifest_from_tar(entry)
                schema_v: Any = int(manifest.schema_version)
                tables = manifest.tables
                created = manifest.created_at
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    f"backup_cli list: skipping unreadable manifest in "
                    f"{entry.name}: {exc}"
                )
                schema_v = "?"
                tables = {t: 0 for t in _MANIFEST_TABLES}
                # Fall back to file mtime so the operator at least sees
                # *when* the file landed.
                created = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()

            rows.append({
                "filename":          entry.name,
                "created_at":        created,
                "schema":            schema_v,
                "size":              _human_size(size_bytes),
                "users":             tables.get("users", 0),
                "alerts":            tables.get("alerts", 0),
                "alert_rules":       tables.get("alert_rules", 0),
                "delivery_channels": tables.get("delivery_channels", 0),
                "report_history":    tables.get("report_history", 0),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"backup_cli list: skipping {entry}: {exc}")
            continue

    if not rows:
        print("(no backups)")
        return

    # Oldest first — ISO-8601 sorts lexically so this is just a
    # straight key=created_at sort. Operators piping into `tail -n 1`
    # then get the newest entry.
    rows.sort(key=lambda r: str(r.get("created_at", "")))
    _print_table(
        rows,
        columns=[
            "filename", "created_at", "schema", "size",
            "users", "alerts", "alert_rules",
            "delivery_channels", "report_history",
        ],
    )


# ─── Subcommand: verify ────────────────────────────────────────────────────


def _cmd_verify(args: argparse.Namespace) -> None:
    """Open the backup tarball, extract manifest + DB to a tmpdir,
    re-open the DB, and check the recorded counts/schema against
    what's actually inside.

    Prints one PASS / FAIL line per check; if ANY check fails, exits 1
    via a RuntimeError so a scripted caller can detect the failure.
    """
    src = Path(args.from_path)
    if not src.exists():
        raise RuntimeError(f"backup not found at {src}")

    with tempfile.TemporaryDirectory(prefix="ship_verify_") as staging:
        staging_dir = Path(staging)

        # Extract everything so we can inspect both the manifest and
        # the staged DB. The dest dir is per-test (tmp_path in tests)
        # so we never collide with a concurrent verify.
        _extract_all(src, staging_dir)

        # Manifest comes from the archive root.
        manifest_path = staging_dir / "manifest.json"
        if not manifest_path.exists():
            raise RuntimeError("backup is missing manifest.json")
        try:
            manifest = Manifest.from_dict(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise RuntimeError(f"manifest.json invalid: {exc}") from exc

        # DB comes from the archive root too.
        staged_db = staging_dir / "ship_tracker.db"
        if not staged_db.exists():
            raise RuntimeError("backup is missing ship_tracker.db")

        # Open the snapshot DB and re-derive the same numbers the
        # manifest claims it captured.
        conn = sqlite3.connect(str(staged_db))
        try:
            db_schema = _read_db_schema_version(conn)
            db_tables = {
                t: _count_table_rows(conn, t)
                for t in _MANIFEST_TABLES
            }
        finally:
            conn.close()

    # Build the check list. Each entry is (check name, pass?, detail).
    checks: list[tuple[str, bool, str]] = []
    checks.append((
        "schema_version matches manifest",
        db_schema == manifest.schema_version,
        f"db={db_schema} manifest={manifest.schema_version}",
    ))
    for table in _MANIFEST_TABLES:
        m = int(manifest.tables.get(table, 0))
        d = int(db_tables.get(table, 0))
        checks.append((
            f"{table} row count matches manifest",
            m == d,
            f"db={d} manifest={m}",
        ))

    # Print the results — one line per check, PASS / FAIL prefix so a
    # quick `grep FAIL` answers "did anything go wrong?".
    print(f"verify: {src.name}")
    print(f"  created_at: {manifest.created_at}")
    print(f"  schema_version: {manifest.schema_version}")
    any_failed = False
    for name, ok, detail in checks:
        label = "PASS" if ok else "FAIL"
        print(f"  {label}  {name}  ({detail})")
        if not ok:
            any_failed = True

    if any_failed:
        # Raise so the CLI returns 1, matching the contract that a
        # failed handler exits non-zero. The message lands on stderr.
        raise RuntimeError("one or more verify checks failed")


# ─── Subcommand: restore ───────────────────────────────────────────────────


def _cmd_restore(args: argparse.Namespace) -> None:
    """Restore the live DB from a backup tar.gz.

    Hard requirements (each one independently exits 1 if violated):
      * ``--confirm`` must be passed. Restore is destructive — it
        overwrites ``cache/ship_tracker.db`` — so the flag is the
        operator's explicit ack of that.
      * The backup's ``manifest.schema_version`` must be <= the
        currently-running ``state.db.SCHEMA_VERSION``. Restoring a
        backup taken on a NEWER schema would feed the running code a
        DB it does not know how to read; the safest stance is to
        refuse and tell the operator to upgrade the running code
        first.
      * The tarball must contain a readable ``ship_tracker.db``.

    On success the DB is swapped atomically via ``os.replace`` so a
    concurrent reader either sees the old DB or the new DB, never a
    half-written file.
    """
    if not args.confirm:
        raise RuntimeError(
            "this will overwrite cache/ship_tracker.db; pass --confirm"
        )

    src = Path(args.from_path)
    if not src.exists():
        raise RuntimeError(f"backup not found at {src}")

    # Read the manifest before we extract anything so the
    # schema-forward check can fire without writing to disk.
    manifest = _read_manifest_from_tar(src)
    live_schema = _live_schema_version()
    if int(manifest.schema_version) > int(live_schema):
        raise RuntimeError(
            f"refusing to restore: backup schema_version="
            f"{manifest.schema_version} > running code SCHEMA_VERSION="
            f"{live_schema}. Upgrade the running code first."
        )

    db_target = _resolve_db_path()
    reports_target = _resolve_reports_dir()

    # Drop the live connection cache so the swap below doesn't race
    # with a Streamlit reader still holding the old file handle.
    # ``reset_for_tests`` is a misnomer — it is the right primitive
    # for "close the cached connection, please re-open on next use".
    try:
        from state import db as state_db
        state_db.reset_for_tests()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"backup_cli restore: connection reset skipped: {exc}")

    with tempfile.TemporaryDirectory(
        prefix="ship_restore_",
        # IMPORTANT: place the tempdir on the same filesystem as the
        # target so ``os.replace`` can be atomic. The DB lives under
        # the project root, so anchor the tempdir there.
        dir=str(db_target.parent),
    ) as staging:
        staging_dir = Path(staging)

        # Extract the archive into the staging dir (path-traversal
        # check fires inside _safe_extract_member).
        _extract_all(src, staging_dir)

        staged_db = staging_dir / "ship_tracker.db"
        if not staged_db.exists():
            raise RuntimeError("backup is missing ship_tracker.db")

        # Sanity-check the snapshot opens as a DB — protects against
        # an operator pointing --from at a random tar.gz that happens
        # to contain a file named ship_tracker.db.
        try:
            test_conn = sqlite3.connect(str(staged_db))
            try:
                test_conn.execute("SELECT 1 FROM kv_state LIMIT 1")
            finally:
                test_conn.close()
        except Exception as exc:
            raise RuntimeError(
                f"backup DB unreadable: {exc}"
            ) from exc

        # Make sure the target dir exists before we replace into it.
        db_target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic swap — on POSIX (and Windows since 3.3) os.replace
        # either succeeds entirely or leaves the original file in
        # place. WAL/SHM sidecars from the old DB are stale after the
        # swap; unlink them so the next open creates fresh ones for
        # the restored DB.
        os.replace(staged_db, db_target)
        for sidecar in ("-wal", "-shm"):
            side_path = Path(str(db_target) + sidecar)
            try:
                if side_path.exists():
                    side_path.unlink()
            except Exception:  # noqa: BLE001
                pass

        # Reports — restore by directory replacement so a removed
        # report in the backup is also removed live (matches what a
        # "restore" semantically means). Skipped when the backup
        # carries no reports tree.
        archived_reports_root = staging_dir / "cache" / "reports"
        n_reports_restored = 0
        if archived_reports_root.exists() and archived_reports_root.is_dir():
            reports_target.parent.mkdir(parents=True, exist_ok=True)
            # Best-effort wipe of the live reports dir, then copy
            # everything from the archive over.
            if reports_target.exists():
                try:
                    shutil.rmtree(reports_target)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(
                        f"backup_cli restore: reports wipe skipped: {exc}"
                    )
            shutil.copytree(archived_reports_root, reports_target)
            n_reports_restored = sum(
                1 for _ in reports_target.glob("*.html")
            )

    _print_kv({
        "restored_from":   str(src),
        "db_target":       str(db_target),
        "schema_version":  int(manifest.schema_version),
        "reports_restored": int(n_reports_restored),
        "users":           int(manifest.tables.get("users", 0)),
        "alerts":          int(manifest.tables.get("alerts", 0)),
        "alert_rules":     int(manifest.tables.get("alert_rules", 0)),
        "delivery_channels": int(manifest.tables.get("delivery_channels", 0)),
        "report_history":    int(manifest.tables.get("report_history", 0)),
    })


# ─── Argparse wiring ───────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Construct the top-level parser + every subparser. Mirrors the
    factory style in ``tools.ops_cli._build_parser`` so the two CLIs
    feel the same to operators."""
    p = argparse.ArgumentParser(
        prog="python -m tools.backup_cli",
        description=(
            "Backup + restore CLI for the Ship Tracker SQLite state DB "
            "and the generated-reports tree."
        ),
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # ── create ────────────────────────────────────────────────────────────
    sc = sub.add_parser(
        "create",
        help="Snapshot the DB + reports into a new tar.gz backup",
    )
    sc.add_argument(
        "--out",
        default=None,
        help=(
            "Output path. Default: "
            "./backups/ship_tracker_<timestamp>.tar.gz"
        ),
    )
    sc.set_defaults(func=_cmd_create)

    # ── list ──────────────────────────────────────────────────────────────
    sl = sub.add_parser(
        "list",
        help="List backups in a directory (default: ./backups)",
    )
    sl.add_argument(
        "--dir",
        default=None,
        help=f"Directory to scan. Default: {DEFAULT_BACKUP_DIR}",
    )
    sl.set_defaults(func=_cmd_list)

    # ── verify ────────────────────────────────────────────────────────────
    sv = sub.add_parser(
        "verify",
        help="Open a backup and check its manifest + row counts",
    )
    sv.add_argument(
        "--from",
        dest="from_path",
        required=True,
        help="Path to the backup tar.gz to verify",
    )
    sv.set_defaults(func=_cmd_verify)

    # ── restore ───────────────────────────────────────────────────────────
    sr = sub.add_parser(
        "restore",
        help=(
            "Restore the live DB from a backup tar.gz "
            "(REQUIRES --confirm)"
        ),
    )
    sr.add_argument(
        "--from",
        dest="from_path",
        required=True,
        help="Path to the backup tar.gz to restore",
    )
    sr.add_argument(
        "--confirm",
        action="store_true",
        help=(
            "Required for destructive restore — confirms you accept "
            "the live DB will be overwritten."
        ),
    )
    sr.set_defaults(func=_cmd_restore)

    return p


# ─── Entry point ───────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    """Parse ``argv`` and dispatch to the matching handler. Mirrors the
    contract of ``tools.ops_cli.main``:

      * ``0`` — handler ran cleanly.
      * ``1`` — handler raised; the message went to stderr.
      * ``2`` — argparse rejected the invocation (missing/unknown flag).

    Tests call this directly with a synthetic ``argv``.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse raises SystemExit on bad input. Normalise the code
        # so tests don't depend on the platform default.
        code = exc.code if isinstance(exc.code, int) else 0
        return code

    handler: Optional[Callable[[argparse.Namespace], None]] = getattr(
        args, "func", None
    )
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        handler(args)
        return 0
    except Exception as exc:  # noqa: BLE001 — top-level guard by contract
        # Single-line stderr message; tests assert no traceback ever
        # leaks out to the shell.
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
