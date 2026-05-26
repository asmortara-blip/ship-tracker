"""Defining-property tests for tools/port_supply_diff.py."""
from __future__ import annotations

import json

import pytest

from tools.port_supply_diff import (
    DiffReport,
    PortDelta,
    PortRow,
    compare_snapshots,
    format_json_report,
    format_markdown_report,
    format_text_report,
    main,
    parse_summary_csv,
)


# ── Helpers for hand-built snapshots ─────────────────────────────────────


def _row(
    locode: str,
    name: str = "Port",
    region: str = "Region",
    deficit: float = 0.0,
    severity: str = "Balanced",
    tickers: list[str] | None = None,
) -> PortRow:
    return PortRow(
        locode=locode, name=name, region=region,
        supply_deficit_days=deficit, severity_label=severity,
        top_exposed_tickers=tickers or [],
    )


# ── 1. CSV parser tolerates BOM + comment-header layout ──────────────────


def test_parse_summary_csv_handles_bom_and_comments(tmp_path) -> None:
    """The exporter writes a UTF-8 BOM + commented metadata lines; the
    parser must strip both before handing rows to csv.DictReader."""
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_summary_csv

    chains = build_port_supply_chains()
    csv_text = chains_to_summary_csv(chains)
    path = tmp_path / "summary.csv"
    path.write_text(csv_text, encoding="utf-8")

    rows = parse_summary_csv(path)
    assert len(rows) == len(chains)
    sample = rows[0]
    assert sample.locode
    assert isinstance(sample.supply_deficit_days, float)
    assert isinstance(sample.top_exposed_tickers, list)


def test_parse_summary_csv_skips_rows_with_blank_locode(tmp_path) -> None:
    path = tmp_path / "weird.csv"
    path.write_text(
        "# header comment\n"
        "deficit_rank,locode,port_name,supply_deficit_days,severity_label,top_exposed_tickers,summary\n"
        "1,,Blank,-5.0,Deficit,ZIM,blank\n"
        "2,CNSHA,Shanghai,-3.0,Deficit,ZIM,real\n",
        encoding="utf-8",
    )
    rows = parse_summary_csv(path)
    assert len(rows) == 1
    assert rows[0].locode == "CNSHA"


def test_parse_summary_csv_tolerates_missing_file(tmp_path) -> None:
    """Empty parse on a missing file delegates to FileNotFoundError —
    the CLI catches it + exits 1."""
    with pytest.raises(FileNotFoundError):
        parse_summary_csv(tmp_path / "does-not-exist.csv")


# ── 2. compare_snapshots — basic shape contract ──────────────────────────


def test_compare_empty_inputs_returns_empty_report() -> None:
    r = compare_snapshots([], [])
    assert isinstance(r, DiffReport)
    assert r.n_ports_before == 0
    assert r.n_ports_after == 0
    assert r.severity_shifts == []
    assert r.deficit_moves == []


def test_compare_identical_snapshots_reports_no_changes() -> None:
    rows = [
        _row("CNSHA", "Shanghai", deficit=-2.0, severity="Deficit"),
        _row("USLAX", "Los Angeles", deficit=+5.0, severity="Surplus"),
    ]
    r = compare_snapshots(rows, rows)
    assert r.severity_shifts == []
    assert r.deficit_moves == []
    assert r.entered_deficit == []
    assert r.exited_deficit == []
    assert r.ticker_shuffles == []


# ── 3. Severity-band shifts surface correctly ────────────────────────────


def test_severity_shift_detected() -> None:
    before = [_row("X", deficit=+5.0, severity="Surplus")]
    after  = [_row("X", deficit=-8.0, severity="Deficit")]
    r = compare_snapshots(before, after)
    assert len(r.severity_shifts) == 1
    d = r.severity_shifts[0]
    assert d.severity_before == "Surplus"
    assert d.severity_after  == "Deficit"
    assert d.severity_shifted is True
    assert d.deficit_delta == -13.0


# ── 4. Deficit-move threshold respected ──────────────────────────────────


def test_deficit_move_filtered_by_min_delta() -> None:
    """A 0.5d move below the default 1.0d threshold must NOT appear in
    deficit_moves, but a 5d move MUST."""
    before = [
        _row("SMALL", deficit=0.0, severity="Balanced"),
        _row("BIG",   deficit=0.0, severity="Balanced"),
    ]
    after = [
        _row("SMALL", deficit=-0.5, severity="Balanced"),  # 0.5d move
        _row("BIG",   deficit=-5.0, severity="Deficit"),   # 5.0d move
    ]
    r = compare_snapshots(before, after, min_delta_days=1.0)
    moved = {d.locode for d in r.deficit_moves}
    assert "BIG" in moved
    assert "SMALL" not in moved


def test_min_delta_zero_includes_all_moves() -> None:
    """min_delta_days=0 captures every change, however small."""
    before = [_row("X", deficit=0.0)]
    after  = [_row("X", deficit=-0.001)]
    r = compare_snapshots(before, after, min_delta_days=0.0)
    assert len(r.deficit_moves) == 1


# ── 5. Watchlist transitions (entered / exited) ──────────────────────────


def test_entered_deficit_flagged_when_crossing_zero() -> None:
    """Port that was > 0d and is now <= 0d → entered_deficit."""
    before = [_row("X", deficit=+2.0, severity="Surplus")]
    after  = [_row("X", deficit=-1.0, severity="Balanced")]
    r = compare_snapshots(before, after)
    locodes = {d.locode for d in r.entered_deficit}
    assert "X" in locodes


def test_exited_deficit_flagged_when_crossing_zero_other_way() -> None:
    before = [_row("X", deficit=-5.0, severity="Deficit")]
    after  = [_row("X", deficit=+3.0, severity="Surplus")]
    r = compare_snapshots(before, after)
    locodes = {d.locode for d in r.exited_deficit}
    assert "X" in locodes


def test_port_stays_in_deficit_is_not_flagged() -> None:
    """A port that was AND is in deficit doesn't fire either transition flag."""
    before = [_row("X", deficit=-5.0, severity="Deficit")]
    after  = [_row("X", deficit=-7.0, severity="Deficit")]
    r = compare_snapshots(before, after)
    assert r.entered_deficit == []
    assert r.exited_deficit == []


# ── 6. Ticker reshuffle detection ────────────────────────────────────────


def test_ticker_addition_and_removal_detected() -> None:
    before = [_row("X", tickers=["ZIM", "MATX", "DAC"])]
    after  = [_row("X", tickers=["ZIM", "GOGL", "SBLK"])]
    r = compare_snapshots(before, after)
    assert len(r.ticker_shuffles) == 1
    d = r.ticker_shuffles[0]
    assert set(d.tickers_added) == {"GOGL", "SBLK"}
    assert set(d.tickers_removed) == {"MATX", "DAC"}


def test_unchanged_ticker_set_not_reshuffled() -> None:
    """Same set, different order, → not flagged (set semantics)."""
    before = [_row("X", tickers=["ZIM", "MATX", "DAC"])]
    after  = [_row("X", tickers=["DAC", "ZIM", "MATX"])]
    r = compare_snapshots(before, after)
    assert r.ticker_shuffles == []


# ── 7. Set membership — locodes only in one snapshot ─────────────────────


def test_locode_only_in_before_surfaces() -> None:
    before = [_row("A"), _row("B"), _row("C")]
    after  = [_row("A"), _row("B")]
    r = compare_snapshots(before, after)
    assert r.locodes_only_in_before == ["C"]
    assert r.locodes_only_in_after == []


def test_locode_only_in_after_surfaces() -> None:
    before = [_row("A"), _row("B")]
    after  = [_row("A"), _row("B"), _row("NEW")]
    r = compare_snapshots(before, after)
    assert r.locodes_only_in_before == []
    assert r.locodes_only_in_after == ["NEW"]


# ── 8. Sort order — most-material first within each bucket ──────────────


def test_severity_shifts_ordered_by_abs_delta_desc() -> None:
    before = [
        _row("A", deficit=+1.0, severity="Surplus"),
        _row("B", deficit=+10.0, severity="Heavy Surplus"),
        _row("C", deficit=+5.0, severity="Surplus"),
    ]
    after = [
        _row("A", deficit=-2.0, severity="Balanced"),    # |delta|=3
        _row("B", deficit=-15.0, severity="Critical Deficit"),  # |delta|=25
        _row("C", deficit=-1.0, severity="Balanced"),    # |delta|=6
    ]
    r = compare_snapshots(before, after)
    abs_deltas = [abs(d.deficit_delta) for d in r.severity_shifts]
    assert abs_deltas == sorted(abs_deltas, reverse=True)


# ── 9. Formatters — text / json / markdown ───────────────────────────────


def test_format_text_includes_section_headers_and_counts() -> None:
    rows_a = [_row("X", deficit=+5.0, severity="Surplus")]
    rows_b = [_row("X", deficit=-8.0, severity="Deficit")]
    r = compare_snapshots(rows_a, rows_b)
    out = format_text_report(r)
    assert "Port Supply Lines — snapshot diff" in out
    assert "Severity band shifts" in out
    assert "Material deficit-day moves" in out
    assert "Entered deficit" in out
    assert "Exited deficit" in out
    assert "Top-ticker reshuffles" in out


def test_format_json_is_valid_json_with_expected_keys() -> None:
    rows_a = [_row("X", deficit=+5.0, severity="Surplus")]
    rows_b = [_row("X", deficit=-8.0, severity="Deficit")]
    r = compare_snapshots(rows_a, rows_b)
    out = format_json_report(r)
    payload = json.loads(out)
    for key in ("n_ports_before", "n_ports_after",
                "severity_shifts", "deficit_moves",
                "entered_deficit", "exited_deficit",
                "ticker_shuffles"):
        assert key in payload


def test_format_markdown_emits_tables() -> None:
    rows_a = [_row("X", deficit=+5.0, severity="Surplus")]
    rows_b = [_row("X", deficit=-8.0, severity="Deficit")]
    r = compare_snapshots(rows_a, rows_b)
    out = format_markdown_report(r)
    # Markdown table syntax: '| ... | ... |'
    assert "| Locode | Port |" in out
    assert "Severity band shifts" in out


# ── 10. CLI end-to-end ───────────────────────────────────────────────────


def test_cli_diff_two_files_returns_zero(tmp_path, capsys) -> None:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_summary_csv

    chains = build_port_supply_chains()
    (tmp_path / "a.csv").write_text(
        chains_to_summary_csv(chains), encoding="utf-8",
    )
    # Same data → expect zero diffs but still exit 0
    (tmp_path / "b.csv").write_text(
        chains_to_summary_csv(chains), encoding="utf-8",
    )
    code = main([str(tmp_path / "a.csv"), str(tmp_path / "b.csv")])
    assert code == 0


def test_cli_missing_file_returns_one(tmp_path, capsys) -> None:
    code = main([
        str(tmp_path / "missing.csv"),
        str(tmp_path / "also-missing.csv"),
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "could not read" in err


def test_cli_json_format_emits_valid_json(tmp_path, capsys) -> None:
    from processing.port_supply_lines import build_port_supply_chains
    from utils.port_supply_csv import chains_to_summary_csv

    chains = build_port_supply_chains()
    (tmp_path / "a.csv").write_text(
        chains_to_summary_csv(chains), encoding="utf-8",
    )
    (tmp_path / "b.csv").write_text(
        chains_to_summary_csv(chains), encoding="utf-8",
    )
    code = main([
        str(tmp_path / "a.csv"), str(tmp_path / "b.csv"),
        "--format", "json",
    ])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert "n_ports_before" in payload
