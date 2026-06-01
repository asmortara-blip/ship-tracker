"""Defining-property tests for tools/forecast_accuracy_cli.py."""
from __future__ import annotations

import json

import pytest

from tools.forecast_accuracy_cli import main, _build_parser
from processing.forecast_accuracy_tracker import (
    ActualRecord, ForecastRecord, log_actual, log_forecast,
)


# ── Parser shape ─────────────────────────────────────────────────────────


def test_parser_default_format_is_text() -> None:
    p = _build_parser()
    args = p.parse_args([])
    assert args.format == "text"


def test_parser_window_days_defaults_to_60() -> None:
    p = _build_parser()
    args = p.parse_args([])
    assert args.window_days == 60


# ── Empty store ──────────────────────────────────────────────────────────


def test_cli_on_empty_root_returns_zero_pairs(tmp_path, capsys) -> None:
    code = main(["--root", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Pairs scored: 0" in out
    assert "no paired rows" in out


# ── Single paired forecast ───────────────────────────────────────────────


def test_cli_text_output_includes_mae_for_one_pair(tmp_path, capsys) -> None:
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.4, lane_id="fleet",
    ), root=tmp_path)
    code = main([
        "--root", str(tmp_path),
        "--today", "2026-05-27",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Pairs scored: 1" in out
    # error = 0.5 - 0.4 = 0.1; MAE on one pair is the abs error
    assert "MAE:" in out


def test_cli_lane_filter_applies(tmp_path, capsys) -> None:
    """--lane=X drops rows that don't belong to X."""
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.4, lane_id="fleet",
    ), root=tmp_path)
    code = main([
        "--root", str(tmp_path),
        "--today", "2026-05-27",
        "--lane", "transpacific_eb",   # non-matching lane
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "transpacific_eb" in out
    assert "Pairs scored: 0" in out   # filtered out


# ── JSON format ──────────────────────────────────────────────────────────


def test_cli_json_format_is_valid_json_with_required_keys(
    tmp_path, capsys,
) -> None:
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-05-20", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-05-27", actual_value=0.4, lane_id="fleet",
    ), root=tmp_path)
    code = main([
        "--root", str(tmp_path),
        "--today", "2026-05-27",
        "--format", "json",
    ])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert "summary" in payload
    assert "rows" in payload
    assert payload["summary"]["n_pairs"] == 1
    assert len(payload["rows"]) == 1
    row = payload["rows"][0]
    assert row["lane_id"] == "fleet"
    assert row["horizon_days"] == 7


# ── --today validation ──────────────────────────────────────────────────


def test_cli_rejects_bad_today_format(tmp_path, capsys) -> None:
    code = main([
        "--root", str(tmp_path),
        "--today", "not-a-date",
    ])
    assert code == 1
    err = capsys.readouterr().err
    assert "bad --today" in err


# ── --window-days clamp ─────────────────────────────────────────────────


def test_cli_window_days_drops_out_of_window_pairs(tmp_path, capsys) -> None:
    """A pair whose target is older than --window-days is filtered out."""
    # Forecast target 2026-01-08, anchor 2026-05-27, window 30d → out
    log_forecast(ForecastRecord(
        forecast_date_iso="2026-01-01", horizon_days=7,
        predicted_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    log_actual(ActualRecord(
        actual_date_iso="2026-01-08", actual_value=0.5, lane_id="fleet",
    ), root=tmp_path)
    code = main([
        "--root", str(tmp_path),
        "--today", "2026-05-27",
        "--window-days", "30",
    ])
    assert code == 0
    out = capsys.readouterr().out
    assert "Pairs scored: 0" in out
