"""Defining-property tests for tools/port_supply_bulk_diff.py.

Covers the pure aggregator (no I/O) + the CLI end-to-end. Per-test
isolation comes via tmp_path so the on-disk snapshot tree never leaks
between tests + never touches the real SNAPSHOT_ROOT.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tools.port_supply_bulk_diff import (
    BulkDiffResult,
    PortVolatilityRow,
    aggregate_diff_window,
    build_bulk_diff,
    format_json_report,
    format_markdown_report,
    format_text_report,
    main,
)
from tools.port_supply_diff import PortRow


# ─────────────────────────────────────────────────────────────────────────
# Helpers — hand-built rows + on-disk snapshot fixtures
# ─────────────────────────────────────────────────────────────────────────


def _row(
    locode: str,
    name: str = "Port",
    region: str = "Region",
    deficit: float = 0.0,
    severity: str = "Balanced",
    tickers: list[str] | None = None,
) -> PortRow:
    """Build a PortRow for aggregator tests."""
    return PortRow(
        locode=locode,
        name=name,
        region=region,
        supply_deficit_days=deficit,
        severity_label=severity,
        top_exposed_tickers=tickers or [],
    )


def _write_snapshot(
    root: Path,
    snapshot_date: date,
    rows: list[tuple[str, str, float, str]],
    container_type: str = "40FT_DRY",
) -> Path:
    """Write a minimal per-port summary CSV under root/<date>/...csv.

    ``rows`` is a list of (locode, port_name, supply_deficit_days,
    severity_label) tuples — the four fields the aggregator actually
    reads. Mirrors the layout parse_summary_csv expects (BOM + comment
    header + DictReader body).
    """
    out_dir = root / snapshot_date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"port_supply_summary_{container_type.lower()}.csv"

    header = (
        "deficit_rank,locode,port_name,region,supply_deficit_days,"
        "severity_label,top_exposed_tickers,summary"
    )
    body_lines = [header]
    for i, (locode, name, deficit, severity) in enumerate(rows, start=1):
        body_lines.append(
            f"{i},{locode},{name},NAM,{deficit},{severity},,test"
        )
    path.write_text("\n".join(body_lines) + "\n", encoding="utf-8")
    return path


# ─────────────────────────────────────────────────────────────────────────
# 1. Aggregator — empty + single-pair shape contracts
# ─────────────────────────────────────────────────────────────────────────


def test_aggregator_zero_pairs_returns_empty_list() -> None:
    """No pairs to walk → empty leaderboard (not None, not raise)."""
    rows = aggregate_diff_window([])
    assert rows == []


def test_aggregator_one_pair_entered_deficit_increments_counter() -> None:
    """One pair where port X crosses from surplus into deficit must
    bump that port's n_entered_deficit to exactly 1."""
    before = [_row("X", deficit=+2.0, severity="Surplus")]
    after = [_row("X", deficit=-1.0, severity="Balanced")]
    leaderboard = aggregate_diff_window([(before, after)])
    by_locode = {r.locode: r for r in leaderboard}
    assert "X" in by_locode
    assert by_locode["X"].n_entered_deficit == 1
    assert by_locode["X"].n_exited_deficit == 0


def test_aggregator_one_pair_exited_deficit_increments_counter() -> None:
    before = [_row("X", deficit=-3.0, severity="Deficit")]
    after = [_row("X", deficit=+2.0, severity="Surplus")]
    leaderboard = aggregate_diff_window([(before, after)])
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].n_exited_deficit == 1
    assert by_locode["X"].n_entered_deficit == 0


# ─────────────────────────────────────────────────────────────────────────
# 2. cumulative_deficit_day_delta — sum of abs day-to-day deltas
# ─────────────────────────────────────────────────────────────────────────


def test_cumulative_delta_sums_absolute_day_to_day_moves() -> None:
    """Hand-built 3-pair (4-snapshot) walk where port X moves:
    +5 -> +2 (Δ=-3), +2 -> -1 (Δ=-3), -1 -> +4 (Δ=+5).
    Cumulative |Δ| = 3 + 3 + 5 = 11.0."""
    s0 = [_row("X", deficit=+5.0)]
    s1 = [_row("X", deficit=+2.0)]
    s2 = [_row("X", deficit=-1.0)]
    s3 = [_row("X", deficit=+4.0)]
    pairs = [(s0, s1), (s1, s2), (s2, s3)]

    leaderboard = aggregate_diff_window(pairs, min_delta_days=0.5)
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].cumulative_deficit_day_delta == pytest.approx(11.0)


def test_cumulative_delta_respects_min_delta_threshold() -> None:
    """Per-pair moves below min_delta_days are dropped from
    cumulative — matches how the per-day diff CLI's --min-delta works."""
    s0 = [_row("X", deficit=0.0)]
    s1 = [_row("X", deficit=-0.3)]   # |Δ|=0.3 (filtered)
    s2 = [_row("X", deficit=-5.3)]   # |Δ|=5.0 (kept)
    pairs = [(s0, s1), (s1, s2)]

    leaderboard = aggregate_diff_window(pairs, min_delta_days=1.0)
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].cumulative_deficit_day_delta == pytest.approx(5.0)


# ─────────────────────────────────────────────────────────────────────────
# 3. n_days_in_deficit — counts AFTER side of every pair
# ─────────────────────────────────────────────────────────────────────────


def test_n_days_in_deficit_counts_after_side() -> None:
    """A 3-pair walk where port X is in deficit on the after side of
    pairs 2 + 3 (but not pair 1) → n_days_in_deficit == 2."""
    s0 = [_row("X", deficit=+5.0)]
    s1 = [_row("X", deficit=+3.0)]    # after side surplus
    s2 = [_row("X", deficit=-1.0)]    # after side deficit
    s3 = [_row("X", deficit=-4.0)]    # after side deficit
    pairs = [(s0, s1), (s1, s2), (s2, s3)]

    leaderboard = aggregate_diff_window(pairs, min_delta_days=0.5)
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].n_days_in_deficit == 2


# ─────────────────────────────────────────────────────────────────────────
# 4. n_severity_shifts + worst_single_day_delta
# ─────────────────────────────────────────────────────────────────────────


def test_severity_shifts_counted_per_pair() -> None:
    """Two pairs where X shifts severity each time → n_severity_shifts==2."""
    s0 = [_row("X", deficit=+5.0, severity="Surplus")]
    s1 = [_row("X", deficit=-1.0, severity="Balanced")]
    s2 = [_row("X", deficit=-8.0, severity="Deficit")]
    pairs = [(s0, s1), (s1, s2)]

    leaderboard = aggregate_diff_window(pairs, min_delta_days=0.5)
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].n_severity_shifts == 2


def test_worst_single_day_delta_tracks_largest_abs_signed_move() -> None:
    """X moves +5 -> +4 (Δ=-1), +4 -> -10 (Δ=-14), -10 -> -8 (Δ=+2).
    Largest |Δ| is 14 with sign -, so worst_single_day_delta == -14."""
    s0 = [_row("X", deficit=+5.0)]
    s1 = [_row("X", deficit=+4.0)]
    s2 = [_row("X", deficit=-10.0)]
    s3 = [_row("X", deficit=-8.0)]
    pairs = [(s0, s1), (s1, s2), (s2, s3)]

    leaderboard = aggregate_diff_window(pairs, min_delta_days=0.5)
    by_locode = {r.locode: r for r in leaderboard}
    assert by_locode["X"].worst_single_day_delta == pytest.approx(-14.0)


# ─────────────────────────────────────────────────────────────────────────
# 5. Sort order — most-concerning ports first
# ─────────────────────────────────────────────────────────────────────────


def test_leaderboard_sorted_by_volatility_signals() -> None:
    """A port that's in deficit on every pair AND moves materially must
    come before a port that's never in deficit + barely moves."""
    # Calm port: surplus throughout, tiny moves.
    calm_pair = (
        [_row("CALM", deficit=+10.0)],
        [_row("CALM", deficit=+10.5)],
    )
    # Volatile port: in deficit on after side + big move.
    hot_pair = (
        [_row("HOT", deficit=+1.0, severity="Surplus")],
        [_row("HOT", deficit=-12.0, severity="Deficit")],
    )
    # Combined snapshot pair with both ports.
    before = calm_pair[0] + hot_pair[0]
    after = calm_pair[1] + hot_pair[1]

    leaderboard = aggregate_diff_window([(before, after)], min_delta_days=0.5)
    # HOT must rank above CALM.
    locodes = [r.locode for r in leaderboard]
    assert locodes.index("HOT") < locodes.index("CALM")


# ─────────────────────────────────────────────────────────────────────────
# 6. build_bulk_diff — on-disk walker with tmp_path isolation
# ─────────────────────────────────────────────────────────────────────────


def test_build_bulk_diff_walks_consecutive_snapshots(tmp_path) -> None:
    """Five planted snapshots, --window-days=4 → 4 pairs walked."""
    _write_snapshot(tmp_path, date(2026, 5, 20), [("X", "Port X", +5.0, "Surplus")])
    _write_snapshot(tmp_path, date(2026, 5, 21), [("X", "Port X", +2.0, "Surplus")])
    _write_snapshot(tmp_path, date(2026, 5, 22), [("X", "Port X", -1.0, "Balanced")])
    _write_snapshot(tmp_path, date(2026, 5, 23), [("X", "Port X", -4.0, "Deficit")])
    _write_snapshot(tmp_path, date(2026, 5, 24), [("X", "Port X", -7.0, "Deficit")])

    result = build_bulk_diff(
        window_days=4,
        today=date(2026, 5, 24),
        root=tmp_path,
        min_delta_days=0.5,
    )
    assert result.n_pairs == 4
    assert len(result.snapshot_dates) == 5
    # X is in deficit on the after side of pairs 3 + 4 + 5? Actually
    # after-sides are 5/21, 5/22, 5/23, 5/24 → deficit on 22/23/24 → 3.
    by_locode = {r.locode: r for r in result.leaderboard}
    assert by_locode["X"].n_days_in_deficit == 3


def test_window_clamping_when_history_thinner_than_requested(tmp_path) -> None:
    """Requesting 30 days when only 5 snapshots exist must use what's
    available + populate the warning field rather than erroring."""
    for day, deficit in [
        (20, +5.0), (21, +3.0), (22, +1.0), (23, -1.0), (24, -3.0),
    ]:
        _write_snapshot(
            tmp_path, date(2026, 5, day),
            [("X", "Port X", deficit, "Balanced")],
        )

    result = build_bulk_diff(
        window_days=30,
        today=date(2026, 5, 24),
        root=tmp_path,
        min_delta_days=0.5,
    )
    assert result.warning != ""
    assert "clamped" in result.warning.lower() or "have" in result.warning.lower()
    # 5 snapshots → at most 4 pairs.
    assert result.n_pairs == 4


def test_build_bulk_diff_zero_window_returns_empty(tmp_path) -> None:
    """window_days=0 → zero pairs to diff, no warning, empty leaderboard."""
    _write_snapshot(tmp_path, date(2026, 5, 24), [("X", "Port X", -1.0, "Deficit")])

    result = build_bulk_diff(
        window_days=0,
        today=date(2026, 5, 24),
        root=tmp_path,
    )
    assert result.n_pairs == 0
    assert result.leaderboard == []


def test_build_bulk_diff_insufficient_snapshots(tmp_path) -> None:
    """Only 1 snapshot in the tree → can't form any pair → warning,
    empty leaderboard, no exception."""
    _write_snapshot(tmp_path, date(2026, 5, 24), [("X", "Port X", -1.0, "Deficit")])

    result = build_bulk_diff(
        window_days=7,
        today=date(2026, 5, 24),
        root=tmp_path,
    )
    assert result.n_pairs == 0
    assert result.leaderboard == []
    assert "insufficient" in result.warning.lower()


def test_build_bulk_diff_no_snapshots_at_all(tmp_path) -> None:
    """Empty snapshot tree → warning + empty leaderboard, no exception."""
    result = build_bulk_diff(
        window_days=7,
        today=date(2026, 5, 24),
        root=tmp_path,
    )
    assert result.n_pairs == 0
    assert result.leaderboard == []
    assert result.warning != ""


# ─────────────────────────────────────────────────────────────────────────
# 7. CLI end-to-end — exit codes + format outputs
# ─────────────────────────────────────────────────────────────────────────


def test_cli_default_format_text(tmp_path, capsys) -> None:
    for day, deficit in [(22, +5.0), (23, +1.0), (24, -3.0)]:
        _write_snapshot(
            tmp_path, date(2026, 5, day),
            [("X", "Port X", deficit, "Balanced")],
        )
    code = main([
        "--window-days", "2",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Port Supply Lines" in out
    assert "Locode" in out


def test_cli_format_json_emits_valid_json(tmp_path, capsys) -> None:
    for day, deficit in [(22, +5.0), (23, +1.0), (24, -3.0)]:
        _write_snapshot(
            tmp_path, date(2026, 5, day),
            [("X", "Port X", deficit, "Balanced")],
        )
    code = main([
        "--window-days", "2",
        "--format", "json",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "leaderboard" in payload
    assert "window_days_requested" in payload
    assert "n_pairs" in payload
    assert payload["window_days_requested"] == 2


def test_cli_format_markdown_emits_pipe_table(tmp_path, capsys) -> None:
    for day, deficit in [(22, +5.0), (23, +1.0), (24, -3.0)]:
        _write_snapshot(
            tmp_path, date(2026, 5, day),
            [("X", "Port X", deficit, "Balanced")],
        )
    code = main([
        "--window-days", "2",
        "--format", "markdown",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    # Markdown pipe-table rows look like '| ... | ... |'
    assert "| Locode |" in out
    assert "| --- |" in out or "| ---" in out


def test_cli_zero_window_returns_zero_and_empty_leaderboard(
    tmp_path, capsys,
) -> None:
    """--window-days=0 → exit 0, empty leaderboard, no error."""
    _write_snapshot(tmp_path, date(2026, 5, 24), [("X", "Port X", -1.0, "Deficit")])
    code = main([
        "--window-days", "0",
        "--format", "json",
        "--root", str(tmp_path),
    ])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["leaderboard"] == []
    assert payload["n_pairs"] == 0


def test_cli_insufficient_snapshots_returns_zero_with_stderr_warning(
    tmp_path, capsys,
) -> None:
    """Only 1 snapshot → exit 0 + stderr warning (not exit 1)."""
    _write_snapshot(tmp_path, date(2026, 5, 24), [("X", "Port X", -1.0, "Deficit")])
    code = main([
        "--window-days", "7",
        "--root", str(tmp_path),
    ])
    assert code == 0
    cap = capsys.readouterr()
    assert "warning" in cap.err.lower()


def test_cli_empty_snapshot_tree_returns_zero_with_warning(
    tmp_path, capsys,
) -> None:
    """No snapshots at all → exit 0 + stderr warning."""
    code = main([
        "--window-days", "7",
        "--root", str(tmp_path),
    ])
    assert code == 0
    cap = capsys.readouterr()
    assert "warning" in cap.err.lower()


def test_cli_out_path_writes_file(tmp_path) -> None:
    """--out <path> writes the report to a file instead of stdout."""
    for day, deficit in [(22, +5.0), (23, +1.0), (24, -3.0)]:
        _write_snapshot(
            tmp_path, date(2026, 5, day),
            [("X", "Port X", deficit, "Balanced")],
        )
    out_path = tmp_path / "out" / "report.json"
    code = main([
        "--window-days", "2",
        "--format", "json",
        "--root", str(tmp_path),
        "--out", str(out_path),
    ])
    assert code == 0
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert "leaderboard" in payload


# ─────────────────────────────────────────────────────────────────────────
# 8. Multi-port leaderboard — sanity check end-to-end
# ─────────────────────────────────────────────────────────────────────────


def test_multi_port_walk_ranks_most_concerning_first(tmp_path) -> None:
    """Plant a 4-snapshot tree with one calm port and one volatile
    port — the volatile one must lead the leaderboard."""
    # Day 1
    _write_snapshot(tmp_path, date(2026, 5, 21), [
        ("CALM", "Calm", +8.0, "Surplus"),
        ("HOT",  "Hot",  +5.0, "Surplus"),
    ])
    # Day 2 — HOT crosses into deficit
    _write_snapshot(tmp_path, date(2026, 5, 22), [
        ("CALM", "Calm", +7.8, "Surplus"),
        ("HOT",  "Hot",  -3.0, "Balanced"),
    ])
    # Day 3 — HOT worsens further, severity shift
    _write_snapshot(tmp_path, date(2026, 5, 23), [
        ("CALM", "Calm", +7.7, "Surplus"),
        ("HOT",  "Hot",  -10.0, "Deficit"),
    ])
    # Day 4 — HOT recovers briefly (entered/exited shows movement)
    _write_snapshot(tmp_path, date(2026, 5, 24), [
        ("CALM", "Calm", +7.9, "Surplus"),
        ("HOT",  "Hot",  -2.0, "Balanced"),
    ])

    result = build_bulk_diff(
        window_days=3,
        today=date(2026, 5, 24),
        root=tmp_path,
        min_delta_days=0.5,
    )
    assert result.n_pairs == 3
    locodes = [r.locode for r in result.leaderboard]
    assert "HOT" in locodes
    # HOT in deficit on 3 after-sides (5/22, 5/23, 5/24); CALM in
    # deficit 0 times → HOT must lead.
    assert locodes[0] == "HOT"
    hot_row = next(r for r in result.leaderboard if r.locode == "HOT")
    assert hot_row.n_days_in_deficit == 3
    assert hot_row.n_severity_shifts >= 1
