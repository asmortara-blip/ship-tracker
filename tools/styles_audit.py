"""Design-system audit tool.

Walks ``ui/*.py`` and emits a CSV ranking each tab by migration ROI — the
higher the redundancy score, the more duplication the refactor pattern will
eliminate. This is the *read-only* gate for Phase 2: never migrate a tab
without first looking up its row here.

Signals per file:
    palette_redecls   count of ``C_BG``/``C_SURFACE``/``C_CARD``/``C_BORDER``
                      /``C_HIGH``/``C_MOD``/``C_LOW``/``C_ACCENT``/``C_CONV``
                      /``C_MACRO``/``C_TEXT``/``C_TEXT2``/``C_TEXT3``
                      assignments inside the file
    inline_divs       count of inline ``st.markdown('<div style='`` blocks
    unsafe_html       count of ``unsafe_allow_html=True`` calls
    random_calls      count of ``np.random.`` or ``random.`` usages
                      (proxy for mock data)
    imports_styles    1 if the module imports from ``ui.styles``, else 0
    loc               raw line count

ROI score: redundancy components, weighted. Ties broken by LOC descending.

Usage:
    python tools/styles_audit.py                    # writes docs/audit-baseline.csv
    python tools/styles_audit.py --out path.csv
    python tools/styles_audit.py --print            # print top-20 to stdout
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path


# ── Pattern definitions ─────────────────────────────────────────────────────

PALETTE_CONSTS: tuple[str, ...] = (
    "C_BG", "C_SURFACE", "C_CARD", "C_BORDER",
    "C_HIGH", "C_MOD", "C_LOW", "C_ACCENT", "C_CONV", "C_MACRO",
    "C_TEXT", "C_TEXT2", "C_TEXT3", "C_RULE",
)

# Match assignments like: C_BG = "#0c0e14"  (but not references/imports).
_PALETTE_ASSIGN_RE = re.compile(
    r"^\s*(?:" + "|".join(PALETTE_CONSTS) + r")\s*=\s*['\"#]",
    re.MULTILINE,
)

_INLINE_DIV_RE = re.compile(r"<div\s+style\s*=", re.IGNORECASE)

_UNSAFE_HTML_RE = re.compile(r"unsafe_allow_html\s*=\s*True")

_RANDOM_RE = re.compile(r"\b(?:np\.random\.|random\.(?:Random|random|uniform|randint|choice|seed|sample|gauss|normal))")

_STYLES_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+ui\.styles\s+import|from\s+ui\s+import\s+styles|import\s+ui\.styles)",
    re.MULTILINE,
)


# ── Result record ───────────────────────────────────────────────────────────

@dataclass
class AuditRow:
    file: str
    loc: int
    palette_redecls: int
    inline_divs: int
    unsafe_html: int
    random_calls: int
    imports_styles: int

    @property
    def roi_score(self) -> float:
        """Higher score = higher refactor ROI."""
        return (
            self.palette_redecls * 8
            + self.inline_divs * 1
            + self.unsafe_html * 0.5
            + self.random_calls * 2
            + (0 if self.imports_styles else 5)
        )

    def to_csv_row(self) -> dict[str, object]:
        d = asdict(self)
        d["roi_score"] = round(self.roi_score, 2)
        return d


# ── Core ────────────────────────────────────────────────────────────────────

def audit_file(path: Path) -> AuditRow:
    text = path.read_text(encoding="utf-8", errors="replace")
    return AuditRow(
        file=path.name,
        loc=text.count("\n") + 1,
        palette_redecls=len(_PALETTE_ASSIGN_RE.findall(text)),
        inline_divs=len(_INLINE_DIV_RE.findall(text)),
        unsafe_html=len(_UNSAFE_HTML_RE.findall(text)),
        random_calls=len(_RANDOM_RE.findall(text)),
        imports_styles=1 if _STYLES_IMPORT_RE.search(text) else 0,
    )


def audit_dir(ui_dir: Path) -> list[AuditRow]:
    files = sorted(p for p in ui_dir.glob("tab_*.py"))
    return [audit_file(p) for p in files]


def write_csv(rows: list[AuditRow], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ranked = sorted(rows, key=lambda r: (-r.roi_score, -r.loc))
    fieldnames = ["rank", "file", "roi_score", "palette_redecls", "inline_divs",
                  "unsafe_html", "random_calls", "imports_styles", "loc"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, row in enumerate(ranked, start=1):
            d = row.to_csv_row()
            d["rank"] = i
            writer.writerow({k: d.get(k, "") for k in fieldnames})


def print_top(rows: list[AuditRow], n: int = 20) -> None:
    ranked = sorted(rows, key=lambda r: (-r.roi_score, -r.loc))[:n]
    header = f"{'Rank':>4}  {'File':<40} {'ROI':>7}  {'pal':>4} {'div':>4} {'html':>5} {'rnd':>4} {'uses':>4} {'loc':>5}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(ranked, start=1):
        print(
            f"{i:>4}  {r.file:<40} {r.roi_score:>7.1f}  "
            f"{r.palette_redecls:>4} {r.inline_divs:>4} {r.unsafe_html:>5} "
            f"{r.random_calls:>4} {r.imports_styles:>4} {r.loc:>5}"
        )


def _default_root() -> Path:
    return Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=_default_root(),
                        help="Project root (defaults to parent of tools/)")
    parser.add_argument("--out", type=Path, default=None,
                        help="CSV output path (default: docs/audit-baseline.csv)")
    parser.add_argument("--print", dest="do_print", action="store_true",
                        help="Also print the top-20 to stdout")
    args = parser.parse_args(argv)

    ui_dir = args.root / "ui"
    if not ui_dir.is_dir():
        print(f"error: {ui_dir} not found", file=sys.stderr)
        return 2

    rows = audit_dir(ui_dir)
    out = args.out or (args.root / "docs" / "audit-baseline.csv")
    write_csv(rows, out)
    print(f"Audited {len(rows)} tabs → {out}")

    if args.do_print:
        print()
        print_top(rows)

    totals = {
        "palette_redecls": sum(r.palette_redecls for r in rows),
        "inline_divs":     sum(r.inline_divs for r in rows),
        "unsafe_html":     sum(r.unsafe_html for r in rows),
        "random_calls":    sum(r.random_calls for r in rows),
        "imports_styles":  sum(r.imports_styles for r in rows),
    }
    print(f"Totals: {totals}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
