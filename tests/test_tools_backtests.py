"""Defining-property tests for tools/backtests.py.

Pins the CLI's contract: every validator adapter must produce a
BacktestResult; the three formatters must each emit a non-empty string;
strict mode must exit 1 only when at least one validator reports
unhealthy. The CLI is the public, CI-facing entry point for the
backtest layer — these tests guard against drift in the adapter API
or formatter shape.
"""
from __future__ import annotations

import json

import pytest

from tools.backtests import (
    ADAPTERS,
    BacktestResult,
    format_json,
    format_markdown,
    format_text,
    main,
    run_all_backtests,
)


# ── 1. Adapter contract: every adapter returns a well-formed result ────────

def test_every_adapter_returns_a_backtestresult() -> None:
    """All canonical adapters must be wired in and each must return
    a BacktestResult with the required string fields populated."""
    results = run_all_backtests()
    assert len(results) == len(ADAPTERS)
    assert len(results) == 20
    for r in results:
        assert isinstance(r, BacktestResult)
        assert isinstance(r.name, str) and r.name
        assert isinstance(r.headline_label, str) and r.headline_label
        assert isinstance(r.headline_value, str) and r.headline_value
        assert isinstance(r.healthy, bool)
        assert isinstance(r.summary, str) and r.summary


def test_validator_names_are_unique() -> None:
    """Two adapters must never share a name — the report ordering
    + the JSON output rely on uniqueness."""
    results = run_all_backtests()
    names = [r.name for r in results]
    assert len(names) == len(set(names))


# ── 2. Healthiness signal — synth defaults should all read healthy ────────

def test_all_validators_healthy_on_default_synth() -> None:
    """The bundled synthetic generators are tuned so each validator's
    primary calibration / monotonicity check passes at default quality.
    If a future refactor moves the noise floor without retuning the
    synth, this catches it."""
    results = run_all_backtests()
    # "VaR Coverage" is the one REAL-data validator — its health depends on
    # the live price cache, not the bundled synth, so it's out of scope for
    # this synth-tuning guard (it has its own tests in
    # test_var_coverage_backtest.py).
    unhealthy = [r.name for r in results
                 if not r.healthy and "VaR Coverage" not in r.name]
    assert not unhealthy, (
        f"{len(unhealthy)} validator(s) unhealthy on default synth: {unhealthy}"
    )


# ── 3. Formatters always return non-empty strings ─────────────────────────

def test_format_text_contains_validator_names() -> None:
    results = run_all_backtests()
    text = format_text(results)
    assert isinstance(text, str) and text
    for r in results:
        assert r.name in text


def test_format_json_is_valid_json_with_expected_keys() -> None:
    results = run_all_backtests()
    blob = format_json(results)
    payload = json.loads(blob)
    assert "validators" in payload
    assert "healthy_count" in payload
    assert "total" in payload
    assert payload["total"] == len(results)
    assert len(payload["validators"]) == len(results)
    for v in payload["validators"]:
        assert {"name", "headline_label", "headline_value",
                "healthy", "summary", "raw"} <= set(v.keys())


def test_format_markdown_renders_table_header_and_one_row_per_validator() -> None:
    results = run_all_backtests()
    md = format_markdown(results)
    assert md.startswith("| Validator | Status | Headline |")
    # One header row + one separator row + N validator rows + blank + footer
    lines = [l for l in md.splitlines() if l.startswith("|")]
    assert len(lines) == 2 + len(results)


# ── 4. CLI exit codes ─────────────────────────────────────────────────────

def test_cli_default_returns_zero_when_all_healthy(capsys) -> None:
    """Non-strict default mode exits 0 regardless of healthy flags."""
    code = main([])
    assert code == 0
    captured = capsys.readouterr().out
    assert "Ship Tracker — consolidated backtest report" in captured


def test_cli_strict_mode_returns_zero_on_all_healthy(capsys) -> None:
    code = main(["--strict"])
    assert code == 0


def test_cli_format_json_emits_valid_json(capsys) -> None:
    code = main(["--format", "json"])
    assert code == 0
    captured = capsys.readouterr().out
    payload = json.loads(captured)
    assert payload["total"] == 20


def test_cli_format_markdown_emits_table_header(capsys) -> None:
    code = main(["--format", "markdown"])
    assert code == 0
    captured = capsys.readouterr().out
    assert "| Validator | Status | Headline |" in captured


# ── 5. Strict mode flips to exit 1 when an adapter reports unhealthy ──────

def test_cli_strict_returns_one_when_an_adapter_is_unhealthy(monkeypatch, capsys) -> None:
    """Force one BacktestResult to be unhealthy and verify strict exits 1."""
    import tools.backtests as tb

    # Cache the original before monkeypatching to avoid recursion.
    _original = tb.run_all_backtests

    def _stub() -> list[BacktestResult]:
        results = _original()
        if results:
            results[0].healthy = False
        return results

    monkeypatch.setattr(tb, "run_all_backtests", _stub)
    code = tb.main(["--strict"])
    assert code == 1


# ── 6. Baseline save + compare modes ──────────────────────────────────────

def test_save_baseline_writes_valid_json_with_validator_block(tmp_path) -> None:
    """`save_baseline` must write a JSON file the compare reader can load."""
    import json
    from tools.backtests import save_baseline

    results = run_all_backtests()
    out = tmp_path / "baseline.json"
    save_baseline(results, str(out))
    payload = json.loads(out.read_text())
    assert "validators" in payload
    assert payload["total"] == len(results)
    for v in payload["validators"]:
        assert {"name", "healthy", "headline_label", "headline_value",
                "summary", "raw"} <= set(v.keys())


def test_compare_to_baseline_returns_empty_on_identical_snapshot(tmp_path) -> None:
    """Saving + immediately comparing must report zero drift."""
    from tools.backtests import compare_to_baseline, save_baseline

    results = run_all_backtests()
    out = tmp_path / "baseline.json"
    save_baseline(results, str(out))
    drifts = compare_to_baseline(results, str(out))
    assert drifts == []


def test_compare_to_baseline_flags_healthy_flip(tmp_path) -> None:
    """A healthy-flag flip in either direction must register as drift."""
    import json
    from tools.backtests import compare_to_baseline, save_baseline

    results = run_all_backtests()
    out = tmp_path / "baseline.json"
    save_baseline(results, str(out))
    # Mutate the saved baseline to flip the first validator's healthy flag
    payload = json.loads(out.read_text())
    payload["validators"][0]["healthy"] = not payload["validators"][0]["healthy"]
    out.write_text(json.dumps(payload))
    drifts = compare_to_baseline(results, str(out))
    healthy_drifts = [d for d in drifts if d.field_name == "healthy"]
    assert len(healthy_drifts) == 1
    assert healthy_drifts[0].validator == results[0].name


def test_compare_to_baseline_flags_numeric_drift_beyond_tolerance(tmp_path) -> None:
    """A raw_field shift larger than the per-metric tolerance triggers drift."""
    import json
    from tools.backtests import compare_to_baseline, save_baseline

    results = run_all_backtests()
    out = tmp_path / "baseline.json"
    save_baseline(results, str(out))
    payload = json.loads(out.read_text())
    # Pick a validator that carries 'best_rate' (SSI / SCHI) and shift far enough
    target = None
    for v in payload["validators"]:
        if "best_rate" in (v.get("raw") or {}):
            v["raw"]["best_rate"] = 0.10   # large drift from ~0.80
            target = v["name"]
            break
    assert target is not None, "no validator with 'best_rate' raw_field"
    out.write_text(json.dumps(payload))
    drifts = compare_to_baseline(results, str(out))
    rate_drifts = [d for d in drifts
                   if d.field_name == "best_rate" and d.validator == target]
    assert len(rate_drifts) == 1
    assert rate_drifts[0].delta is not None and rate_drifts[0].delta > 0.05


def test_compare_to_baseline_ignores_tiny_numeric_jitter(tmp_path) -> None:
    """Sub-tolerance drift (e.g. 0.001 on best_rate where tolerance is 0.05)
    must NOT count as drift."""
    import json
    from tools.backtests import compare_to_baseline, save_baseline

    results = run_all_backtests()
    out = tmp_path / "baseline.json"
    save_baseline(results, str(out))
    payload = json.loads(out.read_text())
    for v in payload["validators"]:
        if "best_rate" in (v.get("raw") or {}):
            v["raw"]["best_rate"] = float(v["raw"]["best_rate"]) + 0.001
    out.write_text(json.dumps(payload))
    drifts = compare_to_baseline(results, str(out))
    assert all(d.field_name != "best_rate" for d in drifts)


def test_cli_save_baseline_writes_file_and_returns_zero(tmp_path, capsys) -> None:
    out = tmp_path / "baseline.json"
    code = main(["--save-baseline", str(out)])
    assert code == 0
    assert out.exists()


def test_cli_compare_baseline_returns_zero_on_no_drift(tmp_path, capsys) -> None:
    out = tmp_path / "baseline.json"
    main(["--save-baseline", str(out)])
    capsys.readouterr()  # drain the save-baseline output
    code = main(["--compare-baseline", str(out)])
    assert code == 0


def test_cli_compare_baseline_returns_one_on_drift(tmp_path, capsys) -> None:
    import json

    out = tmp_path / "baseline.json"
    main(["--save-baseline", str(out)])
    capsys.readouterr()
    # Force one large numeric drift in the saved baseline
    payload = json.loads(out.read_text())
    for v in payload["validators"]:
        if "best_rate" in (v.get("raw") or {}):
            v["raw"]["best_rate"] = 0.10
            break
    out.write_text(json.dumps(payload))
    code = main(["--compare-baseline", str(out)])
    assert code == 1


def test_cli_rejects_save_and_compare_together(capsys) -> None:
    code = main(["--save-baseline", "/tmp/x.json", "--compare-baseline", "/tmp/y.json"])
    assert code == 2


# ── 7. Verbose mode renders per-class scorecard rows ──────────────────────

def test_every_validator_carries_scorecard_rows() -> None:
    """Every adapter populates scorecard_rows so --verbose has content
    to render for every validator."""
    results = run_all_backtests()
    for r in results:
        assert isinstance(r.scorecard_rows, list)
        assert len(r.scorecard_rows) > 0, f"{r.name} has no scorecard rows"
        for row in r.scorecard_rows:
            assert {"label", "metric_name", "value"} <= set(row.keys())


def test_format_text_verbose_renders_scorecard_rows() -> None:
    """Verbose text includes per-class rows below each validator block."""
    results = run_all_backtests()
    out = format_text(results, verbose=True)
    # Pick a known label that should appear in the SSI scorecard rows.
    assert "chokepoint" in out
    # And a known momentum signal class.
    assert "STRONG_BUY" in out
    # And the metric-name template.
    assert "sign-agreement = " in out


def test_format_text_non_verbose_omits_scorecard_rows() -> None:
    """Default (non-verbose) text must NOT include the per-class rows —
    it's a one-line-per-validator overview. We specifically check that
    the row template (8-space prefix + dash + label + double-space +
    metric = value) is absent, not the loose phrase 'sign-agreement'
    (which still appears in each validator's summary text)."""
    results = run_all_backtests()
    out = format_text(results, verbose=False)
    # The per-row template uses "        - " prefix; should be absent.
    assert "        - " not in out


def test_cli_verbose_flag_renders_per_class_rows(capsys) -> None:
    """`--verbose` flag wires through to the text formatter."""
    code = main(["--verbose"])
    assert code == 0
    out = capsys.readouterr().out
    assert "        - chokepoint" in out


# ── 8. Company Supply Risk validator — specific contract ──────────────────


def test_company_supply_risk_validator_is_registered() -> None:
    """The per-ticker supply-risk validator must show up in the canonical
    list with the expected name + raw_fields shape."""
    results = run_all_backtests()
    by_name = {r.name: r for r in results}
    assert "Company Supply Risk Stability" in by_name
    r = by_name["Company Supply Risk Stability"]
    assert {"n_runs", "noise", "top_n",
            "overall_mean_stability", "overall_min_stability",
            "stable"} <= set(r.raw_fields.keys())
    assert isinstance(r.healthy, bool)
    assert r.scorecard_rows  # one row per perturbation run


def test_company_supply_risk_verbose_renders_per_run_rows(capsys) -> None:
    """Verbose mode surfaces the per-run Jaccard scorecards for the new
    validator."""
    code = main(["--verbose"])
    assert code == 0
    out = capsys.readouterr().out
    # Run labels are run0, run1, ... — pin at least one shows up.
    assert "        - run0" in out
    assert "top-N jaccard = " in out


def test_compare_baseline_missing_file_returns_2(capsys) -> None:
    """A missing baseline path is a config error → exit 2, NOT exit 1 (which is
    reserved for 'drift detected'), so CI can distinguish the two."""
    from tools.backtests import main
    rc = main(["--compare-baseline", "/nonexistent/dir/missing-baseline.json"])
    assert rc == 2
    assert "cannot read baseline" in capsys.readouterr().err


def test_compare_baseline_corrupt_file_returns_2(tmp_path, capsys) -> None:
    """A malformed baseline JSON exits 2 cleanly, not via an uncaught traceback."""
    from tools.backtests import main
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    rc = main(["--compare-baseline", str(bad)])
    assert rc == 2
    assert "cannot read baseline" in capsys.readouterr().err


def test_save_baseline_bad_dir_returns_2(tmp_path, capsys) -> None:
    """--save-baseline into a non-existent directory exits 2, not a traceback."""
    from tools.backtests import main
    rc = main(["--save-baseline", str(tmp_path / "no_such_dir" / "b.json")])
    assert rc == 2
    assert "cannot write baseline" in capsys.readouterr().err
