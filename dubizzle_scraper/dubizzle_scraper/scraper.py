"""Walk a dubizzle Algolia index and yield every matching listing.

Algolia only lets a single query page through the first N hits (10,000 on
dubizzle's indices). To get past that, a result set that is too large is
split into time windows on the `added` timestamp, bisected until every window
fits, and each window is paged through on its own.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from .client import AlgoliaClient

log = logging.getLogger(__name__)

# Friendly names -> (index, category path filter or None)
ALIASES: Dict[str, Tuple[str, Optional[str]]] = {
    "cars": ("motors.com", "motors/used-cars"),
    "motors": ("motors.com", None),
    "classifieds": ("classified.com", None),
    "jobs": ("jobs.com", None),
    "community": ("community.com", None),
    "property-rent": ("property-for-rent-residential.com", None),
    "property-sale": ("property-for-sale-residential.com", None),
    "commercial-rent": ("property-for-rent-commercial.com", None),
    "commercial-sale": ("property-for-sale-commercial.com", None),
}

MAX_WINDOW = 10_000   # hits reachable by paging one query
# nbHits is only an estimate on large result sets, so split well below the cap.
SPLIT_ABOVE = 8_000
HITS_PER_PAGE = 1000  # Algolia's maximum
TIME_FIELD = "added"


def resolve(target: str) -> Tuple[str, Optional[str]]:
    return ALIASES.get(target, (target, None))


def build_filters(category: Optional[str] = None, filters: Optional[str] = None) -> Optional[str]:
    parts = []
    if category:
        parts.append(f'category_v2.slug_paths:"{category}"')
    if filters:
        parts.append(f"({filters})")
    return " AND ".join(parts) or None


def count(client: AlgoliaClient, index: str, *, query: str = "", filters: Optional[str] = None,
          numeric_filters: Optional[List[str]] = None) -> int:
    res = client.search(index, {
        "query": query, "hitsPerPage": 0, "filters": filters,
        "numericFilters": numeric_filters or None, "analytics": False,
    })
    return int(res.get("nbHits", 0))


def categories(client: AlgoliaClient, index: str, *, query: str = "",
               filters: Optional[str] = None) -> Dict[str, int]:
    """Category paths (usable with --category) and their listing counts."""
    res = client.search(index, {
        "query": query, "hitsPerPage": 0, "filters": filters,
        "facets": ["category_v2.slug_paths"], "maxValuesPerFacet": 1000, "analytics": False,
    })
    facet = res.get("facets", {}).get("category_v2.slug_paths", {})
    return dict(sorted(facet.items()))


def _page_through(client: AlgoliaClient, index: str, params: Dict[str, Any],
                  hits_per_page: int = HITS_PER_PAGE) -> Iterator[Dict[str, Any]]:
    page = 0
    while True:
        res = client.search(index, {**params, "page": page, "hitsPerPage": hits_per_page})
        hits = res.get("hits", [])
        yield from hits
        page += 1
        if not hits or page >= int(res.get("nbPages", 0)):
            return


def iter_listings(
    client: AlgoliaClient,
    index: str,
    *,
    query: str = "",
    filters: Optional[str] = None,
    numeric_filters: Optional[List[str]] = None,
    limit: Optional[int] = None,
    attributes: Optional[List[str]] = None,
) -> Iterator[Dict[str, Any]]:
    """Yield raw Algolia hits for everything matching the query, newest windows first."""
    base = {
        "query": query,
        "filters": filters,
        "attributesToRetrieve": attributes or ["*"],
        "attributesToHighlight": [],
        "attributesToSnippet": [],
        "analytics": False,
    }
    base_numeric = list(numeric_filters or [])
    seen: Set[str] = set()
    yielded = 0
    per_page = min(HITS_PER_PAGE, limit) if limit else HITS_PER_PAGE

    # Stack of (lo, hi) inclusive `added` windows; None means "no time filter".
    stack: List[Optional[Tuple[int, int]]] = [None]
    while stack:
        window = stack.pop()
        numeric = base_numeric + ([f"{TIME_FIELD}:{window[0]} TO {window[1]}"] if window else [])
        n = count(client, index, query=query, filters=filters, numeric_filters=numeric)
        if n == 0:
            continue
        if n > SPLIT_ABOVE:
            lo, hi = window or (0, int(time.time()) + 86_400)
            if hi > lo:
                mid = (lo + hi) // 2
                log.info("%d hits in window %s, splitting at %d", n, window, mid)
                # Pushed last = popped first, so newer listings come out first.
                stack.append((lo, mid))
                stack.append((mid + 1, hi))
                continue
            log.warning("%d hits share %s=%d; only the first %d are reachable", n, TIME_FIELD, lo, MAX_WINDOW)
        log.info("fetching %d hits for window %s", n, window)
        for hit in _page_through(client, index, {**base, "numericFilters": numeric or None}, per_page):
            oid = hit.get("objectID") or str(hit.get("id"))
            if oid in seen:
                continue
            seen.add(oid)
            yield hit
            yielded += 1
            if limit is not None and yielded >= limit:
                return
