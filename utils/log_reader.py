"""utils/log_reader.py — read + filter the latest log lines.

Backs the in-app log viewer. Three responsibilities:

  1. ``read_recent_log_lines(n, log_path)`` — tail the last N lines from
     the active log file. Efficient: reads from EOF backwards so a 10 MB
     log isn't loaded into memory just to show the last 200 lines.
  2. ``filter_log_lines(lines, level?, contains?)`` — narrow the tail by
     log level and/or substring match.
  3. ``parse_log_line(line)`` — extract structured fields from one
     loguru-formatted line so the viewer can colorize by level.

Pure functions; no streamlit, no globals. Importable in tests.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Parsing
# ─────────────────────────────────────────────────────────────────────────────

# Matches loguru's default text format from logging_setup.configure_logging:
#   "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}"
_LINE_RE = re.compile(
    r"^"
    r"(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})"
    r"\s*\|\s*"
    r"(?P<level>[A-Z]+)\s*"
    r"\|\s*"
    r"(?P<source>[\w.<>:]+:[^:]+:\d+)"
    r"\s*-\s*"
    r"(?P<message>.*)"
    r"$"
)

_VALID_LEVELS: tuple[str, ...] = ("TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


@dataclass(frozen=True)
class ParsedLogLine:
    """One parsed log line. ``raw`` keeps the original text in case the
    parse missed a field or the caller wants to display it verbatim."""
    timestamp: str
    level: str
    source: str          # name:function:line
    message: str
    raw: str


def parse_log_line(line: str) -> ParsedLogLine:
    """Parse a single loguru-formatted line into structured fields.

    Returns a ParsedLogLine with empty fields and the raw line preserved
    when the line doesn't match (e.g., multi-line stack-trace continuations,
    debug-mode dumps, blank lines).
    """
    line = line.rstrip("\n")
    if not line.strip():
        return ParsedLogLine("", "", "", "", line)
    m = _LINE_RE.match(line)
    if not m:
        return ParsedLogLine("", "", "", line.strip(), line)
    return ParsedLogLine(
        timestamp=m.group("timestamp"),
        level=m.group("level"),
        source=m.group("source"),
        message=m.group("message"),
        raw=line,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tail reading
# ─────────────────────────────────────────────────────────────────────────────

def read_recent_log_lines(
    n: int = 200,
    log_path: Optional[Path] = None,
) -> list[str]:
    """Return the last ``n`` lines from ``log_path``.

    Reads backwards from EOF in 8 KB chunks until enough newlines have been
    seen — so reading the tail of a multi-megabyte log doesn't load the whole
    thing into memory.

    Missing file or unreadable → empty list. Encoding errors are replaced
    rather than raised; the viewer should never crash because of an odd
    byte sequence in a log line.
    """
    if log_path is None:
        # Lazy import keeps log_reader unit-testable without logging_setup.
        try:
            from utils.logging_setup import get_active_log_file
            log_path = get_active_log_file()
        except Exception:
            return []
    log_path = Path(log_path)
    if not log_path.exists():
        return []
    if n <= 0:
        return []

    try:
        size = log_path.stat().st_size
        if size == 0:
            return []
        chunk_size = 8192
        with log_path.open("rb") as fh:
            buf = b""
            line_count = 0
            pos = size
            while pos > 0 and line_count <= n:
                read_size = min(chunk_size, pos)
                pos -= read_size
                fh.seek(pos)
                buf = fh.read(read_size) + buf
                line_count = buf.count(b"\n")
            lines = buf.decode("utf-8", errors="replace").splitlines()
            return lines[-n:] if len(lines) > n else lines
    except (OSError, IOError):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Filtering
# ─────────────────────────────────────────────────────────────────────────────

def filter_log_lines(
    lines: list[str],
    *,
    min_level: Optional[str] = None,
    contains: Optional[str] = None,
    case_sensitive: bool = False,
) -> list[str]:
    """Narrow a list of log lines by level and/or substring match.

    ``min_level`` keeps lines at ``min_level`` AND ABOVE on the standard
    loguru ordering (TRACE < DEBUG < INFO < WARNING < ERROR < CRITICAL).
    Lines that don't parse to a recognised level are KEPT (they're often
    stack-trace continuations of an already-passed ERROR).

    ``contains`` is a substring match against the raw line. Empty / None
    means "no substring filter".
    """
    if not lines:
        return []

    # Level filter
    if min_level is not None and min_level.upper() in _VALID_LEVELS:
        min_idx = _VALID_LEVELS.index(min_level.upper())
        kept_levels = set(_VALID_LEVELS[min_idx:])
        filtered = []
        for line in lines:
            parsed = parse_log_line(line)
            if not parsed.level:
                # Unparseable line — keep it (often a continuation of a
                # prior message at any level).
                filtered.append(line)
                continue
            if parsed.level in kept_levels:
                filtered.append(line)
        lines = filtered

    # Substring filter
    if contains:
        needle = contains if case_sensitive else contains.lower()
        if case_sensitive:
            lines = [l for l in lines if needle in l]
        else:
            lines = [l for l in lines if needle in l.lower()]

    return lines


__all__ = [
    "ParsedLogLine",
    "parse_log_line",
    "read_recent_log_lines",
    "filter_log_lines",
]
