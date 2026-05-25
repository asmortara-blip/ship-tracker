"""tools.test_flakiness — track and surface flaky tests over time.

The suite is at 5260+ tests and climbing. As the suite grows, intermittent
flakes (a test that fails 1-in-20 runs but passes on retry) start to drown
out real regressions. The cost of a flake is twofold:

  1. The operator gets desensitised to red runs ("oh, that one always
     fails sometimes") and learns to ignore the indicator that should
     be loudest.
  2. A real regression that lands inside the flake's noise floor is
     invisible — by the time someone notices, blame-assignment to a
     specific commit is much harder.

This module ingests pytest's JUnit XML output (produced by running
pytest with ``--junitxml=PATH``), persists the run summary to the
project's ``kv_state`` table, and surfaces:

  * **Flaky tests** — nodeids whose failure rate across recent runs
    exceeds a threshold (default 10%).
  * **Slow tests** — nodeids whose mean duration across runs exceeds
    a slowness threshold (default 1.0s).
  * **Streaks** — for a specific test, the current pass/fail streak.
  * **Trends** — newly-flaky tests (clean before, flaking now) and
    regressions (passing tests that suddenly started failing).

The persistence model mirrors ``state.worker_runs``: a single
``kv_state`` row at key ``'test_runs'`` carries a rolling list of the
most recent 200 runs. No schema bump — kv_state is the documented
zero-migration extension point for operator telemetry.

Defensive contract
------------------
Every public helper:

* NEVER raises on caller-side bad input. ``parse_junit_xml`` returns
  a partial ``TestRun`` with empty fields on a malformed XML;
  ``persist_test_run`` returns ``False`` on a DB failure; list-returning
  helpers degrade to an empty list; ``get_test_streak`` returns
  all-zeros for an unknown nodeid.
* Per-test DB isolation via the standard
  ``monkeypatch.setattr(state_db, "DB_PATH", tmp_path / ...)`` pattern
  is required in tests — this module does NOT clear the row on import.
* Stdlib only — ``xml.etree.ElementTree`` + ``json`` + ``sqlite3`` (via
  ``state.db.get_connection``). No new pip dependencies.

What this module does NOT do
----------------------------
* No auto-ingest. The CLI's ``ingest`` subcommand is the only path
  that persists data — there is no daemon watching a directory.
* No source-code surfacing. Output is ``nodeid`` + ``message`` only;
  if an operator needs the source line, they can grep the nodeid.
* No notion of test ownership / blame. That's a separate concern.
* No re-running of flakes. We surface them; the operator decides
  whether to rerun, fix, mark xfail, or quarantine.
"""
from __future__ import annotations

import json
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger


# ─── Storage / threshold constants ─────────────────────────────────────────

# kv_state row key. Single source of truth.
_KV_KEY: str = "test_runs"

# Rolling cap on persisted runs. 200 runs covers ~20 days at one CI run
# per hour, or several months at one-run-per-day cadence — long enough
# to spot week-over-week trends without unbounded blob growth. The whole
# blob stays under a few hundred KB even at the cap.
_MAX_RUNS: int = 200

# Tests slower than this (seconds) are recorded in ``TestRun.slow_tests``
# so the slow-test history aggregation has data to chew on. 1.0s is the
# operator-tuned threshold — slow enough to be noticed, fast enough that
# even moderately heavy tests don't get flagged.
SLOW_TEST_THRESHOLD_SECONDS: float = 1.0


# ─── Dataclasses ───────────────────────────────────────────────────────────


@dataclass
class TestRun:
    """One parsed pytest invocation. The summary + per-failure detail."""

    # Tell pytest this isn't a test class — the leading "Test" in the
    # name triggers the collector's class-discovery heuristic, which
    # would otherwise emit a PytestCollectionWarning on every run.
    __test__ = False

    run_id: str               # UUID — stable across reads
    run_at: str               # ISO-8601 UTC timestamp of ingestion
    total: int
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    failures: list[dict] = field(default_factory=list)
    """One ``{nodeid, message, duration}`` per failed testcase. The
    ``message`` is the failure ``<failure>``/``<error>`` element's
    ``message`` attr (or text body if attr missing) truncated to keep
    blob size bounded."""
    slow_tests: list[dict] = field(default_factory=list)
    """One ``{nodeid, duration}`` per testcase whose ``time`` attr
    exceeded ``SLOW_TEST_THRESHOLD_SECONDS``."""


@dataclass
class FlakyTest:
    """A nodeid whose failure rate across recent runs warrants attention."""

    nodeid: str
    total_runs: int
    failure_count: int
    last_failed_at: Optional[str]
    last_failure_message: str
    failure_rate: float       # 0.0-1.0


# ─── Internal helpers ──────────────────────────────────────────────────────


def _now_iso() -> str:
    """Wall-clock UTC timestamp for record-keeping."""
    return datetime.now(timezone.utc).isoformat()


def _safe_int(val: Any, default: int = 0) -> int:
    """Coerce ``val`` to int; return default on TypeError/ValueError."""
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Coerce ``val`` to float; return default on TypeError/ValueError."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _nodeid_from_testcase(elem: ET.Element) -> str:
    """Build a pytest-style nodeid from a ``<testcase>`` element.

    pytest's JUnit emitter sets ``classname`` to the dotted module path
    (e.g. ``tests.test_foo``) and ``name`` to the test function (e.g.
    ``test_bar`` or ``test_bar[param-id]``). We reassemble into the
    canonical ``tests/test_foo.py::test_bar`` form so the nodeid round-
    trips with what an operator would paste from a failure line.

    A bare ``name`` with no classname (rare — usually a freestanding
    fixture error) falls back to just the name. Empty/missing fields
    degrade to an empty string so callers can filter trivially.
    """
    name = elem.get("name", "") or ""
    classname = elem.get("classname", "") or ""
    if not name:
        return ""
    if not classname:
        return name
    # classname is dotted: tests.test_foo or tests.subpkg.test_foo.TestClass.
    # The last segment may be a class name (CamelCase / contains capitals);
    # the rest is the module path. We don't try to distinguish — pytest's
    # nodeid form for class-based tests is path::Class::method, which
    # the operator's grep handles either way.
    parts = classname.split(".")
    # Heuristic: a part that starts with an uppercase letter is a class.
    # Split into (module_parts, class_parts).
    module_parts: list[str] = []
    class_parts: list[str] = []
    seen_class = False
    for p in parts:
        if seen_class or (p and p[0].isupper()):
            seen_class = True
            class_parts.append(p)
        else:
            module_parts.append(p)
    file_path = "/".join(module_parts) + ".py" if module_parts else ""
    if class_parts:
        return f"{file_path}::{'::'.join(class_parts)}::{name}" if file_path else f"{'::'.join(class_parts)}::{name}"
    return f"{file_path}::{name}" if file_path else name


def _extract_failure_message(child: ET.Element) -> str:
    """Pull a human-readable failure message from a ``<failure>`` or
    ``<error>`` child. Prefer the ``message`` attribute (which pytest
    sets to the assertion's first line); fall back to the text body
    (which carries the full traceback). Truncate either to 500 chars
    so a single test's traceback can't bloat the kv_state blob.
    """
    msg = child.get("message", "") or ""
    if not msg and child.text:
        # First non-empty line of the traceback body.
        for line in child.text.splitlines():
            stripped = line.strip()
            if stripped:
                msg = stripped
                break
    return msg[:500]


# ─── XML parser ────────────────────────────────────────────────────────────


def parse_junit_xml(xml_path: Any) -> TestRun:
    """Parse a pytest JUnit XML file into a ``TestRun``.

    Uses stdlib ``xml.etree.ElementTree``. NEVER raises — a malformed
    XML, missing file, or unexpected schema returns an empty
    ``TestRun`` (all counts 0, empty lists, fresh ``run_id``).

    Recognises both the ``<testsuites>`` root that pytest emits when
    multiple suites would be present AND the bare ``<testsuite>`` root
    that the default emitter uses when there's only one. Either way
    we aggregate counts across every ``<testsuite>`` element so the
    output is identical.

    Per-testcase classification:

      * a ``<testcase>`` with no failure/error/skipped child → passed
      * a ``<testcase>`` with a ``<failure>`` or ``<error>`` child
        → failed; the message goes into ``failures``
      * a ``<testcase>`` with a ``<skipped>`` child → skipped (not
        counted as a failure)
      * any ``<testcase>`` whose ``time`` attr exceeds
        ``SLOW_TEST_THRESHOLD_SECONDS`` → added to ``slow_tests``
    """
    run = TestRun(
        run_id=str(uuid.uuid4()),
        run_at=_now_iso(),
        total=0,
        passed=0,
        failed=0,
        skipped=0,
        duration_seconds=0.0,
    )

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
    except (ET.ParseError, FileNotFoundError, OSError, TypeError) as exc:
        logger.debug(f"tools.test_flakiness.parse_junit_xml: parse failed for {xml_path!r}: {exc}")
        return run

    # The root may be <testsuites> wrapping multiple <testsuite> elements,
    # or a single <testsuite> at the root. Normalise to a list.
    if root.tag == "testsuites":
        suites = list(root.findall("testsuite"))
    elif root.tag == "testsuite":
        suites = [root]
    else:
        # Unrecognised root tag — degrade to empty rather than raising.
        logger.debug(f"tools.test_flakiness.parse_junit_xml: unexpected root tag {root.tag!r}")
        return run

    total = 0
    failed = 0
    skipped = 0
    duration_total = 0.0
    failures: list[dict] = []
    slow_tests: list[dict] = []

    for suite in suites:
        # Prefer summary attrs on the suite element (the emitter computes
        # them once); fall back to walking testcases. Both code paths
        # produce identical results on a well-formed XML.
        suite_duration = _safe_float(suite.get("time"))
        duration_total += suite_duration

        for case in suite.findall("testcase"):
            total += 1
            duration = _safe_float(case.get("time"))
            nodeid = _nodeid_from_testcase(case)

            # Classification: skipped wins over failure (a skipped test
            # that ALSO carries a failure child would be a malformed
            # XML; the skipped semantics are clearer).
            skip_child = case.find("skipped")
            failure_child = case.find("failure")
            error_child = case.find("error")

            if skip_child is not None:
                skipped += 1
            elif failure_child is not None or error_child is not None:
                failed += 1
                child = failure_child if failure_child is not None else error_child
                msg = _extract_failure_message(child) if child is not None else ""
                failures.append({
                    "nodeid": nodeid,
                    "message": msg,
                    "duration": duration,
                })
            # else: passed — no per-test record needed (we only persist
            # failures + slow tests to keep the blob bounded).

            if duration >= SLOW_TEST_THRESHOLD_SECONDS:
                slow_tests.append({
                    "nodeid": nodeid,
                    "duration": duration,
                })

    run.total = total
    run.failed = failed
    run.skipped = skipped
    run.passed = max(0, total - failed - skipped)
    run.duration_seconds = duration_total
    run.failures = failures
    run.slow_tests = slow_tests
    return run


# ─── kv_state I/O ──────────────────────────────────────────────────────────


def _load_blob() -> list[dict[str, Any]]:
    """Read the rolling test-run list from kv_state. NEVER raises.

    Returns an empty list when the row is missing, the JSON fails to
    parse, the schema is unexpected, or the underlying DB call fails.
    """
    try:
        from state.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT value FROM kv_state WHERE key = ?", (_KV_KEY,)
        ).fetchone()
        if row is None:
            return []
        raw = row["value"] if hasattr(row, "keys") else row[0]
        if not raw:
            return []
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [r for r in parsed if isinstance(r, dict)]
    except Exception as exc:
        logger.debug(f"tools.test_flakiness._load_blob: read failed: {exc}")
        return []


def _save_blob(rows: list[dict[str, Any]]) -> bool:
    """Persist ``rows`` to kv_state. Returns success. NEVER raises."""
    try:
        from state.db import get_connection

        payload = json.dumps(rows, default=str)
        now = _now_iso()
        conn = get_connection()
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO kv_state (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (_KV_KEY, payload, now),
            )
        return True
    except Exception as exc:
        logger.debug(f"tools.test_flakiness._save_blob: write failed: {exc}")
        return False


def _row_to_run(row: dict[str, Any]) -> Optional[TestRun]:
    """Convert a raw dict row to a typed ``TestRun``; ``None`` on bad shape."""
    try:
        failures_raw = row.get("failures") or []
        slow_raw = row.get("slow_tests") or []
        failures = [f for f in failures_raw if isinstance(f, dict)]
        slow_tests = [s for s in slow_raw if isinstance(s, dict)]
        return TestRun(
            run_id=str(row.get("run_id", "") or ""),
            run_at=str(row.get("run_at", "") or ""),
            total=_safe_int(row.get("total")),
            passed=_safe_int(row.get("passed")),
            failed=_safe_int(row.get("failed")),
            skipped=_safe_int(row.get("skipped")),
            duration_seconds=_safe_float(row.get("duration_seconds")),
            failures=failures,
            slow_tests=slow_tests,
        )
    except (TypeError, ValueError):
        return None


# ─── Public API ────────────────────────────────────────────────────────────


def persist_test_run(run: TestRun) -> bool:
    """Append ``run`` to the rolling kv_state list. NEVER raises.

    Enforces the ``_MAX_RUNS`` cap on every persist — oldest runs at the
    front of the list are dropped first. This is the only place runs
    are added to storage; the CLI's ``ingest`` and the
    ``run --pytest-args`` subcommand both go through here.

    Returns ``True`` on successful persistence, ``False`` on any
    failure (bad input, DB error, etc).
    """
    try:
        if not isinstance(run, TestRun):
            return False
        rows = _load_blob()
        rows.append(asdict(run))
        if len(rows) > _MAX_RUNS:
            rows = rows[-_MAX_RUNS:]
        return _save_blob(rows)
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.persist_test_run: failed: {exc}")
        return False


def list_recent_runs(*, limit: int = 50) -> list[TestRun]:
    """Return the most-recent runs in descending ``run_at`` order.

    Clamps ``limit`` to [1, _MAX_RUNS]. NEVER raises — degrades to an
    empty list on any failure.
    """
    try:
        try:
            n = int(limit)
        except (TypeError, ValueError):
            n = 50
        n = max(1, min(n, _MAX_RUNS))

        rows = _load_blob()
        runs: list[TestRun] = []
        for r in rows:
            tr = _row_to_run(r)
            if tr is None:
                continue
            runs.append(tr)
        runs.sort(key=lambda x: x.run_at, reverse=True)
        return runs[:n]
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.list_recent_runs: failed: {exc}")
        return []


def get_flaky_tests(
    *, min_runs: int = 5, threshold: float = 0.1
) -> list[FlakyTest]:
    """Compute per-nodeid failure rates across recent runs.

    A nodeid is considered "seen" in a run when EITHER it appears in
    that run's ``failures`` list (= failed) OR it appears in any other
    failure list in any other persisted run (= we know about it). The
    latter is necessary because we don't persist per-pass nodeids
    (would explode blob size) — so a test's "total runs" is approximated
    as the number of persisted runs SINCE its first observed failure.
    This is a slight under-count of true pass-rate but it's the right
    bound for flakiness: a test that fails once and never appears again
    won't reach ``min_runs`` and gets filtered out.

    For tests that have failed at least once:
      * ``total_runs`` = number of persisted runs at-or-after the
        run where the test first appeared in failures
      * ``failure_count`` = how many of those runs listed the test
        as failed
      * ``failure_rate`` = failure_count / total_runs

    Filters: returns only tests with ``total_runs >= min_runs`` AND
    ``failure_rate >= threshold``. Sorted by failure_rate descending,
    with stable ordering for ties (alphabetical by nodeid).

    NEVER raises — degrades to an empty list on any failure.
    """
    try:
        runs = list_recent_runs(limit=_MAX_RUNS)
        if not runs:
            return []

        # runs is descending by run_at; reverse to chronological so we
        # can index "first seen at run index i".
        chronological = list(reversed(runs))
        n_runs = len(chronological)

        # Walk all failures to build per-nodeid: list of run indices it
        # was failed in + last failure message + last_failed_at.
        failed_at_indices: dict[str, list[int]] = {}
        last_message: dict[str, str] = {}
        last_failed_at: dict[str, str] = {}
        for idx, run in enumerate(chronological):
            for f in run.failures:
                nid = str(f.get("nodeid", "") or "")
                if not nid:
                    continue
                failed_at_indices.setdefault(nid, []).append(idx)
                msg = str(f.get("message", "") or "")
                if msg:
                    last_message[nid] = msg
                last_failed_at[nid] = run.run_at

        out: list[FlakyTest] = []
        for nid, indices in failed_at_indices.items():
            first_idx = indices[0]
            total_runs = n_runs - first_idx
            if total_runs <= 0:
                continue
            failure_count = len(indices)
            rate = failure_count / total_runs if total_runs else 0.0
            if total_runs < min_runs:
                continue
            if rate < threshold:
                continue
            out.append(FlakyTest(
                nodeid=nid,
                total_runs=total_runs,
                failure_count=failure_count,
                last_failed_at=last_failed_at.get(nid),
                last_failure_message=last_message.get(nid, ""),
                failure_rate=rate,
            ))

        # Sort: failure_rate desc, then nodeid asc for deterministic ties.
        out.sort(key=lambda f: (-f.failure_rate, f.nodeid))
        return out
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.get_flaky_tests: failed: {exc}")
        return []


def get_slow_tests_history(*, top_n: int = 20) -> list[dict]:
    """Aggregate per-nodeid duration across runs — surface consistently
    slow tests, not just one-off blips.

    A test only enters this aggregation if it crossed
    ``SLOW_TEST_THRESHOLD_SECONDS`` in at least one persisted run (i.e.
    appears in some ``run.slow_tests``). For each such test:

      * ``mean_duration``  — arithmetic mean across all sampled runs
      * ``max_duration``   — slowest observed run
      * ``sample_count``   — how many runs contributed samples

    Sorted by ``mean_duration`` descending; truncated to ``top_n``.
    NEVER raises — degrades to an empty list on any failure.
    """
    try:
        try:
            n = int(top_n)
        except (TypeError, ValueError):
            n = 20
        n = max(1, n)

        runs = list_recent_runs(limit=_MAX_RUNS)
        if not runs:
            return []

        samples: dict[str, list[float]] = {}
        for run in runs:
            for s in run.slow_tests:
                nid = str(s.get("nodeid", "") or "")
                if not nid:
                    continue
                dur = _safe_float(s.get("duration"))
                if dur <= 0:
                    continue
                samples.setdefault(nid, []).append(dur)

        out: list[dict] = []
        for nid, durs in samples.items():
            if not durs:
                continue
            out.append({
                "nodeid": nid,
                "mean_duration": sum(durs) / len(durs),
                "max_duration": max(durs),
                "sample_count": len(durs),
            })

        # Sort: mean_duration desc, then nodeid asc for deterministic ties.
        out.sort(key=lambda r: (-r["mean_duration"], r["nodeid"]))
        return out[:n]
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.get_slow_tests_history: failed: {exc}")
        return []


def get_test_streak(nodeid: str) -> dict:
    """Return the current pass/fail streak for ``nodeid``.

    Walks recent runs in REVERSE chronological order (most-recent
    first) and computes:

      * ``pass_streak``  — how many of the most-recent consecutive
        runs the test passed. Once we hit a run where the test failed,
        the streak ends.
      * ``fail_streak``  — the symmetric counter for failures.
      * ``total_runs``   — total persisted runs since the test was
        first observed (matches ``get_flaky_tests``'s definition).
      * ``last_status``  — ``'pass'`` | ``'fail'`` | ``'unknown'``
        (the latter when the test has never been observed).

    A test that's been observed but isn't in the most-recent run's
    failures is counted as a pass for that run — since we don't persist
    per-pass nodeids, "absent from failures" is our only signal.

    NEVER raises — unknown nodeid or DB error returns an all-zeros
    dict with ``last_status='unknown'``.
    """
    empty = {
        "pass_streak": 0,
        "fail_streak": 0,
        "total_runs": 0,
        "last_status": "unknown",
    }
    try:
        if not isinstance(nodeid, str) or not nodeid:
            return empty

        runs = list_recent_runs(limit=_MAX_RUNS)
        if not runs:
            return empty

        # Determine the first run (chronologically) where the test was
        # observed — needed for total_runs.
        chronological = list(reversed(runs))
        first_idx: Optional[int] = None
        for idx, run in enumerate(chronological):
            failed_nids = {str(f.get("nodeid", "") or "") for f in run.failures}
            if nodeid in failed_nids:
                if first_idx is None:
                    first_idx = idx
                    break

        if first_idx is None:
            # Test never appeared in any failure list — we don't actually
            # know if it exists. Treat as unknown.
            return empty

        total_runs = len(chronological) - first_idx

        # Walk most-recent first to compute current streak.
        pass_streak = 0
        fail_streak = 0
        last_status = "unknown"
        for run in runs:  # already descending by run_at
            failed_nids = {str(f.get("nodeid", "") or "") for f in run.failures}
            failed_now = nodeid in failed_nids
            if last_status == "unknown":
                # The most-recent run sets the streak direction.
                last_status = "fail" if failed_now else "pass"
                if failed_now:
                    fail_streak = 1
                else:
                    pass_streak = 1
                continue
            # Subsequent runs extend the streak iff the status matches.
            if last_status == "fail" and failed_now:
                fail_streak += 1
            elif last_status == "pass" and not failed_now:
                pass_streak += 1
            else:
                # Streak broken — stop walking.
                break

        return {
            "pass_streak": pass_streak,
            "fail_streak": fail_streak,
            "total_runs": total_runs,
            "last_status": last_status,
        }
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.get_test_streak: failed for {nodeid!r}: {exc}")
        return empty


def summarize_recent(*, runs_to_analyze: int = 10) -> dict:
    """Big-picture summary of the most-recent ``runs_to_analyze`` runs.

    Returns a dict with keys:

      * ``avg_pass_rate``    — mean of (passed / total) across the
        window. 1.0 when no runs are persisted (operator reads "no
        data" from ``runs_analyzed=0``, not from a misleading 0.0).
      * ``avg_duration``     — mean ``duration_seconds`` across the
        window.
      * ``runs_analyzed``    — actual count of runs that made it into
        the window (may be < requested if storage has fewer).
      * ``trending_flakes``  — nodeids that are flaking in the window
        but didn't flake in the prior comparable window. Empty when
        we don't have enough history to compare. Each entry is a
        ``{nodeid, recent_failure_rate, prior_failure_rate}`` dict.
      * ``regressions``      — nodeids that failed in the most-recent
        run but had a clean ``fail_streak`` of 0 in the run before.
        These are the operator's first read: "what just broke?"
        Each entry is ``{nodeid, message}``.

    The window comparison for ``trending_flakes`` uses the same
    threshold defaults as :func:`get_flaky_tests` (10% failure rate,
    min 3 runs in each window — relaxed from 5 since the window is
    intentionally short).

    NEVER raises — degrades to a dict with sensible empty/zero values.
    """
    empty: dict[str, Any] = {
        "avg_pass_rate": 1.0,
        "avg_duration": 0.0,
        "runs_analyzed": 0,
        "trending_flakes": [],
        "regressions": [],
    }
    try:
        try:
            window = int(runs_to_analyze)
        except (TypeError, ValueError):
            window = 10
        window = max(1, window)

        all_runs = list_recent_runs(limit=_MAX_RUNS)
        if not all_runs:
            return empty

        recent = all_runs[:window]
        prior = all_runs[window:window * 2]

        # avg_pass_rate + avg_duration over the recent window.
        rates: list[float] = []
        durations: list[float] = []
        for r in recent:
            if r.total > 0:
                rates.append(r.passed / r.total)
            durations.append(r.duration_seconds)
        avg_pass_rate = sum(rates) / len(rates) if rates else 1.0
        avg_duration = sum(durations) / len(durations) if durations else 0.0

        # Trending flakes — compute failure rate per nodeid in each
        # window separately, surface those that crossed from below to
        # above the threshold.
        def _rates_in_window(window_runs: list[TestRun]) -> dict[str, float]:
            failed: dict[str, int] = {}
            seen_from: dict[str, int] = {}
            n = len(window_runs)
            chrono = list(reversed(window_runs))
            for idx, run in enumerate(chrono):
                for f in run.failures:
                    nid = str(f.get("nodeid", "") or "")
                    if not nid:
                        continue
                    if nid not in seen_from:
                        seen_from[nid] = idx
                    failed[nid] = failed.get(nid, 0) + 1
            out: dict[str, float] = {}
            for nid, count in failed.items():
                total_seen = n - seen_from[nid]
                if total_seen >= 3:  # min_runs relaxed for short windows
                    out[nid] = count / total_seen
            return out

        recent_rates = _rates_in_window(recent)
        prior_rates = _rates_in_window(prior) if prior else {}

        trending: list[dict] = []
        for nid, rate in recent_rates.items():
            if rate < 0.1:
                continue
            prior_rate = prior_rates.get(nid, 0.0)
            if prior_rate < 0.1:
                # Flaking now, wasn't flaking before — that's the
                # interesting case.
                trending.append({
                    "nodeid": nid,
                    "recent_failure_rate": rate,
                    "prior_failure_rate": prior_rate,
                })
        trending.sort(key=lambda r: (-r["recent_failure_rate"], r["nodeid"]))

        # Regressions — failed in the newest run but the run before
        # had this test passing (i.e. not in its failures list).
        regressions: list[dict] = []
        if len(all_runs) >= 2:
            newest = all_runs[0]
            previous = all_runs[1]
            prev_failed = {str(f.get("nodeid", "") or "") for f in previous.failures}
            for f in newest.failures:
                nid = str(f.get("nodeid", "") or "")
                if not nid:
                    continue
                if nid not in prev_failed:
                    regressions.append({
                        "nodeid": nid,
                        "message": str(f.get("message", "") or ""),
                    })
            regressions.sort(key=lambda r: r["nodeid"])

        return {
            "avg_pass_rate": avg_pass_rate,
            "avg_duration": avg_duration,
            "runs_analyzed": len(recent),
            "trending_flakes": trending,
            "regressions": regressions,
        }
    except Exception as exc:
        logger.debug(f"tools.test_flakiness.summarize_recent: failed: {exc}")
        return empty


def clear_all_runs() -> bool:
    """Drop every persisted run. Used by the CLI's ``clear`` subcommand
    (with --confirm) and by tests for isolation. Returns success."""
    return _save_blob([])


__all__ = [
    "SLOW_TEST_THRESHOLD_SECONDS",
    "TestRun",
    "FlakyTest",
    "parse_junit_xml",
    "persist_test_run",
    "list_recent_runs",
    "get_flaky_tests",
    "get_slow_tests_history",
    "get_test_streak",
    "summarize_recent",
    "clear_all_runs",
]
