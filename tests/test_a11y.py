"""Accessibility tests for custom HTML emitted by ``ui/styles.py``.

Streamlit doesn't expose deep a11y APIs, but every custom HTML component we
render owns its own role/aria attributes. These tests pin those attributes in
place so a future refactor can't silently regress screen-reader support, and
verify the documented foreground/background color pairs all clear the WCAG
AA contrast threshold.

What's NOT tested here:
  - Visual layout/styling correctness (covered by manual review).
  - Live-region announcement behavior in actual browsers (out of scope for
    a unit-test runner; verified via aria-live="polite" attribute presence).
"""
from __future__ import annotations

import re

import pytest

from ui.styles import (
    A11Y_LARGE_TEXT_PAIRS,
    A11Y_TEXT_PAIRS,
    WCAG_AA_LARGE_THRESHOLD,
    WCAG_AA_THRESHOLD,
    _contrast_ratio,
    alert_banner,
    insight_card_html,
    metric_card_row,
    section_header,
    status_badge,
    tldr_lede,
    wsj_market_table,
)


# ─── helpers ────────────────────────────────────────────────────────────────

class _MarkdownSpy:
    """Stand-in for ``st.markdown`` that records every HTML string emitted.

    The real ``st.markdown`` only side-effects into the Streamlit DOM, so
    the test grabs the rendered HTML out of the spy instead.
    """

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, html, *args, **kwargs):
        self.calls.append(str(html))

    @property
    def html(self) -> str:
        return "\n".join(self.calls)


@pytest.fixture
def md_spy(monkeypatch):
    """Capture every HTML payload passed to ``st.markdown`` inside ui.styles."""
    spy = _MarkdownSpy()
    import ui.styles as styles
    monkeypatch.setattr(styles.st, "markdown", spy)
    # ``st.columns(n)`` must yield `n` context managers so ``with col:`` works.
    class _Ctx:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False
    def _columns(spec, *args, **kwargs):
        n = spec if isinstance(spec, int) else (len(spec) if hasattr(spec, "__len__") else 1)
        return [_Ctx() for _ in range(max(1, n))]
    monkeypatch.setattr(styles.st, "columns", _columns)
    return spy


# ─── ARIA on custom HTML components ─────────────────────────────────────────

def test_metric_card_row_has_role_group_and_aria_label(md_spy):
    metric_card_row([
        {"label": "Active Vessels", "value": "1,284", "delta": "+12"},
        {"label": "Avg Rate", "value": "$2,850"},
    ])
    html = md_spy.html
    assert 'role="group"' in html, "metric card missing role='group'"
    # Both cards should expose an aria-label that includes label + value.
    assert "aria-label='Active Vessels: 1,284'" in html
    assert "aria-label='Avg Rate: $2,850'" in html


def test_metric_card_row_inner_divs_are_presentation(md_spy):
    metric_card_row([{"label": "X", "value": "1"}])
    assert 'role="presentation"' in md_spy.html, (
        "decorative inner divs should be role='presentation' so screen "
        "readers don't re-announce label/value separately"
    )


def test_tldr_lede_has_note_role_and_renders_text(md_spy):
    tldr_lede("Suez disruption lifts SSI to 0.62 as rates firm.", source="claude")
    html = md_spy.html
    assert 'role="note"' in html, "TL;DR lede should be a note region"
    assert "Suez disruption lifts SSI to 0.62 as rates firm." in html
    assert "LLM" in html, "claude source should show the LLM provenance chip"
    # Eyebrow row is decorative — must not be re-announced as content.
    assert 'role="presentation"' in html


def test_tldr_lede_template_source_shows_template_chip(md_spy):
    tldr_lede("Headline. First bullet.", source="template")
    assert "Template" in md_spy.html


def test_tldr_lede_empty_text_renders_nothing(md_spy):
    tldr_lede("   ", source="claude")
    assert md_spy.html == "", "empty TL;DR text should emit no markup"


def test_alert_banner_has_role_alert_and_aria_live(md_spy):
    alert_banner("Port congestion easing", level="warning")
    html = md_spy.html
    assert 'role="alert"' in html
    assert 'aria-live="polite"' in html
    assert "Port congestion easing" in html


def test_status_badge_has_role_status_and_aria_label():
    out = status_badge("LIVE", status="success")
    assert 'role="status"' in out
    assert "aria-label='LIVE'" in out


def test_wsj_market_table_has_table_row_cell_roles(md_spy):
    wsj_market_table(
        headers=["Route", "Rate", "Δ"],
        rows=[["TPEB", "$2,850", "+1.2%"], ["AE", "$1,680", "-0.4%"]],
    )
    html = md_spy.html
    assert 'role="table"' in html
    assert 'role="row"' in html
    assert 'role="cell"' in html
    # Header cells should be columnheaders, not just bare <th>.
    assert 'role="columnheader"' in html


def test_wsj_market_table_caption_when_title_set(md_spy):
    wsj_market_table(
        headers=["A"],
        rows=[["1"]],
        title="Spot Rates by Lane",
    )
    html = md_spy.html
    assert "<caption>Spot Rates by Lane</caption>" in html


def test_wsj_market_table_no_caption_when_title_empty(md_spy):
    wsj_market_table(headers=["A"], rows=[["1"]])
    assert "<caption>" not in md_spy.html


def test_section_header_has_heading_role_and_level(md_spy):
    section_header("Disruption Outlook")
    html = md_spy.html
    assert 'role="heading"' in html
    assert 'aria-level="2"' in html
    assert "Disruption Outlook" in html


def test_insight_card_html_has_article_role_and_aria_label():
    out = insight_card_html(
        title="Suez transit recovering",
        score=0.74,
        action="Prioritize",
        rationale="VTS shows queue clearing",
        category="MACRO",
    )
    assert 'role="article"' in out
    assert "aria-label='Suez transit recovering'" in out


def test_insight_card_progress_bar_has_progressbar_role():
    out = insight_card_html(title="X", score=0.6, action="Monitor")
    assert 'role="progressbar"' in out
    assert 'aria-valuenow="60"' in out
    assert 'aria-valuemin="0"' in out
    assert 'aria-valuemax="100"' in out


# ─── WCAG contrast helper ───────────────────────────────────────────────────

def test_contrast_ratio_white_on_black_is_21():
    assert _contrast_ratio("#ffffff", "#000000") == pytest.approx(21.0, abs=1e-3)


def test_contrast_ratio_white_on_white_is_1():
    assert _contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=1e-6)


def test_contrast_ratio_is_symmetric():
    """Ratio is independent of which color is foreground vs background."""
    a = _contrast_ratio("#e8e6e1", "#0c0e14")
    b = _contrast_ratio("#0c0e14", "#e8e6e1")
    assert a == pytest.approx(b, abs=1e-6)


def test_contrast_ratio_black_on_white_is_21():
    assert _contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=1e-3)


def test_contrast_ratio_known_mid_gray():
    """#777 on white sits near the WCAG AA threshold — sanity-check."""
    r = _contrast_ratio("#777777", "#ffffff")
    # Reference value computed via the WCAG 2.1 formula.
    assert r == pytest.approx(4.48, abs=0.05)


def test_contrast_ratio_accepts_no_hash_prefix():
    assert _contrast_ratio("ffffff", "000000") == pytest.approx(21.0, abs=1e-3)


@pytest.mark.parametrize("fg,bg,label", A11Y_TEXT_PAIRS, ids=lambda v: v if isinstance(v, str) else None)
def test_documented_body_text_pairs_pass_wcag_aa(fg, bg, label):
    """Every documented body-text foreground/background pair must clear AA."""
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= WCAG_AA_THRESHOLD, (
        f"WCAG AA failure: {label!r} — ratio={ratio:.2f} "
        f"(fg={fg}, bg={bg}), threshold={WCAG_AA_THRESHOLD}"
    )


@pytest.mark.parametrize("fg,bg,label", A11Y_LARGE_TEXT_PAIRS, ids=lambda v: v if isinstance(v, str) else None)
def test_documented_large_text_pairs_pass_wcag_aa_large(fg, bg, label):
    """Large-text/UI pairs use the relaxed 3.0:1 threshold."""
    ratio = _contrast_ratio(fg, bg)
    assert ratio >= WCAG_AA_LARGE_THRESHOLD, (
        f"WCAG AA-large failure: {label!r} — ratio={ratio:.2f} "
        f"(fg={fg}, bg={bg}), threshold={WCAG_AA_LARGE_THRESHOLD}"
    )


# ─── regression: blank-line-then-indent code-block trap (commit a0c980d) ───

def test_metric_card_row_no_code_block_trap(md_spy):
    """Re-confirm no blank line precedes 4-space-indented HTML inside any
    st.markdown payload — that pattern triggers Streamlit's markdown parser
    to render the rest as a <code> block (see commit a0c980d)."""
    metric_card_row([{"label": "X", "value": "1"}])
    for call in md_spy.calls:
        # Find any double-newline followed by 4+ spaces of HTML — the trap.
        assert not re.search(r"\n\s*\n {4,}<", call), (
            f"code-block trap detected: blank line then indented HTML "
            f"in:\n{call!r}"
        )
