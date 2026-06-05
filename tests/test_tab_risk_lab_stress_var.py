"""Risk Lab surfaces the coherent cascade-grounded Stress-VaR/ES (R009 inc2)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def st_stub(monkeypatch):
    import streamlit as st

    def _make_col() -> MagicMock:
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__ = MagicMock(return_value=False)
        return col

    stub = MagicMock()
    stub.columns.side_effect = lambda spec, *a, **k: [
        _make_col() for _ in range(spec if isinstance(spec, int) else len(list(spec)))
    ]
    for attr in ("markdown", "caption", "write", "info", "warning", "error",
                 "success", "metric", "subheader", "plotly_chart", "divider"):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    yield stub


def _emitted(stub) -> str:
    out = []
    for c in stub.call_args_list:
        for a in c.args:
            if isinstance(a, str):
                out.append(a)
    return "\n".join(out)


def _idea(ticker, direction, conviction=0.8):
    return SimpleNamespace(
        ticker=ticker, direction=direction, conviction_score=conviction)


def _stock(tickers, n=200, seed=2):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    return {t: pd.DataFrame(
        {"close": 100.0 * np.exp(np.cumsum(rng.normal(0.0003, 0.02, n)))}, index=idx)
        for t in tickers}


def test_section_renders_es_and_component_bar(st_stub) -> None:
    from ui.tab_risk_lab import _render_stress_var_es
    weights = {"ZIM": 0.4, "SBLK": 0.3, "DAC": 0.3}
    ideas = [_idea("ZIM", "Bearish", 0.9), _idea("SBLK", "Bullish", 0.5)]
    _render_stress_var_es(weights, ideas, _stock(["ZIM", "SBLK", "DAC"]),
                          1_000_000.0, 0.95)
    text = _emitted(st_stub)
    assert "Expected Shortfall" in text
    assert "Stress VaR" in text
    # render reached the trailing caption (i.e. ran past the component-ES bar)
    assert "Euler split" in text
    # the component-ES bar figure was handed to a render call
    import plotly.graph_objects as go
    drew_fig = any(
        isinstance(a, go.Figure) for c in st_stub.call_args_list for a in c.args
    )
    assert drew_fig


def test_section_empty_book_is_quiet(st_stub) -> None:
    from ui.tab_risk_lab import _render_stress_var_es
    _render_stress_var_es({}, [], {}, 1_000_000.0, 0.95)
    assert "No positions to stress" in _emitted(st_stub)


def test_build_cascade_ideas_never_raises() -> None:
    from ui.tab_risk_lab import _build_cascade_ideas
    out = _build_cascade_ideas(None, None, None, None, None, None)
    assert isinstance(out, list)


def test_basis_label_real_vs_fallback(st_stub) -> None:
    from ui.tab_risk_lab import _render_stress_var_es
    # real cov -> "Real" surfaces; dark prices -> "Fallback"
    _render_stress_var_es({"ZIM": 0.5, "SBLK": 0.5}, [],
                          _stock(["ZIM", "SBLK"]), 1_000_000.0, 0.95)
    assert "Real" in _emitted(st_stub)

    st_stub.reset_mock()
    st_stub.columns.side_effect = lambda spec, *a, **k: [
        MagicMock(__enter__=MagicMock(return_value=MagicMock()),
                  __exit__=MagicMock(return_value=False))
        for _ in range(spec if isinstance(spec, int) else len(list(spec)))
    ]
    _render_stress_var_es({"ZIM": 0.5, "SBLK": 0.5}, [], {}, 1_000_000.0, 0.95)
    assert "Fallback" in _emitted(st_stub)
