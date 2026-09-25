"""Turn friendly search options into an Algolia query + filter string.

The same option names are used by the CLI, the Python API and the MCP server:

    target, query, category, make, model, city, area, price, year, km, beds, baths,
    size, furnished, seller, rent_period, spec, where, since_days, exclude

Range options accept 5, "5", "3..7", "3+", "..7", [3, 7] or {"min": 3, "max": 7}.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .client import AlgoliaClient
from .sources import (MOTORS_SELLER, PROPERTY_LISTED_BY, RENT_PERIODS, Target, resolve_city,
                      resolve_target)

OPTION_KEYS = ("target", "query", "category", "make", "model", "city", "area", "price", "year", "km",
               "beds", "baths", "size", "furnished", "seller", "rent_period", "spec", "where",
               "since_days", "exclude")


@dataclass
class Resolved:
    target: Target
    query: str = ""
    category: Optional[str] = None
    clauses: List[str] = field(default_factory=list)
    exclude: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def index(self) -> str:
        return self.target.index

    @property
    def filters(self) -> Optional[str]:
        return " AND ".join(self.clauses) or None

    def describe(self) -> Dict[str, Any]:
        return {"index": self.index, "query": self.query, "category": self.category,
                "filters": self.filters, "exclude": self.exclude, "notes": self.notes}


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")


def _quote(value: str) -> str:
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _attr(name: str) -> str:
    """Attribute names with spaces etc. ("details.Body Type.en.value") must be quoted."""
    return name if re.fullmatch(r"[\w.]+", name) else _quote(name)


def _num(value: Any) -> str:
    f = float(value)
    return str(int(f)) if f.is_integer() else repr(f)


def parse_range(value: Any) -> Tuple[Optional[float], Optional[float]]:
    """5 | "5" | "3..7" | "3-7" | "3+" | "..7" | [3, 7] | {"min": 3, "max": 7} -> (lo, hi)."""
    if value is None or value == "":
        return None, None
    if isinstance(value, dict):
        lo, hi = value.get("min"), value.get("max")
        return (None if lo is None else float(lo)), (None if hi is None else float(hi))
    if isinstance(value, (list, tuple)):
        lo, hi = (list(value) + [None, None])[:2]
        return (None if lo in (None, "") else float(lo)), (None if hi in (None, "") else float(hi))
    if isinstance(value, (int, float)):
        return float(value), float(value)
    s = str(value).strip().replace(",", "").replace("_", "")
    if s.endswith("+"):
        return float(s[:-1]), None
    m = re.fullmatch(r"(-?[\d.]*)\s*(?:\.\.|\s-\s|-|to)\s*(-?[\d.]*)", s)
    if m and (m.group(1) or m.group(2)) and not re.fullmatch(r"-[\d.]+", s):
        lo, hi = m.groups()
        return (float(lo) if lo else None), (float(hi) if hi else None)
    return float(s), float(s)


def range_clause(attr: str, value: Any) -> Optional[str]:
    attr = _attr(attr)
    lo, hi = parse_range(value)
    if lo is not None and hi is not None:
        return f"{attr} = {_num(lo)}" if lo == hi else f"{attr}:{_num(lo)} TO {_num(hi)}"
    if lo is not None:
        return f"{attr} >= {_num(lo)}"
    if hi is not None:
        return f"{attr} <= {_num(hi)}"
    return None


_WHERE = re.compile(r"^\s*(.+?)\s*(>=|<=|!=|=|>|<|:)\s*(.*?)\s*$")
_NUMBER = re.compile(r"^-?\d+(\.\d+)?$")


def parse_where(expr: str) -> str:
    """Generic filter on any facet or numeric attribute.

    `a=v`, `a=v1|v2` (OR), `a!=v`, `a>=n`, `a<n`, `a=lo..hi`, `a="2021"` (force string facet).
    Anything already looking like Algolia syntax (contains ' AND ', ' OR ', 'NOT ', ' TO ') is passed through.
    """
    if re.search(r"\s(AND|OR|TO)\s|^NOT\s|\(", expr):
        return expr
    m = _WHERE.match(expr)
    if not m:
        raise ValueError(f"cannot parse filter {expr!r}; use attr=value, attr>=n, attr=lo..hi")
    name, op, raw = m.groups()
    attr = _attr(name.strip("\"'"))
    if op == ":":
        op = "="
    if op in (">", "<", ">=", "<="):
        return f"{attr} {op} {_num(raw)}"
    if ".." in raw:
        clause = range_clause(name.strip("\"'"), raw)
        return f"NOT {clause}" if op == "!=" and clause else clause
    parts = []
    for v in raw.split("|"):
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            parts.append(f"{attr}:{_quote(v[1:-1])}")
        elif _NUMBER.match(v):
            parts.append(f"{attr} = {v}")
        elif v.lower() in ("true", "false"):
            parts.append(f"{attr}:{v.lower()}")
        else:
            parts.append(f"{attr}:{_quote(v)}")
    if op == "!=":
        return " AND ".join(f"NOT {p}" for p in parts)
    return parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"


def _as_list(value: Any) -> List[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _spec_pairs(spec: Any) -> List[Tuple[str, str]]:
    if isinstance(spec, dict):
        return [(k, str(v)) for k, vals in spec.items() for v in _as_list(vals)]
    if isinstance(spec, str):
        spec = [x for x in spec.split(",") if x.strip()]
    pairs = []
    for item in _as_list(spec):
        if "=" not in str(item):
            raise ValueError(f"spec {item!r} must look like key=value, e.g. fuel-type=hybrid")
        k, v = str(item).split("=", 1)
        pairs.append((k.strip(), v.strip()))
    return pairs


def _truthy(value: Any) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "y", "furnished")


# --- lookups against the index -------------------------------------------------------------

def child_categories(client: AlgoliaClient, index: str, parent: Optional[str], filters: Optional[str] = None
                     ) -> Dict[str, int]:
    res = client.search(index, {"hitsPerPage": 0, "filters": filters, "facets": ["category_v2.slug_paths"],
                                "maxValuesPerFacet": 1000, "analytics": False})
    paths = res.get("facets", {}).get("category_v2.slug_paths", {})
    depth = 0 if not parent else parent.count("/") + 1
    return {p: n for p, n in paths.items()
            if p.count("/") == depth and (not parent or p.startswith(parent + "/"))}


def match_category(client: AlgoliaClient, index: str, parent: str, name: str) -> Tuple[str, Optional[str]]:
    """Find the child category of `parent` that best matches `name` ("RX" -> .../rx-series)."""
    children = child_categories(client, index, parent, f"category_v2.slug_paths:{_quote(parent)}")
    want = slugify(name)
    last = {p: p.rsplit("/", 1)[-1] for p in children}
    for test in (lambda s: s == want,
                 lambda s: s.startswith(want + "-") or s.startswith(want),
                 lambda s: want in s,
                 lambda s: s.replace("-", "") == want.replace("-", "")):
        hits = [p for p in children if test(last[p])]
        if hits:
            best = max(hits, key=lambda p: children[p])
            note = None
            if len(hits) > 1 or last[best] != want:
                note = f"{name!r} matched {best}" + (f" (also: {', '.join(h for h in hits if h != best)})"
                                                     if len(hits) > 1 else "")
            return best, note
    options = ", ".join(sorted(v for v in last.values())[:60])
    raise ValueError(f"no category under {parent} matches {name!r}. Options: {options}")


_SPEC_LINK = re.compile(r"^(.*)/s/([^/]+)/([^/]+)/$")


def spec_links(client: AlgoliaClient, index: str, filters: Optional[str]) -> Dict[str, int]:
    res = client.search(index, {"hitsPerPage": 0, "filters": filters, "facets": ["seo_links.attribute_links"],
                                "maxValuesPerFacet": 1000, "analytics": False})
    return res.get("facets", {}).get("seo_links.attribute_links", {})


def _top_level(prefixed: Dict[str, int]) -> int:
    """Sum counts over prefixes, skipping any prefix nested under another one (it is a subset)."""
    keep = [p for p in prefixed if not any(q != p and p.startswith(q + "/") for q in prefixed)]
    return sum(prefixed[p] for p in keep)


def available_specs(client: AlgoliaClient, index: str, filters: Optional[str]) -> Dict[str, Dict[str, int]]:
    """{spec key: {value: approx count}} for the current result set (from the site's own filter links)."""
    grouped: Dict[Tuple[str, str], Dict[str, int]] = {}
    for link, n in spec_links(client, index, filters).items():
        m = _SPEC_LINK.match(link)
        if m:
            prefix, key, value = m.groups()
            grouped.setdefault((key, value), {})[prefix] = n
    out: Dict[str, Dict[str, int]] = {}
    for (key, value), prefixed in grouped.items():
        out.setdefault(key, {})[value] = _top_level(prefixed)
    return {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in sorted(out.items())}


def resolve_spec(client: AlgoliaClient, index: str, filters: Optional[str], key: str, value: str) -> List[str]:
    """All site filter links for key=value within the current results (ORed together by the caller).

    Cars have one category-wide link per value; classifieds often only have per-brand links.
    """
    want_k, want_v = slugify(key), slugify(value)
    res = client.search_facet(index, "seo_links.attribute_links", value.replace("-", " "), filters=filters,
                              max_hits=100)
    matches: Dict[Tuple[str, str], Dict[str, int]] = {}
    for hit in res:
        m = _SPEC_LINK.match(hit["value"])
        if not m:
            continue
        prefix, k, v = m.groups()
        if (k == want_k or k.startswith(want_k) or want_k in k) and (v == want_v or v.startswith(want_v)):
            matches.setdefault((k, v), {})[prefix] = hit.get("count", 0)
    if not matches:
        specs = available_specs(client, index, filters)
        if want_k in specs or any(want_k in k for k in specs):
            key_name = want_k if want_k in specs else next(k for k in specs if want_k in k)
            raise ValueError(f"no {key_name}={value!r}. Values: {', '.join(specs[key_name])}")
        raise ValueError(f"unknown spec {key!r}. Keys: {', '.join(specs) or '(none for this section)'}")
    (k, v), prefixed = min(matches.items(), key=lambda kv: (kv[0][1] != want_v, kv[0][0] != want_k,
                                                            -_top_level(kv[1])))
    keep = [p for p in prefixed if not any(q != p and p.startswith(q + "/") for q in prefixed)]
    return [f"{p}/s/{k}/{v}/" for p in sorted(keep)]


def resolve_area(client: AlgoliaClient, r: Resolved, area: str) -> Optional[str]:
    if r.target.family != "property" or not r.target.top:
        return None
    res = client.search(r.index, {"hitsPerPage": 0, "filters": r.filters, "facets": ["seo_links.location_links"],
                                  "maxValuesPerFacet": 1000, "analytics": False})
    links = res.get("facets", {}).get("seo_links.location_links", {})
    want = slugify(area)
    pat = re.compile(rf"^{re.escape(r.target.top)}/in/(?:[^/]+/\d+/)*([^/]+)/(\d+)$")
    scored = []
    for link, n in links.items():
        m = pat.match(link)
        if not m:
            continue
        slug = m.group(1)
        if slug == want:
            rank = 0
        elif f"-{want}-" in f"-{slug}-":  # whole words: "jvc" in "jumeirah-village-circle-jvc"
            rank = 1
        elif slug.startswith(want) or want in slug:
            rank = 2
        elif want.replace("-", "") in slug.replace("-", ""):
            rank = 3
        else:
            continue
        # Prefer the best match, then the broadest area (fewest /in/ levels), then the most listings.
        scored.append((rank, link.count("/"), -n, link))
    return sorted(scored)[0][-1] if scored else None


# --- main entry point ------------------------------------------------------------------------

def resolve(client: AlgoliaClient, opts: Dict[str, Any]) -> Resolved:
    unknown = set(k for k, v in opts.items() if v not in (None, "", [], {})) - set(OPTION_KEYS) - {
        "sort", "limit", "fields", "lang", "max_scan", "by", "output", "format"}
    if unknown:
        raise ValueError(f"unknown option(s): {', '.join(sorted(unknown))}")
    target = resolve_target(opts.get("target") or "cars")
    r = Resolved(target, query=str(opts.get("query") or "").strip())
    fam = target.family

    # Category, optionally narrowed by make / model.
    category = opts.get("category") or target.category
    if category and "/" not in category and (target.category or target.top):
        category = f"{target.category or target.top}/{slugify(category)}"
    for level in ("make", "model"):
        name = opts.get(level)
        if not name:
            continue
        if not category:
            if fam != "motors":
                raise ValueError(f"{level} only applies to motors targets")
            category = "motors/used-cars"
        category, note = match_category(client, target.index, category, str(name))
        if note:
            r.notes.append(note)
    r.category = category
    if category:
        r.clauses.append(f"category_v2.slug_paths:{_quote(category)}")

    # City.
    if opts.get("city"):
        cities = [resolve_city(c) for c in str(opts["city"]).split("|")] if isinstance(opts["city"], str) \
            else [resolve_city(c) for c in _as_list(opts["city"])]
        if fam in ("motors", "jobs"):
            parts = [f"site.id = {cid}" for cid, _ in cities]
        elif fam == "property":
            parts = [f"city.id = {cid}" for cid, _ in cities]
        else:
            top = (category or target.top or target.index.split(".")[0]).split("/")[0]
            parts = [f"site_categories_slug_tree:{_quote(f'{slug}/{top}')}" for _, slug in cities]
        r.clauses.append(parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")")

    # Numeric ranges.
    ranges = {"price": "price", "year": "handover_year" if fam == "property" else "year", "km": "kilometers",
              "beds": "bedrooms", "baths": "bathrooms", "size": "size"}
    for opt, attr in ranges.items():
        if opts.get(opt) not in (None, ""):
            if opt in ("km",) and fam != "motors" or opt in ("beds", "baths", "size") and fam != "property":
                raise ValueError(f"{opt} does not apply to {target.index}")
            clause = range_clause(attr, opts[opt])
            if clause:
                r.clauses.append(clause)

    if opts.get("since_days") not in (None, ""):
        r.clauses.append(f"added >= {int(time.time() - float(opts['since_days']) * 86400)}")

    if opts.get("furnished") not in (None, ""):
        if fam != "property":
            raise ValueError("furnished only applies to property targets")
        r.clauses.append(f"furnished:{'true' if _truthy(opts['furnished']) else 'false'}")

    if opts.get("rent_period"):
        period = RENT_PERIODS.get(str(opts["rent_period"]).lower(), str(opts["rent_period"]))
        r.clauses.append(f"rent_is_paid.name.en:{_quote(period)}")

    # Seller: motors via the site's spec links, property via listed_by.
    spec_pairs = _spec_pairs(opts.get("spec"))
    if opts.get("seller"):
        s = str(opts["seller"]).lower()
        if fam == "motors":
            spec_pairs.append(("seller-type", MOTORS_SELLER.get(s, s)))
        elif fam == "property":
            r.clauses.append(f"listed_by.value:{_quote(PROPERTY_LISTED_BY.get(s, s.upper()))}")
        else:
            raise ValueError("seller only applies to motors and property targets")

    # Specs (body type, fuel, colour, specs region, features...). Several values of a key are ORed.
    if spec_pairs:
        base = r.filters
        grouped: Dict[str, List[str]] = {}
        for key, value in spec_pairs:
            for v in value.split("|"):
                links = resolve_spec(client, target.index, base, key, v.strip())
                grouped.setdefault(links[0].split("/s/")[1].split("/")[0], []).extend(
                    f"seo_links.attribute_links:{_quote(link)}" for link in links)
        for parts in grouped.values():
            r.clauses.append(parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")")

    for expr in _as_list(opts.get("where")):
        r.clauses.append(parse_where(str(expr)))

    if opts.get("area"):
        link = resolve_area(client, r, str(opts["area"]))
        if link:
            r.clauses.append(f"seo_links.location_links:{_quote(link)}")
            r.notes.append(f"area {opts['area']!r} matched {link}")
        else:
            r.query = f"{r.query} {opts['area']}".strip()
            r.notes.append(f"area {opts['area']!r} applied as a text search")

    r.exclude = [str(x).lower() for x in _as_list(opts.get("exclude")) if str(x).strip()]
    return r


def excluded(hit: Dict[str, Any], words: Iterable[str]) -> bool:
    words = list(words)
    if not words:
        return False
    name = hit.get("name")
    text = " ".join(str(v) for v in name.values()) if isinstance(name, dict) else str(name or "")
    desc = hit.get("description")
    text += " " + (" ".join(str(v) for v in desc.values()) if isinstance(desc, dict) else str(desc or ""))
    text = text.lower()
    return any(w in text for w in words)
