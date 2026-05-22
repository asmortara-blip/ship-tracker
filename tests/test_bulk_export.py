"""Tests for ``utils.bulk_export`` — durable-state archive utility.

Covers:
  * ExportManifest dataclass shape & defaults.
  * build_export produces a tarfile that re-opens cleanly via tarfile.open.
  * The archive contains MANIFEST.json + the DB file + cache parquets +
    report HTML files.
  * include_db=False / include_cache=False / include_reports=False each
    independently exclude their slice from the archive.
  * Empty cache + no DB still produces a valid (small) archive.
  * build_export never raises on simulated failures (mock tarfile.open
    to throw).
  * list_exports returns newest-first.
  * prune_old_exports keep_n=2 across 5 exports deletes the 3 oldest.
  * Audit hook fires with safe path detail (no home-dir leak).
  * CLI _main exits 0 on success, 1 on failure.

The ``isolated_state_db`` fixture redirects ``state.db.DB_PATH`` to a
tmp_path so every test gets a fresh DB and an isolated exports dir.
"""
from __future__ import annotations

import json
import tarfile
import time
from dataclasses import fields
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect every persistence path to a per-test tmp_path so no test
    touches the real cache/ tree or the real DB."""
    from state import db as state_db
    from utils import bulk_export as be
    from utils import report_history as rh

    # Lay out a synthetic cache/ inside tmp_path that mirrors the real
    # one: cache/ship_tracker.db, cache/<source>/*.parquet, cache/reports.
    tmp_cache = tmp_path / "cache"
    tmp_cache.mkdir(parents=True, exist_ok=True)
    tmp_reports = tmp_cache / "reports"
    tmp_reports.mkdir(parents=True, exist_ok=True)
    tmp_exports = tmp_cache / "exports"
    # exports/ is created lazily by build_export — do NOT pre-create.

    monkeypatch.setattr(state_db, "DB_PATH", tmp_cache / "ship_tracker.db")
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_reports)
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_reports / "report_index.json")
    monkeypatch.setattr(be, "EXPORT_DIR", tmp_exports)

    state_db.reset_for_tests()
    yield tmp_cache
    state_db.reset_for_tests()


def _seed_cache(cache_root: Path) -> dict:
    """Populate the tmp cache with a DB row + a parquet + a report HTML.

    Returns a dict of paths the tests can poke at directly.
    """
    # 1. Touch the DB so state.db actually creates the file. We don't
    # need any rows — just the file on disk.
    from state.db import get_connection
    conn = get_connection()
    conn.execute(
        "INSERT INTO kv_state (key, value, updated_at) "
        "VALUES ('test', '1', '2026-05-22')"
    )

    # 2. Create a parquet file under cache/fred/ — content can be a
    # one-byte placeholder; bulk_export only cares about the path.
    fred_dir = cache_root / "fred"
    fred_dir.mkdir(parents=True, exist_ok=True)
    parquet_file = fred_dir / "test_series.parquet"
    parquet_file.write_bytes(b"PAR1FAKE")

    # 3. Create an HTML report under cache/reports/.
    reports_dir = cache_root / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    html_file = reports_dir / "report_20260522.html"
    html_file.write_text("<html>fake</html>", encoding="utf-8")

    return {
        "db": cache_root / "ship_tracker.db",
        "parquet": parquet_file,
        "html": html_file,
    }


# ─── ExportManifest dataclass shape ────────────────────────────────────────


def test_export_manifest_fields_are_exactly_eight() -> None:
    """The dataclass exposes exactly the eight documented fields."""
    from utils.bulk_export import ExportManifest

    field_names = {f.name for f in fields(ExportManifest)}
    assert field_names == {
        "generated_at",
        "schema_version",
        "db_size_bytes",
        "parquet_count",
        "report_count",
        "includes_db",
        "includes_cache",
        "includes_reports",
    }


def test_export_manifest_is_json_serializable() -> None:
    """An ExportManifest round-trips through dataclasses.asdict + json.dumps."""
    from dataclasses import asdict

    from utils.bulk_export import ExportManifest

    m = ExportManifest(
        generated_at="2026-05-22T00:00:00+00:00",
        schema_version=12,
        db_size_bytes=1234,
        parquet_count=3,
        report_count=2,
        includes_db=True,
        includes_cache=True,
        includes_reports=True,
    )
    payload = json.dumps(asdict(m))
    parsed = json.loads(payload)
    assert parsed["schema_version"] == 12
    assert parsed["parquet_count"] == 3


# ─── build_export: happy path ───────────────────────────────────────────────


def test_build_export_returns_a_real_tarfile(isolated_state_db) -> None:
    """The output path is a tar.gz that opens cleanly with tarfile.open."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export()
    assert out is not None
    assert out.exists()
    assert out.suffixes[-2:] == [".tar", ".gz"]

    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
    # Sanity — the tar must have at least the manifest.
    assert "MANIFEST.json" in names


def test_build_export_archive_contains_manifest_db_and_parquet(
    isolated_state_db,
) -> None:
    """The archive holds MANIFEST.json + ship_tracker.db + the parquet."""
    paths = _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export()
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
    assert "MANIFEST.json" in names
    # DB lands at the archive root.
    assert paths["db"].name in names
    # Parquet preserved under cache/<source>/
    assert "cache/fred/test_series.parquet" in names
    # Report preserved under cache/reports/
    assert "cache/reports/report_20260522.html" in names


def test_build_export_manifest_payload_is_correct(isolated_state_db) -> None:
    """The MANIFEST.json inside the tar matches the actual archive contents."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export()
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        manifest_member = tar.extractfile("MANIFEST.json")
        assert manifest_member is not None
        payload = json.loads(manifest_member.read().decode("utf-8"))

    assert payload["includes_db"] is True
    assert payload["includes_cache"] is True
    assert payload["includes_reports"] is True
    assert payload["parquet_count"] == 1
    assert payload["report_count"] == 1
    assert payload["db_size_bytes"] > 0
    assert payload["schema_version"] >= 12


def test_build_export_respects_custom_output_path(
    isolated_state_db, tmp_path
) -> None:
    """An explicit output_path overrides the default exports dir."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    custom = tmp_path / "custom" / "myexport.tar.gz"
    out = build_export(output_path=custom)
    assert out == custom
    assert out.exists()
    with tarfile.open(out, "r:gz") as tar:
        assert "MANIFEST.json" in tar.getnames()


# ─── build_export: flag exclusions ──────────────────────────────────────────


def test_build_export_no_db_excludes_db(isolated_state_db) -> None:
    """include_db=False produces a tar without the DB file."""
    paths = _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export(include_db=False)
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
        manifest_member = tar.extractfile("MANIFEST.json")
        payload = json.loads(manifest_member.read().decode("utf-8"))
    assert paths["db"].name not in names
    assert payload["includes_db"] is False
    assert payload["db_size_bytes"] == 0


def test_build_export_no_cache_excludes_parquets(isolated_state_db) -> None:
    """include_cache=False produces a tar without parquet files."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export(include_cache=False)
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
        manifest_member = tar.extractfile("MANIFEST.json")
        payload = json.loads(manifest_member.read().decode("utf-8"))
    assert "cache/fred/test_series.parquet" not in names
    assert payload["includes_cache"] is False
    assert payload["parquet_count"] == 0


def test_build_export_no_reports_excludes_html(isolated_state_db) -> None:
    """include_reports=False produces a tar without HTML reports."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import build_export

    out = build_export(include_reports=False)
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
        manifest_member = tar.extractfile("MANIFEST.json")
        payload = json.loads(manifest_member.read().decode("utf-8"))
    assert "cache/reports/report_20260522.html" not in names
    assert payload["includes_reports"] is False
    assert payload["report_count"] == 0


# ─── build_export: edge cases ───────────────────────────────────────────────


def test_build_export_empty_cache_no_db_still_valid(isolated_state_db) -> None:
    """An empty cache + no DB still produces a valid (small) archive
    containing at least the manifest."""
    # Do NOT seed — let the cache stay empty.
    from utils.bulk_export import build_export

    out = build_export(include_db=False)
    assert out is not None
    assert out.exists()
    with tarfile.open(out, "r:gz") as tar:
        names = tar.getnames()
        manifest_member = tar.extractfile("MANIFEST.json")
        payload = json.loads(manifest_member.read().decode("utf-8"))
    assert "MANIFEST.json" in names
    assert payload["parquet_count"] == 0
    assert payload["report_count"] == 0


def test_build_export_skips_exports_subdir(isolated_state_db) -> None:
    """A pre-existing tar.gz inside cache/exports/ must NOT appear inside
    a newly-built archive — would cause unbounded recursive bloat."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import EXPORT_DIR, build_export

    # Place a fake prior export inside exports/. Its content is
    # irrelevant — bulk_export must skip the entire exports/ subdir.
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    fake_prior = EXPORT_DIR / "ship-tracker-20260101-000000.tar.gz"
    fake_prior.write_bytes(b"PRIOR_EXPORT")

    out = build_export()
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
    # No member must reference the exports/ subdir.
    for n in names:
        assert "exports/" not in n


def test_build_export_never_raises_on_tarfile_failure(isolated_state_db) -> None:
    """A tarfile.open exception must NOT propagate — returns None."""
    _seed_cache(isolated_state_db)
    from utils import bulk_export

    with patch.object(
        bulk_export.tarfile,
        "open",
        side_effect=OSError("disk full"),
    ):
        out = bulk_export.build_export()
    assert out is None


def test_build_export_never_raises_on_db_copy_failure(isolated_state_db) -> None:
    """A shutil.copy2 exception is logged but the export still succeeds
    (without the DB) — failures elsewhere should not abort the entire
    archive build."""
    _seed_cache(isolated_state_db)
    from utils import bulk_export

    with patch.object(
        bulk_export.shutil,
        "copy2",
        side_effect=OSError("permission denied"),
    ):
        out = bulk_export.build_export()
    # Export should still produce a tar; the manifest just records
    # includes_db=False for the failed-copy case.
    assert out is not None
    with tarfile.open(out, "r:gz") as tar:
        manifest_member = tar.extractfile("MANIFEST.json")
        payload = json.loads(manifest_member.read().decode("utf-8"))
    assert payload["includes_db"] is False
    assert payload["db_size_bytes"] == 0


# ─── list_exports ───────────────────────────────────────────────────────────


def test_list_exports_returns_empty_when_dir_missing(isolated_state_db) -> None:
    """No exports dir → empty list (not an exception)."""
    from utils.bulk_export import EXPORT_DIR, list_exports

    # Make sure the dir doesn't exist.
    assert not EXPORT_DIR.exists()
    assert list_exports() == []


def test_list_exports_returns_newest_first(isolated_state_db) -> None:
    """Older archives sort after newer ones."""
    from utils.bulk_export import EXPORT_DIR, list_exports

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    older = EXPORT_DIR / "ship-tracker-20260101-000000.tar.gz"
    newer = EXPORT_DIR / "ship-tracker-20260520-000000.tar.gz"
    older.write_bytes(b"OLD")
    newer.write_bytes(b"NEW")

    # Force the mtimes so the test does not rely on filesystem-level
    # second resolution between two writes inside the same tick.
    now = time.time()
    import os as _os
    _os.utime(older, (now - 86400, now - 86400))  # 1 day ago
    _os.utime(newer, (now, now))

    entries = list_exports()
    assert len(entries) == 2
    assert entries[0]["filename"] == newer.name
    assert entries[1]["filename"] == older.name


def test_list_exports_includes_size_bytes(isolated_state_db) -> None:
    """size_bytes reflects the actual file size on disk."""
    from utils.bulk_export import EXPORT_DIR, list_exports

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    f = EXPORT_DIR / "ship-tracker-20260520-120000.tar.gz"
    f.write_bytes(b"X" * 100)

    entries = list_exports()
    assert len(entries) == 1
    assert entries[0]["size_bytes"] == 100


# ─── prune_old_exports ──────────────────────────────────────────────────────


def test_prune_old_exports_keeps_newest_n(isolated_state_db) -> None:
    """5 archives, keep_n=2 → deletes the 3 oldest."""
    from utils.bulk_export import EXPORT_DIR, list_exports, prune_old_exports

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    paths = []
    import os as _os
    now = time.time()
    for i in range(5):
        p = EXPORT_DIR / f"ship-tracker-2026010{i + 1}-000000.tar.gz"
        p.write_bytes(b"FAKE")
        # Set mtime so the 5 files have a strict ordering (i=0 oldest).
        _os.utime(p, (now - (5 - i) * 86400, now - (5 - i) * 86400))
        paths.append(p)

    assert len(list_exports()) == 5

    deleted = prune_old_exports(keep_n=2)
    assert deleted == 3
    survivors = {e["filename"] for e in list_exports()}
    # The two newest (i=3, i=4) should survive.
    assert paths[3].name in survivors
    assert paths[4].name in survivors
    # The three oldest are gone.
    assert paths[0].name not in survivors
    assert paths[1].name not in survivors
    assert paths[2].name not in survivors


def test_prune_old_exports_noop_when_under_limit(isolated_state_db) -> None:
    """fewer archives than keep_n → 0 deletions, no errors."""
    from utils.bulk_export import EXPORT_DIR, prune_old_exports

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / "ship-tracker-20260101-000000.tar.gz").write_bytes(b"A")
    (EXPORT_DIR / "ship-tracker-20260102-000000.tar.gz").write_bytes(b"B")

    deleted = prune_old_exports(keep_n=5)
    assert deleted == 0


def test_prune_old_exports_rejects_non_positive_keep_n(
    isolated_state_db,
) -> None:
    """keep_n=0 or negative must NOT wipe every archive — returns 0."""
    from utils.bulk_export import EXPORT_DIR, prune_old_exports

    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    f = EXPORT_DIR / "ship-tracker-20260101-000000.tar.gz"
    f.write_bytes(b"A")

    assert prune_old_exports(keep_n=0) == 0
    assert prune_old_exports(keep_n=-1) == 0
    # The archive must still exist after a bad keep_n.
    assert f.exists()


# ─── Audit hook ─────────────────────────────────────────────────────────────


def test_build_export_fires_audit_hook_with_safe_path(isolated_state_db) -> None:
    """The audit row records the export and only stores a filename
    (no absolute home dir leak)."""
    _seed_cache(isolated_state_db)
    from auth.audit import query_audit
    from utils.bulk_export import build_export

    out = build_export()
    assert out is not None

    rows = query_audit(action="bulk_export", limit=10)
    assert len(rows) >= 1
    row = rows[0]
    assert row.action == "bulk_export"
    detail = row.detail_json
    # Path detail is just the filename — no leading "/" and no
    # "/Users/" or "/home/" prefix.
    assert "output_path" in detail
    safe_path = detail["output_path"]
    assert "/" not in safe_path
    assert "Users" not in safe_path
    assert "home" not in safe_path
    assert safe_path == out.name
    # size_bytes is recorded and > 0.
    assert int(detail.get("size_bytes", 0)) > 0


def test_build_export_audit_hook_failure_does_not_block_export(
    isolated_state_db,
) -> None:
    """If record_audit blows up, build_export must still succeed."""
    _seed_cache(isolated_state_db)
    from utils import bulk_export

    with patch("auth.audit.record_audit", side_effect=RuntimeError("audit dead")):
        out = bulk_export.build_export()
    assert out is not None
    assert out.exists()


# ─── CLI ─────────────────────────────────────────────────────────────────────


def test_cli_main_returns_zero_on_success(isolated_state_db, capsys) -> None:
    """_main returns exit code 0 and prints a JSON payload."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import _main

    rc = _main([])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["output_path"].endswith(".tar.gz")


def test_cli_main_returns_one_on_failure(isolated_state_db, capsys) -> None:
    """_main returns exit code 1 when build_export returns None."""
    from utils import bulk_export

    with patch.object(bulk_export, "build_export", return_value=None):
        rc = bulk_export._main([])
    assert rc == 1
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["success"] is False


def test_cli_main_prune_flag(isolated_state_db, capsys) -> None:
    """--prune triggers prune_old_exports after the build."""
    _seed_cache(isolated_state_db)
    from utils.bulk_export import _main

    rc = _main(["--prune"])
    assert rc == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["success"] is True
    # 'pruned' is present (0 here because we only made one archive).
    assert "pruned" in payload
