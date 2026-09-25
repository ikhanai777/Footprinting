"""High-level operations shared by the CLI, the Python API and the MCP server.

Every function takes an `AlgoliaClient` and a dict of search options (see `query.OPTION_KEYS`)
and returns plain JSON-serialisable data.
"""

from __future__ import annotations

import os
import re
import statistics
from typing import Any, Dict, Iterator, List, Optional, Tuple

from . import export
from .client import DEFAULT_API_KEY, DEFAULT_APP_ID, AlgoliaClient
from .query import Resolved, available_specs, child_categories, excluded, resolve
from .scraper import count as _count
from .scraper import iter_listings
from .sources import NUMERIC, PRESETS, TARGETS, resolve_target

DEFAULT_MAX_SCAN = 20_000
SORT_ALIASES = {"newest": "-added", "latest": "-added", "oldest": "added", "cheapest": "price",
                "expensive": "-price", "lowest-km": "kilometers", "low-mileage": "kilometers"}
STAT_FIELDS = ("price", "year", "kilometers", "bedrooms", "bathrooms", "size", "daily_rental_price",
               "monthly_rental_price")

# Named options each family understands (shown by `filters`).
NAMED_OPTIONS = {
    "motors": ["query", "category", "make", "model", "city", "price", "year", "km", "seller (dealer|owner|certified)",
               "spec key=value (see specs)", "since_days", "where", "exclude"],
    "property": ["query", "category", "city", "area", "price", "beds", "baths", "size", "furnished",
                 "rent_period (yearly|monthly|quarterly)", "seller (agent|landlord)", "year (handover)",
                 "since_days", "where", "exclude"],
    "classified": ["query", "category", "city", "price", "spec key=value (see specs)", "since_days", "where",
                   "exclude"],
    "jobs": ["query", "category", "city", "since_days", "where", "exclude"],
    "community": ["query", "category", "city", "price", "since_days", "where", "exclude"],
    "other": ["query", "category", "price", "since_days", "where", "exclude"],
}
_NOISY_FACET = re.compile(r"(^|\.)(ar|slug|v2_key|ordering|label|lpv_card_ordering|uuids?|ids?)(\.|$)|names_ar|"
                          r"^seo_links|absolute_url|map_geo|_geoloc|^tags|^objectID|^allowed_pages|^language$|"
                          r"site_categories_slug_tree|Questions|^score$|^usage$|^content_type$|^added$")


def make_client(app_id: Optional[str] = None, api_key: Optional[str] = None, delay: float = 0.25) -> AlgoliaClient:
    return AlgoliaClient(app_id or os.environ.get("DUBIZZLE_ALGOLIA_APP_ID") or DEFAULT_APP_ID,
                         api_key or os.environ.get("DUBIZZLE_ALGOLIA_API_KEY") or DEFAULT_API_KEY,
                         delay=delay)


def _fields_for(r: Resolved, fields: Any) -> Optional[List[str]]:
    """None -> every column; "compact" -> the section's preset; list/"a,b" -> those columns."""
    if fields in (None, "", "all"):
        return None
    if isinstance(fields, str):
        if fields in ("compact", "basic", "summary"):
            return PRESETS.get(r.target.family, PRESETS["other"])
        fields = [f.strip() for f in fields.split(",") if f.strip()]
    return list(fields)


def parse_sort(sort: Optional[str]) -> Optional[Tuple[str, bool]]:
    if not sort:
        return None
    sort = SORT_ALIASES.get(sort.lower().strip(), sort.strip())
    m = re.match(r"^([\w.]+)[:\s]+(asc|desc)$", sort, re.I)
    if m:
        sort = ("-" if m.group(2).lower() == "desc" else "") + m.group(1)
    desc = sort.startswith("-")
    key = sort.lstrip("-+")
    key = {"km": "kilometers", "beds": "bedrooms", "baths": "bathrooms", "date": "added"}.get(key, key)
    return key, desc


def _sort_value(hit: Dict[str, Any], key: str) -> Any:
    value: Any = hit
    for part in key.split("."):
        value = value.get(part) if isinstance(value, dict) else None
    if isinstance(value, dict):
        value = value.get("en")
    return value


def sort_hits(hits: List[Dict[str, Any]], sort: Optional[str]) -> List[Dict[str, Any]]:
    parsed = parse_sort(sort)
    if not parsed:
        return hits
    key, desc = parsed
    present = [h for h in hits if _sort_value(h, key) not in (None, "")]
    missing = [h for h in hits if _sort_value(h, key) in (None, "")]
    try:
        present.sort(key=lambda h: _sort_value(h, key), reverse=desc)
    except TypeError:
        present.sort(key=lambda h: str(_sort_value(h, key)), reverse=desc)
    return present + missing


def iter_hits(client: AlgoliaClient, opts: Dict[str, Any], r: Optional[Resolved] = None,
              attributes: Optional[List[str]] = None) -> Tuple[Resolved, Iterator[Dict[str, Any]]]:
    """Resolve options and stream matching raw hits (sorted if `sort` is set; sorting scans everything)."""
    r = r or resolve(client, opts)
    limit = opts.get("limit")
    limit = int(limit) if limit not in (None, "", 0) else None
    keep = (lambda h: not excluded(h, r.exclude)) if r.exclude else None
    if not opts.get("sort"):
        return r, iter_listings(client, r.index, query=r.query, filters=r.filters, limit=limit,
                                attributes=attributes, keep=keep)
    max_scan = int(opts.get("max_scan") or DEFAULT_MAX_SCAN)
    total = _count(client, r.index, query=r.query, filters=r.filters)
    if total > max_scan:
        raise ValueError(f"sorting needs every match, but {total} listings match (max_scan={max_scan}). "
                         f"Narrow the search or raise max_scan.")
    hits = sort_hits(list(iter_listings(client, r.index, query=r.query, filters=r.filters,
                                        attributes=attributes, keep=keep)), opts.get("sort"))
    return r, iter(hits[:limit] if limit else hits)


def search(client: AlgoliaClient, opts: Dict[str, Any], default_limit: Optional[int] = 20) -> Dict[str, Any]:
    opts = dict(opts)
    if opts.get("limit") in (None, ""):
        opts["limit"] = default_limit
    r, hits = iter_hits(client, opts)
    lang = opts.get("lang") or "en"
    fields = _fields_for(r, opts.get("fields", "compact"))
    rows = [export.select(export.flatten(h, lang), fields) for h in hits]
    total = _count(client, r.index, query=r.query, filters=r.filters)
    return {"total_matches": total, "returned": len(rows), "search": r.describe(), "results": rows}


def count(client: AlgoliaClient, opts: Dict[str, Any]) -> Dict[str, Any]:
    r = resolve(client, opts)
    out = {"count": _count(client, r.index, query=r.query, filters=r.filters), "search": r.describe()}
    if r.exclude:
        out["note"] = "count does not apply `exclude` (it is a client-side filter)"
    return out


def _summary(values: List[float]) -> Dict[str, Any]:
    values = sorted(values)
    q = lambda p: values[min(len(values) - 1, int(round(p * (len(values) - 1))))]  # noqa: E731
    return {"n": len(values), "min": values[0], "p10": q(0.1), "p25": q(0.25), "median": statistics.median(values),
            "p75": q(0.75), "p90": q(0.9), "max": values[-1], "mean": round(statistics.fmean(values), 2)}


def stats(client: AlgoliaClient, opts: Dict[str, Any], by: Optional[str] = None, top: int = 25) -> Dict[str, Any]:
    """Market summary of every match: price/year/km/size distributions and an optional breakdown."""
    opts = {**opts, "limit": None, "sort": None}
    r = resolve(client, opts)
    total = _count(client, r.index, query=r.query, filters=r.filters)
    max_scan = int(opts.get("max_scan") or DEFAULT_MAX_SCAN)
    if total > max_scan:
        raise ValueError(f"{total} listings match (max_scan={max_scan}); narrow the search or raise max_scan")
    lang = opts.get("lang") or "en"
    attrs = list(STAT_FIELDS) + ["added", "name", "description", "objectID", "id"]
    if by:
        attrs.append(by.split(".")[0])
    _, hits = iter_hits(client, opts, r, attributes=attrs)
    hits = list(hits)
    out: Dict[str, Any] = {"matches": len(hits), "search": r.describe()}
    for f in STAT_FIELDS:
        vals = [float(h[f]) for h in hits if isinstance(h.get(f), (int, float)) and h[f] > 0]
        if vals:
            out[f] = _summary(vals)
    if by:
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for h in hits:
            key = export.flatten(h, lang).get(by)
            groups.setdefault("(none)" if key in (None, "") else str(key), []).append(h)
        rows = []
        for key, members in groups.items():
            prices = [float(m["price"]) for m in members if isinstance(m.get("price"), (int, float)) and m["price"] > 0]
            rows.append({by: key, "count": len(members),
                         "median_price": statistics.median(prices) if prices else None,
                         "min_price": min(prices) if prices else None})
        rows.sort(key=lambda x: -x["count"])
        out["by"] = rows[:top]
    return out


def filters(client: AlgoliaClient, opts: Dict[str, Any], top: int = 15, facet: Optional[str] = None
            ) -> Dict[str, Any]:
    """Everything that can be filtered on for this target/category, with current counts.

    With `facet`, return every value (up to 1000) of that one facet instead.
    """
    r = resolve(client, opts)
    if facet:
        res = client.search(r.index, {"query": r.query, "hitsPerPage": 0, "filters": r.filters, "facets": [facet],
                                      "maxValuesPerFacet": 1000, "analytics": False})
        values = res.get("facets", {}).get(facet)
        if values is None:
            raise ValueError(f"{facet!r} is not a facet of {r.index}; see list_filters without facet")
        return {"index": r.index, "facet": facet, "values": values, "search": r.describe()}
    res = client.search(r.index, {"query": r.query, "hitsPerPage": 0, "filters": r.filters, "facets": ["*"],
                                  "maxValuesPerFacet": top, "analytics": False})
    facets = {k: dict(list(v.items())[:top]) for k, v in sorted(res.get("facets", {}).items())
              if not _NOISY_FACET.search(k)}
    out: Dict[str, Any] = {
        "target": opts.get("target") or "cars", "index": r.index, "total": res.get("nbHits"),
        "search": r.describe(),
        "named_options": NAMED_OPTIONS.get(r.target.family, NAMED_OPTIONS["other"]),
        "numeric_attributes": NUMERIC.get(r.target.family, NUMERIC["other"]),
        "subcategories": child_categories(client, r.index, r.category, r.filters),
        "facets_for_where": facets,
    }
    if r.target.family in ("motors", "classified"):
        specs = available_specs(client, r.index, r.filters)
        if specs:
            out["specs"] = specs
    out["examples"] = _examples(r.target.family)
    return out


def _examples(family: str) -> List[str]:
    return {
        "motors": ['make=lexus model=rx year=2021', 'spec regional-specs=gcc-specs', 'spec fuel-type=hybrid|electric',
                   'km=..60000 price=50000..120000', 'city=dubai seller=owner', "where 'details.Year.en.value=2021'"],
        "property": ['city=dubai area="dubai marina" beds=2 furnished=yes', 'price=..90000 rent_period=yearly',
                     'size=1500+ baths=2+', "where 'amenities_v2.value=private_pool'"],
        "classified": ['category=classified/mobile-phones-pdas query="iphone 15" price=..3000',
                       "where 'details.Brand.en.value=Apple'"],
        "jobs": ['city=dubai query="accountant"', "where 'details.Employment Type.en.value=Full Time'"],
    }.get(family, ['query="..." price=..1000'])


def categories(client: AlgoliaClient, opts: Dict[str, Any]) -> Dict[str, int]:
    r = resolve(client, opts)
    return child_categories(client, r.index, r.category, r.filters)


def get_listing(client: AlgoliaClient, listing_id: Any, target: Optional[str] = None,
                lang: str = "en") -> Dict[str, Any]:
    indices = [resolve_target(target).index] if target else list(dict.fromkeys(t.index for t in TARGETS.values()))
    lid = str(listing_id).strip()
    if lid.isdigit():
        params = {"hitsPerPage": 1, "filters": f"id = {lid}"}
        wanted = None
    else:  # a listing URL: search its title words inside its category, then match the URL
        m = re.match(r"^(?:https?://)?[^/]+/(.+?)/\d{4}/\d{1,2}/\d{1,2}/([^/?#]+)", lid)
        if not m:
            raise ValueError("pass a numeric listing id or a full dubizzle listing URL")
        category, slug = m.groups()
        slug = slug.split("---")[0]
        words = [w for w in slug.split("-") if not w.isdigit()]
        params = {"hitsPerPage": 100, "query": " ".join(words[:8]), "filters": f'category_v2.slug_paths:"{category}"'}
        wanted = "/" + category + m.group(0).split(category, 1)[1]  # URL path from the category on
        if not target:
            indices = sorted(indices, key=lambda i: not category.startswith(i.split(".")[0].split("-for-")[0]))
    for index in indices:
        try:
            res = client.search(index, {**params, "attributesToHighlight": [], "analytics": False})
        except Exception:
            continue
        for hit in res.get("hits", []):
            url = export._localize(hit.get("absolute_url"), "en") or ""
            if wanted is None or wanted.rstrip("/") in str(url).rstrip("/"):
                return {"index": index, "listing": export.flatten(hit, lang)}
    raise ValueError(f"listing {listing_id} not found")


def export_to_file(client: AlgoliaClient, opts: Dict[str, Any], path: str, fmt: Optional[str] = None) -> Dict[str, Any]:
    fmt = fmt or os.path.splitext(path)[1].lstrip(".").lower() or "csv"
    if fmt not in ("csv", "json", "jsonl"):
        raise ValueError("format must be csv, json or jsonl")
    r, hits = iter_hits(client, opts)
    fields = _fields_for(r, opts.get("fields"))
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as out:
        n = export.write(hits, out, fmt, lang=opts.get("lang") or "en", fields=fields)
    return {"path": path, "format": fmt, "written": n, "search": r.describe()}
