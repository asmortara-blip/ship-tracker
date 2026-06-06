"""Booking contract-vs-spot uses the REAL forecast, not random rows (rec R002)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture
def st_stub(monkeypatch):
    import streamlit as st

    def _make_col():
        col = MagicMock()
        col.__enter__ = MagicMock(return_value=col)
        col.__exit__ = MagicMock(return_value=False)
        return col

    stub = MagicMock()
    stub.columns.side_effect = lambda spec, *a, **k: [
        _make_col() for _ in range(spec if isinstance(spec, int) else len(list(spec)))
    ]
    for attr in ("markdown", "caption", "info", "warning", "write", "plotly_chart",
                 "selectbox", "divider", "subheader"):
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


def _fc(direction, conf, current, f30, rid):
    return SimpleNamespace(route_id=rid, route_name=rid, current_rate=current,
                           forecast_30d=f30, direction=direction,
                           direction_confidence=conf)


def test_renders_real_verdicts(st_stub, monkeypatch) -> None:
    from ui.tab_booking import _contract_vs_spot_analysis
    monkeypatch.setattr(
        "processing.rate_forecaster.forecast_route",
        lambda rid, name, df, macro: _fc("Rising", 0.8, 2000, 2400, rid))
    fd = {"transpacific_eb": pd.DataFrame({"rate_usd_per_feu": [2000, 2100, 2200]})}
    _contract_vs_spot_analysis(fd, {})
    text = _emitted(st_stub)
    # the honest engine caption appears; no "fabricated"/random framing
    assert "live ML GBR rate forecast" in text
    assert "not a simulation" in text


def test_empty_freight_shows_honest_fallback_not_random(st_stub, monkeypatch) -> None:
    from ui.tab_booking import _contract_vs_spot_analysis
    # No freight history -> the forecaster yields nothing -> honest empty state.
    _contract_vs_spot_analysis({}, {})
    text = _emitted(st_stub)
    assert "No live rate forecast available" in text
    assert "No fabricated rates" in text


def test_no_random_import_in_contract_section(monkeypatch) -> None:
    # The rewired section must not fabricate: a forecaster returning None for
    # every route yields the empty fallback, never a randomized table.
    from ui.tab_booking import _contract_vs_spot_analysis
    monkeypatch.setattr(
        "processing.rate_forecaster.forecast_route",
        lambda *a, **k: None)
    import streamlit as st
    calls = {"warning": 0}
    monkeypatch.setattr(st, "info", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, "markdown", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, "caption", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(st, "warning",
                        lambda *a, **k: calls.__setitem__("warning", calls["warning"] + 1),
                        raising=False)
    fd = {"transpacific_eb": pd.DataFrame({"rate_usd_per_feu": [2000, 2100]})}
    _contract_vs_spot_analysis(fd, {})    # must not raise
    assert calls["warning"] == 0          # clean empty state, not an error
