"""Cross-tab provenance/honesty guard (recommendation R091).

The 2026-06-01 honesty pass added provenance infrastructure (``source_footer``,
``live_data_badge``, illustrative banners) but nothing *enforced* that every
market-data tab actually surfaces it — so facade tabs could (and did) ship
"provenance-blind", presenting modeled/sample data with no honest framing.

This is a ratchet: every wired ``ui/tab_*.py`` must reference at least one
provenance/honesty marker, except an explicit, documented allowlist of internal
operational views that present the system's own state (not market/modeled data).
The allowlist may only shrink — a new provenance-blind tab fails the build, and
removing the marker from a currently-compliant tab fails the build.
"""

from __future__ import annotations

import pathlib

# Markers that count as "this tab surfaces provenance / honesty framing".
# Call-based where possible so an incidental mention does not satisfy the guard.
_MARKERS = (
    "source_footer(",        # ui.styles provenance footer (real | modeled | synthetic)
    "live_data_badge(",      # live/real data badge
    "live_badge(",
    "alert_banner(",         # used for ILLUSTRATIVE / SAMPLE banners
    "provenance",
    "ILLUSTRATIVE",
    "illustrative",
    "modeled",
    "Modeled",
    "not investment advice",
    "Not investment advice",
)

# Internal operational views that present the platform's OWN state (rule-change
# history, worker/system health) rather than market or modeled data — they make
# no data-provenance claim, so a provenance pill does not apply. Explicit by
# design: this set may shrink but must never silently grow.
_ALLOWLIST = {
    "tab_rule_history.py",   # user's own alert-rule change log (internal audit)
    "tab_worker_health.py",  # worker/scheduler health telemetry (internal ops)
}

_TAB_DIR = pathlib.Path(__file__).resolve().parents[1] / "ui"


def _all_tabs() -> list[pathlib.Path]:
    return sorted(_TAB_DIR.glob("tab_*.py"))


def _provenance_blind() -> set[str]:
    blind = set()
    for tab in _all_tabs():
        src = tab.read_text(encoding="utf-8")
        if not any(m in src for m in _MARKERS):
            blind.add(tab.name)
    return blind


def test_every_market_data_tab_surfaces_provenance() -> None:
    """No tab outside the documented allowlist may ship provenance-blind."""
    offenders = _provenance_blind() - _ALLOWLIST
    assert not offenders, (
        "These wired tabs present data with no provenance / honesty marker "
        f"(add a source_footer / live_data_badge / illustrative banner): {sorted(offenders)}"
    )


def test_provenance_allowlist_does_not_grow_silently() -> None:
    """Ratchet: the allowlist must stay a subset of the genuinely-blind tabs.

    If a tab is added to ``_ALLOWLIST`` but actually DOES carry provenance (or
    no longer exists), remove it from the allowlist — the gate only loosens on
    purpose, never by accident.
    """
    blind = _provenance_blind()
    stale = _ALLOWLIST - blind
    assert not stale, (
        "These tabs are allowlisted but already carry provenance (or were "
        f"removed) — drop them from _ALLOWLIST: {sorted(stale)}"
    )


def test_allowlisted_tabs_exist() -> None:
    names = {t.name for t in _all_tabs()}
    missing = _ALLOWLIST - names
    assert not missing, f"_ALLOWLIST names a non-existent tab: {sorted(missing)}"
