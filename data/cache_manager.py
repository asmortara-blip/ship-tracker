from __future__ import annotations

import hashlib
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable
import re

import pandas as pd
from loguru import logger


# Default number of dated vintages retained per cache key (R110). The oldest
# beyond this are pruned (and logged) so the bitemporal store can't grow
# unbounded. Tunable per-instance via ``CacheManager(..., vintage_retention=N)``.
_DEFAULT_VINTAGE_RETENTION: int = 30

# Subdirectory under the cache root that holds the bitemporal vintage store
# (cache/vintages/{source}/{slug}/{fetch_date}.parquet). It deliberately lives
# inside the cache tree but is EXCLUDED from the legacy TTL-cache tree walks
# (``invalidate_all`` / ``list_entries``) so those keep their pre-R110 counts.
_VINTAGE_DIRNAME: str = "vintages"


class CacheManager:
    """TTL-based Parquet file cache, with an additive bitemporal vintage store.

    Wraps any fetch function so that repeated calls within the TTL
    window return cached data instead of hitting the API.

    Storage layout (TTL cache — unchanged):
        cache/{source}/{slug}.parquet

    Bitemporal vintage store (R110 — additive, never on the existing hot path):
        cache/vintages/{source}/{slug}/{fetch_date}.parquet

    Each successful live fetch ALSO writes a vintage stamped with the
    knowledge-date (today). A backtest can then read data AS IT WAS KNOWN on a
    past date via ``as_of_date`` / ``load_as_of`` — the latest vintage whose
    fetch_date <= as_of — instead of today's possibly-revised values (FRED
    revisions, World Bank restatements, …). When no such vintage exists the
    as-of read returns empty rather than falling back to today's live data —
    falling back would reintroduce the very look-ahead this prevents.

    Keys are slugified strings built from query parameters.
    Parquet is used (over CSV/JSON) to preserve pandas dtypes,
    especially timezone-aware DatetimeIndex needed for correlation.
    """

    def __init__(
        self,
        cache_dir: str | Path = "cache",
        *,
        vintage_retention: int = _DEFAULT_VINTAGE_RETENTION,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Retain at least one vintage; 0/negative would defeat the point-in-time
        # store, so clamp up to 1.
        self.vintage_retention = max(1, int(vintage_retention))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], pd.DataFrame],
        ttl_hours: float,
        source: str = "misc",
        as_of_date: str | date | datetime | None = None,
    ) -> pd.DataFrame:
        """Return cached DataFrame if fresh, otherwise call fetch_fn and cache result.

        Args:
            key: Unique string identifying this query (e.g. "comtrade_CNSHA_8471_import_2024").
            fetch_fn: Zero-argument callable that returns a fresh DataFrame.
            ttl_hours: Cache lifetime in hours.
            source: Subdirectory name (e.g. "comtrade", "fred").
            as_of_date: **Bitemporal point-in-time read (R110).** When ``None``
                (the default) behaviour is EXACTLY the legacy path: TTL cache +
                live fetch. When provided, the live fetch is BYPASSED and the
                latest dated vintage whose fetch_date <= ``as_of_date`` is
                served — the data as it was known on that knowledge-date, not
                today's revised values. If no such vintage exists this returns an
                empty DataFrame (stamped honestly) and does NOT fall back to a
                live fetch — that would reintroduce look-ahead.

        Returns:
            DataFrame — either from cache, freshly fetched, or (as-of mode) the
            point-in-time vintage / empty.
        """
        # ── Bitemporal as-of read (R110) — never touches the live/TTL path. ──
        if as_of_date is not None:
            vintage = self._load_vintage_as_of(source, key, as_of_date)
            if vintage is not None and not vintage.empty:
                logger.debug(
                    f"As-of read: {source}/{key} @ {as_of_date} "
                    f"→ vintage ({len(vintage)} rows)"
                )
                self._stamp_provenance(source, key, "cache", vintage)
                return vintage
            # No vintage as-known-then. Honest empty — NEVER a live fetch (that
            # would leak today's data into a past-dated backtest = look-ahead).
            logger.info(
                f"As-of read: {source}/{key} @ {as_of_date} → no vintage "
                f"as-known-then; returning empty (no live fallback)"
            )
            self._stamp_provenance(source, key, "empty", None)
            return pd.DataFrame()

        path = self._path(source, key)

        if self._is_fresh(path, ttl_hours):
            logger.debug(f"Cache hit: {source}/{key}")
            cached = pd.read_parquet(path)
            self._stamp_provenance(source, key, "cache", cached)
            return cached

        logger.info(f"Cache miss — fetching: {source}/{key}")
        try:
            df = fetch_fn()
        except Exception:
            # A raising fetch is a non-real outcome too — record it before
            # propagating so the ledger doesn't under-count failures (R003/R097).
            self._stamp_provenance(source, key, "failed", None)
            raise

        if df is not None and not df.empty:
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(path, index=True)
            logger.debug(f"Cached {len(df)} rows → {path}")
            # ALSO persist today's knowledge-date vintage (best-effort — a
            # vintage failure must NEVER break the fetch). Belt-and-braces: the
            # method is internally guarded, and the call is guarded again so a
            # monkeypatched / unexpected failure still degrades to the legacy path.
            try:
                self._write_vintage(source, key, df)
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"Vintage write skipped for {source}/{key}: {exc}")
            self._stamp_provenance(source, key, "live", df)
        else:
            logger.warning(f"Fetch returned empty DataFrame for {source}/{key}; not caching")
            self._stamp_provenance(source, key, "empty", df)

        return df if df is not None else pd.DataFrame()

    @staticmethod
    def _stamp_provenance(source: str, key: str, kind: str, df) -> None:
        """Best-effort per-fetch provenance stamp (rec R003/R097).

        ``cache`` on a hit, ``live``/``empty`` on a miss, ``failed`` when the
        fetch raised. NEVER affects the fetch — a provenance write failure (or no
        DB) is swallowed. Off in tests unless explicitly enabled (see
        state.fetch_ledger.RECORDING_ENABLED)."""
        try:
            from state.fetch_ledger import cheap_content_hash, record_fetch
            n = len(df) if (df is not None and hasattr(df, "__len__")) else 0
            quality = ("GOOD" if kind == "live"
                       else "STALE" if kind == "cache" else "UNKNOWN")
            # Surface the data's own as-of (latest row date) for lineage.
            as_of = None
            try:
                import pandas as _pd
                if (df is not None and not df.empty
                        and isinstance(df.index, _pd.DatetimeIndex) and len(df.index)):
                    as_of = df.index.max().isoformat()
            except Exception:
                as_of = None
            record_fetch(source, key, kind, row_count=n, quality=quality,
                         byte_hash=cheap_content_hash(df), as_of=as_of)
        except Exception:  # pragma: no cover - defensive
            pass

    # ------------------------------------------------------------------
    # Bitemporal point-in-time store (R110) — public API
    # ------------------------------------------------------------------

    def load_as_of(
        self,
        key: str,
        as_of_date: str | date | datetime,
        source: str = "misc",
    ) -> pd.DataFrame | None:
        """Return the vintage as-known-on ``as_of_date`` for a key — no fetch.

        Serves the latest dated vintage whose fetch_date <= ``as_of_date`` (the
        data as it was known then). Returns ``None`` when no such vintage
        exists. NEVER fetches and NEVER falls back to today's data — that's the
        whole point of a point-in-time read (no look-ahead). Never raises; a
        read error degrades to ``None``.
        """
        try:
            return self._load_vintage_as_of(source, key, as_of_date)
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"load_as_of degraded for {source}/{key}: {exc}")
            return None

    def list_vintages(self, key: str, source: str = "misc") -> list[str]:
        """List the fetch_dates (ISO ``YYYY-MM-DD``) of stored vintages, oldest first.

        Empty list when no vintages exist. Never raises.
        """
        try:
            vdir = self._vintage_dir(source, key)
            if not vdir.exists():
                return []
            dates = sorted(f.stem for f in vdir.glob("*.parquet"))
            return dates
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"list_vintages degraded for {source}/{key}: {exc}")
            return []

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
        """Delete all cache entries across all sources, vintage store included.

        Returns the count of TTL-cache entries removed (the bitemporal vintages
        are cleared too, but NOT counted — preserving the pre-R110 return value).
        To clear vintages alone, use :meth:`invalidate_all_vintages`.
        """
        count = 0
        for f in self.cache_dir.rglob("*.parquet"):
            if _VINTAGE_DIRNAME in f.parts:
                continue  # counted/cleared separately below
            f.unlink()
            count += 1
        logger.info(f"Invalidated all {count} cache entries")
        # Also clear the vintage store so the cache tree is fully emptied, but
        # don't fold it into the TTL-entry count (back-compat with callers/tests
        # that expect the number of refreshable entries).
        self.invalidate_all_vintages()
        return count

    def invalidate_all_vintages(self) -> int:
        """Delete every stored vintage across all sources. Returns count deleted.

        Separate from :meth:`invalidate_all` so the point-in-time history is
        never wiped as a side effect of a routine TTL-cache refresh.
        """
        vroot = self.cache_dir / _VINTAGE_DIRNAME
        if not vroot.exists():
            return 0
        count = 0
        for f in vroot.rglob("*.parquet"):
            f.unlink()
            count += 1
        logger.info(f"Invalidated all {count} vintage entries")
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
            if _VINTAGE_DIRNAME in f.parts:
                continue  # the vintage store is not a TTL-cache entry
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

    # ------------------------------------------------------------------
    # Bitemporal point-in-time store (R110) — internals
    # ------------------------------------------------------------------

    def _vintage_dir(self, source: str, key: str) -> Path:
        """Per-key vintage directory: cache/vintages/{source}/{slug}/.

        The directory carries the slugified key, but ``slug`` truncates and so
        could collide for two very-long keys. A short hash of the full key is
        appended to disambiguate while keeping the path filesystem-safe.
        """
        slug = self._slugify(key)
        digest = hashlib.sha1(str(key).encode("utf-8", "ignore")).hexdigest()[:8]
        return self.cache_dir / _VINTAGE_DIRNAME / source / f"{slug}-{digest}"

    @staticmethod
    def _today_iso() -> str:
        """Today's knowledge-date (UTC) as ``YYYY-MM-DD`` for vintage stamping."""
        from datetime import timezone
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _coerce_date_iso(value: str | date | datetime) -> str:
        """Normalise a date-ish value to an ``YYYY-MM-DD`` string for comparison.

        Accepts ISO strings (``2024-01-15`` or full datetimes — truncated to the
        date), ``datetime.date`` and ``datetime.datetime``.
        """
        if isinstance(value, datetime):
            return value.date().isoformat()
        if isinstance(value, date):
            return value.isoformat()
        # String — keep only the leading date portion so a full ISO timestamp
        # ("2024-01-15T12:00:00Z") compares correctly against "YYYY-MM-DD" stems.
        return str(value).strip()[:10]

    def _write_vintage(self, source: str, key: str, df: pd.DataFrame) -> None:
        """Persist ``df`` as today's knowledge-date vintage. Best-effort, never raises.

        One file per fetch_date; a second fetch on the same calendar day
        overwrites that day's vintage (the latest-known value for that day).
        Retention is enforced afterwards.
        """
        try:
            vdir = self._vintage_dir(source, key)
            vdir.mkdir(parents=True, exist_ok=True)
            vpath = vdir / f"{self._today_iso()}.parquet"
            df.to_parquet(vpath, index=True)
            logger.debug(f"Wrote vintage {source}/{key} @ {vpath.stem}")
            self._prune_vintages(vdir, source, key)
        except Exception as exc:  # noqa: BLE001 — a vintage write must never break a fetch
            logger.debug(f"Vintage write skipped for {source}/{key}: {exc}")

    def _prune_vintages(self, vdir: Path, source: str, key: str) -> None:
        """Keep only the newest ``vintage_retention`` vintages; log what's pruned."""
        try:
            files = sorted(vdir.glob("*.parquet"), key=lambda p: p.stem)
            excess = len(files) - self.vintage_retention
            if excess <= 0:
                return
            for old in files[:excess]:
                pruned_date = old.stem
                old.unlink()
                logger.info(
                    f"Pruned vintage {source}/{key} @ {pruned_date} "
                    f"(retention={self.vintage_retention})"
                )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"Vintage prune skipped for {source}/{key}: {exc}")

    def _load_vintage_as_of(
        self, source: str, key: str, as_of_date: str | date | datetime
    ) -> pd.DataFrame | None:
        """Return the latest vintage with fetch_date <= ``as_of_date``, or None.

        Point-in-time semantics: the data as it was KNOWN on that date. Returns
        ``None`` if no vintage was fetched on or before that date (no
        look-ahead, no live fallback). Never raises.
        """
        try:
            vdir = self._vintage_dir(source, key)
            if not vdir.exists():
                return None
            cutoff = self._coerce_date_iso(as_of_date)
            # Stems are ISO YYYY-MM-DD, so lexical sort == chronological sort.
            eligible = sorted(
                (f for f in vdir.glob("*.parquet") if f.stem <= cutoff),
                key=lambda p: p.stem,
            )
            if not eligible:
                return None
            return pd.read_parquet(eligible[-1])
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug(f"Vintage as-of read degraded for {source}/{key}: {exc}")
            return None
