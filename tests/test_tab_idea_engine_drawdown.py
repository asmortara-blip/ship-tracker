"""The Idea Engine track-record panel surfaces the drawdown kill-switch (B2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


@pytest.fixture
def st_stub(monkeypatch):
    """Minimal Streamlit stub: every render call funnels through one mock so
    the test can scan all emitted strings."""
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
                 "success", "metric", "subheader", "divider"):
        monkeypatch.setattr(st, attr, stub, raising=False)
    monkeypatch.setattr(st, "columns", stub.columns, raising=False)
    yield stub


class _Idea:
    def __init__(self, ticker, direction, price=100.0, conviction_label="High"):
        self.ticker = ticker
        self.direction = direction
        self.price = price
        self.conviction_score = 0.7
        self.conviction_label = conviction_label
        self.conviction_weight_set = "default"


def _stock(last_closes: dict):
    idx = pd.date_range("2026-01-01", periods=3, freq="B")
    return {t: pd.DataFrame({"close": [p * 0.9, p * 0.95, p]}, index=idx)
            for t, p in last_closes.items()}


def _emitted_text(stub) -> str:
    """Concatenate every positional string the stub was ever called with."""
    out = []
    for c in stub.call_args_list:
        for a in c.args:
            if isinstance(a, str):
                out.append(a)
    return "\n".join(out)


def test_track_record_panel_flags_stand_down_tier(st_stub) -> None:
    from state.signal_ledger import freeze_ideas
    from ui.tab_idea_engine import _render_track_record

    # 3 winners then 3 big losers in the High tier -> kill-switch trips.
    freeze_ideas([_Idea(f"W{i}", "Bullish", conviction_label="High") for i in range(3)],
                 issue_date="2026-06-01")
    freeze_ideas([_Idea(f"L{i}", "Bullish", conviction_label="High") for i in range(3)],
                 issue_date="2026-06-02")
    stock = _stock({**{f"W{i}": 110.0 for i in range(3)},
                    **{f"L{i}": 80.0 for i in range(3)}})

    _render_track_record(stock)
    text = _emitted_text(st_stub)
    assert "STAND DOWN" in text
    assert "High" in text


def test_track_record_panel_quiet_when_healthy(st_stub) -> None:
    from state.signal_ledger import freeze_ideas
    from ui.tab_idea_engine import _render_track_record

    freeze_ideas([_Idea(f"T{i}", "Bullish", conviction_label="High") for i in range(5)],
                 issue_date="2026-06-01")
    _render_track_record(_stock({f"T{i}": 110.0 for i in range(5)}))
    assert "STAND DOWN" not in _emitted_text(st_stub)
