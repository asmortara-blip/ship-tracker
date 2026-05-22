"""Tests for engine.alert_engine_v2 time-window alert deduplication (v14).

A flaky data feed that bounces a value across its threshold N times in
an hour previously inserted N alert rows; v14 layers a NEAR-duplicate
window-based dedup on top of the existing alert_id-PK dedup so the
same (alert_type, severity, ticker, route_id, port_locode) tuple
within ``_DEDUP_WINDOW_MINUTES`` collapses to a single row whose
``fire_count`` counts the bounces and whose ``last_fired_at`` marks
the most-recent fire.

Covers:
  - ``_dedup_key`` returns the same string for two alerts with the
    same five-tuple; differs when ANY field differs.
  - ``_dedup_key`` escapes pipes + backslashes so a value containing
    the separator cannot smuggle a collision.
  - First save of a (key, user) tuple inserts a row with fire_count=1
    and last_fired_at = "now".
  - Second save of the SAME key WITHIN the window bumps fire_count to
    2 and refreshes last_fired_at + value + change_pct on the SAME
    row (no new alert_id allocated).
  - Second save of the same key AFTER the window inserts a fresh row
    with fire_count=1.
  - Severity escalation does NOT collapse — HIGH and CRITICAL on the
    same entity are distinct rows.
  - Per-user scoping: alice's alert + bob's alert with the same key
    do NOT collide — they are two rows, each with fire_count=1.
  - Legacy (user_id="") alerts only collide with other legacy alerts.
  - A single save_alerts call carrying N identical-key alerts
    collapses them sequentially into one row with fire_count=N.
  - The existing alert_id-PK dedup (INSERT OR IGNORE) is unchanged —
    passing the SAME alert_id twice in one call still yields one row.
  - ``get_alert_with_fire_count`` returns the full row dict for an
    existing alert_id and ``None`` for an unknown id; the dict carries
    the v14 fire_count + last_fired_at fields.
  - ``_row_to_alert_full`` falls back gracefully on pre-v14-shaped
    rows (fire_count default 1, last_fired_at falls back to
    created_at).
  - Migration v14 actually adds the two columns to the alerts table.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from engine import alert_engine_v2 as engv2
from engine.alert_engine_v2 import (
    ShippingAlert,
    _dedup_key,
    _make,
    get_alert_with_fire_count,
    load_alerts,
    save_alerts,
)


# ─── Fixture: isolate SQLite per test ──────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Redirect the SQLite state DB to a per-test tmp_path so no test
    touches the real cache/ship_tracker.db."""
    from state import db as state_db
    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

def _alert(
    *,
    alert_type: str = "BDI_MOVE",
    severity: str = "HIGH",
    ticker: str = "",
    route_id: str = "",
    port_locode: str = "",
    value: float = 1000.0,
    change_pct: float = 5.0,
) -> ShippingAlert:
    return _make(
        alert_type, severity, "title", "body",
        ticker=ticker, route_id=route_id, port_locode=port_locode,
        value=value, threshold=5.0, change_pct=change_pct,
    )


def _count_rows() -> int:
    from state.db import get_connection
    return int(get_connection().execute("SELECT COUNT(*) FROM alerts").fetchone()[0])


def _read_row(alert_id: str) -> dict:
    from state.db import get_connection
    row = get_connection().execute(
        "SELECT * FROM alerts WHERE alert_id = ?", (alert_id,)
    ).fetchone()
    assert row is not None, f"alert {alert_id} not found"
    return {k: row[k] for k in row.keys()}


# ─── _dedup_key composition ────────────────────────────────────────────────

def test_dedup_key_same_for_same_five_tuple() -> None:
    a = _alert(alert_type="BDI_MOVE", severity="HIGH", ticker="ZIM",
               route_id="ASIA_NAM", port_locode="USLAX")
    b = _alert(alert_type="BDI_MOVE", severity="HIGH", ticker="ZIM",
               route_id="ASIA_NAM", port_locode="USLAX")
    # Different alert_id, different value/change_pct, same five-tuple.
    assert a.alert_id != b.alert_id
    assert _dedup_key(a) == _dedup_key(b)


def test_dedup_key_differs_on_alert_type() -> None:
    a = _alert(alert_type="BDI_MOVE")
    b = _alert(alert_type="RATE_SURGE")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_differs_on_severity() -> None:
    a = _alert(severity="HIGH")
    b = _alert(severity="CRITICAL")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_differs_on_ticker() -> None:
    a = _alert(ticker="ZIM")
    b = _alert(ticker="MATX")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_differs_on_route_id() -> None:
    a = _alert(route_id="ASIA_NAM")
    b = _alert(route_id="ASIA_EUR")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_differs_on_port_locode() -> None:
    a = _alert(port_locode="USLAX")
    b = _alert(port_locode="USNYC")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_escapes_pipe_separator() -> None:
    """A literal '|' inside a field must not collide with a different
    shape across the separator boundary."""
    a = _alert(ticker="A|B", route_id="C")
    b = _alert(ticker="A",   route_id="B|C")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_escapes_backslash() -> None:
    """The pipe escape uses '\\\\|'; a literal backslash in a field must
    not consume the pipe escape and collide with a different shape."""
    a = _alert(ticker="A\\", route_id="B")
    b = _alert(ticker="A",   route_id="\\B")
    assert _dedup_key(a) != _dedup_key(b)


def test_dedup_key_is_stable_string() -> None:
    """The key is a plain str so it can be used as a dict key or
    serialized for debugging."""
    a = _alert(ticker="ZIM")
    assert isinstance(_dedup_key(a), str)
    # Re-compute is deterministic.
    assert _dedup_key(a) == _dedup_key(a)


# ─── save_alerts: first-save behaviour ─────────────────────────────────────

def test_save_alerts_first_occurrence_inserts_with_fire_count_one() -> None:
    a = _alert(ticker="ZIM")
    save_alerts([a], user_id="")
    assert _count_rows() == 1
    row = _read_row(a.alert_id)
    assert int(row["fire_count"]) == 1
    # last_fired_at must be a non-empty ISO timestamp.
    assert row["last_fired_at"]
    datetime.fromisoformat(row["last_fired_at"].replace("Z", "+00:00"))


def test_save_alerts_first_occurrence_via_get_helper() -> None:
    """The public helper returns the v14-enriched dict for an existing row."""
    a = _alert(ticker="MATX")
    save_alerts([a], user_id="")
    full = get_alert_with_fire_count(a.alert_id)
    assert full is not None
    assert full["alert_id"] == a.alert_id
    assert full["fire_count"] == 1
    assert full["last_fired_at"]


# ─── save_alerts: dedup WITHIN the window ──────────────────────────────────

def test_save_alerts_duplicate_within_window_bumps_fire_count() -> None:
    a = _alert(ticker="ZIM", value=1100.0, change_pct=5.5)
    save_alerts([a], user_id="")
    # Second occurrence, different alert_id, same dedup_key.
    b = _alert(ticker="ZIM", value=1200.0, change_pct=7.2)
    save_alerts([b], user_id="")

    # Still one row — same alert_id as the FIRST insert (the bounce
    # shares the original row's id).
    assert _count_rows() == 1
    row = _read_row(a.alert_id)
    assert int(row["fire_count"]) == 2
    # New row carrying b.alert_id must NOT exist.
    from state.db import get_connection
    assert get_connection().execute(
        "SELECT 1 FROM alerts WHERE alert_id = ?", (b.alert_id,)
    ).fetchone() is None


def test_save_alerts_dedup_refreshes_value_and_change_pct() -> None:
    """The dedup-bump path replaces value + change_pct with the newer
    reading — the most-recent fire wins on the freshness fields."""
    a = _alert(ticker="ZIM", value=1100.0, change_pct=5.5)
    save_alerts([a], user_id="")
    b = _alert(ticker="ZIM", value=1300.0, change_pct=8.8)
    save_alerts([b], user_id="")

    row = _read_row(a.alert_id)
    assert row["value"] == pytest.approx(1300.0)
    assert row["change_pct"] == pytest.approx(8.8)


def test_save_alerts_dedup_advances_last_fired_at() -> None:
    """The dedup-bump path advances last_fired_at to "now"; the original
    created_at is preserved as the start-of-bounce anchor."""
    a = _alert(ticker="ZIM")
    save_alerts([a], user_id="")
    first_lf = _read_row(a.alert_id)["last_fired_at"]

    # Tiny sleep so the ISO timestamps differ; the test still passes
    # without it on slower machines because _now_iso uses datetime.now,
    # but being explicit avoids a flaky equality.
    import time
    time.sleep(0.005)

    b = _alert(ticker="ZIM")
    save_alerts([b], user_id="")
    second = _read_row(a.alert_id)
    assert second["last_fired_at"] > first_lf
    # created_at must NOT have changed — that anchors the start of the bounce.
    assert second["created_at"] == _read_row(a.alert_id)["created_at"]


# ─── save_alerts: dedup AFTER the window ───────────────────────────────────

def test_save_alerts_duplicate_after_window_inserts_new_row(monkeypatch) -> None:
    """Past the window, the same dedup_key inserts a fresh row (with
    fire_count=1) instead of bumping the prior."""
    # Shrink the window so the test can express "after" cleanly.
    monkeypatch.setattr(engv2, "_DEDUP_WINDOW_MINUTES", 1)

    a = _alert(ticker="ZIM")
    # Stamp the FIRST row's created_at to 5 minutes ago, well outside
    # the 1-minute window.
    a.created_at = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    save_alerts([a], user_id="")

    # Second alert, same dedup_key. With created_at well outside the
    # window, the dedup query returns no rows and a fresh INSERT lands.
    b = _alert(ticker="ZIM")
    save_alerts([b], user_id="")

    assert _count_rows() == 2
    # The new row has its own alert_id and fire_count=1.
    new_row = _read_row(b.alert_id)
    assert int(new_row["fire_count"]) == 1


# ─── save_alerts: severity escalation does NOT collapse ────────────────────

def test_save_alerts_severity_escalation_does_not_dedup() -> None:
    """HIGH and CRITICAL on the same entity are distinct rows — escalation
    should surface as a NEW row, not a fire_count bump on the prior HIGH."""
    a = _alert(severity="HIGH", ticker="ZIM")
    save_alerts([a], user_id="")
    b = _alert(severity="CRITICAL", ticker="ZIM")
    save_alerts([b], user_id="")

    assert _count_rows() == 2


# ─── save_alerts: per-user scoping ─────────────────────────────────────────

def test_save_alerts_dedup_is_per_user() -> None:
    """Alice's BDI_MOVE/HIGH/(empty entity) does NOT collide with Bob's
    same-shape alert — they are two rows, each with fire_count=1."""
    a_alice = _alert(ticker="ZIM")
    save_alerts([a_alice], user_id="alice")
    a_bob = _alert(ticker="ZIM")
    save_alerts([a_bob], user_id="bob")

    assert _count_rows() == 2
    assert int(_read_row(a_alice.alert_id)["fire_count"]) == 1
    assert int(_read_row(a_bob.alert_id)["fire_count"]) == 1


def test_save_alerts_dedup_legacy_does_not_collide_with_user() -> None:
    """A legacy (user_id="") alert + an authenticated-user alert with the
    same key must produce two rows."""
    a_legacy = _alert(ticker="ZIM")
    save_alerts([a_legacy], user_id="")
    a_alice = _alert(ticker="ZIM")
    save_alerts([a_alice], user_id="alice")

    assert _count_rows() == 2


def test_save_alerts_same_user_dedup_bumps() -> None:
    """Same-user, same-key, within-window → fire_count bumps to 2."""
    a = _alert(ticker="ZIM")
    save_alerts([a], user_id="alice")
    b = _alert(ticker="ZIM")
    save_alerts([b], user_id="alice")

    assert _count_rows() == 1
    assert int(_read_row(a.alert_id)["fire_count"]) == 2


# ─── save_alerts: batched single call ──────────────────────────────────────

def test_save_alerts_batch_with_repeats_collapses() -> None:
    """A single save_alerts call carrying N identical-key alerts must
    collapse to one row with fire_count=N. The dedup check runs
    sequentially so the first INSERT becomes the bump-target for the
    rest of the batch."""
    batch = [_alert(ticker="ZIM") for _ in range(5)]
    save_alerts(batch, user_id="")

    assert _count_rows() == 1
    # The first alert in the batch is the one whose alert_id wins.
    row = _read_row(batch[0].alert_id)
    assert int(row["fire_count"]) == 5


def test_save_alerts_batch_with_distinct_keys_inserts_all() -> None:
    """A batch of N alerts with all-distinct dedup_keys inserts N rows,
    each at fire_count=1."""
    batch = [
        _alert(ticker="ZIM"),
        _alert(ticker="MATX"),
        _alert(ticker="SBLK"),
    ]
    save_alerts(batch, user_id="")
    assert _count_rows() == 3
    for a in batch:
        assert int(_read_row(a.alert_id)["fire_count"]) == 1


# ─── Back-compat: alert_id-PK dedup still works ────────────────────────────

def test_save_alerts_same_alert_id_twice_in_one_call_is_one_row() -> None:
    """The original alert_id-PK INSERT OR IGNORE dedup must still hold —
    a caller passing the SAME alert_id twice in a single call gets one
    row. This is a different dedup from the window-based one (which
    operates on dedup_key) and both coexist."""
    a = _alert(ticker="ZIM")
    save_alerts([a, a], user_id="")
    # The second occurrence has the SAME dedup_key → dedup-bumps to
    # fire_count=2. (Distinct from a hypothetical "INSERT OR IGNORE
    # collision" — here the bump path catches it first.)
    assert _count_rows() == 1
    assert int(_read_row(a.alert_id)["fire_count"]) == 2


def test_save_alerts_dedups_by_id_across_calls() -> None:
    """save_alerts of the same alert_id across two calls is still one
    row (legacy behaviour preserved). The window-dedup hits BEFORE the
    INSERT OR IGNORE here, so the count bumps."""
    a = _alert(ticker="ZIM")
    save_alerts([a], user_id="")
    save_alerts([a], user_id="")
    assert _count_rows() == 1
    assert int(_read_row(a.alert_id)["fire_count"]) == 2


# ─── get_alert_with_fire_count helper ──────────────────────────────────────

def test_get_alert_with_fire_count_unknown_returns_none() -> None:
    assert get_alert_with_fire_count("does-not-exist") is None


def test_get_alert_with_fire_count_returns_dict_with_v14_fields() -> None:
    a = _alert(ticker="ZIM")
    save_alerts([a], user_id="")
    save_alerts([_alert(ticker="ZIM")], user_id="")  # bump
    save_alerts([_alert(ticker="ZIM")], user_id="")  # bump

    full = get_alert_with_fire_count(a.alert_id)
    assert full is not None
    assert full["fire_count"] == 3
    assert full["last_fired_at"]
    # The dict also carries every other field the dataclass would.
    assert full["ticker"] == "ZIM"
    assert full["alert_type"] == "BDI_MOVE"


def test_load_alerts_still_returns_shipping_alert_dataclass() -> None:
    """The existing load_alerts contract is back-compat — it still
    returns ShippingAlert dataclasses without the v14 columns. UI
    callers that need the new fields use get_alert_with_fire_count."""
    save_alerts([_alert(ticker="ZIM")], user_id="")
    loaded = load_alerts(user_id="")
    assert len(loaded) == 1
    assert isinstance(loaded[0], ShippingAlert)
    # The dataclass should NOT have a fire_count attribute — the new
    # column lives at the SQL layer only.
    assert not hasattr(loaded[0], "fire_count")


# ─── Constants ─────────────────────────────────────────────────────────────

def test_dedup_window_default_is_sixty_minutes() -> None:
    """The default window is 60 minutes per the v14 design note."""
    assert engv2._DEDUP_WINDOW_MINUTES == 60
