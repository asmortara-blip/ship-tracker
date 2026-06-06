from __future__ import annotations

import time
from pathlib import Path
from typing import Callable
import re

import pandas as pd
from loguru import logger


class CacheManager:
    """TTL-based Parquet file cache.

    Wraps any fetch function so that repeated calls within the TTL
    window return cached data instead of hitting the API.

    Storage layout:
        cache/{source}/{slug}.parquet

    Keys are slugified strings built from query parameters.
    Parquet is used (over CSV/JSON) to preserve pandas dtypes,
    especially timezone-aware DatetimeIndex needed for correlation.
    """

    def __init__(self, cache_dir: str | Path = "cache") -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], pd.DataFrame],
        ttl_hours: float,
        source: str = "misc",
    ) -> pd.DataFrame:
        """Return cached DataFrame if fresh, otherwise call fetch_fn and cache result.

        Args:
            key: Unique string identifying this query (e.g. "comtrade_CNSHA_8471_import_2024").
            fetch_fn: Zero-argument callable that returns a fresh DataFrame.
            ttl_hours: Cache lifetime in hours.
            source: Subdirectory name (e.g. "comtrade", "fred").

        Returns:
            DataFrame — either from cache or freshly fetched.
        """
        path = self._path(source, key)

        if self._is_fresh(path, ttl_hours):
            logger.debug(f"Cache hit: {source}/{key}")
            cached = pd.read_parquet(path)
            self._stamp_provenance(source, key, "cache", cached)
            return cached

        logger.info(f"Cache miss — fetching: {source}/{key}")
        df = fetch_fn()

        if df is not None and not df.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=True)
            logger.debug(f"Cached {len(df)} rows → {path}")
            self._stamp_provenance(source, key, "live", df)
        else:
            logger.warning(f"Fetch returned empty DataFrame for {source}/{key}; not caching")
            self._stamp_provenance(source, key, "empty", df)

        return df if df is not None else pd.DataFrame()

    @staticmethod
    def _stamp_provenance(source: str, key: str, kind: str, df) -> None:
        """Best-effort per-fetch provenance stamp (rec R003/R097).

        ``cache`` on a hit, ``live``/``empty`` on a miss. NEVER affects the
        fetch — a provenance write failure (or no DB) is swallowed. Off in tests
        unless explicitly enabled (see state.fetch_ledger.RECORDING_ENABLED)."""
        try:
            from state.fetch_ledger import cheap_content_hash, record_fetch
            n = len(df) if (df is not None and hasattr(df, "__len__")) else 0
            quality = ("GOOD" if kind == "live"
                       else "STALE" if kind == "cache" else "UNKNOWN")
            record_fetch(source, key, kind, row_count=n, quality=quality,
                         byte_hash=cheap_content_hash(df))
        except Exception:  # pragma: no cover - defensive
            pass

    def invalidate(self, key: str, source: str = "misc") -> None:
        """Delete a specific cache entry."""
        path = self._path(source, key)
        if path.exists():
            path.unlink()
            logger.info(f"Invalidated cache: {source}/{key}")

    def invalidate_source(self, source: str) -> int:
        """Delete all cache entries for a given source. Returns count deleted."""
        source_dir = self.cache_dir / source
        if not source_dir.exists():
            return 0
        count = 0
        for f in source_dir.glob("*.parquet"):
            f.unlink()
            count += 1
        logger.info(f"Invalidated {count} entries for source '{source}'")
        return count

    def invalidate_all(self) -> int:
        """Delete all cache entries across all sources."""
        count = 0
        for f in self.cache_dir.rglob("*.parquet"):
            f.unlink()
            count += 1
        logger.info(f"Invalidated all {count} cache entries")
        return count

    def cache_age_hours(self, key: str, source: str = "misc") -> float | None:
        """Return age of cache entry in hours, or None if not cached."""
        path = self._path(source, key)
        if not path.exists():
            return None
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds / 3600

    def is_cached(self, key: str, source: str = "misc", ttl_hours: float = 0) -> bool:
        """Return True if a fresh cache entry exists."""
        return self._is_fresh(self._path(source, key), ttl_hours)

    def list_entries(self, source: str | None = None) -> list[dict]:
        """List all cache entries with metadata."""
        entries = []
        search_dir = self.cache_dir / source if source else self.cache_dir
        if not search_dir.exists():
            return entries
        pattern = "*.parquet" if source else "**/*.parquet"
        for f in search_dir.glob(pattern):
            age_h = (time.time() - f.stat().st_mtime) / 3600
            entries.append({
                "source": f.parent.name,
                "key": f.stem,
                "age_hours": round(age_h, 2),
                "size_kb": round(f.stat().st_size / 1024, 1),
            })
        return sorted(entries, key=lambda x: x["age_hours"])

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _path(self, source: str, key: str) -> Path:
        slug = self._slugify(key)
        return self.cache_dir / source / f"{slug}.parquet"

    @staticmethod
    def _slugify(text: str) -> str:
        text = str(text).lower().strip()
        text = re.sub(r"[^\w\s-]", "", text)
        text = re.sub(r"[\s_-]+", "_", text)
        return text[:120]  # filesystem path length safety

    @staticmethod
    def _is_fresh(path: Path, ttl_hours: float) -> bool:
        if not path.exists():
            return False
        if ttl_hours <= 0:
            return True  # ttl=0 means "always fresh" (cache forever)
        age_seconds = time.time() - path.stat().st_mtime
        return age_seconds < ttl_hours * 3600
