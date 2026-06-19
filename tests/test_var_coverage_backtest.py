"""VaR-coverage Kupiec POF backtest — risk number vs realized P&L (R070)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


# ── Kupiec POF math ──────────────────────────────────────────────────────────

def test_kupiec_at_nominal_does_not_reject() -> None:
    from processing.var_coverage_backtest import kupiec_pof
    # 50 breaches in 1000 days at 5% nominal == exactly nominal -> LR ~ 0.
    k = kupiec_pof(1000, 50, 0.05)
    assert k["lr"] == pytest.approx(0.0, abs=1e-6)
    assert not k["rejected"] and k["pvalue"] > 0.99


def test_kupiec_rejects_far_too_many_breaches() -> None:
    from processing.var_coverage_backtest import kupiec_pof
    k = kupiec_pof(1000, 200, 0.05)   # 20% breach rate vs 5% nominal
    assert k["rejected"] and k["lr"] > 3.841 and k["pvalue"] < 0.05


def test_kupiec_rejects_far_too_few_breaches() -> None:
    from processing.var_coverage_backtest import kupiec_pof
    # 0 breaches in 1000 days at 5% nominal -> dangerously conservative, rejects.
    k = kupiec_pof(1000, 0, 0.05)
    assert k["rejected"] and k["lr"] > 3.841


def test_kupiec_degenerate_inputs_safe() -> None:
    from processing.var_coverage_backtest import kupiec_pof
    assert kupiec_pof(0, 0, 0.05)["rejected"] is False
    assert kupiec_pof(100, 5, 0.0)["rejected"] is False     # bad nominal
    assert kupiec_pof(100, 150, 0.05)["rejected"] is False  # x>n guarded


# ── backtest_var_coverage over an injected panel ─────────────────────────────

def _normal_panel(tickers, *, n=700, seed=0, scale=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {t: rng.normal(0.0, scale, n) for t in tickers}, index=idx)


def test_wellcalibrated_iid_panel_not_rejected() -> None:
    # iid-normal returns -> a rolling historical VaR should cover at ~nominal,
    # so the Kupiec test must NOT reject (basis real).
    from processing.var_coverage_backtest import backtest_var_coverage
    panel = _normal_panel(["A", "B", "C"], n=700, seed=11)
    sc = backtest_var_coverage(panel, confidence=0.95, window=100)
    assert sc.basis == "real"
    assert sc.n_observations >= 30
    assert not sc.rejected and sc.well_calibrated
    assert abs(sc.breach_rate - sc.nominal_rate) < 0.05


def test_insufficient_history_is_not_fabricated() -> None:
    from processing.var_coverage_backtest import backtest_var_coverage
    assert backtest_var_coverage(pd.DataFrame()).basis == "insufficient"
    # Panel too short for window+min_oos -> insufficient, no verdict.
    short = _normal_panel(["A", "B"], n=60)
    sc = backtest_var_coverage(short, window=100)
    assert sc.basis == "insufficient" and not sc.well_calibrated


def test_insufficient_branch_breach_rate_is_self_consistent() -> None:
    # When OOS days are below min_oos the scorecard still must not disagree
    # with itself: breach_rate == n_breaches / n_observations (not a hardcoded 0).
    from processing.var_coverage_backtest import backtest_var_coverage
    # 40 rows, window 30 -> ~10 OOS days (< min_oos 25) -> insufficient branch.
    idx = pd.date_range("2024-01-01", periods=40, freq="B")
    rng = np.random.default_rng(9)
    rets = rng.normal(0.0, 0.02, 40)
    rets[-5:] = -0.25                      # force a couple of late breaches
    panel = pd.DataFrame({"A": rets, "B": rng.normal(0, 0.02, 40)}, index=idx)
    sc = backtest_var_coverage(panel, window=30, min_oos=25)
    assert sc.basis == "insufficient"
    expected = sc.n_breaches / sc.n_observations if sc.n_observations else 0.0
    assert sc.breach_rate == pytest.approx(expected)


def test_weights_subset_to_panel() -> None:
    from processing.var_coverage_backtest import backtest_var_coverage
    panel = _normal_panel(["A", "B", "C"], n=400, seed=2)
    sc = backtest_var_coverage(panel, weights={"A": 0.5, "B": 0.5, "Z": 1.0},
                               window=80)
    assert set(sc.tickers) == {"A", "B"}        # Z dropped (not in panel)
    assert sc.basis == "real"


def test_breach_rate_consistent_with_count() -> None:
    from processing.var_coverage_backtest import backtest_var_coverage
    sc = backtest_var_coverage(_normal_panel(["A", "B"], n=500, seed=5),
                               confidence=0.95, window=100)
    assert sc.breach_rate == pytest.approx(sc.n_breaches / sc.n_observations)


# ── live entry + cache loader ────────────────────────────────────────────────

def test_run_backtest_never_raises() -> None:
    from processing.var_coverage_backtest import run_var_coverage_backtest
    sc = run_var_coverage_backtest()
    assert sc.basis in ("real", "insufficient")


def test_cache_loader_handles_missing_dir(tmp_path) -> None:
    from processing.var_coverage_backtest import _load_cached_stock_data
    assert _load_cached_stock_data(str(tmp_path / "nope")) == {}


def test_adapter_registered_and_runs() -> None:
    from tools.backtests import ADAPTERS, _run_var_coverage
    assert _run_var_coverage in ADAPTERS
    r = _run_var_coverage()
    assert r.name == "VaR Coverage (Kupiec POF)"
    assert isinstance(r.healthy, bool)
    # New conditional-coverage fields are surfaced (None-safe when timing n/a).
    assert "breaches_clustered" in r.raw_fields
    assert "lr_independence" in r.raw_fields


# ── Christoffersen conditional-coverage battery (breach TIMING, not count) ────

def _seq(n, breach_idx):
    h = [0] * n
    for i in breach_idx:
        h[i] = 1
    return h


def test_christoffersen_spaced_breaches_not_assessable() -> None:
    # 3 SPACED breaches in 29 days: Kupiec passes (the count is fine) and, with
    # no consecutive breaches, independence is honestly 'not assessable' — never
    # a fabricated "independent" verdict, never NaN.
    from processing.var_coverage_backtest import (
        kupiec_pof, christoffersen_independence)
    k = kupiec_pof(29, 3, 0.05)
    assert not k["rejected"] and k["lr"] == pytest.approx(1.3513, abs=1e-3)
    ind = christoffersen_independence(_seq(29, [5, 14, 23]))
    assert ind["assessable"] is False
    assert ind["lr"] is None and ind["pvalue"] is None
    assert ind["rejected"] is False


def test_christoffersen_clustered_breaches_reject_while_kupiec_passes() -> None:
    # SAME count (3 breaches / 29 days) but BUNCHED. Kupiec is mathematically
    # blind to timing (still passes); Christoffersen independence REJECTS. This
    # controlled pair is the whole justification for the test.
    from processing.var_coverage_backtest import (
        kupiec_pof, christoffersen_independence)
    assert not kupiec_pof(29, 3, 0.05)["rejected"]          # count looks fine
    ind = christoffersen_independence(_seq(29, [10, 11, 12]))
    assert ind["assessable"] is True and ind["rejected"] is True
    assert ind["lr"] == pytest.approx(6.8517, abs=1e-3)
    assert ind["pvalue"] == pytest.approx(0.00886, abs=1e-3)


def test_conditional_coverage_is_uc_plus_ind() -> None:
    # LR_cc = LR_uc (Kupiec) + LR_ind (Christoffersen); chi2(2) p = exp(-lr/2).
    import math
    from processing.var_coverage_backtest import (
        kupiec_pof, christoffersen_independence)
    lr_uc = kupiec_pof(29, 3, 0.05)["lr"]
    lr_ind = christoffersen_independence(_seq(29, [10, 11, 12]))["lr"]
    lr_cc = lr_uc + lr_ind
    assert lr_cc == pytest.approx(8.203, abs=1e-2)
    assert math.exp(-lr_cc / 2.0) == pytest.approx(0.0166, abs=1e-3)


def test_independence_degenerate_inputs_safe() -> None:
    # Empty, single-obs, all-zero, all-one, and alternating sequences must all
    # return assessable=False with lr/pvalue None — never NaN, never a crash.
    from processing.var_coverage_backtest import christoffersen_independence
    for seq in ([], [0], [0] * 20, [1, 1], [1, 1, 1], [0, 1, 0, 1, 0, 1]):
        out = christoffersen_independence(seq)
        assert out["assessable"] is False
        assert out["lr"] is None and out["pvalue"] is None
        assert out["rejected"] is False


def test_backtest_populates_conditional_coverage_fields() -> None:
    # On a real-basis panel the new fields are populated & self-consistent:
    # when independence is assessable, LR_cc == kupiec_lr + LR_ind exactly and
    # p_cc == exp(-LR_cc/2); otherwise both are None (never NaN).
    import math
    from processing.var_coverage_backtest import backtest_var_coverage
    sc = backtest_var_coverage(_normal_panel(["A", "B", "C"], n=700, seed=11),
                               confidence=0.95, window=100)
    assert sc.basis == "real"
    if sc.independence_assessable:
        assert sc.lr_independence is not None
        assert sc.lr_conditional_coverage == pytest.approx(
            sc.kupiec_lr + sc.lr_independence, abs=1e-9)
        assert sc.pvalue_conditional_coverage == pytest.approx(
            math.exp(-sc.lr_conditional_coverage / 2.0), abs=1e-9)
    else:
        assert sc.lr_independence is None
        assert sc.lr_conditional_coverage is None
