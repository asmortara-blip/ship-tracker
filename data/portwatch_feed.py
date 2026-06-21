"""portwatch_feed.py — REAL chokepoint-transit telemetry from IMF PortWatch.

The Shipping Stress Index's congestion + chokepoint inputs have historically
been SYNTHETIC: ``data/ais_feed.py`` probes dead ``/api/v1/portcalls`` endpoints
and falls back to a seeded seasonal generator (``_synthetic_congestion``). This
module wires the REAL, key-free IMF PortWatch *Daily Chokepoints* ArcGIS feed —
daily vessel-transit counts and capacity for Suez, Panama, Bab-el-Mandeb, Hormuz,
Malacca and the other monitored chokepoints, with multi-year history.

It is built to the same OFFLINE-SAFE contract as ``data/gdelt_feed.py``:

  * an injectable ``http_get`` (so tests never touch the network),
  * a short network timeout + a TTL JSON cache sidecar,
  * a best-effort provenance stamp that never raises,
  * and — the load-bearing honesty rule — it NEVER DOWNGRADES on silence: a
    network failure, a non-200, or a structurally-bad 200 body returns an
    ``"unavailable"`` result with zero rows so the caller keeps its existing
    (synthetic) baseline. Absence of data is never turned into a fake signal,
    and a real fetch that genuinely returns zero rows is ``"empty"`` (distinct
    from ``"unavailable"``), never cached as if real.

Nothing in the SSI consumes this yet — it lands the verified real source + its
integrity contract first; wiring it into the congestion/chokepoint axes (never
-downgrade, synthetic kept as a labelled fallback) is a separate, deliberate step.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
from loguru import logger

from data.quality import DataSource

# IMF PortWatch "Daily Chokepoints Data" ArcGIS FeatureServer (key-free, public).
_ARCGIS_BASE = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
_REQUEST_TIMEOUT = 15            # seconds — per-query network timeout
_DEFAULT_RECORD_COUNT = 2000     # most-recent rows (≈ months across all chokepoints)
_CACHE_DIR = Path("cache") / "portwatch"


@dataclass(frozen=True)
class ChokepointTransit:
    """One chokepoint's transit activity on one day (all fields REAL)."""

    chokepoint_id: str           # PortWatch portid, e.g. "chokepoint1"
    name: str                    # "Suez Canal", "Panama Canal", …
    date: str                    # ISO date string "YYYY-MM-DD"
    n_total: float               # total vessel transits that day (the disruption signal)
    capacity: float              # PortWatch total CARGO capacity transiting (DWT-scale,
                                 # NOT a vessel-count denominator — verified on live data:
                                 # ~1.4e6 vs n_total ~39, so n_total/capacity is NOT a
                                 # utilization ratio. Kept raw; never divided by n_total.)
    n_container: float = 0.0
    n_tanker: float = 0.0
    n_dry_bulk: float = 0.0

    def to_dict(self) -> dict:
        return {
            "chokepoint_id": self.chokepoint_id, "name": self.name,
            "date": self.date, "n_total": self.n_total, "capacity": self.capacity,
            "n_container": self.n_container, "n_tanker": self.n_tanker,
            "n_dry_bulk": self.n_dry_bulk,
        }


@dataclass(frozen=True)
class PortWatchTransits:
    """Result of a chokepoint-transit fetch + its honest provenance."""

    rows: list                   # list[ChokepointTransit], newest-first
    basis: str                   # "real" | "empty" | "unavailable"
    latest_date: str             # max date seen ("" when no rows)
    source: DataSource

    @property
    def is_real(self) -> bool:
        return self.basis == "real" and bool(self.rows)


# ── Provenance stamp (best-effort; never raises) ──────────────────────────────

def _stamp(kind: str, row_count: int) -> None:
    """Best-effort per-fetch provenance stamp (mirrors gdelt_feed). Never raises."""
    try:
        from state.fetch_ledger import record_fetch
        quality = ("GOOD" if kind == "live"
                   else "UNKNOWN" if kind in ("empty", "failed") else "DEMO")
        record_fetch("portwatch", "daily_chokepoints", kind,
                     row_count=int(row_count), quality=quality)
    except Exception:  # pragma: no cover - defensive
        pass


# ── TTL cache (JSON sidecar, mirrors gdelt_feed) ──────────────────────────────

def _cache_path(key: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in key)[:120]
    return _CACHE_DIR / f"{safe}.json"


def _read_cache(key: str, ttl_hours: float):
    path = _cache_path(key)
    if not path.exists():
        return None
    try:
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if age_hours > ttl_hours:
            return None
        rows = json.loads(path.read_text())
        return [ChokepointTransit(**r) for r in rows]
    except Exception as exc:
        logger.debug(f"portwatch_feed: cache read failed ({key}): {exc}")
        return None


def _write_cache(key: str, rows: list) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(
            json.dumps([r.to_dict() for r in rows], indent=2)
        )
    except Exception as exc:  # pragma: no cover - read-only FS guard
        logger.debug(f"portwatch_feed: cache write failed ({key}): {exc}")


# ── Parse ─────────────────────────────────────────────────────────────────────

def _num(value, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _iso_date(attrs: dict) -> str:
    """ISO date for a feature. PortWatch returns a 'date' string; fall back to
    composing it from year/month/day, or an epoch-ms integer if ever present."""
    raw = attrs.get("date")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()[:10]
    if isinstance(raw, (int, float)) and raw > 0:
        # ArcGIS sometimes serves epoch milliseconds.
        try:
            t = time.gmtime(float(raw) / 1000.0)
            return f"{t.tm_year:04d}-{t.tm_mon:02d}-{t.tm_mday:02d}"
        except Exception:
            pass
    y, m, d = attrs.get("year"), attrs.get("month"), attrs.get("day")
    if y and m and d:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    return ""


def _parse_features(features: list) -> list:
    rows: list = []
    for feat in features or []:
        attrs = (feat or {}).get("attributes") or {}
        cid = str(attrs.get("portid") or "").strip()
        date = _iso_date(attrs)
        if not cid or not date:
            continue  # an unidentifiable row is dropped, never guessed
        rows.append(ChokepointTransit(
            chokepoint_id=cid,
            name=str(attrs.get("portname") or cid),
            date=date,
            n_total=_num(attrs.get("n_total")),
            capacity=_num(attrs.get("capacity")),
            n_container=_num(attrs.get("n_container")),
            n_tanker=_num(attrs.get("n_tanker")),
            n_dry_bulk=_num(attrs.get("n_dry_bulk")),
        ))
    return rows


# ── Network fetch (offline-safe, injectable) ──────────────────────────────────

def fetch_chokepoint_transits(
    *,
    record_count: int = _DEFAULT_RECORD_COUNT,
    cache_ttl_hours: float = 6.0,
    http_get=None,
) -> PortWatchTransits:
    """Fetch recent daily chokepoint transits from IMF PortWatch. OFFLINE-SAFE.

    Parameters
    ----------
    record_count
        Most-recent rows to request (``orderByFields=date DESC``).
    cache_ttl_hours
        Serve a cached result younger than this instead of hitting the network.
    http_get
        Injectable ``(url, *, headers, timeout) -> response`` for OFFLINE tests.
        Defaults to ``requests.get``.

    Returns
    -------
    PortWatchTransits
        ``basis="real"`` (≥1 row from a real JSON fetch), ``"empty"`` (a real
        fetch that genuinely returned zero rows), or ``"unavailable"`` (network
        failure / non-200 / structurally-bad body). The ``"unavailable"`` case
        carries zero rows so the caller keeps its baseline — silence is NEVER
        turned into a signal. Never raises.
    """
    getter = http_get or requests.get
    cache_key = f"chokepoints_{int(record_count)}"

    cached = _read_cache(cache_key, cache_ttl_hours)
    if cached is not None:
        latest = max((r.date for r in cached), default="")
        return PortWatchTransits(
            rows=cached, basis=("real" if cached else "empty"), latest_date=latest,
            source=DataSource.cached("IMF PortWatch (chokepoint transits)",
                                     age_hours=0.0, url=_ARCGIS_BASE),
        )

    params = {
        "where": "1=1",
        "outFields": ("date,year,month,day,portid,portname,"
                      "n_total,n_container,n_tanker,n_dry_bulk,capacity"),
        "orderByFields": "date DESC",
        "resultRecordCount": int(record_count),
        "f": "json",
    }
    unavailable = PortWatchTransits(
        rows=[], basis="unavailable", latest_date="",
        source=DataSource.modeled(
            "IMF PortWatch (unavailable — caller keeps baseline)",
            notes="Network failure / non-200 / non-JSON body; no real transits.",
        ),
    )

    try:
        resp = getter(_ARCGIS_BASE, params=params, timeout=_REQUEST_TIMEOUT)
    except Exception as exc:
        logger.debug(f"portwatch_feed: request failed: {exc}")
        _stamp("failed", 0)
        return unavailable

    status = getattr(resp, "status_code", 200)
    if status != 200:
        logger.debug(f"portwatch_feed: non-200 ({status})")
        _stamp("failed", 0)
        return unavailable

    # A structurally-bad 200 (HTML error page, rate-limit text) is a FAILURE,
    # never an honest "empty", and is NEVER cached.
    try:
        payload = resp.json()
    except Exception:
        text = (getattr(resp, "text", "") or "").strip()
        if text[:1] in ("{", "["):
            try:
                payload = json.loads(text)
            except Exception:
                logger.debug("portwatch_feed: unparseable JSON body — failure")
                _stamp("failed", 0)
                return unavailable
        else:
            logger.debug(f"portwatch_feed: non-JSON 200 body ({text[:60]!r}) — failure")
            _stamp("failed", 0)
            return unavailable

    # ArcGIS signals query errors inside a 200 JSON body via an "error" object.
    if isinstance(payload, dict) and payload.get("error"):
        logger.debug(f"portwatch_feed: ArcGIS error payload: {payload.get('error')}")
        _stamp("failed", 0)
        return unavailable

    rows = _parse_features((payload or {}).get("features") or [])
    if not rows:
        _stamp("empty", 0)
        return PortWatchTransits(
            rows=[], basis="empty", latest_date="",
            source=DataSource.live("IMF PortWatch (chokepoint transits)",
                                   url=_ARCGIS_BASE, notes="Real fetch, zero rows."),
        )

    _write_cache(cache_key, rows)
    _stamp("live", len(rows))
    latest = max((r.date for r in rows), default="")
    return PortWatchTransits(
        rows=rows, basis="real", latest_date=latest,
        source=DataSource.live("IMF PortWatch (chokepoint transits)",
                               url=_ARCGIS_BASE,
                               notes=f"{len(rows)} real daily transit rows."),
    )


# ── Pure helpers (no network) ─────────────────────────────────────────────────

def latest_transits(rows) -> dict:
    """Most-recent daily transit count (n_total) per chokepoint. Pure; safe on
    any ``rows`` list. Raw real values — no fabricated denominator."""
    by_cp: dict[str, ChokepointTransit] = {}
    for r in rows or []:
        prev = by_cp.get(r.chokepoint_id)
        if prev is None or r.date > prev.date:
            by_cp[r.chokepoint_id] = r
    return {cid: r.n_total for cid, r in by_cp.items()}


def transit_drop_ratio(rows, chokepoint_id: str, *, recent: int = 7,
                       baseline: int = 90) -> float | None:
    """Real disruption signal: 1 − (mean recent transits / mean baseline transits)
    for one chokepoint, in (−∞, 1]. ~0 = normal; →1 = collapse (e.g. Suez during
    the 2024 Red Sea crisis); negative = above-baseline throughput.

    Returns ``None`` when there isn't enough real history (never a fabricated 0).
    """
    series = sorted((r for r in (rows or []) if r.chokepoint_id == chokepoint_id),
                    key=lambda r: r.date)
    if len(series) < max(recent + 1, 2):
        return None
    totals = [r.n_total for r in series]
    recent_mean = sum(totals[-recent:]) / float(min(recent, len(totals)))
    base_slice = totals[-baseline:] if len(totals) >= baseline else totals
    base_mean = sum(base_slice) / float(len(base_slice))
    if base_mean <= 0:
        return None
    return round(1.0 - (recent_mean / base_mean), 6)


__all__ = [
    "ChokepointTransit",
    "PortWatchTransits",
    "fetch_chokepoint_transits",
    "latest_transits",
    "transit_drop_ratio",
]
