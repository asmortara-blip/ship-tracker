"""utils/logging_setup.py — production logging configuration.

By default ``loguru`` writes everything to ``stderr`` with no rotation. For a
long-running Streamlit deployment that's two problems: the stderr stream gets
buffered/swallowed by some orchestrators, and an unrotated file grows without
bound. This module sets up rotated file sinks + a stderr sink + structured
formatting in one call.

Public API
----------
  configure_logging(log_dir=None, level="INFO", max_size_mb=10, retention=5,
                    json_format=False) -> Path
      Adds the rotated file sink + a stderr sink to loguru's default
      handler. Returns the directory used so the log-viewer UI can find
      the file. Safe to call multiple times — removes any prior sinks
      added by previous calls so settings aren't doubled.

Conventions
-----------
File path: ``<log_dir>/ship_tracker.log``. Rotated files get a numeric
suffix (``ship_tracker.log.1``, ``.2``, …) when they grow past
``max_size_mb``; the oldest is dropped once ``retention`` rotations
have accumulated.

Default ``log_dir`` is ``<project_root>/logs`` so it survives across
runs and is mounted as the cache/ volume in the Dockerfile (commit
17b3e20). Override with the explicit ``log_dir=`` arg for tests.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from loguru import logger


# Project-root anchor — same convention as cache/alerts/rules.json and
# narration cache paths shipped earlier this session.
_PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR: Path = _PROJECT_ROOT / "logs"
DEFAULT_LOG_FILENAME: str = "ship_tracker.log"


# Track sink handles we own so configure_logging is idempotent.
# Module-level state is fine here — there's exactly one logging config
# per process, and configure_logging() may be called more than once
# (Streamlit's hot-reload, tests).
_SINK_HANDLES: list[int] = []


def configure_logging(
    log_dir: Optional[Path] = None,
    *,
    level: str = "INFO",
    max_size_mb: int = 10,
    retention: int = 5,
    json_format: bool = False,
) -> Path:
    """Wire up loguru's stderr + rotated-file sinks.

    Parameters
    ----------
    log_dir : Path | None
        Where to write log files. Defaults to ``<project_root>/logs``.
    level : str
        Minimum log level (``"DEBUG"`` | ``"INFO"`` | ``"WARNING"`` | ``"ERROR"``).
    max_size_mb : int
        File-size rotation threshold. When the active log file exceeds this,
        a new file is started and the prior one is renamed with a numeric
        suffix.
    retention : int
        How many rotated files to keep. The oldest get deleted once this
        count is exceeded.
    json_format : bool
        When True, emit JSON-formatted log lines instead of the human
        format. Useful for shipping to log aggregators that parse JSON.

    Returns
    -------
    Path
        The log directory in use, so callers (the log viewer) can find the
        active log file.
    """
    target_dir = Path(log_dir) if log_dir is not None else DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / DEFAULT_LOG_FILENAME

    # Remove any sinks we added previously — keeps the call idempotent
    # across Streamlit hot-reloads and explicit reconfigure calls.
    global _SINK_HANDLES
    for handle in _SINK_HANDLES:
        try:
            logger.remove(handle)
        except (ValueError, KeyError):
            pass
    _SINK_HANDLES = []

    # Stderr sink — keeps the default Docker/Streamlit Cloud experience.
    stderr_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
        "<level>{message}</level>"
    )
    stderr_handle = logger.add(
        sys.stderr, level=level, format=stderr_format, colorize=True,
        backtrace=True, diagnose=False,   # diagnose=False — never leak secrets
    )
    _SINK_HANDLES.append(stderr_handle)

    # File sink — rotated.
    if json_format:
        file_handle = logger.add(
            str(log_path), level=level, rotation=f"{max_size_mb} MB",
            retention=retention, serialize=True,
            backtrace=True, diagnose=False, enqueue=True,
        )
    else:
        file_format = (
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{name}:{function}:{line} - {message}"
        )
        file_handle = logger.add(
            str(log_path), level=level, format=file_format,
            rotation=f"{max_size_mb} MB", retention=retention,
            backtrace=True, diagnose=False, enqueue=True,
        )
    _SINK_HANDLES.append(file_handle)

    return target_dir


def get_log_dir() -> Path:
    """Return the configured log directory. Always returns a valid Path
    (defaults to ``DEFAULT_LOG_DIR`` if logging hasn't been configured)."""
    return DEFAULT_LOG_DIR


def get_active_log_file() -> Path:
    """Return the path to the currently-active log file (the un-rotated one
    that new lines are written to). Doesn't guarantee the file exists yet."""
    return get_log_dir() / DEFAULT_LOG_FILENAME


__all__ = [
    "DEFAULT_LOG_DIR",
    "DEFAULT_LOG_FILENAME",
    "configure_logging",
    "get_log_dir",
    "get_active_log_file",
]
