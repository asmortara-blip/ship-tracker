"""Defining-property tests for delivery.port_supply_shock_digest.

Covers
======
- render_html returns a well-formed HTML document
- render_html includes a <table> for each non-empty bucket
- render_html OMITS sections for empty buckets (no "(none)" filler)
- render_plain_text exposes the same per-bucket counts as the HTML
- build_subject_line: empty diff -> "no material changes"
- build_subject_line: severity shifts + entered both surface
- should_send returns False on an empty diff (or None)
- should_send returns True if ANY bucket has at least one entry
- Edge: ticker_shuffles-only diff still sends
- Edge: very long port name + ticker list don't break the table
- Fixture builder: _make_diff(severity_n=, entered_n=, exited_n=,
  moves_n=, shuffles_n=) keeps tests DRY

Constraints
-----------
- No new deps. ``html.parser`` from stdlib is used for the HTML
  well-formedness check — no bs4, lxml, etc.
- No SMTP. No HTTP. Pure rendering only — these tests touch zero
  filesystem and zero network resources.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pytest

from delivery.port_supply_shock_digest import (
    build_subject_line,
    render_html,
    render_plain_text,
    should_send,
)
from tools.port_supply_diff import DiffReport, PortDelta


# ─── Fixture builder ───────────────────────────────────────────────────────


def _make_delta(
    *,
    locode: str = "USLAX",
    name: str = "Los Angeles",
    region: str = "North America",
    severity_before: str = "WATCH",
    severity_after: str = "STRESSED",
    deficit_before: float = 1.0,
    deficit_after: float = -2.0,
    tickers_added: list[str] | None = None,
    tickers_removed: list[str] | None = None,
) -> PortDelta:
    """Build one PortDelta with sensible defaults. Tests override only
    what they need to assert on."""
    tickers_added = tickers_added or []
    tickers_removed = tickers_removed or []
    return PortDelta(
        locode=locode,
        name=name,
        region=region,
        severity_before=severity_before,
        severity_after=severity_after,
        severity_shifted=severity_before != severity_after,
        deficit_before=deficit_before,
        deficit_after=deficit_after,
        deficit_delta=deficit_after - deficit_before,
        entered_deficit=False,
        exited_deficit=False,
        tickers_before=[],
        tickers_after=[],
        tickers_added=list(tickers_added),
        tickers_removed=list(tickers_removed),
    )


def _make_diff(
    *,
    severity_n: int = 0,
    entered_n: int = 0,
    exited_n: int = 0,
    moves_n: int = 0,
    shuffles_n: int = 0,
) -> DiffReport:
    """Build a DiffReport pre-populated with ``severity_n`` severity
    shifts, ``entered_n`` entered-deficit transitions, etc.

    Each fixture port gets a deterministic synthetic locode + name so
    test failures can pinpoint which bucket the issue is in.
    """
    report = DiffReport(n_ports_before=10, n_ports_after=10)

    for i in range(severity_n):
        report.severity_shifts.append(_make_delta(
            locode=f"USS{i:02d}",
            name=f"Severity Port {i}",
            severity_before="WATCH",
            severity_after="STRESSED",
            deficit_before=2.0,
            deficit_after=-3.0,
        ))

    for i in range(entered_n):
        d = _make_delta(
            locode=f"USE{i:02d}",
            name=f"Entered Port {i}",
            deficit_before=1.5,
            deficit_after=-1.0,
        )
        d.entered_deficit = True
        report.entered_deficit.append(d)

    for i in range(exited_n):
        d = _make_delta(
            locode=f"USX{i:02d}",
            name=f"Exited Port {i}",
            deficit_before=-0.5,
            deficit_after=2.0,
        )
        d.exited_deficit = True
        report.exited_deficit.append(d)

    for i in range(moves_n):
        report.deficit_moves.append(_make_delta(
            locode=f"USM{i:02d}",
            name=f"Move Port {i}",
            severity_before="WATCH",
            severity_after="WATCH",
            deficit_before=3.0,
            deficit_after=0.5,
        ))

    for i in range(shuffles_n):
        report.ticker_shuffles.append(_make_delta(
            locode=f"UST{i:02d}",
            name=f"Shuffle Port {i}",
            severity_before="WATCH",
            severity_after="WATCH",
            deficit_before=2.0,
            deficit_after=2.0,
            tickers_added=["ZIM", "MATX"],
            tickers_removed=["DAC"],
        ))

    return report


# ─── HTML well-formedness checker ─────────────────────────────────────────


class _HTMLValidator(HTMLParser):
    """Minimal HTML well-formedness checker — verifies every opened
    tag is closed in LIFO order. Self-closing tags (br/img/meta/...)
    are tolerated. Counts <table> openings for the per-bucket
    assertion downstream.
    """

    _VOID_TAGS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []
        self.errors: list[str] = []
        self.table_count = 0

    def handle_starttag(self, tag, attrs):  # type: ignore[override]
        if tag in self._VOID_TAGS:
            return
        if tag == "table":
            self.table_count += 1
        self.stack.append(tag)

    def handle_endtag(self, tag):  # type: ignore[override]
        if tag in self._VOID_TAGS:
            return
        if not self.stack:
            self.errors.append(f"orphan closing </{tag}>")
            return
        top = self.stack.pop()
        if top != tag:
            self.errors.append(
                f"mismatched close: expected </{top}>, got </{tag}>"
            )

    @property
    def well_formed(self) -> bool:
        return not self.errors and not self.stack


def _validate_html(html: str) -> _HTMLValidator:
    v = _HTMLValidator()
    v.feed(html)
    return v


# ─── 1. HTML output is well-formed ────────────────────────────────────────


def test_render_html_empty_diff_is_well_formed() -> None:
    diff = _make_diff()
    html = render_html(
        diff, container_type="40FT_DRY",
        snapshot_date_iso="2026-05-26", prior_date_iso="2026-05-25",
    )
    v = _validate_html(html)
    assert v.well_formed, f"HTML not well-formed: {v.errors} stack={v.stack}"


def test_render_html_populated_diff_is_well_formed() -> None:
    diff = _make_diff(
        severity_n=3, entered_n=2, exited_n=1, moves_n=2, shuffles_n=2,
    )
    html = render_html(
        diff, container_type="40FT_DRY",
        snapshot_date_iso="2026-05-26", prior_date_iso="2026-05-25",
    )
    v = _validate_html(html)
    assert v.well_formed, f"HTML not well-formed: {v.errors} stack={v.stack}"


def test_render_html_starts_with_doctype() -> None:
    diff = _make_diff(severity_n=1)
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert html.startswith("<!DOCTYPE html>")


# ─── 2. One <table> per non-empty bucket ──────────────────────────────────


def test_render_html_has_table_for_each_non_empty_bucket() -> None:
    """5 buckets populated -> 5 tables in the document."""
    diff = _make_diff(
        severity_n=1, entered_n=1, exited_n=1, moves_n=1, shuffles_n=1,
    )
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    v = _validate_html(html)
    assert v.table_count == 5


def test_render_html_only_populated_bucket_gets_table() -> None:
    """Only ticker_shuffles populated -> only one <table>."""
    diff = _make_diff(shuffles_n=2)
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    v = _validate_html(html)
    assert v.table_count == 1


# ─── 3. Empty buckets are SUPPRESSED (no "(none)" filler) ────────────────


def test_render_html_omits_section_for_empty_buckets() -> None:
    """Only severity_shifts populated — the other bucket titles must
    NOT appear in the rendered body."""
    diff = _make_diff(severity_n=1)
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert "Severity shifts" in html
    # The other section titles must not appear:
    assert "Entered deficit" not in html
    assert "Exited deficit" not in html
    assert "Material deficit moves" not in html
    assert "Ticker reshuffles" not in html
    # And no "(none)" filler anywhere:
    assert "(none)" not in html


def test_render_html_completely_empty_diff_has_no_section_titles() -> None:
    diff = _make_diff()   # all buckets empty
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert "Severity shifts" not in html
    assert "Entered deficit" not in html
    assert "Exited deficit" not in html
    assert "Material deficit moves" not in html
    assert "Ticker reshuffles" not in html
    # ...but the body has the "no material changes" confirmation:
    assert "No material changes overnight" in html


# ─── 4. Plain-text version surfaces the same diff counts ──────────────────


def test_render_plain_text_has_same_counts_as_html() -> None:
    diff = _make_diff(
        severity_n=3, entered_n=2, exited_n=1, moves_n=4, shuffles_n=2,
    )
    text = render_plain_text(diff, snapshot_date_iso="2026-05-26")

    # The "Counts:" line embeds every bucket count in one place so the
    # plain-text reader sees the same diff shape as the HTML reader.
    assert "severity_shifts=3" in text
    assert "entered=2" in text
    assert "exited=1" in text
    assert "moves=4" in text
    assert "reshuffles=2" in text


def test_render_plain_text_empty_diff_is_one_line_confirmation() -> None:
    diff = _make_diff()
    text = render_plain_text(diff, snapshot_date_iso="2026-05-26")
    assert "No material changes overnight" in text
    # Counts line is suppressed on quiet days — no clutter.
    assert "severity_shifts=" not in text


def test_render_plain_text_omits_section_headers_for_empty_buckets() -> None:
    """Same suppression rule as the HTML side — empty buckets don't
    appear at all in the body."""
    diff = _make_diff(severity_n=1)
    text = render_plain_text(diff, snapshot_date_iso="2026-05-26")
    assert "Severity shifts" in text
    assert "Entered deficit" not in text
    assert "Exited deficit" not in text


# ─── 5. Subject line ──────────────────────────────────────────────────────


def test_subject_line_empty_diff_is_no_material_changes() -> None:
    diff = _make_diff()
    s = build_subject_line(diff, snapshot_date_iso="2026-05-26")
    assert s == "Port-Supply: no material changes (2026-05-26)"


def test_subject_line_none_diff_is_no_material_changes() -> None:
    """Treat a None diff (first-ever run) as a quiet day."""
    s = build_subject_line(None, snapshot_date_iso="2026-05-26")
    assert s == "Port-Supply: no material changes (2026-05-26)"


def test_subject_line_severity_and_entered_both_surface() -> None:
    diff = _make_diff(severity_n=3, entered_n=2)
    s = build_subject_line(diff, snapshot_date_iso="2026-05-26")
    assert "3 severity shifts" in s
    assert "2 entered deficit" in s
    assert "(2026-05-26)" in s


def test_subject_line_singular_when_count_is_one() -> None:
    diff = _make_diff(severity_n=1)
    s = build_subject_line(diff, snapshot_date_iso="2026-05-26")
    assert "1 severity shift" in s
    # Must NOT be "1 severity shifts"
    assert "shifts" not in s.split("(")[0]


def test_subject_line_omits_date_when_empty() -> None:
    diff = _make_diff()
    s = build_subject_line(diff)   # no snapshot_date_iso
    assert s == "Port-Supply: no material changes"


# ─── 6. should_send gating ────────────────────────────────────────────────


def test_should_send_false_on_empty_diff() -> None:
    assert should_send(_make_diff()) is False


def test_should_send_false_on_none_diff() -> None:
    """A None diff (first-ever run) is a quiet day."""
    assert should_send(None) is False


@pytest.mark.parametrize("bucket_kwargs", [
    {"severity_n": 1},
    {"entered_n": 1},
    {"exited_n": 1},
    {"moves_n": 1},
    {"shuffles_n": 1},
])
def test_should_send_true_when_any_bucket_has_entry(bucket_kwargs) -> None:
    """ANY non-empty bucket triggers a send."""
    assert should_send(_make_diff(**bucket_kwargs)) is True


# ─── 7. Edge cases ────────────────────────────────────────────────────────


def test_ticker_shuffles_only_diff_still_sends() -> None:
    """A diff with only ticker reshuffles (no deficit moves, no severity
    shifts) is still material — operators care about exposure changes."""
    diff = _make_diff(shuffles_n=1)
    assert should_send(diff) is True
    s = build_subject_line(diff, snapshot_date_iso="2026-05-26")
    assert "1 ticker reshuffle" in s
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert "Ticker reshuffles" in html


def test_very_long_port_name_and_ticker_list_dont_break_table() -> None:
    """Smoke test: extreme inputs don't raise + produce >100-char body."""
    long_name = "Port " + "Extraordinarily " * 30 + "Long Name"
    long_tickers = [f"TICK{i:03d}" for i in range(50)]
    diff = DiffReport(n_ports_before=1, n_ports_after=1)
    diff.severity_shifts.append(_make_delta(
        locode="USLNG",
        name=long_name,
        severity_before="WATCH",
        severity_after="STRESSED",
    ))
    diff.ticker_shuffles.append(_make_delta(
        locode="USTKR",
        name=long_name,
        tickers_added=long_tickers,
        tickers_removed=long_tickers[:25],
    ))
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert len(html) > 100
    v = _validate_html(html)
    assert v.well_formed, f"HTML not well-formed: {v.errors}"


def test_render_html_escapes_special_chars_in_port_name() -> None:
    """A port name like '<script>alert(1)</script>' must NOT be emitted
    as raw HTML — html.escape should neutralize it."""
    diff = _make_diff(severity_n=1)
    diff.severity_shifts[0].name = "<script>alert(1)</script>"
    html = render_html(diff, snapshot_date_iso="2026-05-26")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_render_html_with_none_diff_renders_quiet_confirmation() -> None:
    """A None diff (first-ever run with nothing to compare) renders the
    quiet-day confirmation, not a crash."""
    html = render_html(None, snapshot_date_iso="2026-05-26")
    assert "No material changes overnight" in html
    v = _validate_html(html)
    assert v.well_formed


# ─── 8. Container type + date land in the rendered output ───────────────


def test_render_html_includes_container_type_and_dates() -> None:
    diff = _make_diff(severity_n=1)
    html = render_html(
        diff,
        container_type="40FT_REEFER",
        snapshot_date_iso="2026-05-26",
        prior_date_iso="2026-05-25",
    )
    assert "40FT_REEFER" in html
    assert "2026-05-26" in html
    assert "2026-05-25" in html


def test_render_plain_text_includes_container_type_and_dates() -> None:
    diff = _make_diff(severity_n=1)
    text = render_plain_text(
        diff,
        container_type="40FT_REEFER",
        snapshot_date_iso="2026-05-26",
        prior_date_iso="2026-05-25",
    )
    assert "40FT_REEFER" in text
    assert "2026-05-26" in text
    assert "2026-05-25" in text


# ── Anomaly-band rendering (new) ─────────────────────────────────────────


def _make_material_diff():
    """Hand-built non-empty diff so the digest renders the full body path."""
    from tools.port_supply_diff import DiffReport, PortDelta
    delta = PortDelta(
        locode="CNSHA", name="Shanghai", region="Asia",
        severity_before="Surplus", severity_after="Critical Deficit",
        severity_shifted=True,
        deficit_before=+2.0, deficit_after=-8.0,
        deficit_delta=-10.0,
        entered_deficit=True, exited_deficit=False,
        tickers_added=[], tickers_removed=[],
    )
    return DiffReport(
        n_ports_before=1, n_ports_after=1,
        severity_shifts=[delta], deficit_moves=[delta],
        entered_deficit=[delta], exited_deficit=[],
        ticker_shuffles=[],
        locodes_only_in_before=[], locodes_only_in_after=[],
    )


def test_subject_line_with_shock_band_prepends_uppercase_tag() -> None:
    from delivery.port_supply_shock_digest import build_subject_line
    diff = _make_material_diff()
    subj = build_subject_line(diff, snapshot_date_iso="2026-05-26",
                              anomaly_band="shock")
    assert "[SHOCK]" in subj
    assert "2026-05-26" in subj


def test_subject_line_with_elevated_band_prepends_lowercase_tag() -> None:
    from delivery.port_supply_shock_digest import build_subject_line
    diff = _make_material_diff()
    subj = build_subject_line(diff, snapshot_date_iso="2026-05-26",
                              anomaly_band="elevated")
    assert "[elevated]" in subj


def test_subject_line_with_normal_band_omits_tag() -> None:
    from delivery.port_supply_shock_digest import build_subject_line
    diff = _make_material_diff()
    subj = build_subject_line(diff, snapshot_date_iso="2026-05-26",
                              anomaly_band="normal")
    assert "[SHOCK]" not in subj
    assert "[elevated]" not in subj
    assert "[normal]" not in subj


def test_subject_line_default_band_omits_tag() -> None:
    """Backwards-compat: callers that don't pass anomaly_band must get
    the same subject as before."""
    from delivery.port_supply_shock_digest import build_subject_line
    diff = _make_material_diff()
    subj_no = build_subject_line(diff, snapshot_date_iso="2026-05-26")
    subj_norm = build_subject_line(diff, snapshot_date_iso="2026-05-26",
                                   anomaly_band="")
    assert subj_no == subj_norm


def test_html_with_shock_band_includes_banner() -> None:
    from delivery.port_supply_shock_digest import render_html
    diff = _make_material_diff()
    html = render_html(
        diff, snapshot_date_iso="2026-05-26",
        anomaly_band="shock",
        anomaly_explanation="composite=42, trailing median=5, +12 MADs",
    )
    assert "SHOCK DAY" in html
    assert "trailing median" in html


def test_html_with_elevated_band_includes_banner() -> None:
    from delivery.port_supply_shock_digest import render_html
    diff = _make_material_diff()
    html = render_html(
        diff, snapshot_date_iso="2026-05-26", anomaly_band="elevated",
    )
    assert "ELEVATED" in html


def test_html_with_normal_band_no_banner() -> None:
    from delivery.port_supply_shock_digest import render_html
    diff = _make_material_diff()
    html = render_html(
        diff, snapshot_date_iso="2026-05-26", anomaly_band="normal",
    )
    assert "SHOCK DAY" not in html
    assert "ELEVATED" not in html


def test_plain_text_with_shock_band_includes_marker() -> None:
    from delivery.port_supply_shock_digest import render_plain_text
    diff = _make_material_diff()
    text = render_plain_text(
        diff, snapshot_date_iso="2026-05-26",
        anomaly_band="shock",
        anomaly_explanation="composite=42, trailing median=5",
    )
    assert "*** SHOCK DAY ***" in text


def test_plain_text_with_elevated_band_includes_marker() -> None:
    from delivery.port_supply_shock_digest import render_plain_text
    diff = _make_material_diff()
    text = render_plain_text(
        diff, snapshot_date_iso="2026-05-26", anomaly_band="elevated",
    )
    assert "[ELEVATED]" in text


def test_quiet_day_with_shock_band_still_renders_banner() -> None:
    """Shouldn't happen in practice (shock implies non-quiet diff) but
    the renderer mustn't drop the banner just because there's no body."""
    from delivery.port_supply_shock_digest import render_html
    html = render_html(
        None, snapshot_date_iso="2026-05-26", anomaly_band="shock",
        anomaly_explanation="forced shock",
    )
    assert "SHOCK DAY" in html
    assert "No material changes" in html
