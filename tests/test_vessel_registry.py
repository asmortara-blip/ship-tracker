"""Pure-function tests for processing.vessel_registry (R126).

The vessel-particulars master is SYNTHETIC and DETERMINISTIC: every voyage
resolves to a stable particulars record, the same vessel/IMO always resolves
to the same record, the registry dedups many-voyages-per-vessel down to one
record per IMO, and the modeled fields stay in plausible bands. These tests
pin that contract without importing Streamlit or touching any live feed, and
assert the synthetic/illustrative provenance is present.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from data.aisstream_feed import _FLAGS
from data.quality import DataKind
from data.voyage_dataset import (
    Voyage,
    build_voyage_fleet,
    derive_vessel_particulars,
)
from processing.vessel_registry import (
    PARTICULARS_SOURCE,
    VesselParticulars,
    build_registry,
    by_flag,
    by_owner,
    flag_counts,
    owner_counts,
    particulars_for_name,
    particulars_for_voyage,
    resolve,
)

_FLAG_SET = set(_FLAGS)


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fleet() -> list[Voyage]:
    return build_voyage_fleet(seed=20260518)


@pytest.fixture(scope="module")
def registry(fleet: list[Voyage]) -> dict[str, VesselParticulars]:
    return build_registry(fleet)


# ── Provenance / honesty ─────────────────────────────────────────────────────


def test_provenance_is_modeled_not_live() -> None:
    """The master must declare MODELED provenance — never a real registry."""
    assert PARTICULARS_SOURCE.kind == DataKind.MODELED
    blob = f"{PARTICULARS_SOURCE.name} {PARTICULARS_SOURCE.notes}".lower()
    # Synthetic/illustrative labeling is explicit.
    assert "synthetic" in blob or "illustrative" in blob
    assert "modeled" in PARTICULARS_SOURCE.name.lower()
    # The record itself self-identifies as synthetic.
    p = particulars_for_name("MSC OSCAR", "Container")
    assert p.is_synthetic is True


def test_module_docstring_flags_synthetic() -> None:
    import processing.vessel_registry as vr

    doc = (vr.__doc__ or "").lower()
    assert "synthetic" in doc or "illustrative" in doc
    assert "not a real registry" in doc


# ── Every voyage resolves; particulars == registry record ────────────────────


def test_every_voyage_resolves_to_particulars(fleet: list[Voyage]) -> None:
    for v in fleet:
        p = resolve(v)
        assert isinstance(p, VesselParticulars)
        assert p.imo == v.imo


def test_voyage_particulars_match_registry_record(
    fleet: list[Voyage], registry: dict[str, VesselParticulars]
) -> None:
    """A voyage's particulars equal the registry record for its IMO exactly."""
    for v in fleet:
        rec = registry[v.imo]
        assert rec == particulars_for_voyage(v)
        # Field-by-field agreement with the voyage's stamped fields.
        assert rec.flag == v.flag
        assert rec.owner_id == v.owner_id
        assert rec.manager_id == v.manager_id
        assert rec.build_year == v.build_year
        assert rec.gross_tonnage == v.gross_tonnage
        assert rec.class_society == v.class_society
        assert rec.p_and_i_club == v.p_and_i_club


# ── Determinism / stability ──────────────────────────────────────────────────


def test_same_name_always_same_particulars() -> None:
    """The same vessel name resolves to the same record across calls."""
    a = particulars_for_name("EVER GIVEN", "Container")
    b = particulars_for_name("EVER GIVEN", "Container")
    assert a == b


def test_derivation_is_process_stable() -> None:
    """Derivation is not salted by per-process hash() — fixed IMO per name."""
    p1 = derive_vessel_particulars("COAL HUNTER", "Bulk Carrier")
    p2 = derive_vessel_particulars("COAL HUNTER", "Bulk Carrier")
    assert p1 == p2
    # And matches the registry-facing helper.
    assert particulars_for_name("COAL HUNTER", "Bulk Carrier").imo == p1["imo"]


def test_same_vessel_same_imo_across_voyages(fleet: list[Voyage]) -> None:
    """Every voyage of a given vessel name carries the identical IMO."""
    by_name: dict[str, set[str]] = {}
    for v in fleet:
        by_name.setdefault(v.vessel_name, set()).add(v.imo)
    for name, imos in by_name.items():
        assert len(imos) == 1, f"{name} resolved to multiple IMOs {imos}"


def test_resolve_by_imo_matches_resolve_by_name(
    registry: dict[str, VesselParticulars],
) -> None:
    """Resolving by IMO returns the same record as resolving by name."""
    rec = next(iter(registry.values()))
    by_imo = resolve(rec.imo, registry=registry)
    by_name = particulars_for_name(rec.vessel_name, rec.vessel_type)
    assert by_imo == rec
    assert by_name.imo == rec.imo


def test_resolve_unknown_imo_returns_none(
    registry: dict[str, VesselParticulars],
) -> None:
    assert resolve("9999999", registry=registry) is None  # 7-digit, absent
    assert resolve("", registry=registry) is None
    assert resolve(None) is None  # type: ignore[arg-type]


# ── build_registry dedups by IMO ─────────────────────────────────────────────


def test_build_registry_dedups_by_imo(
    fleet: list[Voyage], registry: dict[str, VesselParticulars]
) -> None:
    distinct_imos = {v.imo for v in fleet}
    assert set(registry.keys()) == distinct_imos
    # Many voyages, fewer (or equal) distinct vessels.
    assert len(registry) <= len(fleet)
    assert len(registry) == len(distinct_imos)
    for imo, rec in registry.items():
        assert rec.imo == imo


def test_build_registry_default_fleet_runs() -> None:
    reg = build_registry()
    assert isinstance(reg, dict)
    assert len(reg) > 0
    assert all(isinstance(p, VesselParticulars) for p in reg.values())


# ── Field bounds / domains ───────────────────────────────────────────────────


def test_flag_in_known_set(registry: dict[str, VesselParticulars]) -> None:
    for p in registry.values():
        assert p.flag in _FLAG_SET


def test_imo_is_structurally_valid(registry: dict[str, VesselParticulars]) -> None:
    """7 digits with a correct IMO check digit (synthetic but well-formed)."""
    weights = (7, 6, 5, 4, 3, 2)
    for p in registry.values():
        assert p.imo.isdigit() and len(p.imo) == 7
        digits = [int(c) for c in p.imo]
        check = sum(w * d for w, d in zip(weights, digits[:6])) % 10
        assert check == digits[6], f"bad IMO check digit on {p.imo}"


def test_build_year_and_tonnage_plausible(
    registry: dict[str, VesselParticulars],
) -> None:
    for p in registry.values():
        assert 1990 <= p.build_year <= _dt.date.today().year
        assert 5_000 <= p.gross_tonnage <= 400_000
        assert p.class_society
        assert p.p_and_i_club
        assert p.owner_id
        assert p.manager_id


# ── by_flag / by_owner aggregations ──────────────────────────────────────────


def test_by_flag_partitions_registry(
    registry: dict[str, VesselParticulars],
) -> None:
    grouped = by_flag(registry)
    # Every vessel lands in exactly one flag bucket; buckets sum to the total.
    assert sum(len(v) for v in grouped.values()) == len(registry)
    for flag, vessels in grouped.items():
        assert all(p.flag == flag for p in vessels)
        assert flag in _FLAG_SET
    assert flag_counts(registry) == {f: len(v) for f, v in grouped.items()}


def test_by_owner_partitions_registry(
    registry: dict[str, VesselParticulars],
) -> None:
    grouped = by_owner(registry)
    assert sum(len(v) for v in grouped.values()) == len(registry)
    for owner, vessels in grouped.items():
        assert all(p.owner_id == owner for p in vessels)
    assert owner_counts(registry) == {o: len(v) for o, v in grouped.items()}


# ── Bare Voyage construction still works (additive defaults) ──────────────────


def test_bare_voyage_constructs_with_defaults() -> None:
    """The new particulars fields are additive with defaults."""
    v = Voyage(
        voyage_id="VY-X",
        vessel_name="TEST",
        mmsi="200000001",
        vessel_type="Container",
        route_id="r1",
        origin_locode="AAA",
        dest_locode="BBB",
        departed_at=_dt.date(2026, 1, 1),
        nominal_transit_days=10,
        eta_nominal=_dt.date(2026, 1, 11),
        eta_adjusted=_dt.date(2026, 1, 12),
        progress_pct=0.5,
        current_lat=0.0,
        current_lon=0.0,
        status="On Schedule",
        delay_days=0.0,
        speed_kts=18.0,
        congestion_at_dest=0.2,
        weather_delay_days=0.0,
        chokepoints_on_route=[],
    )
    # Defaults present and inert (no particulars derived for a hand-built voyage).
    assert v.imo == ""
    assert v.flag == ""
    assert v.owner_id == ""
    assert v.build_year == 0
    assert v.gross_tonnage == 0
    # resolve() on such a voyage yields the default-valued record (no crash).
    p = resolve(v)
    assert isinstance(p, VesselParticulars)
    assert p.imo == ""
