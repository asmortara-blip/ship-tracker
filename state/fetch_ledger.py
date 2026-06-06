"""Per-fetch provenance ledger (schema v33; rec R003/R097).

Liveness pings (``engine.source_health``) tell you whether a feed was reachable
on a schedule; they do NOT tell you whether a SPECIFIC fetch returned real,
cached, or synthetic data. So no signal can prove which feeds were real when it
issued. This ledger fixes that: every feed fetch stamps one row — source, key,
``kind`` (live / cache / synthetic / empty), quality, row count, a short content
hash, and the data's as-of — so an auditor can answer *"did signal X run on real
or synthetic input on date Y?"*

``record_fetch`` is best-effort and NEVER raises: a provenance write must never
break a data fetch. The natural choke point is ``cache_manager.get_or_fetch``
(every cached feed flows through it), which records ``cache`` on a hit and
``live``/``empty`` on a miss.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

VALID_KINDS = ("live", "cache", "synthetic", "empty", "failed")

# Recording is ON in production (app + scheduler). The test suite disables it
# globally via a conftest autouse fixture so a fetch in a non-DB-isolated test
# never writes a provenance row to the real DB; the provenance tests opt back in.
RECORDING_ENABLED: bool = True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def cheap_content_hash(df) -> str:
    """A cheap, stable lineage hash of a DataFrame (shape + head/tail), '' on miss.

    Not a cryptographic guarantee — enough to tell two fetches apart and to spot
    a feed that returned the identical frame twice. Hashes only the corners so a
    100k-row frame costs the same as a 10-row one.
    """
    try:
        import pandas as pd
        if not isinstance(df, pd.DataFrame) or df.empty:
            return ""
        n = len(df)
        head = df.head(3).to_csv(index=False)
        tail = df.tail(3).to_csv(index=False)
        payload = f"{n}|{list(df.columns)}|{head}|{tail}".encode("utf-8", "ignore")
        return hashlib.sha1(payload).hexdigest()[:16]
    except Exception:
        return ""


def record_fetch(
    source: str,
    cache_key: str,
    kind: str,
    *,
    quality: Optional[str] = None,
    row_count: int = 0,
    byte_hash: Optional[str] = None,
    as_of: Optional[str] = None,
    fetched_at: Optional[str] = None,
) -> bool:
    """Persist one provenance row. Best-effort — returns False, never raises."""
    if not RECORDING_ENABLED:
        return False
    try:
        from auth.ids import opaque_id
        from state.db import get_connection

        k = str(kind or "").strip().lower()
        if k not in VALID_KINDS:
            k = "synthetic" if k.startswith("synth") else (k or "empty")
        conn = get_connection()
        conn.execute(
            "INSERT INTO data_fetches "
            "(fetch_id, source, cache_key, kind, quality, row_count, "
            " byte_hash, as_of, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                opaque_id(), str(source or ""), str(cache_key or ""), k,
                (str(quality) if quality is not None else None),
                int(row_count or 0),
                (str(byte_hash) if byte_hash else None),
                (str(as_of) if as_of else None),
                fetched_at or _now(),
            ),
        )
        return True
    except Exception as exc:  # noqa: BLE001 — provenance must never break a fetch
        logger.debug(f"fetch_ledger.record_fetch: skipped ({exc})")
        return False


def recent_fetches(
    *, source: Optional[str] = None, since: Optional[str] = None, limit: int = 500,
) -> list[dict]:
    """Provenance rows, newest first. Filter by source and/or a since-ISO."""
    try:
        from state.db import get_connection

        clauses, params = ["1=1"], []
        if source:
            clauses.append("source = ?")
            params.append(str(source))
        if since:
            clauses.append("fetched_at >= ?")
            params.append(str(since))
        params.append(int(limit))
        rows = get_connection().execute(
            "SELECT fetch_id, source, cache_key, kind, quality, row_count, "
            "byte_hash, as_of, fetched_at FROM data_fetches WHERE "
            + " AND ".join(clauses)
            + " ORDER BY fetched_at DESC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.debug(f"fetch_ledger.recent_fetches: read failed ({exc})")
        return []


def fetch_realness_summary(*, since: Optional[str] = None) -> dict:
    """Aggregate realness per source + overall.

    Returns ``{"n": N, "by_kind": {...}, "realness_rate": x, "freshness_rate": y,
    "synthetic_rate": z, "by_source": {src: {n, by_kind, ...}}}`` where realness
    = (live+cache)/total (real data, fresh or not), freshness = live/total, and
    synthetic = synthetic/total. NOTE: these rates do NOT partition to 1 — the
    ``empty`` (feed returned nothing) and ``failed`` (fetch raised) buckets are
    neither real nor synthetic, so ``realness_rate + synthetic_rate <= 1``.
    Empty ledger returns a zeroed, stable shape.
    """
    rows = recent_fetches(since=since, limit=100_000)
    empty = {"n": 0, "by_kind": {}, "realness_rate": 0.0,
             "freshness_rate": 0.0, "synthetic_rate": 0.0, "by_source": {}}
    if not rows:
        return empty

    def _agg(subset: list) -> dict:
        n = len(subset)
        by_kind: dict[str, int] = {}
        for r in subset:
            by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        live = by_kind.get("live", 0)
        cache = by_kind.get("cache", 0)
        synth = by_kind.get("synthetic", 0)
        return {
            "n": n, "by_kind": by_kind,
            "realness_rate": round((live + cache) / n, 4) if n else 0.0,
            "freshness_rate": round(live / n, 4) if n else 0.0,
            "synthetic_rate": round(synth / n, 4) if n else 0.0,
        }

    by_source: dict[str, dict] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    out = _agg(rows)
    out["by_source"] = {src: _agg(subset) for src, subset in sorted(by_source.items())}
    return out
