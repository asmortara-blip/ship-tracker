from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShippingRoute:
    id: str              # Unique identifier used throughout the system
    name: str            # Human-readable name
    origin_region: str
    dest_region: str
    origin_locode: str   # Primary origin port
    dest_locode: str     # Primary destination port
    transit_days: int    # Typical transit time
    fbx_index: str       # Freightos Baltic Index code for this lane
    description: str     # Brief description for UI


ROUTES: list[ShippingRoute] = [
    ShippingRoute(
        id="transpacific_eb",
        name="Trans-Pacific Eastbound",
        origin_region="Asia East",
        dest_region="North America West",
        origin_locode="CNSHA",
        dest_locode="USLAX",
        transit_days=14,
        fbx_index="FBX01",
        description="China/Asia to US West Coast — highest-volume container lane globally",
    ),
    ShippingRoute(
        id="asia_europe",
        name="Asia-Europe",
        origin_region="Asia East",
        dest_region="Europe",
        origin_locode="CNSHA",
        dest_locode="NLRTM",
        transit_days=25,
        fbx_index="FBX03",
        description="Asia to North Europe via Suez Canal — second-busiest global lane",
    ),
    ShippingRoute(
        id="transpacific_wb",
        name="Trans-Pacific Westbound",
        origin_region="North America West",
        dest_region="Asia East",
        origin_locode="USLAX",
        dest_locode="CNSHA",
        transit_days=16,
        fbx_index="FBX02",
        description="US West Coast to Asia — typically lower demand (imbalanced trade flow)",
    ),
    ShippingRoute(
        id="transatlantic",
        name="Transatlantic",
        origin_region="Europe",
        dest_region="North America East",
        origin_locode="NLRTM",
        dest_locode="USNYC",
        transit_days=12,
        fbx_index="FBX11",
        description="North Europe to US East Coast — established lane with stable seasonal patterns",
    ),
    ShippingRoute(
        id="sea_transpacific_eb",
        name="Southeast Asia Eastbound",
        origin_region="Southeast Asia",
        dest_region="North America West",
        origin_locode="SGSIN",
        dest_locode="USLAX",
        transit_days=14,
        fbx_index="FBX01",
        description="Singapore to US West Coast — Southeast Asia origin variant of the Trans-Pacific EB lane",
    ),
    ShippingRoute(
        id="ningbo_europe",
        name="Asia-Europe via Suez (Ningbo)",
        origin_region="Asia East",
        dest_region="Europe",
        origin_locode="CNNBO",
        dest_locode="BEANR",
        transit_days=28,
        fbx_index="FBX03",
        description="Ningbo to Antwerp via Suez Canal — key Chinese export leg into North European range",
    ),
    ShippingRoute(
        id="middle_east_to_europe",
        name="Middle East Hub to Europe",
        origin_region="Middle East",
        dest_region="Europe",
        origin_locode="AEJEA",
        dest_locode="NLRTM",
        transit_days=22,
        fbx_index="FBX03",
        description="Jebel Ali to Rotterdam via Suez Canal — Gulf/Indian Ocean cargo to North Europe",
    ),
    ShippingRoute(
        id="middle_east_to_asia",
        name="Middle East Hub to Asia",
        origin_region="Middle East",
        dest_region="Asia East",
        origin_locode="AEJEA",
        dest_locode="CNSHA",
        transit_days=10,
        fbx_index="FBXGLO",
        description="Jebel Ali to Shanghai — westbound repositioning and Gulf-origin goods into China",
    ),
    ShippingRoute(
        id="south_asia_to_europe",
        name="South Asia to Europe",
        origin_region="South Asia",
        dest_region="Europe",
        origin_locode="LKCMB",
        dest_locode="GBFXT",
        transit_days=20,
        fbx_index="FBX03",
        description="Colombo to Felixstowe — Sri Lanka transshipment hub serving the Asia-Europe corridor",
    ),
    ShippingRoute(
        id="intra_asia_china_sea",
        name="Intra-Asia: China to SE Asia",
        origin_region="Asia East",
        dest_region="Southeast Asia",
        origin_locode="CNSHA",
        dest_locode="SGSIN",
        transit_days=5,
        fbx_index="FBXGLO",
        description="Shanghai to Singapore — high-frequency intra-Asia feeder and transshipment flow",
    ),
    ShippingRoute(
        id="intra_asia_china_japan",
        name="Intra-Asia: China to Japan/Korea",
        origin_region="Asia East",
        dest_region="Asia East",
        origin_locode="CNSHA",
        dest_locode="JPYOK",
        transit_days=3,
        fbx_index="FBXGLO",
        description="Shanghai to Yokohama — short-sea intra-Asia trade between China and Japan",
    ),
    ShippingRoute(
        id="china_south_america",
        name="China to South America",
        origin_region="Asia East",
        dest_region="South America",
        origin_locode="CNSHA",
        dest_locode="BRSAO",
        transit_days=35,
        fbx_index="FBX21",
        description="Shanghai to Santos — Asia to South America East Coast via Cape of Good Hope or Suez",
    ),
    ShippingRoute(
        id="europe_south_america",
        name="Europe to South America",
        origin_region="Europe",
        dest_region="South America",
        origin_locode="NLRTM",
        dest_locode="BRSAO",
        transit_days=22,
        fbx_index="FBX31",
        description="Rotterdam to Santos — Europe–Brazil container trade, largest LATAM import lane",
    ),
    ShippingRoute(
        id="med_hub_to_asia",
        name="Mediterranean Hub to Asia",
        origin_region="Europe",
        dest_region="Asia East",
        origin_locode="GRPIR",
        dest_locode="CNSHA",
        transit_days=22,
        fbx_index="FBX04",
        description="Piraeus to Shanghai — Europe-to-Asia return leg via Suez, key Mediterranean gateway",
    ),
    ShippingRoute(
        id="north_africa_to_europe",
        name="North Africa/Med to Europe",
        origin_region="Africa",
        dest_region="Europe",
        origin_locode="MATNM",
        dest_locode="NLRTM",
        transit_days=8,
        fbx_index="FBXGLO",
        description="Tanger Med to Rotterdam — short feeder route through the Strait of Gibraltar",
    ),
    ShippingRoute(
        id="us_east_south_america",
        name="US East Coast to South America",
        origin_region="North America East",
        dest_region="South America",
        origin_locode="USSAV",
        dest_locode="BRSAO",
        transit_days=12,
        fbx_index="FBXGLO",
        description="Savannah to Santos — Americas north-south trade corridor, growing agricultural flow",
    ),
    ShippingRoute(
        id="longbeach_to_asia",
        name="US West Coast (Long Beach) to Asia",
        origin_region="North America West",
        dest_region="Asia East",
        origin_locode="USLGB",
        dest_locode="CNSHA",
        transit_days=16,
        fbx_index="FBX02",
        description="Long Beach to Shanghai — Trans-Pacific westbound return leg from Southern California",
    ),
]

ROUTES_BY_ID: dict[str, ShippingRoute] = {r.id: r for r in ROUTES}
ROUTES_BY_FBX: dict[str, ShippingRoute] = {r.fbx_index: r for r in ROUTES}

# FRED series for freight rate proxies (fallback when FBX scrape fails)
FRED_FREIGHT_PROXIES: dict[str, str] = {
    "transpacific_eb": "WTISPLC",   # WTI spot (tanker proxy; not ideal but available)
    "asia_europe": "WTISPLC",
    "transpacific_wb": "WTISPLC",
    "transatlantic": "WTISPLC",
    "sea_transpacific_eb": "WTISPLC",
    "ningbo_europe": "WTISPLC",
    "middle_east_to_europe": "WTISPLC",
    "middle_east_to_asia": "WTISPLC",
    "south_asia_to_europe": "WTISPLC",
    "intra_asia_china_sea": "WTISPLC",
    "intra_asia_china_japan": "WTISPLC",
    "china_south_america": "WTISPLC",
    "europe_south_america": "WTISPLC",
    "med_hub_to_asia": "WTISPLC",
    "north_africa_to_europe": "WTISPLC",
    "us_east_south_america": "WTISPLC",
    "longbeach_to_asia": "WTISPLC",
}


def get_route(route_id: str) -> ShippingRoute | None:
    return ROUTES_BY_ID.get(route_id)


def get_route_by_fbx(fbx_index: str) -> ShippingRoute | None:
    return ROUTES_BY_FBX.get(fbx_index)


def get_all_route_ids() -> list[str]:
    return [r.id for r in ROUTES]


def get_all_fbx_indices() -> list[str]:
    return [r.fbx_index for r in ROUTES]
