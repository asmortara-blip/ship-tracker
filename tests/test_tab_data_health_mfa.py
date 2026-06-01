"""Tests for ``ui.tab_data_health._render_mfa_panel`` — the self-service
MFA setup panel sibling to ``_render_security_panel``.

Covers:
  * Smoke — the helper renders without raising on the **enabled** branch
    (an MFA-enrolled user).
  * Smoke — the helper renders without raising on the **disabled**
    branch (no MFA on the account).
  * Smoke — the helper renders without raising when the user_id is
    empty (logged-out / unauthenticated case).
  * Smoke — the helper renders without raising mid-enrollment (when
    ``mfa_pending_secret`` is already in session_state, so the QR /
    code-input branch executes).
  * Workflow — calling ``enable_mfa`` directly persists the secret to
    the SQLite ``users`` table, so a follow-up render lands in the
    enabled branch.
  * Workflow — calling ``disable_mfa`` directly clears the secret +
    flag, so a follow-up render lands in the disabled branch.

The smoke tests rely on the shared ``mock_streamlit`` fixture from
``conftest.py`` so a real Streamlit runtime is not required. The
workflow tests use the same isolated-DB fixture pattern as
``tests/test_mfa.py`` so the SQLite ``users`` table is fresh per test.
"""
from __future__ import annotations

import importlib
import sys

import pytest


# ── Fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """Every test gets a fresh DB at a per-test tmp_path — same pattern
    as tests/test_mfa.py. The auth helpers all go through
    ``state.db.get_connection`` so this monkeypatch on the path is
    sufficient to isolate them."""
    from state import db as state_db

    monkeypatch.setattr(state_db, "DB_PATH", tmp_path / "ship_tracker.db")
    state_db.reset_for_tests()
    yield
    state_db.reset_for_tests()


def _reload_tab():
    """Import-or-reload ``ui.tab_data_health`` so it picks up the active
    ``mock_streamlit`` fixture, even if a prior test imported it under
    the real streamlit. Mirrors the helper in ``tests/test_tab_smoke.py``."""
    module_path = "ui.tab_data_health"
    if module_path in sys.modules:
        return importlib.reload(sys.modules[module_path])
    return importlib.import_module(module_path)


# ── Smoke: both branches must render without raising ──────────────────────

def test_mfa_panel_renders_disabled_branch(mock_streamlit) -> None:
    """A fresh user with no MFA enrolled lands in the **disabled** branch.
    The helper must complete without raising — the "Enable MFA" button
    is rendered but never clicked (mock_streamlit.button always returns
    False), so no DB write happens."""
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None

    tab = _reload_tab()
    # Helper must NEVER raise — every internal failure path is caught
    # and converted to st.error / st.info.
    tab._render_mfa_panel(user.user_id)


def test_mfa_panel_renders_enabled_branch(mock_streamlit) -> None:
    """A user with MFA on lands in the **enabled** branch. The helper
    must complete without raising — the "Disable MFA" button is
    rendered but not clicked under the mock streamlit, so the secret
    stays put."""
    from auth.mfa import enable_mfa, generate_secret, is_mfa_enabled
    from auth.users import signup

    user = signup("bob", "correct-password-123")
    assert user is not None
    secret = generate_secret()
    # v21 signature change — enable_mfa returns (ok, recovery_codes).
    ok, _codes = enable_mfa(user.user_id, secret)
    assert ok is True
    assert is_mfa_enabled(user.user_id) is True

    tab = _reload_tab()
    tab._render_mfa_panel(user.user_id)


def test_mfa_panel_renders_with_empty_user_id(mock_streamlit) -> None:
    """Unauthenticated call (empty user_id) must surface a friendly
    "log in to manage" message and return without raising — never
    explode on the no-session path."""
    tab = _reload_tab()
    tab._render_mfa_panel("")


def test_mfa_panel_renders_mid_enrollment(mock_streamlit) -> None:
    """Mid-enrollment branch: a pending secret is already in
    session_state, so the helper must render the QR/code-input view
    without raising. Exercises the provisioning_uri + st.code paths
    and the optional ``qrcode`` import fallback in one shot."""
    from auth.mfa import generate_secret
    from auth.users import signup

    user = signup("carol", "correct-password-123")
    assert user is not None

    # Drop a fake-mid-enrollment secret into session state BEFORE
    # rendering, so the disabled branch falls through to the QR view.
    mock_streamlit.session_state["mfa_pending_secret"] = generate_secret()

    tab = _reload_tab()
    tab._render_mfa_panel(user.user_id)


# ── Workflow: panel-adjacent helpers persist + clear correctly ────────────

def test_enable_mfa_workflow_persists_after_panel_use(mock_streamlit) -> None:
    """After the panel's enable workflow completes (modelled here by a
    direct ``enable_mfa`` call — the panel ultimately delegates to it),
    the secret and flag must be on disk so the NEXT render of the
    panel lands in the enabled branch.

    This protects the contract that the panel does not need to keep
    the secret in session_state past enrollment — once ``enable_mfa``
    returns True, the DB is the source of truth."""
    from auth.mfa import enable_mfa, generate_secret, is_mfa_enabled
    from auth.users import signup
    from state.db import get_connection

    user = signup("alice", "correct-password-123")
    assert user is not None

    secret = generate_secret()
    # v21 signature change — enable_mfa returns (ok, recovery_codes).
    ok, _codes = enable_mfa(user.user_id, secret)
    assert ok is True

    # Direct DB read — proves the panel-adjacent enable_mfa call really
    # persists to the users table, not just to in-memory state.
    conn = get_connection()
    row = conn.execute(
        "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = ?",
        (user.user_id,),
    ).fetchone()
    assert row["mfa_secret"] == secret
    assert int(row["mfa_enabled"]) == 1

    # Re-rendering the panel for this user now hits the enabled branch —
    # smoke that the panel respects the freshly-persisted state.
    assert is_mfa_enabled(user.user_id) is True
    tab = _reload_tab()
    tab._render_mfa_panel(user.user_id)


def test_disable_mfa_workflow_clears_after_panel_use(mock_streamlit) -> None:
    """After the panel's disable workflow completes (modelled by a
    direct ``disable_mfa`` call), the secret column must be empty
    and the flag must be 0 — so the NEXT render lands in the
    disabled branch. Protects the contract that disabling truly
    wipes the secret rather than just flipping the flag."""
    from auth.mfa import (
        disable_mfa,
        enable_mfa,
        generate_secret,
        is_mfa_enabled,
    )
    from auth.users import signup
    from state.db import get_connection

    user = signup("bob", "correct-password-123")
    assert user is not None
    # v21 signature change — enable_mfa returns (ok, recovery_codes).
    ok, _codes = enable_mfa(user.user_id, generate_secret())
    assert ok is True
    assert is_mfa_enabled(user.user_id) is True

    assert disable_mfa(user.user_id) is True

    conn = get_connection()
    row = conn.execute(
        "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = ?",
        (user.user_id,),
    ).fetchone()
    assert row["mfa_secret"] == ""
    assert int(row["mfa_enabled"]) == 0
    assert is_mfa_enabled(user.user_id) is False

    # Disabled-branch render must complete now.
    tab = _reload_tab()
    tab._render_mfa_panel(user.user_id)


# ── v21: recovery-code reveal + regenerate UI ────────────────────────────

def test_mfa_panel_surfaces_pending_recovery_codes(mock_streamlit) -> None:
    """When ``mfa_pending_codes`` is stashed in session_state, the
    panel must render them ONCE in a copyable st.code block and pop
    the key so a reload does not re-show them."""
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    ok, codes = enable_mfa(user.user_id, generate_secret())
    assert ok and codes

    # Simulate the post-enable rerun: codes are in session_state.
    mock_streamlit.session_state["mfa_pending_codes"] = codes

    tab = _reload_tab()
    tab._render_mfa_panel(user.user_id)

    # After the render, the key MUST be popped so a page refresh does
    # not silently re-show the codes.
    assert "mfa_pending_codes" not in mock_streamlit.session_state


def test_mfa_panel_renders_unused_count_caption(mock_streamlit) -> None:
    """The enabled-branch caption surfaces the unused-code count.
    Smoke that the helper renders without raising — the actual caption
    text is checked indirectly via count_unused_recovery_codes."""
    from auth.mfa import (
        count_unused_recovery_codes,
        enable_mfa,
        generate_secret,
    )
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())
    assert count_unused_recovery_codes(user.user_id) == 10

    tab = _reload_tab()
    # Must NOT raise.
    tab._render_mfa_panel(user.user_id)


def test_mfa_panel_regenerate_button_flow(mock_streamlit) -> None:
    """Smoke-test the regenerate-confirm flow: when the regen-confirm
    flag is set in session_state, the panel renders the confirmation
    branch without raising. The actual click is not simulated under
    mock_streamlit (button always returns False), so no regen
    happens — this is a render-path smoke test."""
    from auth.mfa import enable_mfa, generate_secret
    from auth.users import signup

    user = signup("alice", "correct-password-123")
    assert user is not None
    enable_mfa(user.user_id, generate_secret())

    # Force the confirm branch open.
    mock_streamlit.session_state["mfa_regen_confirm"] = True

    tab = _reload_tab()
    # Helper must NOT raise even with the confirm branch active.
    tab._render_mfa_panel(user.user_id)
