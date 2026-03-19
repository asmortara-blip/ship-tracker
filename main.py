"""
main.py — Headless CLI cache-warmer for the Cargo Ship Container Tracker.

Runs all data fetches in sequence, times each one, and prints a summary
table at the end.  No Streamlit UI is launched.

Usage:
    python3 main.py
    python3 main.py --lookback 60
    python3 main.py --force            # clears cache before fetching
    python3 main.py --lookback 120 --force
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv
from loguru import logger

# ── Logging ───────────────────────────────────────────────────────────────────
# Remove default loguru sink; add a simple CLI-oriented one (no rotation needed).
logger.remove()
logger.add(
    sys.stderr,
    format="<level>{level: <8}</level> | {message}",
    level="INFO",
    colorize=True,
)

load_dotenv()

# ── Constants ─────────────────────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_CACHE_DIR = _REPO_ROOT / "cache"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config() -> dict:
    """Load config.yaml from the repo root."""
    with open(_CONFIG_PATH) as fh:
        return yaml.safe_load(fh)


def _cache_size_bytes(cache_dir: Path) -> int:
    """Return the total size in bytes of all .parquet files under cache_dir."""
    if not cache_dir.exists():
        return 0
    return sum(f.stat().st_size for f in cache_dir.rglob("*.parquet"))


def _fmt_bytes(n: int | float) -> str:
    """Return a human-readable byte count string."""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _count_records(result) -> int:
    """
    Return the total number of DataFrame rows contained in *result*.

    *result* may be:
      - a pandas DataFrame
      - a dict mapping keys -> DataFrames (the common case for these feeds)
      - anything else (returns 0)
    """
    import pandas as pd

    if result is None:
        return 0
    if isinstance(result, pd.DataFrame):
        return len(result)
    if isinstance(result, dict):
        total = 0
        for v in result.values():
            if isinstance(v, pd.DataFrame):
                total += len(v)
        return total
    return 0


# ── CLI argument parsing ───────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Warm the Cargo Ship Container Tracker data cache (no UI).",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=90,
        metavar="DAYS",
        help="Number of lookback days for time-series feeds (default: 90).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear the entire cache before fetching (forces a full refresh).",
    )
    return parser.parse_args(argv)


# ── Fetch runner ──────────────────────────────────────────────────────────────

def _run_fetch(label: str, fn, *args, **kwargs):
    """
    Call *fn(*args, **kwargs)*, print timing and status, and return results.

    Prints:
        ⏳ Fetching {label}...
        ✅ {label}: {n} records in {elapsed:.1f}s     (on success)
        ❌ {label}: {error}                            (on failure)

    Returns:
        (success: bool, n_records: int, elapsed_seconds: float)
    """
    print(f"⏳ Fetching {label}...")
    t0 = time.perf_counter()
    try:
        result = fn(*args, **kwargs)
        elapsed = time.perf_counter() - t0
        n = _count_records(result)
        print(f"✅ {label}: {n} records in {elapsed:.1f}s")
        return True, n, elapsed
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        print(f"❌ {label}: {exc}")
        logger.exception(f"Fetch failed for '{label}'")
        return False, 0, elapsed


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    """
    Run all data fetches, print a summary table, and return an exit code.

    Returns:
        0 on full success, 1 if any fetch failed.
    """
    args = _parse_args(argv)

    logger.info(
        f"Cache warm-up starting | lookback={args.lookback}d | force={args.force}"
    )

    # ── Config ────────────────────────────────────────────────────────────────
    cfg = _load_config()
    logger.info(f"Loaded config from {_CONFIG_PATH}")

    cache_cfg = cfg.get("cache", {})
    stocks_list = cfg.get("shipping_stocks", []) + cfg.get("sector_etfs", [])

    # ── CacheManager ──────────────────────────────────────────────────────────
    from data.cache_manager import CacheManager

    cache = CacheManager(cache_dir=str(_CACHE_DIR))

    if args.force:
        deleted = cache.invalidate_all()
        logger.info(f"--force: cleared {deleted} cached entries")
        print(f"  [force] Cleared {deleted} existing cache entries.\n")

    # ── Fetch sequence ────────────────────────────────────────────────────────
    fetch_results: list[dict] = []
    wall_start = time.perf_counter()

    # 1. Stocks / ETFs  (yfinance — no API key needed)
    from data.stock_feed import fetch_all_stocks

    ok, n, t = _run_fetch(
        "fetch_all_stocks",
        fetch_all_stocks,
        stocks_list,
        args.lookback,
        cache,
        cache_cfg.get("stocks_ttl_hours", 1.0),
    )
    fetch_results.append({"source": "fetch_all_stocks", "ok": ok, "records": n, "elapsed": t})

    # 2. FRED macro series
    from data.fred_feed import fetch_macro_series

    ok, n, t = _run_fetch(
        "fetch_macro_series",
        fetch_macro_series,
        args.lookback,
        cache,
        cache_cfg.get("fred_ttl_hours", 24.0),
    )
    fetch_results.append({"source": "fetch_macro_series", "ok": ok, "records": n, "elapsed": t})

    # 3. World Bank port throughput
    from data.worldbank_feed import fetch_port_throughput

    ok, n, t = _run_fetch(
        "fetch_port_throughput",
        fetch_port_throughput,
        cache,
        cache_cfg.get("worldbank_ttl_hours", 168.0),
    )
    fetch_results.append({"source": "fetch_port_throughput", "ok": ok, "records": n, "elapsed": t})

    # 4. AIS vessel counts
    from data.ais_feed import fetch_vessel_counts

    ok, n, t = _run_fetch(
        "fetch_vessel_counts",
        fetch_vessel_counts,
        cache,
        cache_cfg.get("ais_ttl_hours", 6.0),
    )
    fetch_results.append({"source": "fetch_vessel_counts", "ok": ok, "records": n, "elapsed": t})

    # 5. UN Comtrade trade flows
    # Convert lookback_days to approximate months (minimum 1).
    from data.comtrade_feed import fetch_all_ports

    lookback_months = max(1, round(args.lookback / 30))
    ok, n, t = _run_fetch(
        "fetch_all_ports",
        fetch_all_ports,
        lookback_months,
        cache,
        cache_cfg.get("comtrade_ttl_hours", 168.0),
    )
    fetch_results.append({"source": "fetch_all_ports", "ok": ok, "records": n, "elapsed": t})

    # 6. Freight rates (FBX / Freightos)
    from data.freight_scraper import fetch_fbx_rates

    ok, n, t = _run_fetch(
        "fetch_fbx_rates",
        fetch_fbx_rates,
        args.lookback,
        cache,
        cache_cfg.get("freight_ttl_hours", 24.0),
    )
    fetch_results.append({"source": "fetch_fbx_rates", "ok": ok, "records": n, "elapsed": t})

    # ── Summary table ─────────────────────────────────────────────────────────
    total_elapsed = time.perf_counter() - wall_start
    success_count = sum(1 for r in fetch_results if r["ok"])
    fail_count = len(fetch_results) - success_count
    total_records = sum(r["records"] for r in fetch_results)
    cache_size = _cache_size_bytes(_CACHE_DIR)

    col_w = 24
    sep = "-" * (col_w + 32)

    print()
    print("=" * 60)
    print("  CACHE WARM-UP SUMMARY")
    print("=" * 60)
    print(f"  {'Source':<{col_w}} {'Records':>8}  {'Time':>7}  Status")
    print(f"  {sep}")
    for r in fetch_results:
        status_str = "OK" if r["ok"] else "FAIL"
        print(
            f"  {r['source']:<{col_w}} {r['records']:>8}  {r['elapsed']:>6.1f}s  {status_str}"
        )
    print(f"  {sep}")
    print(f"  {'TOTAL':<{col_w}} {total_records:>8}  {total_elapsed:>6.1f}s")
    print()
    print(f"  Succeeded : {success_count}/{len(fetch_results)}")
    print(f"  Failed    : {fail_count}/{len(fetch_results)}")
    print(f"  Cache size: {_fmt_bytes(cache_size)}")
    print("=" * 60)

    if fail_count:
        logger.warning(f"Cache warm-up finished with {fail_count} failure(s).")
        return 1

    logger.info("Cache warm-up complete — all fetches succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
