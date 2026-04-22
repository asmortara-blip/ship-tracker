"""Smoke tests for tools.styles_audit — ensures the audit runs and reports findings."""
from __future__ import annotations

from pathlib import Path

from tools.styles_audit import audit_dir, audit_file, _default_root


UI_DIR = _default_root() / "ui"


def test_audit_dir_returns_rows() -> None:
    rows = audit_dir(UI_DIR)
    assert len(rows) > 0
    # Every file in the report should be an actual tab_*.py
    assert all(r.file.startswith("tab_") and r.file.endswith(".py") for r in rows)


def test_refactored_tab_imports_styles() -> None:
    # The canonical reference tab must always pass the "imports ui.styles" check.
    rows = audit_dir(UI_DIR)
    ref = [r for r in rows if r.file == "tab_rate_analytics_refactored.py"]
    assert ref, "canonical refactored tab missing from audit"
    assert ref[0].imports_styles == 1


def test_roi_score_is_nonnegative() -> None:
    rows = audit_dir(UI_DIR)
    assert all(r.roi_score >= 0 for r in rows)


def test_audit_file_reports_loc(tmp_path: Path) -> None:
    p = tmp_path / "tab_fake.py"
    p.write_text(
        "from ui.styles import C_BG\n"
        "x = 1\n"
        "C_BG = '#000'  # shadowed — counts as redecl\n"
    )
    row = audit_file(p)
    assert row.loc == 4
    assert row.palette_redecls == 1
    assert row.imports_styles == 1
