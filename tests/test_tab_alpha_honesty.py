"""Honesty guards for ui.tab_alpha — the Alpha Signal tab must present REAL
engine output, never fabricated signals / ages / factor scores.

Companion to the engine-side guards in test_alpha_engine.py
(generate_all_signals fabricates nothing on dark input). These are source- and
symbol-level ratchets in the spirit of test_tab_provenance.py: they fail if any
of the removed fabrications is reintroduced.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import ui.tab_alpha as tab_alpha


_SRC = Path(tab_alpha.__file__).read_text()
_TREE = ast.parse(_SRC)


def _top_level_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_mock_signal_scaffolding_is_gone() -> None:
    # The fabricated signal log + factor grid + mock cache that masqueraded as
    # live data are removed at the symbol level (no accidental re-import).
    assert not hasattr(tab_alpha, "_MOCK_SIGNALS")
    assert not hasattr(tab_alpha, "_cached_signals")
    assert not hasattr(tab_alpha, "_FACTOR_SCORES")


def test_no_random_signal_fabrication() -> None:
    # No random ages, no random chart-marker placement, no mock age jitter.
    # (np.random is fine — it backs the clearly-labelled synthetic price chart.)
    # AST-based so it catches every re-introduction form (import random, from
    # random import …, import random as r), not just one substring.
    assert "random" not in _top_level_imports(_TREE), (
        "stdlib `random` must not be imported — use np.random for the labelled "
        "synthetic price chart only"
    )
    assert "random.randint" not in _SRC
    assert "random.Random" not in _SRC


def test_live_monitor_consumes_real_signals() -> None:
    # The monitor must read the signals it is handed, not an internal mock cache.
    src = inspect.getsource(tab_alpha._render_live_monitor)
    assert "_cached_signals" not in src
    assert "_MOCK_SIGNALS" not in src
    assert "signals" in src  # uses the parameter


def test_no_backtest_based_estimate_label() -> None:
    # The "Est. Alpha p.a." card claimed a "backtest-based estimate" for a number
    # that was avg_strength × avg_rr × 0.18 — a heuristic, not a backtest.
    assert "backtest-based estimate" not in _SRC
    assert "Est. Alpha p.a." not in _SRC


def test_no_fabricated_age_column() -> None:
    # Engine signals carry no genuine wall-clock age; the "Age"/"mins_ago"
    # columns (fed by random.randint) are gone.
    assert "mins_ago" not in _SRC


def test_factor_breakdown_uses_real_signal_fields_only() -> None:
    # The breakdown is built from real signal fields (ticker/sig_type/strength/
    # direction), not a hand-coded factor grid keyed by fake tickers.
    src = inspect.getsource(tab_alpha._render_factor_breakdown)
    assert "_FACTOR_SCORES" not in src
    assert "GOGL" not in src  # a fabricated ticker the engine never emits
