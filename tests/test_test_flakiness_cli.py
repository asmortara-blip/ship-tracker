"""Tests for tools.test_flakiness_cli — argparse dispatch + handlers.

We exercise each subcommand by calling ``main()`` with a synthetic
``argv`` list and capturing stdout via the ``capsys`` fixture. Per-test
SQLite isolation follows the same pattern as the underlying module
tests (monkeypatch DB_PATH to tmp_path).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.test_flakiness import (
    TestRun,
    persist_test_run,
)
from tools.test_flakiness_cli import main


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────


_PASS_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuite name="pytest" tests="2" time="0.5">\n'
    '<testcase classname="tests.test_foo" name="test_a" time="0.01"/>\n'
    '<testcase classname="tests.test_foo" name="test_b" time="0.01"/>\n'
    '</testsuite>\n'
)


_FAIL_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<testsuite name="pytest" tests="2" time="0.5">\n'
    '<testcase classname="tests.test_foo" name="test_a" time="0.01"/>\n'
    '<testcase classname="tests.test_foo" name="test_b" time="0.01">\n'
    '<failure message="AssertionError">boom</failure>\n'
    '</testcase>\n'
    '</testsuite>\n'
)


def _write_xml(tmp_path: Path, body: str, *, name: str = "junit.xml") -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _make_run(**overrides) -> TestRun:
    import uuid
    from datetime import datetime, timezone
    base = dict(
        run_id=str(uuid.uuid4()),
        run_at=datetime.now(timezone.utc).isoformat(),
        total=10, passed=10, failed=0, skipped=0,
        duration_seconds=1.0,
        failures=[],
        slow_tests=[],
    )
    base.update(overrides)
    return TestRun(**base)


def _seed_flaky_run(nodeid: str = "tests/test_foo.py::test_b"):
    """Persist enough runs that ``get_flaky_tests`` will surface ``nodeid``."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    for i in range(6):
        persist_test_run(_make_run(
            run_at=(base + timedelta(seconds=i)).isoformat(),
            total=2, passed=1, failed=1,
            failures=[{"nodeid": nodeid, "message": "boom", "duration": 0.01}],
        ))


def _seed_slow_run(nodeid: str = "tests/test_foo.py::test_slow"):
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    for i in range(3):
        persist_test_run(_make_run(
            run_at=(base + timedelta(seconds=i)).isoformat(),
            slow_tests=[{"nodeid": nodeid, "duration": 2.5}],
        ))


# ═══ ingest ════════════════════════════════════════════════════════════════


def test_cli_ingest_reads_and_persists(tmp_path, capsys):
    """`ingest <xml>` parses + persists, prints a one-line summary."""
    xml_path = _write_xml(tmp_path, _FAIL_XML)
    rc = main(["ingest", str(xml_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "ingested run" in out
    assert "total=2" in out
    assert "failed=1" in out

    # Verify persistence happened.
    from tools.test_flakiness import list_recent_runs
    runs = list_recent_runs()
    assert len(runs) == 1
    assert runs[0].total == 2
    assert runs[0].failed == 1


def test_cli_ingest_json_mode(tmp_path, capsys):
    """--json prints a JSON object instead of the human one-liner."""
    import json
    xml_path = _write_xml(tmp_path, _PASS_XML)
    rc = main(["ingest", str(xml_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["persisted"] is True
    assert payload["total"] == 2
    assert payload["failed"] == 0


# ═══ flaky / slow / summary — non-empty when data exists ═══════════════════


def test_cli_flaky_returns_non_empty_when_flakes_seeded(capsys):
    _seed_flaky_run()
    rc = main(["flaky", "--min-runs", "5", "--threshold", "0.1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tests/test_foo.py::test_b" in out
    # Header column from the table render.
    assert "failure_rate" in out


def test_cli_flaky_empty_when_no_data(capsys):
    """No data → '(no flaky tests)' rather than a crash."""
    rc = main(["flaky"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "no flaky tests" in out


def test_cli_slow_returns_non_empty_when_slow_seeded(capsys):
    _seed_slow_run()
    rc = main(["slow", "--top-n", "10"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "tests/test_foo.py::test_slow" in out


def test_cli_summary_renders(capsys):
    _seed_flaky_run()
    rc = main(["summary"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "runs_analyzed" in out
    assert "avg_pass_rate" in out


# ═══ history ═══════════════════════════════════════════════════════════════


def test_cli_history_unknown_nodeid_returns_all_zeros(capsys):
    """history for an unknown nodeid prints zeros, not a crash."""
    rc = main(["history", "tests/never.py::test_x"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "last_status" in out
    assert "unknown" in out
    assert "pass_streak" in out


def test_cli_history_known_nodeid(capsys):
    """history for a seeded flake reports the streak."""
    nid = "tests/test_foo.py::test_b"
    _seed_flaky_run(nid)
    rc = main(["history", nid])
    assert rc == 0
    out = capsys.readouterr().out
    assert nid in out
    assert "fail" in out  # last_status: fail (every seeded run failed)


# ═══ clear ═════════════════════════════════════════════════════════════════


def test_cli_clear_requires_confirm(capsys):
    """clear without --confirm refuses with exit 2."""
    _seed_flaky_run()
    rc = main(["clear"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "refusing without --confirm" in err
    # Storage was NOT touched.
    from tools.test_flakiness import list_recent_runs
    assert len(list_recent_runs()) > 0


def test_cli_clear_with_confirm_wipes(capsys):
    """clear --confirm wipes storage and exits 0."""
    _seed_flaky_run()
    rc = main(["clear", "--confirm"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "cleared" in out
    from tools.test_flakiness import list_recent_runs
    assert list_recent_runs() == []
