"""The daily signal-ledger freeze job (R004, increment 2)."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


class _Idea:
    ticker = "ZIM"
    direction = "Bullish"
    price = 100.0
    conviction_score = 0.7
    conviction_label = "High"
    conviction_weight_set = "default"


def test_freeze_job_freezes_ideas(monkeypatch) -> None:
    import worker.scheduler as sch
    from state.signal_ledger import load_ledger

    # The job lazily imports these inside its body, so module-level patches take.
    monkeypatch.setattr(
        "processing.shipping_stress_index.compute_shipping_stress",
        lambda *a, **k: object())
    monkeypatch.setattr(
        "processing.exposure_matrix.build_exposure_matrix",
        lambda *a, **k: object())
    monkeypatch.setattr(
        "processing.disruption_cascade.score_equity_ideas",
        lambda *a, **k: [_Idea()])

    res = sch.run_signal_ledger_freeze_job({"stock_data": {}})
    assert res["frozen"] == 1 and res["ideas"] == 1
    assert any(r["ticker"] == "ZIM" for r in load_ledger())

    # Idempotent: a second run the same day inserts nothing.
    res2 = sch.run_signal_ledger_freeze_job({"stock_data": {}})
    assert res2["frozen"] == 0


def test_freeze_job_never_raises_on_bad_bundle() -> None:
    import worker.scheduler as sch
    # A None bundle must not raise; it returns the result shape regardless of
    # how many ideas the cascade happens to produce.
    res = sch.run_signal_ledger_freeze_job(None)
    assert isinstance(res, dict) and "frozen" in res and res["frozen"] >= 0
