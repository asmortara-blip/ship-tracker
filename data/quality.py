"""Data quality primitives — DataSource + DataSeries wrappers.

Every feed in ``data/*_feed.py`` should ultimately wrap its return values in
``DataSeries`` so the UI can surface provenance and freshness next to every
figure. During Phase 1 rollout, feeds expose both the legacy plain-DataFrame
API and an additive ``*_series(...)`` variant that returns ``DataSeries`` —
callers migrate at their own pace.

Design:

* ``DataSource`` — immutable metadata about where a bit of data came from,
  when it was produced, and how much we trust it.
* ``DataSeries`` — ``DataSource`` + a pandas object (Series, DataFrame, or
  dict). The wrapper is intentionally thin; consumers can reach the raw
  data via ``.data`` and render a provenance pill via ``quality_pill(...)``.
* ``DataQuality`` / ``DataKind`` — string-enum-ish constants used as the
  ``quality`` and ``kind`` fields. Using plain strings (not Enum) keeps
  them JSON- and parquet-serializable without ceremony.

The helpers defined here are UI-agnostic. The Streamlit-specific pill
renderer lives next to the rest of the design system in ``ui/styles.py``
(``live_data_badge``) and reads the fields defined here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


# ── String constants (stable, human-readable) ───────────────────────────────

class DataKind:
    """Where the data came from / how we got it."""
    LIVE     = "live"        # fresh API call within SLA
    CACHED   = "cached"      # served from parquet cache, still within TTL
    SCRAPED  = "scraped"     # HTML scrape (fragile — label honestly)
    MODELED  = "modeled"     # computed / interpolated / forward curve
    DEMO     = "demo"        # synthetic, DEMO_MODE-gated
    MANUAL   = "manual"      # hand-entered fixture or reference series


class DataQuality:
    """Qualitative trust tier for the data point."""
    GOOD        = "good"         # SLA met, source authoritative
    STALE       = "stale"        # past SLA but still usable
    UNOFFICIAL  = "unofficial"   # scraped/unlicensed — treat as indicative
    MODELED     = "modeled"      # derived, not directly observed
    DEMO        = "demo"         # placeholder, do not trust
    UNKNOWN     = "unknown"


_QUALITY_COLORS: dict[str, str] = {
    DataQuality.GOOD:       "#2e9e6e",  # C_HIGH — green
    DataQuality.STALE:      "#c9962b",  # C_MOD  — amber
    DataQuality.UNOFFICIAL: "#c9962b",  # C_MOD  — amber
    DataQuality.MODELED:    "#7c6eaf",  # C_CONV — purple
    DataQuality.DEMO:       "#c0392b",  # C_LOW  — red
    DataQuality.UNKNOWN:    "#6b6760",  # C_TEXT3 — grey
}


def quality_color(quality: str) -> str:
    """Return the palette hex for a given quality string."""
    return _QUALITY_COLORS.get(quality, _QUALITY_COLORS[DataQuality.UNKNOWN])


# ── DataSource ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class DataSource:
    """Provenance record for a chunk of data.

    Fields:
        name:      Human label, e.g. "FRED", "Baltic Exchange", "Yahoo Finance".
        kind:      One of ``DataKind.*`` — how we obtained it.
        url:       Public URL (for attribution footer). Optional.
        as_of:     When the underlying observation was produced (UTC).
                   Prefer the *observation* timestamp, not fetch time.
        quality:   One of ``DataQuality.*``.
        sla_hours: Maximum acceptable staleness. ``None`` means "no SLA".
        notes:     Free-form, shown in the tooltip.
    """
    name: str
    kind: str = DataKind.LIVE
    url: str = ""
    as_of: datetime | None = None
    quality: str = DataQuality.GOOD
    sla_hours: float | None = None
    notes: str = ""

    # Convenience constructors ------------------------------------------------

    @classmethod
    def live(cls, name: str, *, url: str = "", sla_hours: float | None = None, notes: str = "") -> "DataSource":
        return cls(name=name, kind=DataKind.LIVE, url=url, as_of=_utcnow(),
                   quality=DataQuality.GOOD, sla_hours=sla_hours, notes=notes)

    @classmethod
    def cached(cls, name: str, age_hours: float, *, url: str = "",
               sla_hours: float | None = None, notes: str = "") -> "DataSource":
        quality = DataQuality.STALE if (sla_hours is not None and age_hours > sla_hours) else DataQuality.GOOD
        return cls(name=name, kind=DataKind.CACHED, url=url,
                   as_of=_utcnow(), quality=quality,
                   sla_hours=sla_hours, notes=notes or f"age {age_hours:.1f}h")

    @classmethod
    def scraped(cls, name: str, *, url: str = "", notes: str = "") -> "DataSource":
        return cls(name=name, kind=DataKind.SCRAPED, url=url, as_of=_utcnow(),
                   quality=DataQuality.UNOFFICIAL, notes=notes)

    @classmethod
    def modeled(cls, name: str, *, notes: str = "") -> "DataSource":
        return cls(name=name, kind=DataKind.MODELED, as_of=_utcnow(),
                   quality=DataQuality.MODELED, notes=notes)

    @classmethod
    def demo(cls, name: str = "Synthetic") -> "DataSource":
        return cls(name=name, kind=DataKind.DEMO, as_of=_utcnow(),
                   quality=DataQuality.DEMO, notes="DEMO_MODE fallback")

    @classmethod
    def unknown(cls, name: str = "unknown") -> "DataSource":
        return cls(name=name, kind=DataKind.LIVE, as_of=None,
                   quality=DataQuality.UNKNOWN)

    # Derived properties ------------------------------------------------------

    @property
    def color(self) -> str:
        return quality_color(self.quality)

    @property
    def age_hours(self) -> float | None:
        if self.as_of is None:
            return None
        return (_utcnow() - self.as_of).total_seconds() / 3600.0

    @property
    def within_sla(self) -> bool:
        if self.sla_hours is None or self.as_of is None:
            return True
        age = self.age_hours
        return age is not None and age <= self.sla_hours

    def with_age(self, age_hours: float) -> "DataSource":
        """Return a copy where quality is downgraded if age exceeds SLA."""
        if self.sla_hours is None or age_hours <= self.sla_hours:
            return self
        return DataSource(
            name=self.name, kind=DataKind.CACHED, url=self.url,
            as_of=self.as_of, quality=DataQuality.STALE,
            sla_hours=self.sla_hours,
            notes=self.notes or f"age {age_hours:.1f}h > SLA {self.sla_hours:.1f}h",
        )

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.as_of is not None:
            d["as_of"] = self.as_of.isoformat()
        return d


# ── DataSeries wrapper ──────────────────────────────────────────────────────

@dataclass
class DataSeries:
    """Data payload + its provenance.

    ``data`` is intentionally typed as ``Any`` so this wrapper works for
    pandas Series, DataFrames, dicts-of-DataFrames (common for multi-series
    feeds like FRED), lists, or plain scalars. The UI only needs
    ``.source``; analytics code unwraps ``.data``.
    """
    data: Any
    source: DataSource
    meta: dict = field(default_factory=dict)

    def __bool__(self) -> bool:
        if self.data is None:
            return False
        # pandas objects define .empty
        empty = getattr(self.data, "empty", None)
        if isinstance(empty, bool):
            return not empty
        if hasattr(self.data, "__len__"):
            return len(self.data) > 0
        return True

    def unwrap(self) -> Any:
        """Return the underlying data, discarding provenance.

        Use during incremental migration when the call site hasn't been
        updated to read ``.data`` directly.
        """
        return self.data


# ── Helpers ─────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def wrap(data: Any, source: DataSource, **meta: Any) -> DataSeries:
    """Shorthand to wrap raw data with provenance."""
    return DataSeries(data=data, source=source, meta=dict(meta))


__all__ = [
    "DataKind",
    "DataQuality",
    "DataSource",
    "DataSeries",
    "quality_color",
    "wrap",
]
