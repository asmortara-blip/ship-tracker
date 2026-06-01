"""First-run setup wizard tab.

Shown ONLY when the install is fresh — i.e. ``auth.users.count_users()``
returns ``0`` AND no Streamlit secrets file is present. ``app.py`` routes
to ``render()`` here BEFORE rendering any other section when that
condition holds, so this tab does NOT need to (and intentionally does
not) appear in the regular sidebar navigation.

Three steps:

  1. Create the first admin user — calls ``auth.users.signup`` and on
     success advances the wizard by triggering a rerun. The user becomes
     visible to the rest of the app immediately because ``count_users()``
     will now return >0.
  2. Connect data sources — shows the env-var names the app expects and a
     copy-pasteable ``secrets.toml`` snippet. Optionally pings the
     configured sources via ``engine.source_health.ping_all_sources`` if
     that module is available; otherwise degrades to an info banner.
  3. You're ready — checklist of next actions and a "Go to Overview"
     button that sets the navigation key and marks the wizard complete.

What this tab does NOT do
-------------------------
- Does NOT write to ``.streamlit/secrets.toml`` (the snippet is shown for
  the user to copy themselves — a wizard that edits files at runtime is
  a vector we explicitly avoid).
- Does NOT modify ``auth.users`` or ``auth.gate`` — it only consumes
  ``signup`` and ``generate_password_hash``.
- Does NOT insert itself into the main sidebar nav.
"""
from __future__ import annotations

from typing import Any

import streamlit as st
from loguru import logger

from data.quality import DataSource
from ui.styles import (
    C_ACCENT,
    C_HIGH,
    C_LOW,
    C_MOD,
    C_TEXT,
    C_TEXT2,
    C_TEXT3,
    page_header,
    section_divider,
    source_footer,
)


# Provenance for the footer. The setup wizard ships no market data — it
# configures the instance. The footer is an honest, platform-wide note that
# most surfaces are MODELED, with only a few genuinely live feeds.
_SETUP_SOURCE = DataSource.modeled(
    "Ship Tracker platform",
    notes=(
        "Most dashboards are MODELED derivations; genuinely live feeds are "
        "equity prices / FX (Yahoo, ECB), World Bank, and RSS news."
    ),
)


# Session-state key the wizard uses to remember that the user clicked
# "Go to Overview" — app.py reads this to skip the wizard on subsequent
# reruns within the same session even though a user now exists.
_SETUP_DONE_KEY = "setup_wizard_complete"

# Required + optional env vars surfaced to the user in step 2. Required
# means "the app won't be very useful without it"; optional means "a
# specific tab will degrade gracefully if missing".
_REQUIRED_KEYS: list[tuple[str, str]] = [
    ("ANTHROPIC_API_KEY", "LLM-powered briefings and AI assistant"),
    ("FRED_API_KEY",      "Macro indicators (FRED)"),
]
_OPTIONAL_KEYS: list[tuple[str, str]] = [
    ("ALPHA_VANTAGE_API_KEY", "Company fundamentals (Alpha Vantage)"),
    ("NEWSAPI_KEY",           "News sentiment (NewsAPI)"),
    ("AISSTREAM_KEY",         "Live vessel positions (AIS Stream)"),
]


# ── Step 1: create admin user ─────────────────────────────────────────────

def _render_step_one() -> None:
    section_divider("Step 1 · Create your admin user")
    st.markdown(
        f'<div style="font-size:0.84rem;color:{C_TEXT2};margin-bottom:12px;'
        'font-family:var(--sans);line-height:1.5;">'
        "Your first account becomes the admin. Username is 3-32 characters "
        "(letters, digits, underscore, dash). Password must be at least 8 "
        "characters."
        "</div>",
        unsafe_allow_html=True,
    )

    with st.form("setup_admin_form", clear_on_submit=False):
        username = st.text_input(
            "Username",
            key="setup_admin_username",
            placeholder="admin",
        )
        password = st.text_input(
            "Password",
            type="password",
            key="setup_admin_password",
            placeholder="At least 8 characters",
        )
        confirm = st.text_input(
            "Confirm password",
            type="password",
            key="setup_admin_confirm",
            placeholder="Re-enter the password",
        )
        submitted = st.form_submit_button(
            "Create admin",
            use_container_width=True,
        )

    if submitted:
        try:
            if not username or not password:
                st.error("Username and password are both required.")
                return
            if password != confirm:
                st.error("Passwords do not match.")
                return
            from auth.users import signup
            user = signup(username, password)
            if user is None:
                st.error(
                    "Could not create account. Check that the username is "
                    "3-32 characters (letters, digits, _ or -) and the "
                    "password is at least 8 characters."
                )
                return
            st.success(
                f"Admin user '{user.username}' created. Continuing to step 2..."
            )
            st.rerun()
        except Exception as exc:  # noqa: BLE001 — UI fallback
            logger.warning(f"tab_setup: signup raised: {exc}")
            st.error("Could not create account due to an internal error.")


# ── Step 2: configure data sources ────────────────────────────────────────

def _render_secrets_snippet(generated_hash: str = "", generated_salt: str = "") -> None:
    """Render a copy-pasteable secrets.toml snippet."""
    hash_line = (
        f'APP_PASSWORD_HASH = "{generated_hash}"' if generated_hash
        else 'APP_PASSWORD_HASH = "<paste-from-generator-below>"'
    )
    salt_line = (
        f'APP_PASSWORD_SALT = "{generated_salt}"' if generated_salt
        else 'APP_PASSWORD_SALT = "<paste-from-generator-below>"'
    )
    snippet_lines = [
        "# .streamlit/secrets.toml — copy this template and fill in your values",
        "",
        "# LLM provider (required for briefings + AI assistant)",
        'ANTHROPIC_API_KEY = "sk-ant-..."',
        "",
        "# Macro data (required for FRED-backed indicators)",
        'FRED_API_KEY = "your-fred-key"',
        "",
        "# Optional — degrades gracefully if absent",
        'ALPHA_VANTAGE_API_KEY = "your-av-key"',
        'NEWSAPI_KEY = "your-newsapi-key"',
        'AISSTREAM_KEY = "your-aisstream-key"',
        "",
        "# Optional — legacy single-password fallback gate",
        hash_line,
        salt_line,
    ]
    st.code("\n".join(snippet_lines), language="toml")


def _try_ping_sources() -> tuple[bool, Any]:
    """Attempt to call engine.source_health.ping_all_sources.

    Returns ``(True, results)`` on success, ``(False, None)`` if the
    module is unavailable or the call raised. We never bubble exceptions
    out of the wizard.
    """
    try:
        from engine import source_health  # type: ignore[attr-defined]
    except Exception:
        return False, None
    ping = getattr(source_health, "ping_all_sources", None)
    if not callable(ping):
        return False, None
    try:
        return True, ping()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"tab_setup: ping_all_sources raised: {exc}")
        return False, None


def _render_step_two() -> None:
    section_divider("Step 2 · Connect your data sources")
    st.markdown(
        f'<div style="font-size:0.84rem;color:{C_TEXT2};margin-bottom:12px;'
        'font-family:var(--sans);line-height:1.5;">'
        "Add API keys to <code>.streamlit/secrets.toml</code> (or set them "
        "as environment variables). The checkboxes below are purely visual "
        "reminders — none of this is persisted."
        "</div>",
        unsafe_allow_html=True,
    )

    st.markdown(
        f'<div style="font-size:0.76rem;font-weight:700;color:{C_TEXT};'
        'text-transform:uppercase;letter-spacing:0.08em;margin:8px 0 4px;">'
        "Required</div>",
        unsafe_allow_html=True,
    )
    for env_name, label in _REQUIRED_KEYS:
        st.checkbox(
            f"{env_name} — {label}",
            value=False,
            key=f"setup_chk_{env_name}",
        )

    st.markdown(
        f'<div style="font-size:0.76rem;font-weight:700;color:{C_TEXT};'
        'text-transform:uppercase;letter-spacing:0.08em;margin:16px 0 4px;">'
        "Optional</div>",
        unsafe_allow_html=True,
    )
    for env_name, label in _OPTIONAL_KEYS:
        st.checkbox(
            f"{env_name} — {label}",
            value=False,
            key=f"setup_chk_{env_name}",
        )

    # Generate-hash helper (legacy single-password fallback).
    st.markdown(
        f'<div style="font-size:0.76rem;font-weight:700;color:{C_TEXT};'
        'text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 4px;">'
        "Optional · legacy single-password fallback</div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f'<div style="font-size:0.78rem;color:{C_TEXT2};margin-bottom:8px;'
        'font-family:var(--sans);">'
        "If you also want the legacy single-password gate available (for "
        "scripted login or fallback), generate an <code>APP_PASSWORD_HASH</code> + "
        "<code>APP_PASSWORD_SALT</code> pair below. The plaintext is hashed "
        "in-process; nothing is sent anywhere."
        "</div>",
        unsafe_allow_html=True,
    )

    with st.form("setup_hash_form", clear_on_submit=False):
        legacy_password = st.text_input(
            "Plaintext password to hash",
            type="password",
            key="setup_legacy_password",
            placeholder="(only used to generate the hash; not stored)",
        )
        generate_clicked = st.form_submit_button(
            "Generate hash + salt",
            use_container_width=False,
        )

    generated_hash = st.session_state.get("setup_generated_hash", "")
    generated_salt = st.session_state.get("setup_generated_salt", "")

    if generate_clicked:
        try:
            if not legacy_password or len(legacy_password) < 8:
                st.error("Password must be at least 8 characters.")
            else:
                from auth.gate import generate_password_hash
                generated_hash, generated_salt = generate_password_hash(legacy_password)
                st.session_state["setup_generated_hash"] = generated_hash
                st.session_state["setup_generated_salt"] = generated_salt
                st.success("Hash generated. The snippet below now embeds it.")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tab_setup: hash generation failed: {exc}")
            st.error("Could not generate hash due to an internal error.")

    st.markdown(
        f'<div style="font-size:0.76rem;font-weight:700;color:{C_TEXT};'
        'text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 6px;">'
        "Copy-paste into <code>.streamlit/secrets.toml</code></div>",
        unsafe_allow_html=True,
    )
    _render_secrets_snippet(generated_hash, generated_salt)

    # Source-health ping.
    st.markdown(
        f'<div style="font-size:0.76rem;font-weight:700;color:{C_TEXT};'
        'text-transform:uppercase;letter-spacing:0.08em;margin:18px 0 6px;">'
        "Test connections</div>",
        unsafe_allow_html=True,
    )
    if st.button("Run connection test", key="setup_ping_btn"):
        try:
            ok, results = _try_ping_sources()
            if not ok:
                st.info(
                    "Source health pinging not configured yet — skip to step 3."
                )
            else:
                try:
                    import pandas as pd
                    if isinstance(results, list):
                        df = pd.DataFrame(results)
                    elif isinstance(results, dict):
                        rows = []
                        for src, payload in results.items():
                            row = {"source": src}
                            if isinstance(payload, dict):
                                row.update(payload)
                            else:
                                row["status"] = str(payload)
                            rows.append(row)
                        df = pd.DataFrame(rows)
                    else:
                        df = pd.DataFrame([{"result": str(results)}])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"tab_setup: ping table render failed: {exc}")
                    st.info(
                        "Source health pinging returned an unexpected shape "
                        "— skip to step 3."
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tab_setup: connection test failed: {exc}")
            st.error("Connection test could not run; see logs for details.")


# ── Step 3: you're ready ──────────────────────────────────────────────────

def _render_step_three() -> None:
    section_divider("Step 3 · You're ready")
    st.markdown(
        f'<div style="font-size:0.84rem;color:{C_TEXT2};margin-bottom:14px;'
        'font-family:var(--sans);line-height:1.55;">'
        "Once you've created an admin user and added at least the two "
        "required keys above, the rest of the app will light up. A few "
        "good first stops:"
        "</div>",
        unsafe_allow_html=True,
    )

    checklist = [
        ("Visit the Overview tab to see the live dashboard.", C_HIGH),
        ("Configure alert rules in Intelligence > Alerts.", C_MOD),
        ("Set up delivery channels (Slack / email) for those alerts.", C_MOD),
        ("Bookmark the Operator Dashboard under Risk & Compliance.", C_ACCENT),
        ("Review the data-source health panel in the sidebar.", C_TEXT3),
    ]
    items_html = "".join(
        f'<li style="margin-bottom:6px;color:{C_TEXT};font-family:var(--sans);'
        f'font-size:0.84rem;line-height:1.5;">'
        f'<span style="display:inline-block;width:6px;height:6px;border-radius:50%;'
        f'background:{color};margin-right:8px;vertical-align:middle;"></span>'
        f"{text}</li>"
        for text, color in checklist
    )
    st.markdown(
        f'<ul style="list-style:none;padding-left:0;margin:0 0 18px 0;">{items_html}</ul>',
        unsafe_allow_html=True,
    )

    if st.button(
        "Go to Overview",
        key="setup_done_btn",
        use_container_width=True,
        type="primary",
    ):
        try:
            st.session_state[_SETUP_DONE_KEY] = True
            # The main nav key used by app.py is "nav_section". Overview
            # lives under the "dashboard" section.
            st.session_state["nav_section"] = "dashboard"
            st.session_state["active_section"] = "dashboard"
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"tab_setup: completing wizard raised: {exc}")
            st.error("Could not advance to the dashboard; please reload.")


# ── Public entry point ────────────────────────────────────────────────────

def render(*args: Any, **kwargs: Any) -> None:
    """Render the first-run setup wizard.

    Accepts arbitrary args/kwargs so the smoke harness can call this with
    the same data bundle it passes to every other tab without crashing.
    None of the kwargs are used.
    """
    # Lazy import keeps perf_telemetry off the tab-load critical path
    # and mirrors the pattern in every other ``ui/tab_*.py``.
    from engine.perf_telemetry import track_render

    with track_render("setup"):
        try:
            page_header(
                title="Welcome — First-Run Setup",
                subtitle="Configure your Ship Tracker instance in three quick steps.",
                badge_text="SETUP",
                badge_color=C_ACCENT,
            )
            _render_step_one()
            _render_step_two()
            _render_step_three()

            # ── Source footer ─────────────────────────────────────────────
            try:
                st.markdown(
                    source_footer([_SETUP_SOURCE]),
                    unsafe_allow_html=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"tab_setup: source footer failed: {exc}")
        except Exception as exc:  # noqa: BLE001 — UI fallback
            logger.exception(f"tab_setup: render failed: {exc}")
            try:
                st.error(
                    "Setup wizard encountered an error — please reload the page."
                )
            except Exception:
                pass


__all__ = ["render"]
