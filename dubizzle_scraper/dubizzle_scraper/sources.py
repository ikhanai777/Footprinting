"""What dubizzle exposes through Algolia: targets, cities, and per-section fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Target:
    index: str
    family: str                     # motors | property | classified | jobs | community
    category: Optional[str] = None  # default category path filter
    top: str = ""                   # top-level category slug, e.g. "motors", "property-for-rent/residential"


TARGETS: Dict[str, Target] = {
    "cars": Target("motors.com", "motors", "motors/used-cars", "motors"),
    "rental-cars": Target("motors.com", "motors", "motors/rental-cars", "motors"),
    "motorcycles": Target("motors.com", "motors", "motors/motorcycles", "motors"),
    "boats": Target("motors.com", "motors", "motors/boats", "motors"),
    "heavy-vehicles": Target("motors.com", "motors", "motors/heavy-vehicles", "motors"),
    "number-plates": Target("motors.com", "motors", "motors/number-plates", "motors"),
    "auto-parts": Target("motors.com", "motors", "motors/auto-accessories-parts", "motors"),
    "motors": Target("motors.com", "motors", None, "motors"),
    "property-rent": Target("property-for-rent-residential.com", "property", None, "property-for-rent/residential"),
    "property-sale": Target("property-for-sale-residential.com", "property", None, "property-for-sale/residential"),
    "commercial-rent": Target("property-for-rent-commercial.com", "property", None, "property-for-rent/commercial"),
    "commercial-sale": Target("property-for-sale-commercial.com", "property", None, "property-for-sale/commercial"),
    "classifieds": Target("classified.com", "classified", None, "classified"),
    "jobs": Target("jobs.com", "jobs", None, "jobs"),
    "community": Target("community.com", "community", None, "community"),
}

_INDEX_FAMILY = {t.index: (t.family, t.top) for t in TARGETS.values() if t.category is None}
_INDEX_FAMILY["motors.com"] = ("motors", "motors")


def resolve_target(name: str) -> Target:
    """Alias -> Target. Unknown names are treated as raw Algolia index names."""
    if name in TARGETS:
        return TARGETS[name]
    family, top = _INDEX_FAMILY.get(name, ("other", ""))
    return Target(name, family, None, top)


# name -> (site id used by motors/jobs/property, slug used in site_categories_slug_tree)
CITIES: Dict[str, Tuple[int, str]] = {
    "dubai": (2, "dubai"),
    "abu dhabi": (3, "abudhabi"),
    "sharjah": (12, "sharjah"),
    "ajman": (14, "ajman"),
    "ras al khaimah": (11, "rak"),
    "fujairah": (13, "fujairah"),
    "umm al quwain": (15, "uaq"),
    "al ain": (39, "alain"),
}
_CITY_ALIASES = {"abudhabi": "abu dhabi", "ad": "abu dhabi", "auh": "abu dhabi", "dxb": "dubai",
                 "shj": "sharjah", "rak": "ras al khaimah", "uaq": "umm al quwain", "alain": "al ain"}


def resolve_city(name: str) -> Tuple[int, str]:
    key = " ".join(name.lower().replace("-", " ").replace("_", " ").split())
    key = _CITY_ALIASES.get(key.replace(" ", ""), key)
    if key not in CITIES:
        raise ValueError(f"unknown city {name!r}; choose from: {', '.join(CITIES)}")
    return CITIES[key]


# Numeric attributes worth filtering on, per family (all support >, <, ranges).
NUMERIC: Dict[str, List[str]] = {
    "motors": ["price", "year", "kilometers", "added", "daily_rental_price", "weekly_rental_price",
               "monthly_rental_price"],
    "property": ["price", "bedrooms", "bathrooms", "size", "plot_area", "handover_year", "added"],
    "classified": ["price", "added"],
    "jobs": ["added"],
    "community": ["price", "added"],
    "other": ["price", "added"],
}

# Compact columns for agents and quick looks. `added_date` and `url` are derived.
PRESETS: Dict[str, List[str]] = {
    "motors": ["id", "name", "price", "year", "kilometers", "details.Make", "details.Model", "details.Trim",
               "details.Regional Specs", "details.Body Type", "details.Fuel Type", "details.Transmission Type",
               "details.Exterior Color", "details.Seller Type", "site", "neighbourhood", "added_date", "url"],
    "property": ["id", "name", "price", "rent_is_paid.name", "bedrooms", "bathrooms", "size", "furnished",
                 "categories_v2.name", "city.name", "neighborhoods.name", "building.name", "listed_by",
                 "agent.name", "added_date", "url"],
    "classified": ["id", "name", "price", "details.Condition", "details.Brand", "details.Age", "category_v2.names_en",
                   "site", "neighbourhood", "added_date", "url"],
    "jobs": ["id", "name", "details.Company Name", "details.Monthly Salary", "details.Employment Type",
             "details.Minimum Work Experience", "details.Industry", "site", "added_date", "url"],
    "community": ["id", "name", "price", "category_v2.names_en", "site", "neighbourhood", "added_date", "url"],
    "other": ["id", "name", "price", "added_date", "url"],
}

# Human-friendly seller names.
MOTORS_SELLER = {"dealer": "dealer", "owner": "owner", "private": "owner",
                 "certified": "dealershipcertified-pre-owned", "cpo": "dealershipcertified-pre-owned"}
PROPERTY_LISTED_BY = {"agent": "AG", "agency": "AG", "landlord": "LA", "owner": "LA", "developer": "DV"}
RENT_PERIODS = {"yearly": "Yearly", "monthly": "Monthly", "quarterly": "Quarterly", "bi-yearly": "Bi-Yearly",
                "weekly": "Weekly", "daily": "Daily"}
