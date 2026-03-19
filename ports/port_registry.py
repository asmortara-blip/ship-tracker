from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Port:
    locode: str           # UN/LOCODE (e.g. "CNSHA")
    name: str             # Human-readable name
    region: str           # Broad region label
    country_iso3: str     # ISO 3166-1 alpha-3
    country_numeric: str  # ISO 3166-1 numeric (for Comtrade API)
    lat: float
    lon: float
    bbox: dict            # {"latmin", "latmax", "lonmin", "lonmax"} for AIS queries


PORTS: list[Port] = [
    # ── Tier 1: Top 10 globally by TEU ──────────────────────────────────
    Port(
        locode="CNSHA", name="Shanghai", region="Asia East",
        country_iso3="CHN", country_numeric="156",
        lat=31.23, lon=121.47,
        bbox={"latmin": 30.70, "latmax": 31.80, "lonmin": 121.00, "lonmax": 122.50},
    ),
    Port(
        locode="SGSIN", name="Singapore", region="Southeast Asia",
        country_iso3="SGP", country_numeric="702",
        lat=1.29, lon=103.85,
        bbox={"latmin": 1.00, "latmax": 1.60, "lonmin": 103.50, "lonmax": 104.30},
    ),
    Port(
        locode="CNNBO", name="Ningbo-Zhoushan", region="Asia East",
        country_iso3="CHN", country_numeric="156",
        lat=29.87, lon=121.55,
        bbox={"latmin": 29.50, "latmax": 30.30, "lonmin": 121.00, "lonmax": 122.30},
    ),
    Port(
        locode="CNSZN", name="Shenzhen", region="Asia East",
        country_iso3="CHN", country_numeric="156",
        lat=22.54, lon=113.94,
        bbox={"latmin": 22.20, "latmax": 22.80, "lonmin": 113.60, "lonmax": 114.40},
    ),
    Port(
        locode="CNTAO", name="Qingdao", region="Asia East",
        country_iso3="CHN", country_numeric="156",
        lat=36.07, lon=120.33,
        bbox={"latmin": 35.80, "latmax": 36.40, "lonmin": 120.00, "lonmax": 120.80},
    ),
    Port(
        locode="KRPUS", name="Busan", region="Asia East",
        country_iso3="KOR", country_numeric="410",
        lat=35.10, lon=129.04,
        bbox={"latmin": 34.80, "latmax": 35.40, "lonmin": 128.70, "lonmax": 129.40},
    ),
    Port(
        locode="CNTXG", name="Tianjin", region="Asia East",
        country_iso3="CHN", country_numeric="156",
        lat=38.99, lon=117.72,
        bbox={"latmin": 38.70, "latmax": 39.30, "lonmin": 117.30, "lonmax": 118.20},
    ),
    Port(
        locode="HKHKG", name="Hong Kong", region="Asia East",
        country_iso3="HKG", country_numeric="344",
        lat=22.29, lon=114.18,
        bbox={"latmin": 22.10, "latmax": 22.50, "lonmin": 113.80, "lonmax": 114.50},
    ),
    Port(
        locode="MYPKG", name="Port Klang", region="Southeast Asia",
        country_iso3="MYS", country_numeric="458",
        lat=3.00, lon=101.39,
        bbox={"latmin": 2.70, "latmax": 3.30, "lonmin": 101.10, "lonmax": 101.70},
    ),
    Port(
        locode="NLRTM", name="Rotterdam", region="Europe",
        country_iso3="NLD", country_numeric="528",
        lat=51.92, lon=4.48,
        bbox={"latmin": 51.70, "latmax": 52.10, "lonmin": 3.80, "lonmax": 5.00},
    ),
    # ── Tier 2: Major regional hubs ──────────────────────────────────────
    Port(
        locode="AEJEA", name="Jebel Ali (Dubai)", region="Middle East",
        country_iso3="ARE", country_numeric="784",
        lat=24.99, lon=55.06,
        bbox={"latmin": 24.70, "latmax": 25.30, "lonmin": 54.70, "lonmax": 55.50},
    ),
    Port(
        locode="BEANR", name="Antwerp-Bruges", region="Europe",
        country_iso3="BEL", country_numeric="056",
        lat=51.26, lon=4.40,
        bbox={"latmin": 51.00, "latmax": 51.50, "lonmin": 3.90, "lonmax": 4.90},
    ),
    Port(
        locode="MYTPP", name="Tanjung Pelepas", region="Southeast Asia",
        country_iso3="MYS", country_numeric="458",
        lat=1.36, lon=103.55,
        bbox={"latmin": 1.10, "latmax": 1.60, "lonmin": 103.20, "lonmax": 103.90},
    ),
    Port(
        locode="TWKHH", name="Kaohsiung", region="Asia East",
        country_iso3="TWN", country_numeric="158",
        lat=22.61, lon=120.29,
        bbox={"latmin": 22.30, "latmax": 22.90, "lonmin": 120.00, "lonmax": 120.60},
    ),
    Port(
        locode="USLAX", name="Los Angeles", region="North America West",
        country_iso3="USA", country_numeric="842",
        lat=33.74, lon=-118.27,
        bbox={"latmin": 33.50, "latmax": 34.10, "lonmin": -118.60, "lonmax": -117.90},
    ),
    Port(
        locode="USLGB", name="Long Beach", region="North America West",
        country_iso3="USA", country_numeric="842",
        lat=33.76, lon=-118.19,
        bbox={"latmin": 33.60, "latmax": 34.00, "lonmin": -118.40, "lonmax": -117.90},
    ),
    Port(
        locode="DEHAM", name="Hamburg", region="Europe",
        country_iso3="DEU", country_numeric="276",
        lat=53.55, lon=9.99,
        bbox={"latmin": 53.30, "latmax": 53.80, "lonmin": 9.50, "lonmax": 10.50},
    ),
    Port(
        locode="USNYC", name="New York/New Jersey", region="North America East",
        country_iso3="USA", country_numeric="842",
        lat=40.66, lon=-74.04,
        bbox={"latmin": 40.40, "latmax": 41.00, "lonmin": -74.30, "lonmax": -73.70},
    ),
    Port(
        locode="MATNM", name="Tanger Med", region="Africa",
        country_iso3="MAR", country_numeric="504",
        lat=35.89, lon=-5.50,
        bbox={"latmin": 35.60, "latmax": 36.10, "lonmin": -5.80, "lonmax": -5.10},
    ),
    Port(
        locode="JPYOK", name="Yokohama", region="Asia East",
        country_iso3="JPN", country_numeric="392",
        lat=35.44, lon=139.64,
        bbox={"latmin": 35.20, "latmax": 35.70, "lonmin": 139.40, "lonmax": 140.00},
    ),
    # ── Tier 3: Key strategic / fastest-growing ───────────────────────────
    Port(
        locode="LKCMB", name="Colombo", region="South Asia",
        country_iso3="LKA", country_numeric="144",
        lat=6.93, lon=79.84,
        bbox={"latmin": 6.70, "latmax": 7.20, "lonmin": 79.60, "lonmax": 80.10},
    ),
    Port(
        locode="GRPIR", name="Piraeus", region="Europe",
        country_iso3="GRC", country_numeric="300",
        lat=37.94, lon=23.64,
        bbox={"latmin": 37.70, "latmax": 38.10, "lonmin": 23.30, "lonmax": 24.00},
    ),
    Port(
        locode="USSAV", name="Savannah", region="North America East",
        country_iso3="USA", country_numeric="842",
        lat=32.08, lon=-81.10,
        bbox={"latmin": 31.80, "latmax": 32.40, "lonmin": -81.40, "lonmax": -80.80},
    ),
    Port(
        locode="GBFXT", name="Felixstowe", region="Europe",
        country_iso3="GBR", country_numeric="826",
        lat=51.96, lon=1.35,
        bbox={"latmin": 51.70, "latmax": 52.20, "lonmin": 1.00, "lonmax": 1.80},
    ),
    Port(
        locode="BRSAO", name="Santos", region="South America",
        country_iso3="BRA", country_numeric="076",
        lat=-23.95, lon=-46.33,
        bbox={"latmin": -24.10, "latmax": -23.70, "lonmin": -46.60, "lonmax": -46.00},
    ),
]

# Lookup helpers
PORTS_BY_LOCODE: dict[str, Port] = {p.locode: p for p in PORTS}
PORTS_BY_COUNTRY: dict[str, list[Port]] = {}
for _p in PORTS:
    PORTS_BY_COUNTRY.setdefault(_p.country_iso3, []).append(_p)

# Countries that have multiple tracked ports (special handling in Comtrade)
MULTI_PORT_COUNTRIES = {k for k, v in PORTS_BY_COUNTRY.items() if len(v) > 1}

# Port throughput weights for splitting country-level trade data across ports
# Values are approximate share of national container traffic (sums to 1.0 per country)
PORT_TRAFFIC_WEIGHTS: dict[str, dict[str, float]] = {
    # USA: LA+LB ~37%, NY/NJ ~16%, Savannah ~10% of national container traffic
    "USA": {"USLAX": 0.22, "USLGB": 0.21, "USNYC": 0.16, "USSAV": 0.10},
    # China: Shanghai ~27%, Ningbo ~22%, Shenzhen ~18%, Qingdao ~15%, Tianjin ~13%
    "CHN": {"CNSHA": 0.27, "CNNBO": 0.22, "CNSZN": 0.18, "CNTAO": 0.15, "CNTXG": 0.13},
    # Hong Kong is a separate WB entity from China
    "HKG": {"HKHKG": 1.0},
    "NLD": {"NLRTM": 1.0},
    "SGP": {"SGSIN": 1.0},
    "DEU": {"DEHAM": 1.0},
    "JPN": {"JPYOK": 1.0},
    "KOR": {"KRPUS": 1.0},
    # Malaysia: Port Klang ~55%, Tanjung Pelepas ~45%
    "MYS": {"MYPKG": 0.55, "MYTPP": 0.45},
    "ARE": {"AEJEA": 1.0},
    "BEL": {"BEANR": 1.0},
    "TWN": {"TWKHH": 1.0},
    "MAR": {"MATNM": 1.0},
    "LKA": {"LKCMB": 1.0},
    "GRC": {"GRPIR": 1.0},
    "GBR": {"GBFXT": 1.0},
    "BRA": {"BRSAO": 1.0},
}


def get_port(locode: str) -> Port | None:
    return PORTS_BY_LOCODE.get(locode)


def get_ports_for_country(country_iso3: str) -> list[Port]:
    return PORTS_BY_COUNTRY.get(country_iso3, [])


def get_all_locodes() -> list[str]:
    return [p.locode for p in PORTS]


def get_all_country_numerics() -> list[str]:
    """Return unique country numeric codes for Comtrade API calls."""
    return list({p.country_numeric for p in PORTS})
