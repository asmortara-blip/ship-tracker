"""
data/sanctions_feed.py
──────────────────────
Keyless, offline-safe OFAC SDN sanctions screening feed (R024).

WHAT THIS IS (read before trusting it)
    A live screen against the U.S. Treasury OFAC **Specially Designated
    Nationals (SDN) consolidated list**, which is FREE and KEYLESS. We fetch the
    flat ``SDN.CSV`` (no header, fixed columns), parse it into
    ``SanctionedEntity`` records (name, type, program, and — for vessels — the
    IMO number / flag / vessel type lifted from the record + its remarks), and
    expose:

      * ``fetch_ofac_sdn(...)``   — cache-backed, offline-safe parse → SanctionsList
      * ``screen_entity(...)``    — conservative exact-name / IMO match → ScreeningMatch
      * ``match_voyages(...)``    — flag modeled voyages whose IMO / vessel name / owner
                                    appears on the list
      * ``sanctions_rows(...)``   — adapter to the row shape the Compliance +
                                    Geopolitical tabs already render

HONESTY (the whole point — learn from R014, a "live" feed that was dead)
    * OFFLINE-SAFE: any network error / non-200 / structurally-bad body → an
      EMPTY ``SanctionsList`` stamped ``DataSource.demo`` (kind ``demo``). NEVER
      raises, NEVER blocks, NEVER fabricates entities.
    * The fetch is INJECTABLE (``http_get=None`` defaulting to ``requests.get``)
      so tests are network-free.
    * A parsed live list is stamped ``DataSource.live`` (REAL). A dark/failed
      fetch returns ``SanctionsList(entities=[], source=DataSource.demo(...))``
      — the caller then falls back to its own hardcoded MODELED rows, clearly
      labelled, and NEVER presents them as a live OFAC screen.
    * Provenance is also written to the per-fetch ledger (R003/R097), best-effort.

OFAC SDN.CSV FORMAT (keyless, no API key)
    URL : https://www.treasury.gov/ofac/downloads/sdn.csv
    Also: https://sanctionslist.ofac.treas.gov/Home/ConsolidatedList (the
          consolidated CSV/XML hub; the SDN.CSV above is the canonical flat file).
    It is a HEADER-LESS CSV. Fixed positional columns (OFAC data spec):
        0  ent_num     entity number (int)
        1  SDN_Name    name
        2  SDN_Type    "individual" | "vessel" | "aircraft" | "-0-" (entity/org)
        3  Program     sanctions program(s), e.g. "RUSSIA-EO14024", "IRAN", "SDGT"
        4  Title
        5  Call_Sign
        6  Vess_type
        7  Tonnage
        8  GRT
        9  Vess_flag
        10 Vess_owner
        11 Remarks     free text; vessel IMO appears here as "IMO 9176187"
    "-0-" is OFAC's null token across these columns. We treat it as empty.

Dependencies: requests, loguru (both already in the project).
"""
from __future__ import annotations

import csv
import io
import json
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

from data.quality import DataKind, DataQuality, DataSource


# ── Endpoint / constants ─────────────────────────────────────────────────────

# OFAC SDN consolidated list — PUBLIC, KEYLESS. The flat header-less CSV.
_OFAC_SDN_CSV_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
# Attribution hub (consolidated CSV/XML). Shown in the provenance pill URL.
_OFAC_CONSOLIDATED_URL = "https://sanctionslist.ofac.treas.gov/Home/ConsolidatedList"

_REQUEST_TIMEOUT = 20          # seconds — SDN.CSV is a few MB
_DEFAULT_TTL_HOURS = 24.0      # OFAC updates intra-day on designation events; 1d is fine

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv,text/plain,*/*",
}

# OFAC's null token — appears literally as "-0-" in empty CSV cells.
_OFAC_NULL = "-0-"

# Column indices in the header-less SDN.CSV (see module docstring).
_COL_ENT_NUM = 0
_COL_NAME = 1
_COL_TYPE = 2
_COL_PROGRAM = 3
_COL_CALL_SIGN = 5
_COL_VESS_TYPE = 6
_COL_VESS_FLAG = 9
_COL_VESS_OWNER = 10
_COL_REMARKS = 11

# IMO number: the literal token "IMO" followed by exactly 7 digits, as OFAC
# writes it in the Remarks field (e.g. "Vessel Flag Panama; ... IMO 9176187").
_IMO_RE = re.compile(r"\bIMO\s*([0-9]{7})\b", re.IGNORECASE)

# Structural-plausibility floor: the real OFAC SDN consolidated list always has
# thousands of designations, so a 200 that parses to fewer than this is a failed
# fetch (HTML error/outage page, truncated body) — NOT an honest list. Used to
# refuse badging garbage as a live screen (review).
_MIN_PLAUSIBLE_DESIGNATIONS = 100

_CACHE_DIR = Path(__file__).resolve().parent.parent / "cache" / "sanctions"
try:
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
except Exception:  # pragma: no cover - read-only FS guard
    pass


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SanctionedEntity:
    """One OFAC SDN designation.

    ``imo`` / ``vessel_flag`` / ``vessel_type`` are populated only for vessel
    designations (SDN_Type == "vessel" or an IMO present in remarks). All fields
    come straight from the OFAC record — nothing is inferred or fabricated.
    """

    ent_num: str
    name: str
    sdn_type: str                # "individual" | "vessel" | "entity" | ...
    program: str                 # OFAC program(s), e.g. "RUSSIA-EO14024"
    imo: str = ""                # 7-digit IMO, when present (vessels)
    vessel_flag: str = ""
    vessel_type: str = ""
    vessel_owner: str = ""
    call_sign: str = ""
    remarks: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SanctionsList:
    """Parsed OFAC SDN list + its provenance.

    Truthiness follows whether any entities parsed (so ``if sanctions:`` gates
    "live screen available"). ``source`` is REAL (``DataSource.live``) when the
    live CSV parsed, MODELED/DEMO (``DataSource.demo``) when the fetch was dark.
    """

    entities: list = field(default_factory=list)
    source: DataSource = field(default_factory=lambda: DataSource.demo("OFAC SDN"))

    def __bool__(self) -> bool:
        return bool(self.entities)

    def __len__(self) -> int:
        return len(self.entities)

    @property
    def is_real(self) -> bool:
        """True when this list came from a successfully parsed live OFAC fetch."""
        return self.source.kind in (DataKind.LIVE, DataKind.CACHED) and bool(self.entities)

    @property
    def vessels(self) -> list:
        """Entities that are vessel designations (carry an IMO or vessel type)."""
        return [
            e for e in self.entities
            if e.sdn_type == "vessel" or e.imo or e.vessel_type
        ]


@dataclass
class ScreeningMatch:
    """A positive screen hit: the query and the OFAC entity it matched."""

    query: str
    matched_on: str              # "imo" | "name"
    entity: SanctionedEntity

    def to_dict(self) -> dict:
        d = {"query": self.query, "matched_on": self.matched_on}
        d["entity"] = self.entity.to_dict()
        return d


# ── Provenance stamp (best-effort; never raises) ──────────────────────────────

def _stamp(kind: str, key: str, row_count: int) -> None:
    """Best-effort per-fetch provenance stamp (R003/R097). Never raises."""
    try:
        from state.fetch_ledger import record_fetch
        quality = ("GOOD" if kind in ("live", "cache")
                   else "UNKNOWN" if kind in ("empty", "failed") else "DEMO")
        record_fetch("sanctions", key, kind, row_count=int(row_count), quality=quality)
    except Exception:  # pragma: no cover - defensive
        pass


# ── Cache (JSON sidecar, mirrors gdelt_feed / canal_feed) ─────────────────────

def _cache_path() -> Path:
    return _CACHE_DIR / "ofac_sdn.json"


def _read_cache(ttl_hours: float):
    """Return cached list[SanctionedEntity] if fresh, else None."""
    path = _cache_path()
    if not path.exists():
        return None
    try:
        age_hours = (time.time() - path.stat().st_mtime) / 3600
        if ttl_hours > 0 and age_hours > ttl_hours:
            return None
        rows = json.loads(path.read_text())
        return [SanctionedEntity(**r) for r in rows]
    except Exception as exc:
        logger.debug(f"sanctions_feed: cache read failed: {exc}")
        return None


def _write_cache(entities: list) -> None:
    try:
        _cache_path().write_text(
            json.dumps([e.to_dict() for e in entities], indent=2)
        )
    except Exception as exc:  # pragma: no cover - read-only FS guard
        logger.debug(f"sanctions_feed: cache write failed: {exc}")


# ── Normalisation helpers (pure) ──────────────────────────────────────────────

def _clean(value) -> str:
    """Strip whitespace and collapse OFAC's '-0-' null token to ''."""
    s = str(value or "").strip()
    return "" if s == _OFAC_NULL else s


def normalize_name(name: str) -> str:
    """Canonicalise a vessel/entity name for conservative exact matching.

    Upper-cases, drops common vessel prefixes (M/V, MV, M/T, MT, S/S),
    strips punctuation to single spaces, and collapses whitespace. Two names
    are considered the same designation only when their normalized forms are
    EQUAL — we never substring-match (that would false-positive a ship named
    "STAR" against "MORNING STAR", "POLAR STAR", …).
    """
    s = str(name or "").upper().strip()
    # Drop a leading vessel prefix token (M/V, MV, M/T, MT, S/S, SS).
    s = re.sub(r"^(M/V|M/T|S/S|MV|MT|SS)\b[\s.\-]*", "", s)
    # Punctuation → space, then collapse runs of whitespace.
    s = re.sub(r"[^A-Z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_imo(value: str) -> str:
    """Extract a bare 7-digit IMO from a raw token / 'IMO 1234567' / '1234567'."""
    s = str(value or "").strip()
    m = _IMO_RE.search(s)
    if m:
        return m.group(1)
    digits = re.sub(r"\D", "", s)
    return digits if len(digits) == 7 else ""


# ── Parse (pure; offline-safe at the row level) ───────────────────────────────

def parse_sdn_csv(text: str) -> list:
    """Parse the header-less OFAC SDN.CSV text → list[SanctionedEntity].

    Pure + tolerant: malformed rows are skipped, never raised. Returns ``[]``
    for empty / non-CSV input. The IMO is read from the Remarks free-text
    (``IMO 9176187``) where OFAC records vessel IMOs.
    """
    entities: list = []
    if not text or not isinstance(text, str):
        return entities

    reader = csv.reader(io.StringIO(text))
    for row in reader:
        # Need at least name + type to be a usable designation.
        if not row or len(row) <= _COL_TYPE:
            continue
        name = _clean(row[_COL_NAME])
        if not name:
            continue
        ent_num = _clean(row[_COL_ENT_NUM])
        raw_type = _clean(row[_COL_TYPE]).lower()
        # OFAC leaves the type blank for entities/organisations; label "entity".
        sdn_type = raw_type or "entity"
        program = _clean(row[_COL_PROGRAM]) if len(row) > _COL_PROGRAM else ""
        call_sign = _clean(row[_COL_CALL_SIGN]) if len(row) > _COL_CALL_SIGN else ""
        vess_type = _clean(row[_COL_VESS_TYPE]) if len(row) > _COL_VESS_TYPE else ""
        vess_flag = _clean(row[_COL_VESS_FLAG]) if len(row) > _COL_VESS_FLAG else ""
        vess_owner = _clean(row[_COL_VESS_OWNER]) if len(row) > _COL_VESS_OWNER else ""
        remarks = _clean(row[_COL_REMARKS]) if len(row) > _COL_REMARKS else ""

        # Anchored extraction only: take an IMO from Remarks ONLY when the
        # explicit "IMO 1234567" token is present (how OFAC writes it). Do NOT
        # use the digit-strip fallback here — a stray 7-digit number in free-text
        # remarks (a phone/registration/UN-list id) is NOT a vessel IMO (review).
        _m = _IMO_RE.search(remarks)
        imo = _m.group(1) if _m else ""

        entities.append(SanctionedEntity(
            ent_num=ent_num,
            name=name,
            sdn_type=sdn_type,
            program=program,
            imo=imo,
            vessel_flag=vess_flag,
            vessel_type=vess_type,
            vessel_owner=vess_owner,
            call_sign=call_sign,
            remarks=remarks[:300],
        ))
    return entities


# ── Public fetch (offline-safe, injectable, cache-backed) ─────────────────────

def fetch_ofac_sdn(
    *,
    http_get=None,
    cache_ttl_hours: float = _DEFAULT_TTL_HOURS,
    force_refresh: bool = False,
) -> SanctionsList:
    """Fetch + parse the OFAC SDN consolidated list. OFFLINE-SAFE, cache-backed.

    Waterfall:
      1. JSON sidecar cache (``cache/sanctions/ofac_sdn.json``) within TTL
      2. Live keyless fetch of ``SDN.CSV`` → parse
      3. On ANY failure / dark fetch → EMPTY ``SanctionsList`` stamped
         ``DataSource.demo`` (the caller falls back to its modeled rows)

    Parameters
    ----------
    http_get
        Injectable ``(url, *, headers, timeout) -> response`` for OFFLINE tests.
        Defaults to ``requests.get``. A stub need only expose ``status_code`` and
        ``text``.
    cache_ttl_hours
        How long a cached parse is served before a refresh is attempted.
    force_refresh
        Skip the cache read (used for an explicit "refresh now" path).

    Returns
    -------
    SanctionsList
        ``.is_real`` / truthy with REAL provenance when the live CSV parsed;
        empty with ``DataSource.demo`` when dark. NEVER raises.
    """
    # 1 — cache
    if not force_refresh:
        cached = _read_cache(cache_ttl_hours)
        if cached is not None:
            _stamp("cache", "ofac_sdn", len(cached))
            age_h = (time.time() - _cache_path().stat().st_mtime) / 3600
            src = DataSource.cached(
                "OFAC SDN List", age_h, url=_OFAC_CONSOLIDATED_URL,
                sla_hours=cache_ttl_hours,
                notes="U.S. Treasury OFAC SDN consolidated list (cached).",
            )
            return SanctionsList(entities=cached, source=src)

    # 2 — live fetch
    getter = http_get or requests.get
    try:
        resp = getter(_OFAC_SDN_CSV_URL, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        status = getattr(resp, "status_code", 200)
        if status != 200:
            logger.debug(f"sanctions_feed: OFAC non-200 ({status})")
            return _dark("failed")

        text = getattr(resp, "text", "") or ""
        entities = parse_sdn_csv(text)
        if len(entities) < _MIN_PLAUSIBLE_DESIGNATIONS:
            # The real OFAC SDN list always has THOUSANDS of designations. A 200
            # that parses to fewer than the floor is a STRUCTURAL failure (an HTML
            # error/outage page can csv-parse to a stray row or two when a line
            # contains a comma, truncated body, …), NOT an honest list. Do not
            # cache; degrade to modeled — never badge garbage as a live screen.
            logger.debug(
                f"sanctions_feed: OFAC 200 parsed to {len(entities)} rows "
                f"(< {_MIN_PLAUSIBLE_DESIGNATIONS}) — treated as failure")
            return _dark("failed")

        _write_cache(entities)
        _stamp("live", "ofac_sdn", len(entities))
        src = DataSource(
            name="OFAC SDN List",
            kind=DataKind.LIVE,
            url=_OFAC_CONSOLIDATED_URL,
            as_of=datetime.now(timezone.utc),
            quality=DataQuality.GOOD,
            sla_hours=cache_ttl_hours,
            notes=f"U.S. Treasury OFAC SDN consolidated list — {len(entities)} designations (live).",
        )
        logger.info(f"sanctions_feed: OFAC SDN live fetch OK ({len(entities)} designations)")
        return SanctionsList(entities=entities, source=src)

    except Exception as exc:
        logger.debug(f"sanctions_feed: OFAC fetch failed: {exc}")
        return _dark("failed")


def _dark(kind: str) -> SanctionsList:
    """Return an EMPTY, honestly-labelled list for a dark/failed fetch."""
    _stamp(kind, "ofac_sdn", 0)
    src = DataSource.demo("OFAC SDN")
    # demo() leaves a generic note; make the dark-screen reason explicit.
    src = DataSource(
        name="OFAC SDN List",
        kind=DataKind.DEMO,
        url=_OFAC_CONSOLIDATED_URL,
        as_of=datetime.now(timezone.utc),
        quality=DataQuality.DEMO,
        notes="OFAC SDN feed unavailable — live screen offline; modeled fallback in use.",
    )
    return SanctionsList(entities=[], source=src)


# ── Screening (pure, conservative) ────────────────────────────────────────────

def screen_entity(name_or_imo: str, sanctions_list: SanctionsList) -> Optional[ScreeningMatch]:
    """Screen a single name or IMO against the SDN list. CONSERVATIVE.

    Matching rule (documented, no false positives on common substrings):
      1. IMO match — if ``name_or_imo`` yields a bare 7-digit IMO (raw, or in
         the form ``IMO 9176187``), it must EQUAL a sanctioned vessel's IMO.
         IMO is a globally unique vessel id, so this is the strongest signal.
      2. Name match — otherwise ``normalize_name`` of the query must EQUAL the
         ``normalize_name`` of a designation. EXACT normalized equality only:
         we never substring-match, so "STAR" does NOT hit "MORNING STAR".

    Returns the first ``ScreeningMatch`` (IMO checked before name), or ``None``.
    An empty/dark list ALWAYS returns ``None`` (no live screen → no hit; the
    caller must not present "no hit" as a clean live screen).
    """
    if not sanctions_list or not sanctions_list.entities:
        return None
    raw = str(name_or_imo or "").strip()
    if not raw:
        return None

    # 1 — IMO (strongest, unique). Only when the query genuinely IS an IMO: a
    # bare 7-digit string OR an explicit "IMO 1234567" token. NOT any free text
    # that merely CONTAINS 7 digits — otherwise a company whose name/owner embeds
    # a sanctioned vessel's IMO ("ACME LOGISTICS 9176187") false-positives as
    # sanctioned (review HIGH). The digit-strip fallback is reserved for cleaning
    # fields already KNOWN to be IMOs (parse path / voyage.imo), never detection.
    _im = _IMO_RE.search(raw)
    imo = _im.group(1) if _im else (raw if raw.isdigit() and len(raw) == 7 else "")
    if imo:
        for e in sanctions_list.entities:
            if e.imo and e.imo == imo:
                return ScreeningMatch(query=raw, matched_on="imo", entity=e)
        # An explicit-IMO query that didn't hit is a clean miss — do NOT then
        # fall through to fuzzy-name-match a bare 7-digit number.
        return None

    # 2 — exact normalized-name equality.
    qn = normalize_name(raw)
    if not qn:
        return None
    for e in sanctions_list.entities:
        if normalize_name(e.name) == qn:
            return ScreeningMatch(query=raw, matched_on="name", entity=e)
    return None


def match_voyages(voyages, sanctions_list: SanctionsList) -> list:
    """Screen modeled voyages against the SDN list, keyed on IMO / name / owner.

    For each voyage we try, in order: its ``imo`` (exact), its ``vessel_name``
    (exact normalized), and its ``owner_id`` (exact normalized) against the list.
    Returns a list of dicts ``{voyage, match}`` for every voyage that hit.

    NOTE on the modeled fleet: ``data.voyage_dataset`` IMOs are SYNTHETIC
    (structurally valid but fabricated), so in normal operation they will NOT
    collide with real OFAC vessels — that is correct and honest (a modeled ship
    is not a real sanctioned ship). This function exists so that when a voyage's
    identifiers DO match a real designation (or in a fixture/backtest), the hit
    is surfaced. An empty/dark list yields ``[]`` (no false hits offline).
    """
    out: list = []
    if not sanctions_list or not sanctions_list.entities:
        return out
    for v in (voyages or []):
        imo = getattr(v, "imo", "") or (v.get("imo", "") if isinstance(v, dict) else "")
        name = getattr(v, "vessel_name", "") or (v.get("vessel_name", "") if isinstance(v, dict) else "")
        owner = getattr(v, "owner_id", "") or (v.get("owner_id", "") if isinstance(v, dict) else "")

        match = None
        if imo:
            match = screen_entity(imo, sanctions_list)
        if match is None and name:
            match = screen_entity(name, sanctions_list)
        if match is None and owner:
            match = screen_entity(owner, sanctions_list)
        if match is not None:
            out.append({"voyage": v, "match": match})
    return out


# ── Row adapters (to the tab table shapes) ────────────────────────────────────

def sanctions_rows(sanctions_list: SanctionsList, *, limit: int = 40) -> list:
    """Adapt a live SDN list to the ``tab_compliance._SANCTIONS_ROWS`` shape.

    Produces rows with the same keys the compliance Sanctions Screening table
    renders (``jurisdiction``, ``entity``, ``vessel_types``, ``trade_lanes``,
    ``effective``, ``penalty``, ``severity``) so the tab can swap data source
    with minimal change. Returns ``[]`` for an empty/dark list (caller keeps its
    own modeled rows).

    Severity is derived ONLY from the OFAC program (a structural fact of the
    designation), never invented: comprehensive/terror programs → critical,
    sectoral/EO programs → high, everything else → moderate.
    """
    if not sanctions_list or not sanctions_list.entities:
        return []
    rows: list = []
    vessels = sanctions_list.vessels or sanctions_list.entities
    for e in vessels[:limit]:
        prog = (e.program or "").upper()
        severity = _severity_for_program(prog)
        rows.append({
            "jurisdiction": "US OFAC",
            "entity": e.name,
            "vessel_types": e.vessel_type or (e.sdn_type.title() if e.sdn_type else ""),
            "trade_lanes": e.vessel_flag or "—",
            "effective": e.program or "—",
            "penalty": f"OFAC SDN designation (#{e.ent_num})" if e.ent_num else "OFAC SDN designation",
            "severity": severity,
            "imo": e.imo,
        })
    return rows


def geopolitical_sanctions_rows(sanctions_list: SanctionsList, *, limit: int = 30) -> list:
    """Adapt a live SDN list to the ``tab_geopolitical._SANCTIONS`` row shape.

    Keys: ``entity``, ``body``, ``asset_type``, ``ships_affected``,
    ``effective``, ``notes``. Returns ``[]`` for an empty/dark list.

    The geopolitical table is ENTITY/program-oriented, so this groups vessel
    designations by OFAC program and reports a count per program rather than one
    row per ship.
    """
    if not sanctions_list or not sanctions_list.entities:
        return []
    # Group vessel designations by program.
    by_prog: dict[str, list] = {}
    for e in sanctions_list.vessels:
        by_prog.setdefault(e.program or "Unattributed", []).append(e)
    rows: list = []
    for prog, ents in sorted(by_prog.items(), key=lambda kv: -len(kv[1]))[:limit]:
        flags = sorted({e.vessel_flag for e in ents if e.vessel_flag})
        rows.append({
            "entity": prog,
            "body": "US OFAC (SDN)",
            "asset_type": "Sanctioned vessels",
            "ships_affected": f"{len(ents)} vessel{'s' if len(ents) != 1 else ''}",
            "effective": prog,
            "notes": (
                "Live OFAC SDN designations"
                + (f"; flags: {', '.join(flags[:6])}" if flags else "")
            ),
        })
    return rows


# OFAC program → illustrative severity tier. These are the program *families*,
# not a legal judgement: comprehensive country / terrorism programs are the most
# severe, executive-order sectoral programs next, the rest moderate.
_CRITICAL_PROGRAM_TOKENS = (
    "DPRK", "NPWMD", "IRAN", "SDGT", "FTO", "SDNTK", "SYRIA", "CUBA", "IFSR",
)
_HIGH_PROGRAM_TOKENS = (
    "RUSSIA", "UKRAINE", "VENEZUELA", "BELARUS", "EO14024", "EO13662", "EO13846",
)


def _severity_for_program(program_upper: str) -> str:
    """Map an OFAC program string to critical/high/moderate (structural)."""
    p = program_upper or ""
    if any(tok in p for tok in _CRITICAL_PROGRAM_TOKENS):
        return "critical"
    if any(tok in p for tok in _HIGH_PROGRAM_TOKENS):
        return "high"
    return "moderate"


__all__ = [
    "SanctionedEntity",
    "SanctionsList",
    "ScreeningMatch",
    "fetch_ofac_sdn",
    "parse_sdn_csv",
    "screen_entity",
    "match_voyages",
    "sanctions_rows",
    "geopolitical_sanctions_rows",
    "normalize_name",
]
