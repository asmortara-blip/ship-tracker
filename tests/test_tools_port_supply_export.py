"""Defining-property tests for tools/port_supply_export.py."""
from __future__ import annotations

import csv
import io
import json

import pytest

from tools.port_supply_export import (
    ALL_VIEWS,
    VIEW_REGISTRY,
    ExportResult,
    export_views,
    main,
)


# ── 1. Default export writes every view ───────────────────────────────────

def test_default_export_writes_one_file_per_view(tmp_path) -> None:
    results = export_views(out_dir=tmp_path, stamp="20260526")
    assert len(results) == len(ALL_VIEWS)
    assert all(r.ok for r in results)
    # Each view's file exists on disk + has non-trivial bytes.
    for r in results:
        assert r.bytes_written > 0
        assert (tmp_path / r.path.split("/")[-1]).exists()


def test_default_export_filename_includes_view_and_container_and_stamp(
    tmp_path,
) -> None:
    """Filenames must be uniquely identifiable from the stem so an
    operator pulling per-day exports can sort by date + view."""
    results = export_views(
        out_dir=tmp_path, container_type="40FT_REEFER", stamp="20260526",
    )
    for r in results:
        name = r.path.split("/")[-1]
        assert "40ft_reefer" in name      # container type lowercased
        assert "20260526" in name          # date stamp
        # The view stem appears as the first segment.
        stem, _ = VIEW_REGISTRY[r.view]
        assert name.startswith(stem)


# ── 2. View subset honoured ───────────────────────────────────────────────

def test_export_subset_writes_only_requested_views(tmp_path) -> None:
    results = export_views(
        views=("summary", "watchlist"), out_dir=tmp_path, stamp="20260526",
    )
    assert {r.view for r in results} == {"summary", "watchlist"}
    files = sorted(p.name for p in tmp_path.glob("*.csv"))
    assert len(files) == 2


def test_export_unknown_view_marks_ok_false_but_continues(tmp_path) -> None:
    """An unknown view name in the list must NOT abort the run — other
    views still attempted, the bad one carries ok=False + error msg."""
    results = export_views(
        views=("summary", "bogus_view", "watchlist"),
        out_dir=tmp_path, stamp="20260526",
    )
    by_view = {r.view: r for r in results}
    assert by_view["summary"].ok is True
    assert by_view["bogus_view"].ok is False
    assert "unknown view" in by_view["bogus_view"].error
    assert by_view["watchlist"].ok is True


# ── 3. Files round-trip through csv.reader (proves they're valid CSV) ────

def test_written_files_parse_as_valid_csv(tmp_path) -> None:
    """The CSV writer must produce files that csv.reader can parse
    without choking — covers both the BOM prefix + the comment header
    lines (csv.reader treats them as data rows, which is fine; we
    just need the parse to succeed)."""
    export_views(out_dir=tmp_path, stamp="20260526")
    for path in tmp_path.glob("*.csv"):
        rows = list(csv.reader(open(path, encoding="utf-8")))
        assert len(rows) >= 1
        # First data row (after BOM + the comment header block) should
        # have multiple columns — a single-column file would mean the
        # comma escaping is broken. Strip a leading BOM off the first
        # cell of every row before the comment filter.
        def _strip_bom(s: str) -> str:
            return s[1:] if s.startswith("﻿") else s
        data_rows = [
            r for r in rows
            if r and not _strip_bom(r[0]).startswith("# ")
        ]
        if data_rows:
            assert len(data_rows[0]) > 1


# ── 4. out_dir is created if missing ─────────────────────────────────────

def test_export_creates_missing_out_dir(tmp_path) -> None:
    nested = tmp_path / "nested" / "deep" / "snapshots"
    assert not nested.exists()
    results = export_views(views=("summary",), out_dir=nested,
                           stamp="20260526")
    assert nested.exists()
    assert results[0].ok


# ── 5. Container-type + threshold propagate ──────────────────────────────

def test_container_type_propagates_into_filenames_and_metadata(tmp_path) -> None:
    """Container-type must show up in both the filename suffix and the
    on-disk file's metadata header."""
    results = export_views(
        views=("summary",), out_dir=tmp_path,
        container_type="40FT_REEFER", stamp="20260526",
    )
    path = tmp_path / results[0].path.split("/")[-1]
    body = path.read_text(encoding="utf-8")
    assert "40FT_REEFER" in body  # in the metadata header
    assert "40ft_reefer" in str(path)  # in the filename


def test_threshold_days_propagates_into_watchlist(tmp_path) -> None:
    """Tighter threshold → smaller watchlist (or empty + comment row)."""
    loose = export_views(
        views=("watchlist",), out_dir=tmp_path, threshold_days=0.0,
        stamp="20260526",
    )
    tight = export_views(
        views=("watchlist",), out_dir=tmp_path, threshold_days=-1000.0,
        stamp="20260527",
    )
    assert loose[0].bytes_written > tight[0].bytes_written


# ── 6. Pure entry-point determinism ──────────────────────────────────────

def test_export_results_carry_absolute_paths(tmp_path) -> None:
    results = export_views(out_dir=tmp_path, stamp="20260526")
    for r in results:
        if r.ok:
            assert r.path.startswith("/"), f"expected absolute path, got {r.path}"


# ── 7. CLI argparse + exit codes ─────────────────────────────────────────

def test_cli_default_run_returns_zero_and_writes_files(tmp_path, capsys) -> None:
    code = main(["--out-dir", str(tmp_path), "--quiet"])
    assert code == 0
    assert len(list(tmp_path.glob("*.csv"))) == len(ALL_VIEWS)


def test_cli_subset_returns_zero(tmp_path, capsys) -> None:
    code = main([
        "--views", "summary,watchlist",
        "--out-dir", str(tmp_path),
        "--quiet",
    ])
    assert code == 0
    assert len(list(tmp_path.glob("*.csv"))) == 2


def test_cli_unknown_view_returns_two(tmp_path, capsys) -> None:
    code = main([
        "--views", "summary,bogus",
        "--out-dir", str(tmp_path),
        "--quiet",
    ])
    # Argparse handles --views as a single string; unknown views are
    # rejected by main() with exit 2.
    assert code == 2
    captured = capsys.readouterr()
    assert "unknown view" in captured.err


def test_cli_json_flag_emits_valid_json(tmp_path, capsys) -> None:
    code = main([
        "--out-dir", str(tmp_path),
        "--json",
    ])
    assert code == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["ok_count"] == payload["total"]
    assert payload["total"] == len(ALL_VIEWS)
    for entry in payload["results"]:
        assert entry["ok"] is True
        assert entry["path"]
        assert entry["bytes_written"] > 0


def test_cli_quiet_suppresses_stdout(tmp_path, capsys) -> None:
    code = main([
        "--out-dir", str(tmp_path),
        "--quiet",
    ])
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out == ""


def test_cli_rejects_invalid_container_type(tmp_path, capsys) -> None:
    """argparse choices catches bad container_type → SystemExit(2)."""
    with pytest.raises(SystemExit) as excinfo:
        main([
            "--out-dir", str(tmp_path),
            "--container-type", "BANANA",
        ])
    assert excinfo.value.code == 2


# ── 8. Registry sanity ───────────────────────────────────────────────────

def test_view_registry_covers_five_canonical_views() -> None:
    """Five views, no more, no less — schema check against today's set."""
    assert set(VIEW_REGISTRY.keys()) == {
        "summary", "exposure", "footprint", "regional", "watchlist",
    }


def test_view_registry_filename_stems_are_unique() -> None:
    """Each view's filename stem must be unique so the on-disk files
    don't collide when written into the same directory."""
    stems = [stem for stem, _ in VIEW_REGISTRY.values()]
    assert len(stems) == len(set(stems))
