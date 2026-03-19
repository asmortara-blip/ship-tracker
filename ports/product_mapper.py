from __future__ import annotations

# Maps 4-digit HS codes to human-readable product categories.
# Categories mirror config.yaml hs_categories.

HS_TO_CATEGORY: dict[str, str] = {
    # Electronics
    "8471": "Electronics", "8517": "Electronics", "8542": "Electronics",
    "8541": "Electronics", "8536": "Electronics", "8544": "Electronics",
    "8525": "Electronics", "8528": "Electronics", "8529": "Electronics",
    # Machinery
    "8413": "Machinery", "8479": "Machinery", "8431": "Machinery",
    "8483": "Machinery", "8501": "Machinery", "8503": "Machinery",
    "8414": "Machinery", "8418": "Machinery", "8421": "Machinery",
    # Automotive
    "8703": "Automotive", "8708": "Automotive", "8716": "Automotive",
    "8701": "Automotive", "8702": "Automotive", "8704": "Automotive",
    # Apparel
    "6109": "Apparel", "6110": "Apparel", "6204": "Apparel",
    "6203": "Apparel", "6101": "Apparel", "6102": "Apparel",
    "6105": "Apparel", "6106": "Apparel", "6201": "Apparel",
    # Chemicals
    "2902": "Chemicals", "2903": "Chemicals", "2905": "Chemicals",
    "3901": "Chemicals", "3902": "Chemicals", "2907": "Chemicals",
    "2915": "Chemicals", "3904": "Chemicals", "3906": "Chemicals",
    # Agriculture
    "1001": "Agriculture", "1201": "Agriculture", "0901": "Agriculture",
    "0902": "Agriculture", "1005": "Agriculture", "1507": "Agriculture",
    "0803": "Agriculture", "0806": "Agriculture", "2301": "Agriculture",
    # Metals & Steel
    "7208": "Metals & Steel", "7209": "Metals & Steel", "7210": "Metals & Steel",
    "7213": "Metals & Steel", "7214": "Metals & Steel", "7225": "Metals & Steel",
    "7204": "Metals & Steel", "7219": "Metals & Steel", "7407": "Metals & Steel",
    # Plastics
    "3923": "Plastics", "3926": "Plastics", "3920": "Plastics",
    "3921": "Plastics", "3924": "Plastics",
    # Furniture & Household
    "9401": "Furniture", "9403": "Furniture", "9404": "Furniture",
    "9405": "Furniture",
    # Pharmaceuticals
    "3004": "Pharmaceuticals", "3002": "Pharmaceuticals", "3003": "Pharmaceuticals",
}

CATEGORY_COLORS: dict[str, str] = {
    "Electronics": "#4A90D9",
    "Machinery": "#7B68EE",
    "Automotive": "#E67E22",
    "Apparel": "#E91E63",
    "Chemicals": "#27AE60",
    "Agriculture": "#8BC34A",
    "Metals & Steel": "#9E9E9E",
    "Plastics": "#00BCD4",
    "Furniture": "#FF9800",
    "Pharmaceuticals": "#F44336",
    "Other": "#607D8B",
}

ALL_CATEGORIES = sorted(set(HS_TO_CATEGORY.values()))


def get_category(hs_code: str) -> str:
    """Return human-readable category for a 4-digit HS code."""
    code = str(hs_code).strip()[:4]
    return HS_TO_CATEGORY.get(code, "Other")


def get_color(category: str) -> str:
    """Return hex color for a product category."""
    return CATEGORY_COLORS.get(category, CATEGORY_COLORS["Other"])


def group_by_category(hs_codes: list[str]) -> dict[str, list[str]]:
    """Group a list of HS codes by their product category."""
    groups: dict[str, list[str]] = {}
    for code in hs_codes:
        cat = get_category(code)
        groups.setdefault(cat, []).append(code)
    return groups
