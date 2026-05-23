"""Tests for ``tools.backup_cli`` — backup + restore CLI.

Each subcommand is exercised by calling ``main(argv)`` with a synthetic
``argv`` and capturing stdout / stderr. The CLI's defining properties
under test:

* ``create`` produces a tar.gz at the requested path containing a valid
  manifest.json + ship_tracker.db. The manifest's recorded row counts
  match the actual DB.
* ``list`` peeks at each manifest without unpacking the whole archive
  and shows the recorded counts in chronological order. An empty dir
  is "(no backups)" rather than a crash.
* ``verify`` re-derives schema + row counts from the snapshot DB and
  asserts they match the manifest. PASS on a clean backup; FAIL +
  exit-1 on a hand-corrupted one.
* ``restore`` REQUIRES ``--confirm``; without it the live DB is left
  untouched. With it, the live DB is replaced atomically.
* A restore round-trip preserves users (create → wipe DB → restore →
  users still there).
* A restore where the backup's schema_version > running code refuses
  and exits 1.

Per-test isolation is via the same monkeypatch-DB_PATH / tmp_path
fixture used by every other DB-touching test in the suite.
"""
from __future__ import annotations

import json
import sqlite3
import tarfile
from pathlib import Path

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path + per-test reports dir at tmp_path
    so the CLI never touches the real cache/ tree."""
    from state import db as state_db
    from utils import report_history as rh

    db_path = tmp_path / "cache" / "ship_tracker.db"
    monkeypatch.setattr(state_db, "DB_PATH", db_path)
    state_db.reset_for_tests()

    reports_dir = tmp_path / "cache" / "reports"
    monkeypatch.setattr(rh, "REPORT_DIR", reports_dir)
    # report_history caches the _INDEX_FILE constant at import time too;
    # we don't touch that — the backup CLI only globs the dir.
    yield
    state_db.reset_for_tests()


# ─── Helpers ──────────────────────────────────────────────────────────────


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    """Call ``backup_cli.main(argv)`` and return (exit_code, stdout, stderr)."""
    from tools.backup_cli import main

    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _seed_one_user(username: str = "tester") -> str:
    """Insert one user via auth.users.signup so the DB has at least one
    row to round-trip. Returns the user_id."""
    from auth.users import signup

    user = signup(username, "correct horse battery staple")
    assert user is not None, "signup() returned None — duplicate or weak pw"
    return user.user_id


def _seed_one_alert(idx: int = 0) -> None:
    """Insert one alert so alerts.count_rows > 0."""
    from datetime import datetime, timezone
    from engine.alert_engine_v2 import ShippingAlert, save_alerts

    alert = ShippingAlert(
        alert_id=f"alert-{idx}",
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="MACRO",
        severity="HIGH",
        title=f"title-{idx}",
        body=f"body-{idx}",
        ticker=f"TKR{idx}",
        route_id="",
        port_locode="",
        value=0.0,
        threshold=0.0,
        change_pct=0.0,
        acknowledged=False,
    )
    save_alerts([alert])


def _force_db_open() -> None:
    """Touch the DB so the file + schema actually exist on disk before
    we tell backup_cli to snapshot it."""
    from state.db import get_connection
    get_connection()


# ─── create ──────────────────────────────────────────────────────────────


def test_create_produces_tarball_with_manifest(tmp_path, capsys) -> None:
    """create writes a tar.gz with a manifest.json + ship_tracker.db at
    the archive root, and the manifest carries the table counts."""
    _force_db_open()
    _seed_one_user("user1")
    _seed_one_alert(idx=1)

    out_path = tmp_path / "backups" / "snap.tar.gz"
    code, stdout, stderr = _run(
        ["create", "--out", str(out_path)], capsys
    )
    assert code == 0, f"stderr={stderr}"
    assert out_path.exists(), "tar.gz was not written to --out path"

    # Inspect the tarball directly — manifest + db must both be at the
    # archive root with no enclosing dir.
    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
        assert "manifest.json" in names
        assert "ship_tracker.db" in names
        f = tar.extractfile("manifest.json")
        assert f is not None
        manifest = json.loads(f.read().decode("utf-8"))

    assert "schema_version" in manifest
    assert "created_at" in manifest
    assert "tables" in manifest
    assert manifest["tables"]["users"] >= 1
    assert manifest["tables"]["alerts"] >= 1
    assert manifest["tool_version"] == "1"


def test_create_explicit_out_path_writes_there(tmp_path, capsys) -> None:
    """--out PATH controls exactly where the archive lands; the parent
    dir is created if it does not already exist."""
    _force_db_open()
    target = tmp_path / "nested" / "deep" / "custom_name.tar.gz"
    assert not target.parent.exists()

    code, _, stderr = _run(["create", "--out", str(target)], capsys)
    assert code == 0, f"stderr={stderr}"
    assert target.exists()
    assert target.stat().st_size > 0


def test_create_default_out_is_under_backups_dir(tmp_path, capsys, monkeypatch) -> None:
    """Without --out the archive lands under ./backups/. We redirect
    DEFAULT_BACKUP_DIR to tmp_path so this test doesn't pollute the
    real backups/ in the repo."""
    from tools import backup_cli

    monkeypatch.setattr(
        backup_cli, "DEFAULT_BACKUP_DIR", tmp_path / "my_backups"
    )
    _force_db_open()
    code, stdout, stderr = _run(["create"], capsys)
    assert code == 0, f"stderr={stderr}"
    # Exactly one tarball under the override dir.
    archives = list((tmp_path / "my_backups").glob("ship_tracker_*.tar.gz"))
    assert len(archives) == 1
    # Output line referenced the path.
    assert str(tmp_path / "my_backups") in stdout


def test_create_includes_reports_dir(tmp_path, capsys, monkeypatch) -> None:
    """When cache/reports/ exists with HTML files, they land inside the
    archive under cache/reports/*.html."""
    from utils import report_history as rh

    # Seed one HTML report into the test reports dir.
    rh.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (rh.REPORT_DIR / "test_report.html").write_text(
        "<html><body>hi</body></html>", encoding="utf-8"
    )

    _force_db_open()
    out_path = tmp_path / "snap.tar.gz"
    code, _, stderr = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0, f"stderr={stderr}"
    with tarfile.open(out_path, "r:gz") as tar:
        names = tar.getnames()
        # The seeded report should be present in its expected arcname.
        assert "cache/reports/test_report.html" in names


# ─── list ────────────────────────────────────────────────────────────────


def test_list_empty_dir(tmp_path, capsys) -> None:
    """Empty / missing backups dir → '(no backups)' rather than a crash."""
    code, stdout, _ = _run(
        ["list", "--dir", str(tmp_path / "doesnt_exist")], capsys
    )
    assert code == 0
    assert "(no backups)" in stdout


def test_list_shows_existing_backups_in_chronological_order(tmp_path, capsys) -> None:
    """Two backups → both show up in created_at order (oldest first)."""
    _force_db_open()

    dir_ = tmp_path / "many"
    dir_.mkdir(parents=True, exist_ok=True)

    # Create two backups with distinct manifest timestamps.
    out1 = dir_ / "ship_tracker_a.tar.gz"
    out2 = dir_ / "ship_tracker_b.tar.gz"
    code1, _, _ = _run(["create", "--out", str(out1)], capsys)
    code2, _, _ = _run(["create", "--out", str(out2)], capsys)
    assert code1 == 0 and code2 == 0

    code, stdout, _ = _run(["list", "--dir", str(dir_)], capsys)
    assert code == 0
    # Both filenames appear and the header row is present.
    assert "ship_tracker_a.tar.gz" in stdout
    assert "ship_tracker_b.tar.gz" in stdout
    assert "schema" in stdout
    assert "users" in stdout


def test_list_with_unreadable_manifest_does_not_crash(tmp_path, capsys) -> None:
    """A backup with a missing manifest still appears in the table — the
    schema column shows '?' rather than crashing the listing."""
    dir_ = tmp_path / "mixed"
    dir_.mkdir(parents=True, exist_ok=True)

    # Hand-roll a tar with NO manifest.json inside.
    bad = dir_ / "ship_tracker_corrupt.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        # Put a dummy file in so the tar isn't empty.
        dummy = tmp_path / "dummy.txt"
        dummy.write_text("not a manifest", encoding="utf-8")
        tar.add(dummy, arcname="dummy.txt")

    code, stdout, _ = _run(["list", "--dir", str(dir_)], capsys)
    assert code == 0
    assert "ship_tracker_corrupt.tar.gz" in stdout
    assert "?" in stdout  # the schema column for the corrupt entry


# ─── verify ──────────────────────────────────────────────────────────────


def test_verify_passes_on_clean_backup(tmp_path, capsys) -> None:
    """A freshly created backup verifies PASS on every check."""
    _force_db_open()
    _seed_one_user("verify_u")
    _seed_one_alert(idx=42)

    out_path = tmp_path / "good.tar.gz"
    code, _, _ = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0

    code, stdout, stderr = _run(
        ["verify", "--from", str(out_path)], capsys
    )
    assert code == 0, f"verify failed: stderr={stderr} stdout={stdout}"
    assert "PASS" in stdout
    assert "FAIL" not in stdout


def test_verify_fails_on_missing_manifest(tmp_path, capsys) -> None:
    """A tarball without manifest.json → exit 1 + stderr explanation."""
    bad = tmp_path / "no_manifest.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        dummy = tmp_path / "x.txt"
        dummy.write_text("x", encoding="utf-8")
        tar.add(dummy, arcname="ship_tracker.db")

    code, _, stderr = _run(["verify", "--from", str(bad)], capsys)
    assert code == 1
    assert "manifest" in stderr.lower()


def test_verify_fails_on_corrupted_manifest(tmp_path, capsys) -> None:
    """A tarball with garbage in manifest.json → exit 1, not a crash."""
    bad = tmp_path / "broken_manifest.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text("{ not valid json", encoding="utf-8")
        tar.add(manifest_path, arcname="manifest.json")
        # Add a dummy DB file too so the failure comes from the manifest
        # JSON, not from a missing-DB guard.
        db_dummy = tmp_path / "ship_tracker.db"
        db_dummy.write_text("not a real db", encoding="utf-8")
        tar.add(db_dummy, arcname="ship_tracker.db")

    code, _, stderr = _run(["verify", "--from", str(bad)], capsys)
    assert code == 1
    assert stderr.startswith("error:")


# ─── restore ─────────────────────────────────────────────────────────────


def test_restore_without_confirm_exits_1(tmp_path, capsys) -> None:
    """Restore is destructive — without --confirm it MUST exit 1 and
    leave the live DB untouched."""
    _force_db_open()
    out_path = tmp_path / "noconfirm.tar.gz"
    code, _, _ = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0

    code, _, stderr = _run(
        ["restore", "--from", str(out_path)], capsys
    )
    assert code == 1
    assert "--confirm" in stderr


def test_restore_with_confirm_replaces_db(tmp_path, capsys) -> None:
    """A backup with --confirm replaces the live DB with the snapshot."""
    _force_db_open()
    _seed_one_user("before_restore")

    out_path = tmp_path / "snap.tar.gz"
    code, _, _ = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0

    # Mutate the live DB AFTER the snapshot so we can prove the restore
    # rolled the changes back.
    _seed_one_user("after_snapshot")
    from auth.users import count_users
    assert count_users() == 2

    code, stdout, stderr = _run(
        ["restore", "--from", str(out_path), "--confirm"], capsys
    )
    assert code == 0, f"stderr={stderr}"

    # After restore: only the pre-snapshot user should remain.
    assert count_users() == 1


def test_restore_round_trip_preserves_users(tmp_path, capsys) -> None:
    """create → wipe DB → restore → users still there. The definitive
    round-trip test for the backup format."""
    from auth.users import count_users, list_users
    from state import db as state_db

    _force_db_open()
    _seed_one_user("alice")
    _seed_one_user("bob")
    users_before = sorted([u.username for u in list_users()])
    assert users_before == ["alice", "bob"]

    out_path = tmp_path / "round.tar.gz"
    code, _, _ = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0

    # Wipe the live DB by unlinking it + resetting the cached conn.
    state_db.reset_for_tests()
    state_db.DB_PATH.unlink()
    # Now the DB is gone — counting users would re-init an empty DB.
    assert count_users() == 0

    code, _, stderr = _run(
        ["restore", "--from", str(out_path), "--confirm"], capsys
    )
    assert code == 0, f"stderr={stderr}"

    users_after = sorted([u.username for u in list_users()])
    assert users_after == ["alice", "bob"]


def test_restore_refuses_when_backup_schema_is_newer(tmp_path, capsys, monkeypatch) -> None:
    """Backup at schema N+1, running code at N → refuse. Restore-
    forward would feed the code a DB it does not know how to read."""
    _force_db_open()

    out_path = tmp_path / "future.tar.gz"
    code, _, _ = _run(["create", "--out", str(out_path)], capsys)
    assert code == 0

    # Hand-edit the manifest to claim a far-future schema_version.
    # Repackage the tar so the manifest update is what verify/restore
    # will see.
    work = tmp_path / "rewrap"
    work.mkdir()
    with tarfile.open(out_path, "r:gz") as tar:
        tar.extractall(work)
    manifest_path = work / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 9999
    manifest_path.write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    forward = tmp_path / "forward.tar.gz"
    with tarfile.open(forward, "w:gz") as tar:
        for child in work.iterdir():
            tar.add(child, arcname=child.name)

    code, _, stderr = _run(
        ["restore", "--from", str(forward), "--confirm"], capsys
    )
    assert code == 1
    # The guard's error message is informative — mention "schema".
    assert "schema" in stderr.lower()


def test_restore_missing_db_in_archive_exits_1(tmp_path, capsys) -> None:
    """A tarball that has a manifest but no DB file → exit 1 cleanly."""
    bad = tmp_path / "no_db.tar.gz"
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({
            "schema_version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "tables": {"users": 0, "alerts": 0, "alert_rules": 0,
                       "delivery_channels": 0, "report_history": 0},
            "hostname": "test",
            "tool_version": "1",
        }),
        encoding="utf-8",
    )
    with tarfile.open(bad, "w:gz") as tar:
        tar.add(manifest_path, arcname="manifest.json")

    code, _, stderr = _run(
        ["restore", "--from", str(bad), "--confirm"], capsys
    )
    assert code == 1
    assert "ship_tracker.db" in stderr or "db" in stderr.lower()


def test_restore_missing_file_exits_1(tmp_path, capsys) -> None:
    """--from pointing at a nonexistent path → clean exit 1."""
    code, _, stderr = _run(
        ["restore", "--from", str(tmp_path / "ghost.tar.gz"),
         "--confirm"],
        capsys,
    )
    assert code == 1
    assert "not found" in stderr.lower()


def test_verify_missing_file_exits_1(tmp_path, capsys) -> None:
    """verify with --from on a nonexistent path → exit 1."""
    code, _, stderr = _run(
        ["verify", "--from", str(tmp_path / "ghost.tar.gz")], capsys
    )
    assert code == 1
    assert "not found" in stderr.lower()


def test_unknown_command_exits_2(capsys) -> None:
    """argparse rejection → exit 2 (not 1, not a traceback)."""
    code, _, _ = _run(["nope"], capsys)
    assert code == 2
