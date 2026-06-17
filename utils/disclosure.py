"""Mandatory not-advice / modeled-provenance disclosure for exported artifacts.

R005 de-mocked the PDF/HTML reports and gave them a not-advice disclaimer — but
the other downloadable artifacts (markdown, Excel) shipped modeled BUY/SELL
signals and synthetic freight/macro series under a polished platform footer with
ZERO disclosure. Those are exactly the artifacts that travel OUTSIDE the app
(Slack / Notion / email / a shared Excel workbook), where the in-app provenance
banners don't follow.

This module is the single source of the export disclosure line plus a positive
gate (:func:`assert_disclosed`) that fails loudly if a renderer ever ships text
without a not-advice marker — turning R005's one-format fix into an enforced
invariant across every export format.
"""
from __future__ import annotations


class DisclosureError(RuntimeError):
    """Raised when an artifact would ship without a not-advice disclosure."""


# The one-line notice appended to every exported artifact. It carries the
# canonical "not investment advice" marker so it shares the provenance
# vocabulary the UI ratchet (tests/test_tab_provenance.py) already enforces.
MODELED_NOTICE: str = (
    "Modeled / illustrative analysis — freight, congestion and AIS series are "
    "synthetic and signals are rule-based model output, not investment advice. "
    "See docs/DATA_PROVENANCE.md."
)

# Accepted not-advice markers (compared lower-cased). Either the export
# one-liner's phrase OR the canonical investor-report disclaimer's phrasing
# qualifies, so the HTML/PDF reports (R005) pass the same gate unchanged.
_MARKERS: tuple[str, ...] = (
    "not investment advice",
    "does not constitute investment advice",
)


def is_disclosed(text: str) -> bool:
    """True iff *text* carries a recognised not-advice disclosure marker."""
    if not text:
        return False
    low = str(text).lower()
    return any(m in low for m in _MARKERS)


def assert_disclosed(text: str) -> None:
    """Raise :class:`DisclosureError` if *text* lacks a not-advice marker.

    The positive gate each export renderer calls before returning, so a future
    edit that drops the disclosure fails loudly here instead of silently
    shipping modeled signals dressed as measurements.
    """
    if not is_disclosed(text):
        raise DisclosureError(
            "exported artifact is missing a not-advice disclosure marker"
        )


__all__ = ["DisclosureError", "MODELED_NOTICE", "is_disclosed", "assert_disclosed"]
