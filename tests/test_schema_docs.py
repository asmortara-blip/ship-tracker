"""Tests for ``tools.schema_docs`` and ``tools.schema_docs_cli``.

The schema-docs tooling is a read-only consumer of the live DB and the
``state/migrations.py`` source file. The tests assert:

* ``introspect_schema`` on a fresh ``_init_schema``-built DB picks up
  every table state.db creates, the live schema_version, column
  metadata, and the defined indexes.
* ``render_schema_markdown`` produces a usable Markdown document
  (TOC, every table name present, zero-row tables handled).
* ``schema_history_from_migrations`` parses ``state/migrations.py``,
  returns one entry per ``_migrate_to_vN`` function, sorted by
  version.
* ``introspect_schema`` returns a dict carrying an ``error`` key for a
  missing / unopenable DB path - it never raises.

Per-test isolation follows the same monkeypatch-DB_PATH + tmp_path
pattern every other DB-touching test in the suite uses.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest


# ─── Isolation fixture ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite at tmp_path so the schema-docs tooling never
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    db_path = tmp_path / "cache" / "ship_tracker.db"
    monkeypatch.setattr(state_db, "DB_PATH", db_path)
    state_db.reset_for_tests()
    # Touch the connection so _init_schema runs and the DB file exists.
    state_db.get_connection()
    yield
    state_db.reset_for_tests()


# ─── introspect_schema ────────────────────────────────────────────────────


def test_introspect_schema_picks_up_live_tables():
    """A fresh DB built by state.db._init_schema carries every CREATE
    TABLE in the v1..v24 chain. introspect_schema should surface them."""
    from tools.schema_docs import introspect_schema
    schema = introspect_schema()
    assert "error" not in schema
    tables = schema.get("tables", {})
    # Spot-check the major tables from the prompt context. Every one of
    # these is created by _init_schema (v1..v24) so a healthy fresh DB
    # must carry them all.
    expected = {
        "users", "alerts", "alert_rules", "delivery_channels",
        "report_history", "audit_events", "api_tokens", "kv_state",
        "tab_render_events", "investor_report_snapshots",
        "data_source_health", "user_settings",
        "mfa_recovery_codes", "user_invitations",
        "report_schedules", "alert_silences", "alert_annotations",
        "alert_escalation_chains",
    }
    missing = expected - tables.keys()
    assert not missing, f"missing tables: {sorted(missing)}"


def test_introspect_schema_includes_schema_version():
    """The schema_version field comes from kv_state and should equal
    state.db.SCHEMA_VERSION on a freshly-init'd DB."""
    from state import db as state_db
    from tools.schema_docs import introspect_schema
    schema = introspect_schema()
    assert schema.get("schema_version") == state_db.SCHEMA_VERSION
    assert schema.get("expected_schema_version") == state_db.SCHEMA_VERSION


def test_introspect_schema_includes_column_metadata():
    """Each table's columns array carries name / type / nullable /
    default / pk for every column. Use ``users`` as the canary because
    its v7 CREATE TABLE has a well-known shape."""
    from tools.schema_docs import introspect_schema
    schema = introspect_schema()
    users = schema["tables"]["users"]
    col_names = {c["name"] for c in users["columns"]}
    # v7 + v16 additions — the union is what a fresh DB should carry.
    assert {"user_id", "username", "password_hash", "password_salt",
            "role", "created_at", "last_login_at",
            "mfa_secret", "mfa_enabled"} <= col_names
    # PK detection — user_id is the PRIMARY KEY.
    pk_cols = [c["name"] for c in users["columns"] if c["pk"]]
    assert pk_cols == ["user_id"]


def test_introspect_schema_includes_indexes():
    """A table with a CREATE INDEX statement should surface that index
    via introspection. ``alerts`` has idx_alerts_created_at + idx_alerts_
    unacknowledged from v1."""
    from tools.schema_docs import introspect_schema
    schema = introspect_schema()
    alerts = schema["tables"]["alerts"]
    ix_names = {ix["name"] for ix in alerts["indexes"]}
    assert "idx_alerts_created_at" in ix_names
    assert "idx_alerts_unacknowledged" in ix_names


def test_introspect_schema_row_count_zero_on_empty_table():
    """Every table on a freshly-init'd DB is empty - row_count must be
    exactly 0 (not -1, not None)."""
    from tools.schema_docs import introspect_schema
    schema = introspect_schema()
    # kv_state may have the schema_version row, but alerts is definitely
    # empty on a fresh DB.
    assert schema["tables"]["alerts"]["row_count"] == 0


# ─── render_schema_markdown ────────────────────────────────────────────────


def test_render_schema_markdown_non_empty():
    """Rendering a valid schema dict produces a non-empty Markdown string
    with the canonical title."""
    from tools.schema_docs import introspect_schema, render_schema_markdown
    schema = introspect_schema()
    md = render_schema_markdown(schema)
    assert isinstance(md, str)
    assert len(md) > 100
    assert md.startswith("# Ship Tracker - Database Schema")


def test_render_schema_markdown_has_toc():
    """The Markdown should carry a "Table of contents" section with at
    least one anchor link."""
    from tools.schema_docs import introspect_schema, render_schema_markdown
    md = render_schema_markdown(introspect_schema())
    assert "## Table of contents" in md
    # Anchor format: [name](#name). Spot-check users.
    assert "[users](#users)" in md


def test_render_schema_markdown_contains_every_table_name():
    """Every table the introspection returned should appear as a heading
    in the rendered Markdown."""
    from tools.schema_docs import introspect_schema, render_schema_markdown
    schema = introspect_schema()
    md = render_schema_markdown(schema)
    for table_name in schema["tables"].keys():
        assert f"## {table_name}" in md, f"missing section for {table_name}"


def test_render_schema_markdown_handles_zero_rows():
    """A table with 0 rows should render the literal "Empty (0 rows)."
    line - no division-by-zero, no exception, no missing section."""
    from tools.schema_docs import introspect_schema, render_schema_markdown
    md = render_schema_markdown(introspect_schema())
    # The alerts table is empty on a fresh DB - find its section and
    # confirm the row-count line lands as "Empty (0 rows).".
    section_start = md.index("## alerts")
    section = md[section_start:section_start + 500]
    assert "Empty (0 rows)." in section


def test_render_schema_markdown_handles_error_dict():
    """A dict carrying an ``error`` key should still render a usable
    one-line body rather than blowing up the renderer."""
    from tools.schema_docs import render_schema_markdown
    md = render_schema_markdown({"error": "DB not found at /nope",
                                  "db_path": "/nope", "tables": {}})
    assert "# Ship Tracker - Database Schema" in md
    assert "DB not found at /nope" in md


# ─── schema_history_from_migrations ────────────────────────────────────────


def test_schema_history_from_migrations_returns_one_entry_per_function():
    """Every ``_migrate_to_vN`` function in state/migrations.py should
    surface as one entry. The current chain runs v2..v24 (no v1; that
    is the initial schema, not a migration)."""
    from tools.schema_docs import schema_history_from_migrations
    entries = schema_history_from_migrations()
    versions = [e["version"] for e in entries]
    # We expect every version from v2 through v24 (the current
    # SCHEMA_VERSION). v5 may or may not be present depending on the
    # sibling-agent slot reservation, but the rest must be.
    assert len(entries) >= 20
    assert 2 in versions
    assert 24 in versions
    # Each entry carries the required fields.
    for e in entries:
        assert isinstance(e["version"], int)
        assert e["migration_name"].startswith("_migrate_to_v")
        assert isinstance(e["summary"], str)
        assert isinstance(e["sql_excerpts"], list)


def test_schema_history_from_migrations_is_sorted_by_version():
    """The returned list is sorted by version ASC so the rendered
    history reads chronologically."""
    from tools.schema_docs import schema_history_from_migrations
    entries = schema_history_from_migrations()
    versions = [e["version"] for e in entries]
    assert versions == sorted(versions)


def test_schema_history_from_migrations_extracts_sql_excerpts():
    """Migrations that issue raw ``conn.execute("ALTER TABLE …")``
    calls should surface those literal SQL strings in sql_excerpts.
    v4 (alerts.acknowledged_at) is a stable example."""
    from tools.schema_docs import schema_history_from_migrations
    entries = schema_history_from_migrations()
    v4_entry = next((e for e in entries if e["version"] == 4), None)
    assert v4_entry is not None, "v4 migration missing"
    joined = " ".join(v4_entry["sql_excerpts"])
    assert "ALTER TABLE alerts" in joined
    assert "acknowledged_at" in joined


def test_schema_history_missing_file_returns_empty_list(tmp_path):
    """A nonexistent migrations_path returns an empty list rather than
    raising - the caller from a partial install still gets a usable
    shape."""
    from tools.schema_docs import schema_history_from_migrations
    entries = schema_history_from_migrations(
        migrations_path=tmp_path / "does_not_exist.py"
    )
    assert entries == []


# ─── render_history_markdown ───────────────────────────────────────────────


def test_render_history_markdown_non_empty():
    """render_history_markdown produces a usable doc with the canonical
    title and at least one version section."""
    from tools.schema_docs import (
        render_history_markdown,
        schema_history_from_migrations,
    )
    entries = schema_history_from_migrations()
    md = render_history_markdown(entries)
    assert md.startswith("# Ship Tracker - Migration History")
    assert "v24 - `_migrate_to_v24`" in md


# ─── Bad db_path → error dict, never raises ────────────────────────────────


def test_introspect_schema_bad_path_returns_error_dict(tmp_path):
    """A nonexistent db_path must return a dict carrying an ``error``
    key, NEVER raise. This is the contract the CLI relies on to render
    a clean error line on stderr."""
    from tools.schema_docs import introspect_schema
    bogus = tmp_path / "no_such_db.db"
    result = introspect_schema(db_path=bogus)
    assert isinstance(result, dict)
    assert "error" in result
    assert result["tables"] == {}


def test_introspect_schema_bad_path_does_not_raise():
    """Same contract as the previous test, but explicitly asserts the
    no-raise property via pytest.raises wrapping a no-op."""
    from tools.schema_docs import introspect_schema
    # The bogus path is outside tmp_path on purpose - the function
    # should bail at the .exists() check before any open attempt.
    try:
        result = introspect_schema(db_path=Path("/nonexistent/dir/foo.db"))
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"introspect_schema raised on bad path: {exc}")
    assert "error" in result


# ─── CLI smoke tests ──────────────────────────────────────────────────────


def test_cli_markdown_writes_file(tmp_path):
    """The ``markdown`` subcommand writes a Markdown file at --out and
    returns exit code 0."""
    from tools import schema_docs_cli
    out = tmp_path / "SCHEMA.md"
    rc = schema_docs_cli.main(["markdown", "--out", str(out)])
    assert rc == 0
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert content.startswith("# Ship Tracker - Database Schema")


def test_cli_json_writes_valid_json(tmp_path):
    """The ``json`` subcommand emits valid JSON with the introspected
    shape."""
    from tools import schema_docs_cli
    out = tmp_path / "schema.json"
    rc = schema_docs_cli.main(["json", "--out", str(out)])
    assert rc == 0
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert "tables" in parsed
    assert "schema_version" in parsed


def test_cli_history_writes_file(tmp_path):
    """The ``history`` subcommand writes a Markdown migration history
    at --out."""
    from tools import schema_docs_cli
    out = tmp_path / "SCHEMA_HISTORY.md"
    rc = schema_docs_cli.main(["history", "--out", str(out)])
    assert rc == 0
    content = out.read_text(encoding="utf-8")
    assert content.startswith("# Ship Tracker - Migration History")
