"""Tests for tools.test_flakiness — pytest JUnit XML ingestion + flake
surface.

Storage model: a single rolling list in kv_state at key 'test_runs',
capped at 200 most-recent runs. Each test gets a fresh DB so no
cross-test bleed.

Coverage:
  * parse_junit_xml — minimal/failures/skipped/slow/malformed XML
  * persist_test_run + list_recent_runs — round-trip + rolling cap
  * get_flaky_tests — clean / one-always-fails / min_runs / threshold / sort
  * get_slow_tests_history — top_n + sort by mean_duration
  * get_test_streak — unknown nodeid / streak interruption
  * summarize_recent — avg_pass_rate math
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from tools.test_flakiness import (
    FlakyTest,
    SLOW_TEST_THRESHOLD_SECONDS,
    TestRun,
    clear_all_runs,
    get_flaky_tests,
    get_slow_tests_history,
    get_test_streak,
    list_recent_runs,
    parse_junit_xml,
    persist_test_run,
    summarize_recent,
)


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────


@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── XML helpers ───────────────────────────────────────────────────────────


def _write_xml(tmp_path: Path, body: str, *, name: str = "junit.xml") -> Path:
    """Write ``body`` to a tempfile and return the path."""
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def _suite_xml(*testcases_xml: str, suite_time: str = "0.1") -> str:
    """Wrap ``<testcase>`` snippets in a minimal ``<testsuite>`` root."""
    tcs = "\n".join(testcases_xml)
    return (
        f'<?xml version="1.0" encoding="utf-8"?>\n'
        f'<testsuite name="pytest" tests="{len(testcases_xml)}" time="{suite_time}">\n'
        f'{tcs}\n'
        f'</testsuite>\n'
    )


def _tc_pass(name: str, *, classname: str = "tests.test_foo", time: str = "0.01") -> str:
    return f'<testcase classname="{classname}" name="{name}" time="{time}"/>'


def _tc_fail(
    name: str, *, classname: str = "tests.test_foo",
    time: str = "0.01", message: str = "AssertionError: x != y",
) -> str:
    return (
        f'<testcase classname="{classname}" name="{name}" time="{time}">'
        f'<failure message="{message}">traceback line 1\ntraceback line 2</failure>'
        f'</testcase>'
    )


def _tc_skip(name: str, *, classname: str = "tests.test_foo", time: str = "0.0") -> str:
    return (
        f'<testcase classname="{classname}" name="{name}" time="{time}">'
        f'<skipped message="reason" type="pytest.skip"/>'
        f'</testcase>'
    )


# ═══ parse_junit_xml ═══════════════════════════════════════════════════════


def test_parse_junit_minimal_returns_testrun(tmp_path):
    """A single passing test in a minimal XML returns total=1 passed=1."""
    xml = _suite_xml(_tc_pass("test_a"))
    run = parse_junit_xml(_write_xml(tmp_path, xml))
    assert isinstance(run, TestRun)
    assert run.total == 1
    assert run.passed == 1
    assert run.failed == 0
    assert run.skipped == 0
    assert run.failures == []
    assert run.slow_tests == []
    assert run.run_id  # UUID populated
    assert run.run_at  # timestamp populated


def test_parse_junit_failures_extracted_with_messages(tmp_path):
    """Failures populate the ``failures`` list with nodeid + message."""
    xml = _suite_xml(
        _tc_pass("test_a"),
        _tc_fail("test_b", message="AssertionError: bad value"),
        _tc_fail("test_c", message="ValueError: parse error"),
    )
    run = parse_junit_xml(_write_xml(tmp_path, xml))
    assert run.total == 3
    assert run.passed == 1
    assert run.failed == 2
    assert len(run.failures) == 2
    nids = {f["nodeid"] for f in run.failures}
    assert nids == {"tests/test_foo.py::test_b", "tests/test_foo.py::test_c"}
    msgs = {f["message"] for f in run.failures}
    assert "AssertionError: bad value" in msgs
    assert "ValueError: parse error" in msgs


def test_parse_junit_skipped_counted_separately(tmp_path):
    """Skipped tests don't count as failures."""
    xml = _suite_xml(
        _tc_pass("test_a"),
        _tc_skip("test_b"),
        _tc_skip("test_c"),
    )
    run = parse_junit_xml(_write_xml(tmp_path, xml))
    assert run.total == 3
    assert run.passed == 1
    assert run.failed == 0
    assert run.skipped == 2
    assert run.failures == []


def test_parse_junit_slow_tests_populated(tmp_path):
    """Tests slower than SLOW_TEST_THRESHOLD_SECONDS go into slow_tests."""
    slow_t = str(SLOW_TEST_THRESHOLD_SECONDS + 0.5)
    xml = _suite_xml(
        _tc_pass("test_fast", time="0.01"),
        _tc_pass("test_slow", time=slow_t),
        _tc_pass("test_very_slow", time="5.0"),
    )
    run = parse_junit_xml(_write_xml(tmp_path, xml))
    assert run.total == 3
    nids = {s["nodeid"] for s in run.slow_tests}
    assert "tests/test_foo.py::test_slow" in nids
    assert "tests/test_foo.py::test_very_slow" in nids
    assert "tests/test_foo.py::test_fast" not in nids


def test_parse_junit_malformed_xml_returns_empty_testrun(tmp_path):
    """Malformed XML returns a partial TestRun rather than raising."""
    p = _write_xml(tmp_path, "<not_xml>>>broken<<<")
    run = parse_junit_xml(p)
    assert isinstance(run, TestRun)
    assert run.total == 0
    assert run.failures == []


def test_parse_junit_missing_file_returns_empty_testrun(tmp_path):
    """Missing file path returns a partial TestRun rather than raising."""
    run = parse_junit_xml(tmp_path / "does_not_exist.xml")
    assert isinstance(run, TestRun)
    assert run.total == 0


def test_parse_junit_testsuites_root_aggregates(tmp_path):
    """A <testsuites> root wrapping two <testsuite> elements aggregates
    counts across both."""
    inner1 = _suite_xml(_tc_pass("test_a"), _tc_fail("test_b"))
    inner2 = _suite_xml(_tc_pass("test_c"), _tc_skip("test_d"))
    # Strip the XML declarations from the inners for the combined doc.
    inner1_body = inner1.split("\n", 1)[1] if inner1.startswith("<?xml") else inner1
    inner2_body = inner2.split("\n", 1)[1] if inner2.startswith("<?xml") else inner2
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuites>\n'
        f'{inner1_body}\n'
        f'{inner2_body}\n'
        '</testsuites>\n'
    )
    run = parse_junit_xml(_write_xml(tmp_path, xml))
    assert run.total == 4
    assert run.passed == 2
    assert run.failed == 1
    assert run.skipped == 1


# ═══ persist_test_run + list_recent_runs ═══════════════════════════════════


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


def test_persist_and_list_round_trip():
    """A persisted run reads back with every field intact."""
    r = _make_run(
        total=5, passed=4, failed=1,
        failures=[{"nodeid": "tests/test_x.py::test_one", "message": "boom", "duration": 0.1}],
    )
    assert persist_test_run(r) is True
    out = list_recent_runs()
    assert len(out) == 1
    assert out[0].run_id == r.run_id
    assert out[0].total == 5
    assert out[0].failed == 1
    assert out[0].failures == r.failures


def test_persist_rolling_cap_drops_oldest():
    """The 201st persist drops the oldest run (cap is 200)."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    # Add 205 runs with monotonically-increasing run_at so we can verify
    # the OLDEST got dropped (not the newest).
    for i in range(205):
        r = _make_run(run_at=(base + timedelta(seconds=i)).isoformat())
        persist_test_run(r)
    runs = list_recent_runs(limit=200)
    assert len(runs) == 200
    # Newest first; the very first (i=0) should be GONE; the 5th run (i=4)
    # should be the oldest remaining; the i=204 run is the newest.
    run_ats = [r.run_at for r in runs]
    # No run_at from i=0..4 should be present.
    dropped = {(base + timedelta(seconds=i)).isoformat() for i in range(5)}
    assert dropped.isdisjoint(set(run_ats))


# ═══ get_flaky_tests ═══════════════════════════════════════════════════════


def _persist_runs_with_failures(failures_per_run: list[list[str]]) -> None:
    """Persist N runs, each with the given list of failed nodeids.

    Each entry in ``failures_per_run`` is one run's failed nodeids. Total
    test count is hardcoded to 100; only the failure list shape matters
    for flake math.
    """
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    for i, nids in enumerate(failures_per_run):
        r = _make_run(
            run_at=(base + timedelta(seconds=i)).isoformat(),
            total=100,
            failed=len(nids),
            passed=100 - len(nids),
            failures=[
                {"nodeid": nid, "message": f"failure of {nid}", "duration": 0.05}
                for nid in nids
            ],
        )
        persist_test_run(r)


def test_get_flaky_tests_all_passing_returns_empty():
    """No failures persisted → no flakes."""
    _persist_runs_with_failures([[], [], [], [], [], []])
    assert get_flaky_tests() == []


def test_get_flaky_tests_always_failing_returns_one_with_rate_1():
    """A test that fails in every run → failure_rate=1.0."""
    nid = "tests/test_foo.py::test_always_fails"
    _persist_runs_with_failures([[nid]] * 6)
    flakes = get_flaky_tests(min_runs=5, threshold=0.1)
    assert len(flakes) == 1
    assert flakes[0].nodeid == nid
    assert flakes[0].failure_rate == 1.0
    assert flakes[0].failure_count == 6
    assert flakes[0].total_runs == 6


def test_get_flaky_tests_respects_min_runs():
    """A test that's failed only a few times doesn't meet min_runs cutoff."""
    nid = "tests/test_foo.py::test_rare"
    # Only 3 runs since it was first seen.
    _persist_runs_with_failures([[nid], [], [nid]])
    flakes = get_flaky_tests(min_runs=5, threshold=0.1)
    assert flakes == []
    # Lowering min_runs lets it through.
    flakes = get_flaky_tests(min_runs=3, threshold=0.1)
    assert len(flakes) == 1
    assert flakes[0].nodeid == nid


def test_get_flaky_tests_respects_threshold():
    """A test below the failure_rate threshold is filtered out."""
    nid = "tests/test_foo.py::test_rare"
    # Fail 1-in-10 = 10% failure rate.
    _persist_runs_with_failures([[nid]] + [[]] * 9)
    flakes_strict = get_flaky_tests(min_runs=5, threshold=0.5)
    assert flakes_strict == []
    flakes_loose = get_flaky_tests(min_runs=5, threshold=0.05)
    assert len(flakes_loose) == 1


def test_get_flaky_tests_sorted_by_failure_rate_desc():
    """When multiple flakes meet the threshold, sorted by failure_rate desc."""
    nid_a = "tests/test_foo.py::test_a"  # 50%
    nid_b = "tests/test_foo.py::test_b"  # 100%
    nid_c = "tests/test_foo.py::test_c"  # 20%
    # 10 runs. b in all 10, a in 5, c in 2.
    pattern: list[list[str]] = []
    for i in range(10):
        run_fails = [nid_b]
        if i < 5:
            run_fails.append(nid_a)
        if i < 2:
            run_fails.append(nid_c)
        pattern.append(run_fails)
    _persist_runs_with_failures(pattern)
    flakes = get_flaky_tests(min_runs=5, threshold=0.1)
    nids = [f.nodeid for f in flakes]
    # b (1.0) first, then a (0.5), then c (0.2).
    assert nids == [nid_b, nid_a, nid_c]


def test_get_flaky_tests_failure_message_preserved():
    """The flake's last_failure_message reflects the most-recent message."""
    nid = "tests/test_foo.py::test_x"
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    persist_test_run(_make_run(
        run_at=base.isoformat(),
        failures=[{"nodeid": nid, "message": "old message", "duration": 0.01}],
    ))
    for i in range(1, 6):
        persist_test_run(_make_run(
            run_at=(base + timedelta(seconds=i)).isoformat(),
            failures=[{"nodeid": nid, "message": "new message", "duration": 0.01}],
        ))
    flakes = get_flaky_tests(min_runs=5, threshold=0.1)
    assert len(flakes) == 1
    assert flakes[0].last_failure_message == "new message"


# ═══ get_slow_tests_history ════════════════════════════════════════════════


def test_get_slow_tests_history_returns_top_n_sorted_by_mean():
    """Multiple slow tests sorted by mean_duration desc, capped at top_n."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    # 3 tests, varying mean durations across 4 runs.
    # test_fastish: mean ~1.2s
    # test_medium:  mean ~3.0s
    # test_slowest: mean ~7.0s
    for i in range(4):
        persist_test_run(_make_run(
            run_at=(base + timedelta(seconds=i)).isoformat(),
            slow_tests=[
                {"nodeid": "tests/x.py::test_fastish", "duration": 1.2},
                {"nodeid": "tests/x.py::test_medium",  "duration": 3.0},
                {"nodeid": "tests/x.py::test_slowest", "duration": 7.0},
            ],
        ))
    out = get_slow_tests_history(top_n=2)
    assert len(out) == 2
    assert out[0]["nodeid"] == "tests/x.py::test_slowest"
    assert out[1]["nodeid"] == "tests/x.py::test_medium"
    assert out[0]["mean_duration"] == pytest.approx(7.0)
    assert out[0]["max_duration"] == pytest.approx(7.0)
    assert out[0]["sample_count"] == 4


def test_get_slow_tests_history_empty_when_no_runs():
    """Nothing persisted → empty list, not a crash."""
    assert get_slow_tests_history() == []


# ═══ get_test_streak ═══════════════════════════════════════════════════════


def test_get_test_streak_unknown_nodeid_returns_zeros():
    """A nodeid we've never seen returns all-zeros + last_status='unknown'."""
    s = get_test_streak("tests/never.py::test_x")
    assert s == {
        "pass_streak": 0, "fail_streak": 0,
        "total_runs": 0, "last_status": "unknown",
    }


def test_get_test_streak_pass_streak_interrupted_resets():
    """A failure-then-passes resets the fail_streak as soon as the
    most-recent run passed."""
    nid = "tests/test_foo.py::test_y"
    # Chronological: fail, fail, pass, pass, pass.
    # Most-recent first: pass, pass, pass, fail, fail.
    # Expected: pass_streak=3, fail_streak=0, last_status='pass'.
    _persist_runs_with_failures([[nid], [nid], [], [], []])
    s = get_test_streak(nid)
    assert s["last_status"] == "pass"
    assert s["pass_streak"] == 3
    assert s["fail_streak"] == 0
    assert s["total_runs"] == 5


def test_get_test_streak_currently_failing_counts_fail_streak():
    """A test failing in the most-recent run + the one before reports
    fail_streak=2."""
    nid = "tests/test_foo.py::test_z"
    # Chronological: pass, pass, fail, fail, fail.
    # Most-recent first: fail, fail, fail, pass, pass.
    # First failure index in chronological = 2 → total_runs = 5 - 2 = 3.
    _persist_runs_with_failures([[], [], [nid], [nid], [nid]])
    s = get_test_streak(nid)
    assert s["last_status"] == "fail"
    assert s["fail_streak"] == 3
    assert s["pass_streak"] == 0


# ═══ summarize_recent ══════════════════════════════════════════════════════


def test_summarize_recent_calculates_avg_pass_rate():
    """avg_pass_rate is the mean of (passed / total) across the window."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    # 3 runs: 100%/95%/80% pass rates.
    persist_test_run(_make_run(
        run_at=base.isoformat(),
        total=100, passed=100, failed=0,
    ))
    persist_test_run(_make_run(
        run_at=(base + timedelta(seconds=1)).isoformat(),
        total=100, passed=95, failed=5,
    ))
    persist_test_run(_make_run(
        run_at=(base + timedelta(seconds=2)).isoformat(),
        total=100, passed=80, failed=20,
    ))
    s = summarize_recent(runs_to_analyze=10)
    # mean of [1.0, 0.95, 0.80] = 0.9166...
    assert s["avg_pass_rate"] == pytest.approx((1.0 + 0.95 + 0.80) / 3)
    assert s["runs_analyzed"] == 3


def test_summarize_recent_empty_returns_sensible_defaults():
    """No runs → avg_pass_rate=1.0, runs_analyzed=0, no trending/regressions."""
    s = summarize_recent()
    assert s["runs_analyzed"] == 0
    assert s["avg_pass_rate"] == 1.0
    assert s["trending_flakes"] == []
    assert s["regressions"] == []


def test_summarize_recent_regressions_surface_newly_failing_tests():
    """A test that passed in the prior run + failed in the newest run is
    reported as a regression."""
    from datetime import datetime, timezone, timedelta
    base = datetime.now(timezone.utc)
    nid = "tests/test_foo.py::test_regressed"
    # Prior run: clean. Newest run: nid failed.
    persist_test_run(_make_run(
        run_at=base.isoformat(),
        total=100, passed=100, failed=0,
    ))
    persist_test_run(_make_run(
        run_at=(base + timedelta(seconds=1)).isoformat(),
        total=100, passed=99, failed=1,
        failures=[{"nodeid": nid, "message": "newly broken", "duration": 0.01}],
    ))
    s = summarize_recent()
    nids = [r["nodeid"] for r in s["regressions"]]
    assert nid in nids


# ═══ clear_all_runs ════════════════════════════════════════════════════════


def test_clear_all_runs_wipes_storage():
    """clear_all_runs() empties the rolling list."""
    persist_test_run(_make_run())
    persist_test_run(_make_run())
    assert len(list_recent_runs()) == 2
    assert clear_all_runs() is True
    assert list_recent_runs() == []
