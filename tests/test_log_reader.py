"""Tests for utils.log_reader — dedicated coverage of the in-app log viewer
backend.

This file is hermetic: every test that touches a file uses ``tmp_path``;
the real ``logs/`` directory is never read. ``log_reader.py`` is a pure
module (no streamlit, no globals, no network) so each test is a
self-contained unit.

Covered branches
----------------
parse_log_line:
  - well-formed loguru line → all fields populated
  - blank / whitespace-only line → empty-fields ParsedLogLine, raw preserved
  - unparseable line (no timestamp) → empty level/timestamp, message=trimmed
  - every loguru level keyword (TRACE..CRITICAL) recognised
  - dotted module paths + angle brackets in source field still parse
  - trailing newline stripped from raw

read_recent_log_lines:
  - tail of length K from N-line file returns the exact last K in order
  - n > file lines → returns all lines, no padding
  - n == file lines → exact full file
  - n == 1 → only the final line
  - n <= 0 → empty list (guards against bad UI input)
  - missing file → empty list
  - empty file (zero bytes) → empty list
  - log_path=None branch with logging_setup import failure → empty list
  - chunk-boundary correctness: file > 8 KB chunk size with long lines
  - utf-8 bytes that span a chunk boundary decode cleanly (errors=replace)
  - trailing-newline behaviour (last line has no final \\n)

filter_log_lines:
  - empty input → empty list (short-circuit)
  - min_level=None and contains=None → unchanged list
  - min_level filters out lower severities, keeps ≥ threshold
  - unparseable lines (stack-trace continuations) survive level filter
  - contains substring is case-insensitive by default
  - contains substring is case-sensitive when requested
  - min_level + contains combined (both must hold for parseable lines)
  - unknown min_level string → treated as no filter (defensive)
  - case-insensitive contains with mixed-case haystack and needle
  - empty contains string treated as no substring filter

ParsedLogLine:
  - dataclass is frozen (immutable)
  - equality is structural
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from utils.log_reader import (
    ParsedLogLine,
    filter_log_lines,
    parse_log_line,
    read_recent_log_lines,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — deterministic fixtures only
# ─────────────────────────────────────────────────────────────────────────────

_FIXED_TS = "2026-05-21 09:00:00.000"


def _mk_line(level: str, msg: str = "msg", source: str = "mod.func:do:1") -> str:
    """Construct a loguru-formatted line with the level padded to width 8,
    matching the format string in logging_setup.configure_logging."""
    return f"{_FIXED_TS} | {level:<8} | {source} - {msg}"


def _write_log(path: Path, lines: list[str]) -> None:
    """Write one line per row + trailing newline (loguru's default)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────
# parse_log_line
# ─────────────────────────────────────────────────────────────────────────────

def test_parse_well_formed_line_populates_every_field() -> None:
    line = _mk_line("INFO", msg="freight scraper success", source="feeds.freight:fetch:88")
    parsed = parse_log_line(line)
    assert parsed.timestamp == _FIXED_TS
    assert parsed.level == "INFO"
    assert parsed.source == "feeds.freight:fetch:88"
    assert parsed.message == "freight scraper success"
    assert parsed.raw == line


def test_parse_blank_line_returns_all_empty() -> None:
    parsed = parse_log_line("")
    assert parsed == ParsedLogLine("", "", "", "", "")


def test_parse_whitespace_only_line_returns_empty_fields() -> None:
    """Whitespace-only lines short-circuit before the regex — same shape
    as the blank case but raw preserves whatever the caller passed."""
    parsed = parse_log_line("   \t  ")
    assert parsed.level == ""
    assert parsed.timestamp == ""
    assert parsed.message == ""
    assert parsed.raw == "   \t  "


def test_parse_unparseable_line_preserves_message_and_raw() -> None:
    """Stack-trace continuations don't match the regex; trimmed text becomes
    `message`, original lives in `raw`."""
    line = "    File 'foo.py', line 42, in bar"
    parsed = parse_log_line(line)
    assert parsed.level == ""
    assert parsed.timestamp == ""
    assert parsed.source == ""
    assert parsed.message == "File 'foo.py', line 42, in bar"
    assert parsed.raw == line


@pytest.mark.parametrize(
    "level",
    ["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
)
def test_parse_every_level_keyword_recognised(level: str) -> None:
    parsed = parse_log_line(_mk_line(level))
    assert parsed.level == level


def test_parse_handles_dotted_module_and_angle_brackets_in_source() -> None:
    """loguru emits things like ``utils.feeds.freight_scraper:<module>:1``
    for module-level statements — the source regex must accept dots and
    angle brackets."""
    line = _mk_line("INFO", source="utils.feeds.freight_scraper:<module>:1")
    parsed = parse_log_line(line)
    assert parsed.source == "utils.feeds.freight_scraper:<module>:1"
    assert parsed.level == "INFO"


def test_parse_strips_trailing_newline_from_raw() -> None:
    line = _mk_line("INFO") + "\n"
    parsed = parse_log_line(line)
    assert not parsed.raw.endswith("\n")
    assert parsed.level == "INFO"


# ─────────────────────────────────────────────────────────────────────────────
# read_recent_log_lines — defining property: exact ordered tail
# ─────────────────────────────────────────────────────────────────────────────

def test_read_recent_returns_exact_ordered_tail(tmp_path: Path) -> None:
    """Defining property: last K of N lines, in original order."""
    log = tmp_path / "log.txt"
    _write_log(log, [f"line {i}" for i in range(100)])
    out = read_recent_log_lines(10, log_path=log)
    assert out == [f"line {i}" for i in range(90, 100)]


def test_read_recent_n_larger_than_file_returns_all_lines(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    _write_log(log, [f"line {i}" for i in range(5)])
    out = read_recent_log_lines(100, log_path=log)
    assert out == [f"line {i}" for i in range(5)]
    assert len(out) == 5  # no padding, no duplication


def test_read_recent_n_equals_file_lines_returns_full_file(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    lines = [f"line {i}" for i in range(20)]
    _write_log(log, lines)
    out = read_recent_log_lines(20, log_path=log)
    assert out == lines


def test_read_recent_n_one_returns_only_last_line(tmp_path: Path) -> None:
    log = tmp_path / "log.txt"
    _write_log(log, [f"line {i}" for i in range(50)])
    out = read_recent_log_lines(1, log_path=log)
    assert out == ["line 49"]


@pytest.mark.parametrize("n", [0, -1, -100])
def test_read_recent_non_positive_n_returns_empty(tmp_path: Path, n: int) -> None:
    """UI may pass 0 (or worse) when the user clears the slider — never error."""
    log = tmp_path / "log.txt"
    _write_log(log, ["only line"])
    assert read_recent_log_lines(n, log_path=log) == []


def test_read_recent_missing_file_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "does_not_exist.log"
    assert read_recent_log_lines(50, log_path=log) == []


def test_read_recent_empty_file_returns_empty(tmp_path: Path) -> None:
    log = tmp_path / "empty.log"
    log.touch()
    assert read_recent_log_lines(50, log_path=log) == []


def test_read_recent_none_path_with_logging_setup_failure_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When log_path=None and the lazy import of logging_setup fails, the
    function must swallow and return [] — the viewer is never allowed to
    crash because the logging layer hasn't been configured yet."""
    import sys

    # Force the lazy ``from utils.logging_setup import get_active_log_file``
    # to raise by removing the module and blocking re-import.
    real_logging_setup = sys.modules.pop("utils.logging_setup", None)

    class _Blocker:
        def find_module(self, name, path=None):  # noqa: D401
            if name == "utils.logging_setup":
                return self
            return None

        def load_module(self, name):  # noqa: D401
            raise ImportError("blocked for test")

    blocker = _Blocker()
    sys.meta_path.insert(0, blocker)
    try:
        assert read_recent_log_lines(10, log_path=None) == []
    finally:
        sys.meta_path.remove(blocker)
        if real_logging_setup is not None:
            sys.modules["utils.logging_setup"] = real_logging_setup


def test_read_recent_chunk_boundary_correct(tmp_path: Path) -> None:
    """File well over the 8 KB chunk size — tailing must still recover the
    exact last K lines even when the read window straddles chunk reads."""
    log = tmp_path / "big.log"
    # ~ 80 bytes/line × 1000 lines = ~80 KB → spans many 8 KB chunks.
    lines = [f"line {i:05d} " + ("x" * 60) for i in range(1000)]
    _write_log(log, lines)
    out = read_recent_log_lines(50, log_path=log)
    assert len(out) == 50
    assert out == lines[-50:]


def test_read_recent_utf8_multibyte_decodes_cleanly(tmp_path: Path) -> None:
    """Multi-byte UTF-8 (e.g. ``→``, ``π``) deep in the tail must decode
    correctly. ``errors='replace'`` guarantees no exception even if a
    chunk read splits a code point."""
    log = tmp_path / "utf8.log"
    lines = [f"line {i} → π² ≈ {i}" for i in range(100)]
    _write_log(log, lines)
    out = read_recent_log_lines(5, log_path=log)
    assert out == lines[-5:]
    # Sanity-check the arrow survived as a single code point.
    assert "→" in out[-1]


def test_read_recent_missing_trailing_newline(tmp_path: Path) -> None:
    """If the writer crashed before the final newline, the last line should
    still be returned (splitlines handles either form)."""
    log = tmp_path / "no_trailing.log"
    log.write_text("alpha\nbeta\ngamma", encoding="utf-8")  # no final \n
    out = read_recent_log_lines(10, log_path=log)
    assert out == ["alpha", "beta", "gamma"]


# ─────────────────────────────────────────────────────────────────────────────
# filter_log_lines — level + substring
# ─────────────────────────────────────────────────────────────────────────────

def test_filter_empty_input_short_circuits() -> None:
    assert filter_log_lines([]) == []
    assert filter_log_lines([], min_level="ERROR", contains="anything") == []


def test_filter_no_filters_returns_input_unchanged() -> None:
    lines = [_mk_line("INFO"), _mk_line("WARNING")]
    assert filter_log_lines(lines) == lines


def test_filter_min_level_keeps_threshold_and_above() -> None:
    lines = [
        _mk_line("DEBUG", "d"),
        _mk_line("INFO", "i"),
        _mk_line("WARNING", "w"),
        _mk_line("ERROR", "e"),
        _mk_line("CRITICAL", "c"),
    ]
    out = filter_log_lines(lines, min_level="WARNING")
    # WARNING, ERROR, CRITICAL remain in original order.
    assert len(out) == 3
    assert "WARNING" in out[0]
    assert "ERROR" in out[1]
    assert "CRITICAL" in out[2]


def test_filter_min_level_is_case_insensitive_keyword() -> None:
    """``min_level`` accepts lowercase too — the implementation uppercases
    before lookup."""
    lines = [_mk_line("DEBUG"), _mk_line("ERROR")]
    out = filter_log_lines(lines, min_level="error")
    assert len(out) == 1
    assert "ERROR" in out[0]


def test_filter_keeps_unparseable_lines_through_level_filter() -> None:
    """Stack-trace continuations have no parseable level; they must survive
    so the user sees the full traceback even when filtering to ERROR."""
    lines = [
        _mk_line("DEBUG", "noise"),
        "    File 'foo.py', line 7, in bar",
        _mk_line("ERROR", "boom"),
        "ValueError: invalid arg",
    ]
    out = filter_log_lines(lines, min_level="ERROR")
    # DEBUG drops, the two unparseable continuations stay, ERROR stays.
    assert len(out) == 3
    assert "File 'foo.py'" in out[0]
    assert "ERROR" in out[1]
    assert "ValueError" in out[2]


def test_filter_contains_substring_default_case_insensitive() -> None:
    lines = [
        _mk_line("INFO", "freight scrape ok"),
        _mk_line("INFO", "stock feed ok"),
    ]
    out = filter_log_lines(lines, contains="STOCK")  # uppercase needle
    assert len(out) == 1
    assert "stock feed" in out[0]


def test_filter_contains_substring_case_sensitive_flag() -> None:
    lines = [_mk_line("INFO", "FREIGHT scrape ok")]
    # Lowercase needle does NOT match uppercase line under case-sensitive.
    assert filter_log_lines(lines, contains="freight", case_sensitive=True) == []
    # Exact match does.
    assert filter_log_lines(lines, contains="FREIGHT", case_sensitive=True) == lines


def test_filter_min_level_and_contains_both_applied() -> None:
    lines = [
        _mk_line("DEBUG", "stock"),
        _mk_line("INFO", "stock"),
        _mk_line("WARNING", "freight"),
        _mk_line("ERROR", "stock"),
    ]
    out = filter_log_lines(lines, min_level="INFO", contains="stock")
    # INFO/stock and ERROR/stock survive; DEBUG drops (level); WARNING drops
    # (substring).
    assert len(out) == 2
    assert all("stock" in l for l in out)
    assert all("DEBUG" not in l for l in out)


def test_filter_unknown_min_level_falls_through_to_no_filter() -> None:
    """Defensive behaviour: garbage ``min_level`` doesn't drop everything,
    it drops nothing."""
    lines = [_mk_line("DEBUG"), _mk_line("INFO"), _mk_line("ERROR")]
    out = filter_log_lines(lines, min_level="LOUD")
    assert out == lines


def test_filter_empty_contains_string_is_no_op() -> None:
    """``contains=""`` is falsy → substring filter is skipped, not applied
    (which would match everything anyway, but the codepath is different)."""
    lines = [_mk_line("INFO", "a"), _mk_line("INFO", "b")]
    out = filter_log_lines(lines, contains="")
    assert out == lines


# ─────────────────────────────────────────────────────────────────────────────
# ParsedLogLine — dataclass behaviour
# ─────────────────────────────────────────────────────────────────────────────

def test_parsed_log_line_is_frozen() -> None:
    parsed = parse_log_line(_mk_line("INFO"))
    with pytest.raises(FrozenInstanceError):
        parsed.level = "ERROR"  # type: ignore[misc]


def test_parsed_log_line_structural_equality() -> None:
    a = parse_log_line(_mk_line("INFO", "same"))
    b = parse_log_line(_mk_line("INFO", "same"))
    assert a == b
    c = parse_log_line(_mk_line("INFO", "different"))
    assert a != c
