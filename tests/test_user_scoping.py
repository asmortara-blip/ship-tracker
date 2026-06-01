"""Tests for per-user query scoping (state.user_scope + the five domain
modules that adopted it).

Schema v7 added a ``user_id TEXT NOT NULL DEFAULT ''`` column to alerts,
alert_rules, report_history, delivery_channels, and llm_calls. The five
modules grew opt-in scoping parameters: when ``user_id`` resolves to a
non-empty string the load/save honours dual-set semantics (the user's
own rows PLUS legacy ``user_id=''`` rows); when it resolves to ``""``
(default for callers outside Streamlit / pre-multi-user) every row is
visible. This file is the regression net.

Covers:
  - current_user_id: returns ""  when Streamlit not in sys.modules,
    session_state missing, current_user unset/None, current_user has
    no user_id attribute, attribute is non-string. Returns the real
    user_id when Streamlit + current_user are both present. NEVER
    raises.
  - scope_filter_sql: empty string → empty fragment + empty params,
    so the caller's query is unchanged. Non-empty → dual-set fragment
    plus a one-element params tuple.
  - Per-module round-trips for each of the 5 modules:
        save with user_id=alice → load(alice) sees it, load(bob)
        does not, load("") sees it (legacy all-rows).
  - Legacy data visibility: save with user_id="" → load(alice) still
    sees it (dual-set semantics).
  - ACK security: alice's alert cannot be ACK'd by bob; alice CAN ACK
    her own. acknowledge_all respects scope too.
  - delete_channel security: alice's channel cannot be deleted via
    bob's user_id.
  - load_report_html / delete_report / make_public / revoke_public:
    cross-user access returns the same "not found" outcome as an
    unknown id (no info leak).
  - load_public_report does NOT scope — slug-based access works
    regardless of the active user_id (public link is the auth).
  - prune_old_calls is admin-level and prunes ALL users' old rows.
"""
from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pytest


# ─── DB isolation fixture (matches the pattern in the other test files) ───

@pytest.fixture(autouse=True)
def isolated_state_db(monkeypatch, tmp_path):
    """Per-test SQLite DB, plus a per-test cache/reports dir for the
    report_history tests so save_report's file write goes to tmp_path."""
    from state import db as state_db
    from utils import report_history as rh

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    monkeypatch.setattr(rh, "REPORT_DIR", tmp_path / "reports")
    monkeypatch.setattr(rh, "_INDEX_FILE", tmp_path / "reports" / "report_index.json")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


# ─── Helpers ───────────────────────────────────────────────────────────────

class _FakeUser:
    """Stand-in for auth.users.User — only needs the user_id attribute."""
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


def _install_streamlit_mock(monkeypatch, current_user=None) -> None:
    """Inject a tiny streamlit mock with the requested current_user.

    The mock only needs ``session_state`` exposing ``.get("current_user")``
    — current_user_id() does not touch anything else on the module.
    """
    mock = types.ModuleType("streamlit")
    mock.session_state = {"current_user": current_user} if current_user is not None else {}
    monkeypatch.setitem(sys.modules, "streamlit", mock)


def _make_alert(alert_id: str, severity: str = "HIGH"):
    from engine.alert_engine_v2 import ShippingAlert
    return ShippingAlert(
        alert_id=alert_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        alert_type="STOCK_MOVE",
        severity=severity,
        title=f"test {alert_id}",
        body="test body content with enough text to render.",
        ticker="ZIM",
        route_id="",
        port_locode="",
        value=100.0,
        threshold=50.0,
        change_pct=10.0,
        acknowledged=False,
    )


def _make_channel(channel_id: str = "c1", name: str = "test"):
    from engine.alert_delivery import DeliveryChannel
    return DeliveryChannel(
        channel_id=channel_id,
        name=name,
        kind="slack",
        target="https://hooks.slack.com/services/T/B/C",
        severity_threshold="LOW",
        enabled=True,
    )


# ═══════════════════════════════════════════════════════════════════════════
# state.user_scope.current_user_id
# ═══════════════════════════════════════════════════════════════════════════

def test_current_user_id_returns_empty_when_streamlit_not_importable(monkeypatch):
    """current_user_id() never raises even if streamlit is absent.

    We force the import to fail by mapping the name to ``None`` in
    sys.modules — Python's import machinery raises ImportError for
    that, which the helper swallows."""
    monkeypatch.setitem(sys.modules, "streamlit", None)
    from state.user_scope import current_user_id
    assert current_user_id() == ""


def test_current_user_id_returns_empty_when_session_state_empty(monkeypatch):
    """No ``current_user`` in session_state → empty string."""
    _install_streamlit_mock(monkeypatch)  # no current_user
    from state.user_scope import current_user_id
    assert current_user_id() == ""


def test_current_user_id_returns_empty_when_current_user_is_none(monkeypatch):
    """current_user explicitly None → empty string."""
    _install_streamlit_mock(monkeypatch, current_user=None)
    from state.user_scope import current_user_id
    assert current_user_id() == ""


def test_current_user_id_returns_user_id_when_set(monkeypatch):
    """current_user with a user_id attribute → that user_id string."""
    _install_streamlit_mock(monkeypatch, current_user=_FakeUser("alice-id"))
    from state.user_scope import current_user_id
    assert current_user_id() == "alice-id"


def test_current_user_id_returns_empty_when_user_id_not_string(monkeypatch):
    """A non-string user_id (e.g. int) is rejected — empty return."""
    weird = _FakeUser(12345)  # type: ignore[arg-type]
    _install_streamlit_mock(monkeypatch, current_user=weird)
    from state.user_scope import current_user_id
    assert current_user_id() == ""


def test_current_user_id_never_raises_on_attribute_access(monkeypatch):
    """An exploding ``user_id`` property cannot crash the helper."""
    class _Bomb:
        @property
        def user_id(self):
            raise RuntimeError("boom")
    _install_streamlit_mock(monkeypatch, current_user=_Bomb())
    from state.user_scope import current_user_id
    assert current_user_id() == ""


# ═══════════════════════════════════════════════════════════════════════════
# state.user_scope.scope_filter_sql
# ═══════════════════════════════════════════════════════════════════════════

def test_scope_filter_sql_empty_user_id_returns_empty_fragment():
    from state.user_scope import scope_filter_sql
    sql, params = scope_filter_sql("")
    assert sql == ""
    assert params == ()


def test_scope_filter_sql_non_empty_user_id_returns_dual_set_fragment():
    from state.user_scope import scope_filter_sql
    sql, params = scope_filter_sql("alice-id")
    # Dual-set: own rows + legacy '' rows.
    assert sql == "AND (user_id = ? OR user_id = '')"
    assert params == ("alice-id",)


# ═══════════════════════════════════════════════════════════════════════════
# engine.alert_engine_v2 — save_alerts / load_alerts round-trip
# ═══════════════════════════════════════════════════════════════════════════

def test_alerts_round_trip_with_user_scope():
    """alice saves an alert → alice sees it; bob does not; legacy ('') does."""
    from engine.alert_engine_v2 import save_alerts, load_alerts

    save_alerts([_make_alert("a-alice")], user_id="alice")
    assert {a.alert_id for a in load_alerts(user_id="alice")} == {"a-alice"}
    assert {a.alert_id for a in load_alerts(user_id="bob")} == set()
    # Legacy "all rows" mode sees every alert regardless of owner.
    assert {a.alert_id for a in load_alerts(user_id="")} == {"a-alice"}


def test_alerts_legacy_rows_visible_to_authenticated_users():
    """Save with user_id="" → an authenticated user still sees it.

    This is the dual-set semantics in action: legacy data from before
    the multi-user migration must not vanish on first login.
    """
    from engine.alert_engine_v2 import save_alerts, load_alerts

    save_alerts([_make_alert("a-legacy")], user_id="")
    # Both alice and the legacy reader should see the legacy alert.
    assert any(a.alert_id == "a-legacy" for a in load_alerts(user_id="alice"))
    assert any(a.alert_id == "a-legacy" for a in load_alerts(user_id=""))


def test_alerts_save_uses_session_user_when_no_param(monkeypatch):
    """save_alerts() with no kwarg falls back to current_user_id()."""
    _install_streamlit_mock(monkeypatch, current_user=_FakeUser("session-user"))
    from engine.alert_engine_v2 import save_alerts, load_alerts

    save_alerts([_make_alert("a-session")])
    assert {a.alert_id for a in load_alerts(user_id="session-user")} == {"a-session"}


def test_acknowledge_alert_scoped_to_owner():
    """alice can ACK her own alert; bob CANNOT ACK alice's alert."""
    from engine.alert_engine_v2 import save_alerts, load_alerts, acknowledge_alert

    save_alerts([_make_alert("a-alice")], user_id="alice")

    # bob attempts to ACK alice's alert → silently no-ops.
    acknowledge_alert("a-alice", user_id="bob")
    # alice's read should show it still unacknowledged.
    a = next(x for x in load_alerts(user_id="alice") if x.alert_id == "a-alice")
    assert a.acknowledged is False

    # alice ACKs her own → flag flips.
    acknowledge_alert("a-alice", user_id="alice")
    a = next(x for x in load_alerts(user_id="alice") if x.alert_id == "a-alice")
    assert a.acknowledged is True


def test_acknowledge_all_scoped_to_owner():
    """acknowledge_all only ACKs rows in the caller's scope."""
    from engine.alert_engine_v2 import save_alerts, load_alerts, acknowledge_all

    save_alerts([_make_alert("a-alice")], user_id="alice")
    save_alerts([_make_alert("a-bob")], user_id="bob")

    # alice ACKs everything she can see (her own + legacy). bob's row
    # must remain unacknowledged.
    acknowledge_all(user_id="alice")

    alice_alert = next(x for x in load_alerts(user_id="alice") if x.alert_id == "a-alice")
    assert alice_alert.acknowledged is True

    bob_alert = next(x for x in load_alerts(user_id="bob") if x.alert_id == "a-bob")
    assert bob_alert.acknowledged is False


def test_get_unread_count_scoped():
    """Unread count respects scoping — alice sees only her unread."""
    from engine.alert_engine_v2 import save_alerts, get_unread_count

    save_alerts([_make_alert("a-alice")], user_id="alice")
    save_alerts([_make_alert("a-bob")], user_id="bob")

    # alice's scope: own + legacy. legacy is empty → only her one row.
    assert get_unread_count(user_id="alice") == 1
    assert get_unread_count(user_id="bob") == 1
    # Legacy mode: all rows.
    assert get_unread_count(user_id="") == 2


# ═══════════════════════════════════════════════════════════════════════════
# engine.alert_engine_v2 — rule persistence
# ═══════════════════════════════════════════════════════════════════════════

def test_rules_round_trip_with_user_scope():
    """alice saves a rule → alice sees it; bob does not; legacy does."""
    from engine.alert_engine_v2 import save_rules, load_rules

    rule = {"rule_id": "r1", "name": "alice-rule", "threshold": 5.0}
    save_rules([rule], user_id="alice")

    alice_rules = load_rules(user_id="alice")
    assert any(r.get("name") == "alice-rule" for r in alice_rules)

    bob_rules = load_rules(user_id="bob")
    assert not any(r.get("name") == "alice-rule" for r in bob_rules)

    # Legacy all-rows view picks it up too.
    assert any(r.get("name") == "alice-rule" for r in load_rules(user_id=""))


def test_rules_per_user_replace_does_not_touch_other_users():
    """save_rules(user_id=alice) wipes only alice's + legacy rules,
    leaving bob's untouched. Otherwise switching tabs in a multi-user
    app would silently delete coworkers' rules."""
    from engine.alert_engine_v2 import save_rules, load_rules

    save_rules([{"rule_id": "r-alice", "name": "alice-rule"}], user_id="alice")
    save_rules([{"rule_id": "r-bob", "name": "bob-rule"}], user_id="bob")

    # alice re-saves a fresh list — bob's rule must survive.
    save_rules([{"rule_id": "r-alice-v2", "name": "alice-v2"}], user_id="alice")
    assert {r["rule_id"] for r in load_rules(user_id="bob")} == {"r-bob"}


# ═══════════════════════════════════════════════════════════════════════════
# utils.report_history — list / load / delete / make_public / revoke_public
# ═══════════════════════════════════════════════════════════════════════════

class _FakeReportObj:
    date = "2026-05-22"
    market_sentiment = "BULLISH"
    sentiment_score = 0.7
    risk_level = "MODERATE"
    signal_count = 4
    data_quality = "FULL"


def _insert_report_directly(report_id: str, user_id: str, file_path) -> None:
    """Insert a report_history row with explicit user_id. Bypasses
    save_report so the test can assert on cross-user reads without
    monkey-patching streamlit twice."""
    from state.db import get_connection
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(f"<html>{report_id}</html>", encoding="utf-8")
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO report_history
              (report_id, generated_at, report_date, sentiment_label,
               sentiment_score, risk_level, signal_count, data_quality,
               file_path, file_size_kb, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                datetime.now(timezone.utc).isoformat(),
                "2026-05-22",
                "BULLISH",
                0.5,
                "MODERATE",
                3,
                "FULL",
                str(file_path),
                1.0,
                user_id,
            ),
        )


def test_list_reports_scoped(tmp_path):
    """list_reports(alice) sees alice's + legacy; not bob's."""
    from utils.report_history import list_reports

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")
    _insert_report_directly("r-bob",   "bob",   tmp_path / "bob.html")
    _insert_report_directly("r-legacy", "",     tmp_path / "legacy.html")

    alice_ids = {r.report_id for r in list_reports(user_id="alice")}
    assert alice_ids == {"r-alice", "r-legacy"}
    assert "r-bob" not in alice_ids

    # Legacy mode = every row.
    all_ids = {r.report_id for r in list_reports(user_id="")}
    assert all_ids == {"r-alice", "r-bob", "r-legacy"}


def test_load_report_html_cross_user_returns_none(tmp_path):
    """bob cannot read alice's report HTML — same outcome as unknown id."""
    from utils.report_history import load_report_html

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")

    assert load_report_html("r-alice", user_id="bob") is None
    # alice can read her own.
    assert load_report_html("r-alice", user_id="alice") == "<html>r-alice</html>"


def test_delete_report_cross_user_returns_false(tmp_path):
    """bob cannot delete alice's report — returns False, row stays."""
    from utils.report_history import delete_report, list_reports

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")

    assert delete_report("r-alice", user_id="bob") is False
    # Row must still exist in alice's view.
    assert {r.report_id for r in list_reports(user_id="alice")} == {"r-alice"}


def test_make_public_cross_user_returns_none(tmp_path):
    """bob cannot publish alice's report. None == unknown report_id."""
    from utils.report_history import make_public

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")

    assert make_public("r-alice", user_id="bob") is None
    # alice can publish her own — slug returned.
    slug = make_public("r-alice", user_id="alice")
    assert isinstance(slug, str) and slug


def test_revoke_public_cross_user_returns_false(tmp_path):
    """bob cannot revoke alice's public link."""
    from utils.report_history import make_public, revoke_public

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")
    slug = make_public("r-alice", user_id="alice")
    assert slug

    assert revoke_public("r-alice", user_id="bob") is False
    # alice can revoke her own.
    assert revoke_public("r-alice", user_id="alice") is True


def test_load_public_report_ignores_user_scope(tmp_path):
    """Public links are public by design — slug + expiry are the auth
    credential, the user_id column is NOT consulted. A bob-scoped
    streamlit session (or even no session at all) can still load a
    report alice made public.
    """
    from utils.report_history import make_public, load_public_report

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")
    slug = make_public("r-alice", user_id="alice")
    assert slug

    # The function takes no user_id parameter — verify by call signature.
    html = load_public_report(slug)
    assert html == "<html>r-alice</html>"


def test_get_report_stats_scoped(tmp_path):
    """get_report_stats counts only the rows in the caller's scope."""
    from utils.report_history import get_report_stats

    _insert_report_directly("r-alice", "alice", tmp_path / "alice.html")
    _insert_report_directly("r-bob",   "bob",   tmp_path / "bob.html")

    alice_stats = get_report_stats(user_id="alice")
    assert alice_stats["total_reports"] == 1
    bob_stats = get_report_stats(user_id="bob")
    assert bob_stats["total_reports"] == 1
    # Legacy mode: both.
    assert get_report_stats(user_id="")["total_reports"] == 2


def test_save_report_stamps_session_user(monkeypatch, tmp_path):
    """save_report (no scope param) pulls the user from streamlit."""
    _install_streamlit_mock(monkeypatch, current_user=_FakeUser("session-author"))
    from utils.report_history import save_report, list_reports

    meta = save_report("<html>hi</html>", _FakeReportObj())
    assert meta is not None
    # Only session-author should see the new report.
    assert {r.report_id for r in list_reports(user_id="session-author")} == {meta.report_id}
    assert meta.report_id not in {r.report_id for r in list_reports(user_id="other")}


# ═══════════════════════════════════════════════════════════════════════════
# engine.alert_delivery — channels
# ═══════════════════════════════════════════════════════════════════════════

def test_channels_round_trip_with_user_scope():
    """alice's channel is visible to alice + legacy mode, not bob."""
    from engine.alert_delivery import save_channel, load_channels

    save_channel(_make_channel("c-alice", "alice-slack"), user_id="alice")

    alice_ids = {c.channel_id for c in load_channels(user_id="alice")}
    assert alice_ids == {"c-alice"}
    assert "c-alice" not in {c.channel_id for c in load_channels(user_id="bob")}
    # Legacy all-rows view.
    assert "c-alice" in {c.channel_id for c in load_channels(user_id="")}


def test_delete_channel_cross_user_no_op():
    """bob cannot delete alice's channel by knowing the channel_id."""
    from engine.alert_delivery import save_channel, load_channels, delete_channel

    save_channel(_make_channel("c-alice", "alice-slack"), user_id="alice")
    # bob attempts cross-user delete → no-op.
    delete_channel("c-alice", user_id="bob")
    assert {c.channel_id for c in load_channels(user_id="alice")} == {"c-alice"}

    # alice can delete her own.
    delete_channel("c-alice", user_id="alice")
    assert {c.channel_id for c in load_channels(user_id="alice")} == set()


def test_channels_legacy_visible_to_authenticated_user():
    """A legacy channel (user_id='') is visible to a new authenticated user."""
    from engine.alert_delivery import save_channel, load_channels

    save_channel(_make_channel("c-legacy", "legacy-slack"), user_id="")
    assert "c-legacy" in {c.channel_id for c in load_channels(user_id="alice")}


# ═══════════════════════════════════════════════════════════════════════════
# engine.llm_telemetry — record / summary / recent / prune
# ═══════════════════════════════════════════════════════════════════════════

def test_record_call_stamps_user_id_explicit():
    """record_call(user_id=alice) stamps the row with alice's id."""
    from engine.llm_telemetry import record_call
    from state.db import get_connection

    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50, user_id="alice")
    conn = get_connection()
    rows = conn.execute("SELECT user_id FROM llm_calls").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "alice"


def test_record_call_falls_back_to_session_user(monkeypatch):
    """No explicit user_id → resolve from current_user."""
    _install_streamlit_mock(monkeypatch, current_user=_FakeUser("session-user"))
    from engine.llm_telemetry import record_call
    from state.db import get_connection

    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50)
    conn = get_connection()
    row = conn.execute("SELECT user_id FROM llm_calls").fetchone()
    assert row["user_id"] == "session-user"


def test_get_usage_summary_scoped():
    """Summary aggregates only rows in the caller's scope."""
    from engine.llm_telemetry import record_call, get_usage_summary

    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50, user_id="alice")
    record_call("commentary", "claude-haiku-4-5-20251001", 200, 100, user_id="bob")

    alice = get_usage_summary(user_id="alice")
    assert alice["total_calls"] == 1
    assert alice["total_tokens_in"] == 100

    bob = get_usage_summary(user_id="bob")
    assert bob["total_calls"] == 1
    assert bob["total_tokens_in"] == 200

    # Legacy: all rows.
    assert get_usage_summary(user_id="")["total_calls"] == 2


def test_get_recent_calls_scoped():
    """get_recent_calls honours the scope filter."""
    from engine.llm_telemetry import record_call, get_recent_calls

    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50, user_id="alice")
    record_call("commentary", "claude-haiku-4-5-20251001", 200, 100, user_id="bob")

    alice_calls = get_recent_calls(user_id="alice")
    assert len(alice_calls) == 1
    assert alice_calls[0]["tokens_in"] == 100

    bob_calls = get_recent_calls(user_id="bob")
    assert len(bob_calls) == 1
    assert bob_calls[0]["tokens_in"] == 200

    assert len(get_recent_calls(user_id="")) == 2


def test_legacy_telemetry_visible_to_authenticated_user():
    """A legacy llm_calls row (user_id='') is visible to alice — dual-set."""
    from engine.llm_telemetry import record_call, get_usage_summary

    record_call("commentary", "claude-haiku-4-5-20251001", 100, 50, user_id="")
    # alice's summary must include the legacy row.
    assert get_usage_summary(user_id="alice")["total_calls"] == 1


def test_prune_old_calls_is_admin_level_and_ignores_user_id():
    """prune_old_calls deletes ALL old rows regardless of user_id —
    operational cleanup must not be partitioned by user, otherwise a
    forgotten user's data sticks around forever."""
    from engine.llm_telemetry import record_call, prune_old_calls
    from state.db import get_connection

    # Insert one row per user, then backdate them all by 100 days so
    # the default 90-day retention prunes them.
    record_call("commentary", "claude-haiku-4-5-20251001", 1, 1, user_id="alice")
    record_call("commentary", "claude-haiku-4-5-20251001", 1, 1, user_id="bob")
    record_call("commentary", "claude-haiku-4-5-20251001", 1, 1, user_id="")

    backdated = (datetime.now(timezone.utc) - timedelta(days=100)).isoformat()
    conn = get_connection()
    with conn:
        conn.execute("UPDATE llm_calls SET created_at = ?", (backdated,))

    deleted = prune_old_calls(retention_days=90)
    assert deleted == 3  # ALL rows, every user.
    remaining = conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()
    assert remaining["n"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# Cross-cutting backward-compatibility sanity check
# ═══════════════════════════════════════════════════════════════════════════

def test_legacy_callers_with_no_user_id_param_see_everything():
    """Every load function called with no kwarg AND no Streamlit session
    returns the full table. This is the contract that lets pre-multi-user
    code keep working without touching it."""
    from engine.alert_engine_v2 import save_alerts, load_alerts
    from engine.alert_delivery import save_channel, load_channels
    from engine.llm_telemetry import record_call, get_recent_calls

    save_alerts([_make_alert("a-alice")], user_id="alice")
    save_alerts([_make_alert("a-bob")],   user_id="bob")
    save_channel(_make_channel("c-alice"), user_id="alice")
    save_channel(_make_channel("c-bob", "bob-slack"), user_id="bob")
    record_call("commentary", "claude-haiku-4-5-20251001", 1, 1, user_id="alice")
    record_call("commentary", "claude-haiku-4-5-20251001", 1, 1, user_id="bob")

    # No kwargs, no streamlit installed → current_user_id() returns "" →
    # scope_filter_sql("") → "" → every row is visible.
    assert len(load_alerts()) == 2
    assert len(load_channels()) == 2
    assert len(get_recent_calls()) == 2
