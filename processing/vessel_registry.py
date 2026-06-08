"""Vessel-particulars master keyed on IMO (R126) — MODELED, NOT a real registry.

Every modeled voyage (``data.voyage_dataset.Voyage``) carries a stable set of
vessel particulars — IMO, flag, owner, manager, build year, gross tonnage,
classification society and P&I club — that are **deterministically derived from
the vessel name** (``data.voyage_dataset.derive_vessel_particulars``). The same
vessel therefore resolves to the same particulars on every voyage and in every
process.

This module is the read side of that contract: a pure, deterministic master
that lets you resolve a voyage / vessel name / IMO to a single
``VesselParticulars`` record, build an IMO-keyed registry out of a fleet
(deduplicating the many-voyages-per-vessel relationship), and roll the registry
up by flag or by owner for fleet/owner/flag-level analysis.

HONESTY / PROVENANCE
--------------------
This is **synthetic, illustrative** data. The voyage dataset it reads from is
explicitly modeled (not a real AIS feed), and the particulars here are
deterministically *derived*, not observed. The IMO numbers are structurally
valid (7 digits with a correct check digit) but are **fabricated** — they do
not identify any real ship. Nothing in this module should be presented as live
registry data; the provenance is
``data.voyage_dataset.VESSEL_PARTICULARS_SOURCE`` (``DataKind.MODELED``).

Pure-data module: **no Streamlit imports.**
"""
from __future__ import annotations

from dataclasses import dataclass

from data.quality import DataSource
from data.voyage_dataset import (
    VESSEL_PARTICULARS_SOURCE,
    Voyage,
    build_voyage_fleet,
    derive_vessel_particulars,
)

# Re-export the provenance so consumers can attribute the master without
# reaching back into the dataset module. MODELED — never a live registry.
PARTICULARS_SOURCE: DataSource = VESSEL_PARTICULARS_SOURCE


@dataclass(frozen=True)
class VesselParticulars:
    """One vessel's MODELED, stable particulars record (keyed on IMO).

    Synthetic / illustrative — see the module docstring and
    ``PARTICULARS_SOURCE``. ``imo`` is structurally valid but fabricated.
    ``vessel_name`` is the modeled name the particulars were derived from;
    ``vessel_type`` is the type observed for it in the fleet (it only refines
    the gross-tonnage band and does not change the vessel's identity).
    """

    imo: str
    vessel_name: str
    vessel_type: str
    flag: str
    owner_id: str
    manager_id: str
    build_year: int
    gross_tonnage: int
    class_society: str
    p_and_i_club: str

    @property
    def is_synthetic(self) -> bool:
        """Always ``True`` — these particulars are modeled, never observed."""
        return True


def _particulars_from_fields(
    *,
    vessel_name: str,
    vessel_type: str,
    imo: str,
    flag: str,
    owner_id: str,
    manager_id: str,
    build_year: int,
    gross_tonnage: int,
    class_society: str,
    p_and_i_club: str,
) -> VesselParticulars:
    return VesselParticulars(
        imo=imo,
        vessel_name=vessel_name,
        vessel_type=vessel_type,
        flag=flag,
        owner_id=owner_id,
        manager_id=manager_id,
        build_year=build_year,
        gross_tonnage=gross_tonnage,
        class_society=class_society,
        p_and_i_club=p_and_i_club,
    )


def particulars_for_name(
    vessel_name: str, vessel_type: str = ""
) -> VesselParticulars:
    """Resolve a vessel NAME to its stable MODELED particulars.

    Pure + deterministic — the same name always returns the same record. This
    is the canonical derivation; ``resolve`` and ``build_registry`` are built
    on top of it so a voyage's particulars equal this record exactly.
    """
    p = derive_vessel_particulars(vessel_name, vessel_type)
    return _particulars_from_fields(
        vessel_name=vessel_name,
        vessel_type=vessel_type,
        imo=p["imo"],
        flag=p["flag"],
        owner_id=p["owner_id"],
        manager_id=p["manager_id"],
        build_year=p["build_year"],
        gross_tonnage=p["gross_tonnage"],
        class_society=p["class_society"],
        p_and_i_club=p["p_and_i_club"],
    )


def particulars_for_voyage(voyage: Voyage) -> VesselParticulars:
    """Read a voyage's already-stamped particulars into a record.

    Equivalent to ``particulars_for_name(voyage.vessel_name,
    voyage.vessel_type)`` by construction — ``build_voyage_fleet`` stamps each
    voyage with exactly that derivation — but reads the fields straight off the
    voyage so a registry built from a fleet matches that fleet byte-for-byte.
    """
    return _particulars_from_fields(
        vessel_name=voyage.vessel_name,
        vessel_type=voyage.vessel_type,
        imo=voyage.imo,
        flag=voyage.flag,
        owner_id=voyage.owner_id,
        manager_id=voyage.manager_id,
        build_year=voyage.build_year,
        gross_tonnage=voyage.gross_tonnage,
        class_society=voyage.class_society,
        p_and_i_club=voyage.p_and_i_club,
    )


def resolve(
    vessel_or_imo: object,
    registry: dict[str, VesselParticulars] | None = None,
) -> VesselParticulars | None:
    """Resolve a voyage, vessel name, or IMO to its MODELED particulars.

    Accepts:

    * a ``Voyage`` — returns the record stamped onto that voyage;
    * an IMO string (7 digits) — looked up against ``registry`` (built from a
      fresh fleet if not supplied), so it only resolves IMOs that exist in the
      modeled fleet;
    * any other string — treated as a vessel NAME and derived directly (pure,
      no fleet needed).

    Returns ``None`` for an unknown IMO. Deterministic for every input.
    """
    if isinstance(vessel_or_imo, Voyage):
        return particulars_for_voyage(vessel_or_imo)

    if not isinstance(vessel_or_imo, str):
        return None

    token = vessel_or_imo.strip()
    if not token:
        return None

    # A 7-digit token is an IMO → look it up in the registry.
    if token.isdigit() and len(token) == 7:
        if registry is None:
            registry = build_registry()
        return registry.get(token)

    # Otherwise treat it as a vessel name and derive directly.
    return particulars_for_name(token)


def build_registry(
    voyages: list[Voyage] | None = None,
) -> dict[str, VesselParticulars]:
    """Build an IMO-keyed master from a fleet, deduplicating by IMO.

    A fleet has many voyages per vessel; the registry collapses them to one
    ``VesselParticulars`` per IMO. Because the particulars are stable per
    vessel, every voyage of a given vessel produces the identical record, so
    the dedup is loss-free. When ``voyages`` is not supplied a fresh
    deterministic fleet is built.

    Returns ``{imo: VesselParticulars}``.
    """
    if voyages is None:
        voyages = build_voyage_fleet()

    registry: dict[str, VesselParticulars] = {}
    for v in voyages:
        if not v.imo:
            continue
        # First voyage for this IMO wins; subsequent ones are identical by
        # construction (stable per vessel), so this is a true dedup.
        registry.setdefault(v.imo, particulars_for_voyage(v))
    return registry


def by_flag(
    registry: dict[str, VesselParticulars],
) -> dict[str, list[VesselParticulars]]:
    """Group the registry's vessels by flag state.

    Returns ``{flag: [VesselParticulars, ...]}``. Useful for flag-level
    exposure / concentration analysis over the modeled fleet.
    """
    out: dict[str, list[VesselParticulars]] = {}
    for p in registry.values():
        out.setdefault(p.flag, []).append(p)
    return out


def by_owner(
    registry: dict[str, VesselParticulars],
) -> dict[str, list[VesselParticulars]]:
    """Group the registry's vessels by beneficial-owner group id.

    Returns ``{owner_id: [VesselParticulars, ...]}``. Useful for owner-level
    fleet sizing / concentration analysis over the modeled fleet.
    """
    out: dict[str, list[VesselParticulars]] = {}
    for p in registry.values():
        out.setdefault(p.owner_id, []).append(p)
    return out


def flag_counts(registry: dict[str, VesselParticulars]) -> dict[str, int]:
    """Vessel count per flag state — a headline aggregation for the UI."""
    return {flag: len(vessels) for flag, vessels in by_flag(registry).items()}


def owner_counts(registry: dict[str, VesselParticulars]) -> dict[str, int]:
    """Vessel count per owner group — a headline aggregation for the UI."""
    return {owner: len(vessels) for owner, vessels in by_owner(registry).items()}
