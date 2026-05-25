"""``python -m tools.test_flakiness_cli`` — flaky-test tracker CLI.

Operator-facing front-end to ``tools.test_flakiness``. Six subcommands:

  * ``ingest <junit.xml>`` — parse the XML and persist the run.
  * ``flaky`` — list flaky tests (failure_rate >= threshold).
  * ``slow`` — list consistently-slow tests (mean_duration desc).
  * ``summary`` — big-picture rollup of recent runs.
  * ``history <nodeid>`` — pass/fail streak for a specific test.
  * ``clear`` — wipe stored runs (requires --confirm).
  * ``run --pytest-args "…"`` — convenience: run pytest with
    ``--junitxml=<tmp>`` + ingest + report in one call.

Output modes
------------
Every read-side subcommand accepts ``--json``. Without it the handler
prints an ASCII table or one-line summary; with it the handler prints
``json.dumps(payload, indent=2, default=str)``.

Exit codes
----------
* ``0`` — success
* ``1`` — handler raised; the message went to stderr
* ``2`` — argparse rejected the invocation (missing / unknown flag)
       OR --confirm was missing from ``clear``

This CLI must NEVER bubble an exception out to the shell.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Callable, Optional


# ─── Output helpers ────────────────────────────────────────────────────────


def _to_jsonable(obj: Any) -> Any:
    """Recursively turn dataclasses / Paths into JSON-friendly primitives."""
    if obj is None or isinstance(obj, (bool, int, float, str)):
        return obj
    if isinstance(obj, Path):
        return str(obj)
    if is_dataclass(obj) and not isinstance(obj, type):
        return _to_jsonable(asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_to_jsonable(x) for x in obj]
    return obj


def _print_json(payload: Any) -> None:
    print(json.dumps(_to_jsonable(payload), indent=2, default=str))


def _print_table(rows: list[dict], columns: Optional[list[str]] = None) -> None:
    """Render ``rows`` as a fixed-width ASCII table on stdout.

    Empty input prints ``(no rows)`` so a pipe consumer can tell apart
    empty result from a crash.
    """
    if not rows:
        print("(no rows)")
        return
    cols = columns if columns is not None else list(rows[0].keys())
    widths: dict[str, int] = {}
    for c in cols:
        max_val = max((len(str(r.get(c, ""))) for r in rows), default=0)
        widths[c] = max(len(str(c)), max_val)

    def _fmt(values: list[str]) -> str:
        return "  ".join(values[i].ljust(widths[cols[i]]) for i in range(len(cols)))

    print(_fmt([str(c) for c in cols]))
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print(_fmt([str(r.get(c, "")) for c in cols]))


def _print_kv(payload: dict) -> None:
    """Render a flat dict as ``key: value`` lines."""
    if not payload:
        print("(empty)")
        return
    width = max(len(str(k)) for k in payload.keys())
    for k, v in payload.items():
        print(f"{str(k).ljust(width)} : {v}")


# ─── Subcommand handlers ───────────────────────────────────────────────────


def _cmd_ingest(args: argparse.Namespace) -> None:
    """Parse the XML at ``args.xml_path`` and persist. Prints a one-line
    confirmation (or a JSON summary with --json)."""
    from tools.test_flakiness import parse_junit_xml, persist_test_run

    run = parse_junit_xml(args.xml_path)
    ok = persist_test_run(run)
    if args.json:
        _print_json({
            "persisted": ok,
            "run_id": run.run_id,
            "total": run.total,
            "passed": run.passed,
            "failed": run.failed,
            "skipped": run.skipped,
            "duration_seconds": run.duration_seconds,
            "failure_count": len(run.failures),
            "slow_count": len(run.slow_tests),
        })
        return
    if not ok:
        print(f"ingest: persistence failed (parsed total={run.total})", file=sys.stderr)
        sys.exit(1)
    print(
        f"ingested run {run.run_id}: total={run.total} "
        f"passed={run.passed} failed={run.failed} skipped={run.skipped} "
        f"duration={run.duration_seconds:.2f}s"
    )


def _cmd_flaky(args: argparse.Namespace) -> None:
    from tools.test_flakiness import get_flaky_tests

    flakes = get_flaky_tests(min_runs=args.min_runs, threshold=args.threshold)
    if args.json:
        _print_json(flakes)
        return
    if not flakes:
        print("(no flaky tests)")
        return
    rows = [
        {
            "nodeid":         f.nodeid,
            "failure_rate":   f"{f.failure_rate:.2%}",
            "failures":       f"{f.failure_count}/{f.total_runs}",
            "last_failed_at": f.last_failed_at or "",
            "message":        (f.last_failure_message[:60] + "…")
                              if len(f.last_failure_message) > 60
                              else f.last_failure_message,
        }
        for f in flakes
    ]
    _print_table(rows, columns=["nodeid", "failure_rate", "failures", "last_failed_at", "message"])


def _cmd_slow(args: argparse.Namespace) -> None:
    from tools.test_flakiness import get_slow_tests_history

    slow = get_slow_tests_history(top_n=args.top_n)
    if args.json:
        _print_json(slow)
        return
    if not slow:
        print("(no slow tests)")
        return
    rows = [
        {
            "nodeid":        s["nodeid"],
            "mean_duration": f"{s['mean_duration']:.3f}s",
            "max_duration":  f"{s['max_duration']:.3f}s",
            "samples":       s["sample_count"],
        }
        for s in slow
    ]
    _print_table(rows, columns=["nodeid", "mean_duration", "max_duration", "samples"])


def _cmd_summary(args: argparse.Namespace) -> None:
    from tools.test_flakiness import summarize_recent

    s = summarize_recent(runs_to_analyze=args.runs)
    if args.json:
        _print_json(s)
        return
    _print_kv({
        "runs_analyzed":   s["runs_analyzed"],
        "avg_pass_rate":   f"{s['avg_pass_rate']:.2%}",
        "avg_duration":    f"{s['avg_duration']:.2f}s",
        "trending_flakes": len(s["trending_flakes"]),
        "regressions":     len(s["regressions"]),
    })
    if s["trending_flakes"]:
        print("\ntrending flakes (clean before, flaking now):")
        for t in s["trending_flakes"]:
            print(f"  - {t['nodeid']} ({t['recent_failure_rate']:.0%})")
    if s["regressions"]:
        print("\nregressions (passed before, failed in newest run):")
        for r in s["regressions"]:
            msg = r["message"][:80] + "…" if len(r["message"]) > 80 else r["message"]
            print(f"  - {r['nodeid']}: {msg}")


def _cmd_history(args: argparse.Namespace) -> None:
    from tools.test_flakiness import get_test_streak

    streak = get_test_streak(args.nodeid)
    if args.json:
        _print_json({"nodeid": args.nodeid, **streak})
        return
    _print_kv({"nodeid": args.nodeid, **streak})


def _cmd_clear(args: argparse.Namespace) -> None:
    """Wipe stored runs. Refuses without --confirm — exit 2."""
    from tools.test_flakiness import clear_all_runs

    if not args.confirm:
        print("clear: refusing without --confirm", file=sys.stderr)
        sys.exit(2)
    ok = clear_all_runs()
    if args.json:
        _print_json({"cleared": ok})
        return
    print("cleared" if ok else "clear failed")


def _cmd_run(args: argparse.Namespace) -> None:
    """Convenience: run pytest with ``--junitxml=<tmp>``, ingest result,
    print summary. The pytest exit code is preserved so a CI script can
    still fail the build on real failures while telemetry was recorded.
    """
    from tools.test_flakiness import parse_junit_xml, persist_test_run

    # Tempfile in a tmp_dir we delete after — pytest itself writes the XML.
    with tempfile.TemporaryDirectory(prefix="flakiness_") as tmp:
        xml_path = Path(tmp) / "junit.xml"
        # Split pytest-args on whitespace; the operator passes one quoted
        # string for shell-quoting safety.
        extra = (args.pytest_args or "").split()
        cmd = ["pytest", f"--junitxml={xml_path}", *extra]
        try:
            proc = subprocess.run(cmd, check=False)
            pytest_rc = proc.returncode
        except FileNotFoundError:
            print("run: pytest binary not found on PATH", file=sys.stderr)
            sys.exit(1)
        # Ingest whatever XML the run produced (even on test failures
        # pytest still emits the XML).
        run = parse_junit_xml(xml_path)
        persist_test_run(run)
        if args.json:
            _print_json({
                "pytest_returncode": pytest_rc,
                "run_id": run.run_id,
                "total": run.total,
                "passed": run.passed,
                "failed": run.failed,
                "skipped": run.skipped,
            })
        else:
            print(
                f"pytest exit={pytest_rc} · ingested {run.run_id}: "
                f"total={run.total} passed={run.passed} failed={run.failed} "
                f"skipped={run.skipped}"
            )
        # Surface pytest's exit code so CI behaves naturally.
        if pytest_rc != 0:
            sys.exit(pytest_rc)


# ─── Parser ────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m tools.test_flakiness_cli",
        description="Track + surface flaky tests from pytest JUnit XML.",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="CMD")

    # ingest
    p_ing = sub.add_parser("ingest", help="Parse a JUnit XML + persist as a run")
    p_ing.add_argument("xml_path", help="Path to pytest --junitxml output")
    p_ing.add_argument("--json", action="store_true")
    p_ing.set_defaults(func=_cmd_ingest)

    # flaky
    p_fl = sub.add_parser("flaky", help="List flaky tests by failure_rate desc")
    p_fl.add_argument("--min-runs", type=int, default=5,
                      help="Minimum runs to be considered (default 5)")
    p_fl.add_argument("--threshold", type=float, default=0.1,
                      help="Failure rate cutoff [0.0-1.0] (default 0.1)")
    p_fl.add_argument("--json", action="store_true")
    p_fl.set_defaults(func=_cmd_flaky)

    # slow
    p_sl = sub.add_parser("slow", help="List consistently-slow tests")
    p_sl.add_argument("--top-n", type=int, default=20,
                      help="How many to show (default 20)")
    p_sl.add_argument("--json", action="store_true")
    p_sl.set_defaults(func=_cmd_slow)

    # summary
    p_sm = sub.add_parser("summary", help="Big-picture summary of recent runs")
    p_sm.add_argument("--runs", type=int, default=10,
                      help="How many recent runs to analyse (default 10)")
    p_sm.add_argument("--json", action="store_true")
    p_sm.set_defaults(func=_cmd_summary)

    # history
    p_hi = sub.add_parser("history", help="Pass/fail streak for one nodeid")
    p_hi.add_argument("nodeid", help="e.g. tests/test_foo.py::test_bar")
    p_hi.add_argument("--json", action="store_true")
    p_hi.set_defaults(func=_cmd_history)

    # clear
    p_cl = sub.add_parser("clear", help="Wipe all stored runs (destructive)")
    p_cl.add_argument("--confirm", action="store_true",
                      help="Required to actually wipe data")
    p_cl.add_argument("--json", action="store_true")
    p_cl.set_defaults(func=_cmd_clear)

    # run
    p_rn = sub.add_parser(
        "run", help="Run pytest + ingest + report in one call",
    )
    p_rn.add_argument(
        "--pytest-args", default="",
        help="String passed verbatim to pytest (split on whitespace)",
    )
    p_rn.add_argument("--json", action="store_true")
    p_rn.set_defaults(func=_cmd_run)

    return p


# ─── Entry point ───────────────────────────────────────────────────────────


def main(argv: Optional[list[str]] = None) -> int:
    """Parse + dispatch. Returns the process exit code.

    Tests call this with a synthetic ``argv``; the ``__main__`` block
    invokes with ``sys.argv[1:]``.
    """
    parser = _build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    handler: Optional[Callable[[argparse.Namespace], None]] = getattr(args, "func", None)
    if handler is None:
        parser.print_help(sys.stderr)
        return 2

    try:
        handler(args)
        return 0
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0
    except Exception as exc:  # noqa: BLE001 — top-level guard by contract
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
