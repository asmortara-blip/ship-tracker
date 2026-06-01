"""Tests for utils.logging_setup and utils.log_reader.

Hermetic via monkeypatched log paths — never touches the real ``logs/``
directory. The loguru sinks added by configure_logging are removed at
test teardown so subsequent tests don't see duplicate handlers.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from loguru import logger

from utils.log_reader import (
    ParsedLogLine,
    filter_log_lines,
    parse_log_line,
    read_recent_log_lines,
)
from utils.logging_setup import (
    DEFAULT_LOG_FILENAME,
    configure_logging,
    get_active_log_file,
    get_log_dir,
)


# ─── Fixture: per-test log dir + sink cleanup ──────────────────────────────

@pytest.fixture
def isolated_log_dir(tmp_path):
    """Configure logging into a tmp dir; remove sinks at teardown."""
    target = tmp_path / "logs"
    configure_logging(log_dir=target, level="DEBUG")
    yield target
    # Tear down — remove every sink so tests don't double-log.
    try:
        logger.remove()
    except (ValueError, KeyError):
        pass


# ─── configure_logging ─────────────────────────────────────────────────────

def test_configure_logging_creates_log_directory(isolated_log_dir: Path) -> None:
    assert isolated_log_dir.exists()
    assert isolated_log_dir.is_dir()


def test_configure_logging_returns_log_dir(tmp_path) -> None:
    target = tmp_path / "logs2"
    result = configure_logging(log_dir=target, level="INFO")
    assert result == target
    logger.remove()


def test_configure_logging_writes_to_file(isolated_log_dir: Path) -> None:
    logger.info("hello from the test")
    # loguru's enqueue=True means writes may be buffered briefly; flush
    # by removing the sinks (close() drains the queue).
    logger.remove()
    log_file = isolated_log_dir / DEFAULT_LOG_FILENAME
    assert log_file.exists()
    contents = log_file.read_text(encoding="utf-8")
    assert "hello from the test" in contents


def test_configure_logging_is_idempotent(tmp_path) -> None:
    """Calling configure_logging twice doesn't add duplicate sinks."""
    target = tmp_path / "logs3"
    configure_logging(log_dir=target)
    configure_logging(log_dir=target)   # second call
    logger.info("just once please")
    logger.remove()
    log_file = target / DEFAULT_LOG_FILENAME
    contents = log_file.read_text(encoding="utf-8")
    # Each loguru sink writes its own copy. With idempotent setup we get
    # ONE line. Without it we'd get two.
    assert contents.count("just once please") == 1


def test_get_active_log_file_returns_default() -> None:
    p = get_active_log_file()
    assert isinstance(p, Path)
    assert p.name == DEFAULT_LOG_FILENAME


# ─── parse_log_line ────────────────────────────────────────────────────────

def test_parse_log_line_valid_format() -> None:
    line = (
        "2026-05-20 19:36:35.000 | INFO     | engine.something:do_thing:42 - "
        "doing the thing"
    )
    parsed = parse_log_line(line)
    assert parsed.timestamp == "2026-05-20 19:36:35.000"
    assert parsed.level == "INFO"
    assert parsed.source == "engine.something:do_thing:42"
    assert parsed.message == "doing the thing"


def test_parse_log_line_handles_unrecognised_line() -> None:
    """Continuation / non-standard lines preserve `raw` and put the trimmed
    content in `message`."""
    line = "    Traceback (most recent call last):"
    parsed = parse_log_line(line)
    assert parsed.level == ""
    assert parsed.timestamp == ""
    assert parsed.message == "Traceback (most recent call last):"
    assert parsed.raw == line


def test_parse_log_line_empty() -> None:
    parsed = parse_log_line("")
    assert parsed == ParsedLogLine("", "", "", "", "")


def test_parse_log_line_levels_recognized() -> None:
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        line = f"2026-05-20 19:36:35.000 | {level:<8} | mod.func:do:1 - msg"
        parsed = parse_log_line(line)
        assert parsed.level == level


# ─── read_recent_log_lines ─────────────────────────────────────────────────

def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_recent_returns_tail(tmp_path) -> None:
    log = tmp_path / "log.txt"
    _write_lines(log, [f"line {i}" for i in range(100)])
    out = read_recent_log_lines(10, log_path=log)
    assert out == [f"line {i}" for i in range(90, 100)]


def test_read_recent_n_larger_than_file_returns_all(tmp_path) -> None:
    log = tmp_path / "log.txt"
    _write_lines(log, [f"line {i}" for i in range(5)])
    out = read_recent_log_lines(100, log_path=log)
    assert out == [f"line {i}" for i in range(5)]


def test_read_recent_missing_file_returns_empty(tmp_path) -> None:
    log = tmp_path / "nonexistent.log"
    assert read_recent_log_lines(50, log_path=log) == []


def test_read_recent_empty_file(tmp_path) -> None:
    log = tmp_path / "empty.log"
    log.touch()
    assert read_recent_log_lines(50, log_path=log) == []


def test_read_recent_n_zero_returns_empty(tmp_path) -> None:
    log = tmp_path / "log.txt"
    _write_lines(log, ["only line"])
    assert read_recent_log_lines(0, log_path=log) == []


def test_read_recent_large_file_efficient(tmp_path) -> None:
    """5000 lines of ~50 bytes each → ~250 KB. tailing 100 lines should be
    cheap, and we should still get the right tail."""
    log = tmp_path / "big.log"
    _write_lines(log, [f"line number {i:05d} with some filler text" for i in range(5000)])
    out = read_recent_log_lines(100, log_path=log)
    assert len(out) == 100
    assert out[-1].startswith("line number 04999")
    assert out[0].startswith("line number 04900")


# ─── filter_log_lines ──────────────────────────────────────────────────────

def _mk_log_line(level: str, msg: str = "msg") -> str:
    return f"2026-05-20 19:36:35.000 | {level:<8} | mod.func:do:1 - {msg}"


def test_filter_by_min_level_keeps_higher_severities() -> None:
    lines = [
        _mk_log_line("DEBUG"),
        _mk_log_line("INFO"),
        _mk_log_line("WARNING"),
        _mk_log_line("ERROR"),
    ]
    out = filter_log_lines(lines, min_level="WARNING")
    # WARNING and ERROR survive.
    assert len(out) == 2
    assert "WARNING" in out[0]
    assert "ERROR" in out[1]


def test_filter_keeps_unparseable_lines() -> None:
    """Lines that don't match the parse regex (stack traces, etc.) survive
    the level filter — they're usually continuations of a relevant message."""
    lines = [
        _mk_log_line("DEBUG"),
        "    Traceback (most recent call last):",
        _mk_log_line("ERROR"),
    ]
    out = filter_log_lines(lines, min_level="ERROR")
    # The traceback continuation stays; only the DEBUG drops.
    assert len(out) == 2
    assert "Traceback" in out[0]
    assert "ERROR" in out[1]


def test_filter_by_contains_substring() -> None:
    lines = [
        _mk_log_line("INFO", "freight scraper success"),
        _mk_log_line("INFO", "stock feed success"),
        _mk_log_line("INFO", "alpha vantage success"),
    ]
    out = filter_log_lines(lines, contains="stock")
    assert len(out) == 1
    assert "stock feed" in out[0]


def test_filter_contains_case_insensitive_by_default() -> None:
    lines = [_mk_log_line("INFO", "FREIGHT scrape OK")]
    assert filter_log_lines(lines, contains="freight") == lines


def test_filter_contains_case_sensitive_when_requested() -> None:
    lines = [_mk_log_line("INFO", "FREIGHT scrape OK")]
    # "freight" lowercase won't match the uppercase line under case-sensitive.
    assert filter_log_lines(lines, contains="freight", case_sensitive=True) == []
    assert filter_log_lines(lines, contains="FREIGHT", case_sensitive=True) == lines


def test_filter_combined_level_and_contains() -> None:
    lines = [
        _mk_log_line("DEBUG", "stock"),
        _mk_log_line("INFO", "stock"),
        _mk_log_line("WARNING", "freight"),
        _mk_log_line("ERROR", "stock"),
    ]
    out = filter_log_lines(lines, min_level="INFO", contains="stock")
    # INFO/stock and ERROR/stock survive; WARNING/freight drops the substring.
    assert len(out) == 2
    assert all("stock" in l for l in out)
    assert all("DEBUG" not in l for l in out)


def test_filter_empty_input() -> None:
    assert filter_log_lines([]) == []
    assert filter_log_lines([], min_level="ERROR") == []


def test_filter_unknown_level_treated_as_no_filter() -> None:
    """An unrecognised level string falls through to 'no filter'."""
    lines = [_mk_log_line("INFO"), _mk_log_line("ERROR")]
    out = filter_log_lines(lines, min_level="NOT_A_REAL_LEVEL")
    assert out == lines  # nothing dropped
