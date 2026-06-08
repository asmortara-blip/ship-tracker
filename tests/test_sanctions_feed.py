"""Tests for the keyless OFAC SDN sanctions-screening feed (rec R024).

OFFLINE-SAFE: no network. ``fetch_ofac_sdn`` takes an injectable ``http_get``,
so every test feeds a fixture CSV payload (or a raising / non-200 stub to
exercise the dark path). The pure helpers (``parse_sdn_csv``, ``screen_entity``,
``match_voyages``, ``sanctions_rows``, ``normalize_name``) need no I/O at all.

The screen is CONSERVATIVE: exact normalized-name OR exact 7-digit-IMO match —
never a substring — so a ship named "STAR" must not hit "MORNING STAR".
"""
from __future__ import annotations

import pytest

from data.quality import DataKind, DataQuality
from data.sanctions_feed import (
    SanctionedEntity,
    SanctionsList,
    ScreeningMatch,
    fetch_ofac_sdn,
    geopolitical_sanctions_rows,
    match_voyages,
    normalize_name,
    parse_sdn_csv,
    sanctions_rows,
    screen_entity,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

# A small, header-less OFAC-SDN-shaped CSV (the real file has no header).
# Columns: ent_num, name, type, program, title, call_sign, vess_type, tonnage,
#          grt, vess_flag, vess_owner, remarks
_FIXTURE_CSV = (
    '9999,FORTUNE STAR,vessel,RUSSIA-EO14024,-0-,ABCD,Crude Oil Tanker,'
    '160000,80000,Panama,SOME OWNER LTD,"Secondary sanctions risk; IMO 9176187."\n'
    '8888,POLAR TRADER,vessel,IRAN,-0-,-0-,Products Tanker,-0-,-0-,Cook Islands,-0-,'
    '"Vessel; IMO 9410645."\n'
    '7777,IRGC SHIPPING LINES,-0-,IRAN,-0-,-0-,-0-,-0-,-0-,-0-,-0-,"Designated entity."\n'
    '6666,JANE DOE,individual,SDGT,-0-,-0-,-0-,-0-,-0-,-0-,-0-,"Linked to terror finance."\n'
)


class _Resp:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, *, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _ok(text):
    """A getter returning a 200 with the given CSV body."""
    def _getter(url, **kw):
        return _Resp(status_code=200, text=text)
    return _getter


def _raises(url, **kw):
    raise OSError("offline")


def _non200(url, **kw):
    return _Resp(status_code=503, text="service unavailable")


class _Voyage:
    """Tiny voyage-like exposing the fields match_voyages keys on."""

    def __init__(self, imo="", vessel_name="", owner_id=""):
        self.imo = imo
        self.vessel_name = vessel_name
        self.owner_id = owner_id


@pytest.fixture(autouse=True)
def _no_cache(tmp_path, monkeypatch):
    """Point the JSON cache sidecar at a temp dir so tests never read a real,
    possibly-stale cache and never write into the repo cache tree."""
    import data.sanctions_feed as sf
    monkeypatch.setattr(sf, "_CACHE_DIR", tmp_path)
    yield


# ── parse_sdn_csv (pure) ──────────────────────────────────────────────────────

def test_parse_extracts_fields_and_imo():
    ents = parse_sdn_csv(_FIXTURE_CSV)
    assert len(ents) == 4
    by_name = {e.name: e for e in ents}

    fs = by_name["FORTUNE STAR"]
    assert fs.sdn_type == "vessel"
    assert fs.program == "RUSSIA-EO14024"
    assert fs.imo == "9176187"          # lifted from remarks "IMO 9176187"
    assert fs.vessel_flag == "Panama"
    assert fs.vessel_type == "Crude Oil Tanker"

    # "-0-" type collapses to "entity"; null cells collapse to "".
    irgc = by_name["IRGC SHIPPING LINES"]
    assert irgc.sdn_type == "entity"
    assert irgc.imo == ""
    assert irgc.vessel_flag == ""


def test_parse_empty_and_garbage():
    assert parse_sdn_csv("") == []
    assert parse_sdn_csv(None) == []  # type: ignore[arg-type]
    # An HTML error page parses to zero usable rows (no name col).
    assert parse_sdn_csv("<html><body>error</body></html>") == []


# ── fetch_ofac_sdn — live (REAL) + dark (DEMO), all offline ───────────────────

def test_fetch_live_parses_and_stamps_real():
    sl = fetch_ofac_sdn(http_get=_ok(_FIXTURE_CSV), force_refresh=True)
    assert isinstance(sl, SanctionsList)
    assert len(sl) == 4
    assert sl.is_real is True
    assert bool(sl) is True
    assert sl.source.kind == DataKind.LIVE
    assert sl.source.quality == DataQuality.GOOD
    # 2 vessel designations carry IMOs.
    assert len(sl.vessels) == 2


def test_fetch_offline_returns_empty_demo_no_raise():
    sl = fetch_ofac_sdn(http_get=_raises, force_refresh=True)
    assert len(sl) == 0
    assert sl.is_real is False
    assert bool(sl) is False
    assert sl.source.kind == DataKind.DEMO
    assert sl.source.quality == DataQuality.DEMO


def test_fetch_non200_returns_empty_demo():
    sl = fetch_ofac_sdn(http_get=_non200, force_refresh=True)
    assert len(sl) == 0
    assert sl.source.kind == DataKind.DEMO


def test_fetch_200_but_unparseable_is_failure_not_empty():
    # A 200 whose body parses to zero rows (HTML error page) is a STRUCTURAL
    # failure → DEMO, never presented as a real empty screen.
    sl = fetch_ofac_sdn(http_get=_ok("<html>maintenance</html>"), force_refresh=True)
    assert len(sl) == 0
    assert sl.source.kind == DataKind.DEMO


def test_fetch_uses_cache_within_ttl():
    # First live fetch writes the JSON cache; a second call with a RAISING
    # getter still returns the cached (real) data — proving cache-backed.
    sl1 = fetch_ofac_sdn(http_get=_ok(_FIXTURE_CSV), force_refresh=True)
    assert sl1.is_real and len(sl1) == 4
    sl2 = fetch_ofac_sdn(http_get=_raises)  # would fail if it hit the network
    assert len(sl2) == 4
    assert sl2.source.kind == DataKind.CACHED


# ── screen_entity — IMO + name hits, conservative misses ──────────────────────

@pytest.fixture
def live_list():
    return fetch_ofac_sdn(http_get=_ok(_FIXTURE_CSV), force_refresh=True)


def test_screen_imo_exact_hit(live_list):
    m = screen_entity("9176187", live_list)
    assert isinstance(m, ScreeningMatch)
    assert m.matched_on == "imo"
    assert m.entity.name == "FORTUNE STAR"
    # The "IMO 9410645" form is accepted too.
    m2 = screen_entity("IMO 9410645", live_list)
    assert m2 is not None and m2.entity.name == "POLAR TRADER"


def test_screen_name_exact_hit_with_prefix(live_list):
    # "M/V FORTUNE STAR" normalizes to "FORTUNE STAR" → exact name match.
    m = screen_entity("M/V FORTUNE STAR", live_list)
    assert m is not None
    assert m.matched_on == "name"
    assert m.entity.name == "FORTUNE STAR"


def test_screen_no_false_positive_on_substring(live_list):
    # A common substring of a listed name must NOT hit (no substring matching).
    assert screen_entity("STAR", live_list) is None
    assert screen_entity("MORNING STAR", live_list) is None
    # An unlisted IMO is a clean miss, not a fuzzy name fallback.
    assert screen_entity("1234567", live_list) is None


def test_screen_empty_list_returns_none():
    dark = fetch_ofac_sdn(http_get=_raises, force_refresh=True)
    assert screen_entity("9176187", dark) is None
    assert screen_entity("FORTUNE STAR", dark) is None


# ── match_voyages — flags a voyage whose IMO / name / owner is listed ─────────

def test_match_voyages_flags_listed_imo(live_list):
    voyages = [
        _Voyage(imo="9176187", vessel_name="Fortune Star", owner_id="OWN-ALPHA"),
        _Voyage(imo="1111111", vessel_name="Clean Ship", owner_id="OWN-BRAVO"),
    ]
    hits = match_voyages(voyages, live_list)
    assert len(hits) == 1
    assert hits[0]["match"].matched_on == "imo"
    assert hits[0]["voyage"].vessel_name == "Fortune Star"


def test_match_voyages_by_name_when_no_imo(live_list):
    voyages = [_Voyage(imo="", vessel_name="POLAR TRADER", owner_id="")]
    hits = match_voyages(voyages, live_list)
    assert len(hits) == 1
    assert hits[0]["match"].entity.name == "POLAR TRADER"


def test_match_voyages_empty_list_no_hits():
    dark = fetch_ofac_sdn(http_get=_raises, force_refresh=True)
    voyages = [_Voyage(imo="9176187", vessel_name="Fortune Star")]
    assert match_voyages(voyages, dark) == []


# ── row adapters — shape the tabs render ──────────────────────────────────────

def test_sanctions_rows_shape(live_list):
    rows = sanctions_rows(live_list)
    assert rows  # at least the 2 vessels
    r = rows[0]
    for key in ("jurisdiction", "entity", "vessel_types", "trade_lanes",
                "effective", "penalty", "severity"):
        assert key in r
    assert r["jurisdiction"] == "US OFAC"
    assert r["severity"] in ("critical", "high", "moderate")
    # RUSSIA-EO14024 → high; IRAN → critical (program-driven severity).
    sev = {row["entity"]: row["severity"] for row in rows}
    assert sev["FORTUNE STAR"] == "high"
    assert sev["POLAR TRADER"] == "critical"


def test_geopolitical_rows_group_by_program(live_list):
    rows = geopolitical_sanctions_rows(live_list)
    assert rows
    r = rows[0]
    for key in ("entity", "body", "asset_type", "ships_affected",
                "effective", "notes"):
        assert key in r
    assert r["body"] == "US OFAC (SDN)"


def test_row_adapters_empty_for_dark_list():
    dark = fetch_ofac_sdn(http_get=_raises, force_refresh=True)
    assert sanctions_rows(dark) == []
    assert geopolitical_sanctions_rows(dark) == []


# ── normalize_name (pure) ─────────────────────────────────────────────────────

def test_normalize_name_drops_prefix_and_punct():
    assert normalize_name("M/V Fortune Star") == "FORTUNE STAR"
    assert normalize_name("MT  Polar-Trader") == "POLAR TRADER"
    assert normalize_name("  s/s  Old Ship  ") == "OLD SHIP"
    assert normalize_name("") == ""


# ── tab smokes — offline → modeled fallback renders, no raise ─────────────────

def test_tab_compliance_imports_and_renders_offline(monkeypatch):
    import data.sanctions_feed as sf
    # Force the dark path so the modeled fallback renders.
    monkeypatch.setattr(sf, "requests", _RaisingRequests())
    import ui.tab_compliance as tc
    # The live-screen helper must degrade to (None, None) offline, never raise.
    rows, source = tc._try_live_sanctions()
    assert rows is None and source is None


def test_tab_geopolitical_live_helper_offline(monkeypatch):
    import data.sanctions_feed as sf
    monkeypatch.setattr(sf, "requests", _RaisingRequests())
    import ui.tab_geopolitical as tg
    rows, source = tg._try_live_geo_sanctions()
    assert rows is None and source is None


class _RaisingRequests:
    """Stand-in for the ``requests`` module whose .get always raises."""

    @staticmethod
    def get(*a, **k):
        raise OSError("offline")
