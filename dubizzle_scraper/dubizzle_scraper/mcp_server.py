"""Model Context Protocol server (stdio, standard library only).

Run with `dubizzle-scrape mcp` or `dubizzle-mcp`, and register it with an MCP client such as
Hermes Agent (~/.hermes/config.yaml -> mcp_servers). All tools return JSON text.
"""

from __future__ import annotations

import json
import sys
import traceback
from typing import Any, Callable, Dict, Optional

from . import __version__, service
from .client import AlgoliaError
from .sources import TARGETS

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MAX_LIMIT = 200

INSTRUCTIONS = """Search dubizzle UAE (cars, property, classifieds, jobs, community) with every site filter.
Workflow: 1) call list_filters for the target (and make/model/category) to see valid specs, facets and
sub-categories; 2) call count_listings to size the result; 3) search_listings (sorted, limited) or
market_stats for price analysis; 4) export_listings to save full data to a CSV/JSON file.
Ranges accept 5, "3..7", "3+", "..7". Prices are AED. Always give the user listing URLs."""

_RANGE = {"type": "string", "description": 'Exact value or range: "2021", "2019..2022", "3+", "..60000"'}
_LIST = {"type": "array", "items": {"type": "string"}}

FILTER_PROPS: Dict[str, Any] = {
    "target": {"type": "string", "enum": list(TARGETS), "default": "cars",
               "description": "Section to search. cars = used cars."},
    "query": {"type": "string", "description": "Free-text search, e.g. 'land cruiser gxr'"},
    "category": {"type": "string", "description": "Category path from list_categories, e.g. motors/used-cars/toyota"},
    "make": {"type": "string", "description": "Car make, fuzzy matched (motors targets)"},
    "model": {"type": "string", "description": "Car model, fuzzy matched ('rx' -> rx-series). Needs make."},
    "city": {"type": "string", "description": "dubai, abu dhabi, sharjah, ajman, ras al khaimah, fujairah, "
                                              "umm al quwain, al ain (a|b for several)"},
    "area": {"type": "string", "description": "Neighbourhood, e.g. 'dubai marina' (property targets)"},
    "price": _RANGE, "year": {**_RANGE, "description": "Model year (cars) or handover year (property). " + _RANGE["description"]},
    "km": {**_RANGE, "description": "Kilometers (cars). " + _RANGE["description"]},
    "beds": {**_RANGE, "description": "Bedrooms, 0 = studio (property)"},
    "baths": {**_RANGE, "description": "Bathrooms (property)"},
    "size": {**_RANGE, "description": "Size in sqft (property)"},
    "furnished": {"type": "boolean", "description": "Property only"},
    "seller": {"type": "string", "description": "cars: dealer|owner|certified; property: agent|landlord"},
    "rent_period": {"type": "string", "description": "yearly|monthly|quarterly (property for rent)"},
    "spec": {"type": "object", "additionalProperties": {"type": "string"},
             "description": 'Site filters from list_filters.specs, e.g. {"regional-specs": "gcc-specs", '
                            '"fuel-type": "hybrid|electric", "body-type": "suv"}'},
    "where": {**_LIST, "description": "Generic filters on any facet/numeric attribute from list_filters, e.g. "
                                      "['bedrooms>=2', 'site.en=Dubai|Sharjah', 'amenities_v2.value=private_pool']"},
    "since_days": {"type": "number", "description": "Only listings added in the last N days"},
    "exclude": {**_LIST, "description": "Drop listings whose title/description contains any of these words"},
    "lang": {"type": "string", "enum": ["en", "ar"], "default": "en"},
}


def _schema(extra: Optional[Dict[str, Any]] = None, required=()) -> Dict[str, Any]:
    return {"type": "object", "properties": {**FILTER_PROPS, **(extra or {})}, "required": list(required)}


TOOLS = [
    {"name": "search_listings",
     "description": "Search dubizzle listings with any filters and return compact rows (title, price, key specs, "
                    "location, date, URL). Use sort for cheapest/newest etc.",
     "inputSchema": _schema({
         "sort": {"type": "string", "description": "price, -price, year, -year, kilometers, -added, newest, "
                                                   "oldest, cheapest, expensive (sorting scans every match)"},
         "limit": {"type": "integer", "default": 20, "maximum": MAX_LIMIT},
         "fields": {"type": "string", "description": "'compact' (default), 'all', or comma-separated columns"},
     })},
    {"name": "count_listings", "description": "Count listings matching the filters (fast).",
     "inputSchema": _schema()},
    {"name": "market_stats",
     "description": "Price/year/km/size distribution (min, percentiles, median, max) of every matching listing, "
                    "optionally broken down by a column such as 'year', 'details.Regional Specs', 'site', "
                    "'neighborhoods.name', 'bedrooms'.",
     "inputSchema": _schema({"by": {"type": "string"}, "max_scan": {"type": "integer", "default": 20000}})},
    {"name": "list_filters",
     "description": "Discover every filter for a target/category: named options, numeric attributes, "
                    "sub-categories, site specs (cars: body type, fuel, colour, regional specs, features...), "
                    "and facet values usable in `where`. Call this before filtering on something unfamiliar. "
                    "Pass `facet` to get all values of one facet (the overview shows the top 15).",
     "inputSchema": _schema({"facet": {"type": "string", "description": "e.g. amenities_v2.value"}})},
    {"name": "list_categories", "description": "Sub-categories (makes, models, property types...) with counts.",
     "inputSchema": _schema()},
    {"name": "get_listing", "description": "Full details of one listing by numeric id or dubizzle URL.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "Numeric listing id or listing URL"},
         "target": {"type": "string", "enum": list(TARGETS)},
         "lang": {"type": "string", "enum": ["en", "ar"]}}, "required": ["id"]}},
    {"name": "export_listings",
     "description": "Scrape every matching listing (no 10k limit) to a CSV/JSON/JSONL file on this machine and "
                    "return its path. Use for bulk data.",
     "inputSchema": _schema({
         "path": {"type": "string", "description": "e.g. ~/dubizzle_exports/lexus_rx.csv"},
         "format": {"type": "string", "enum": ["csv", "json", "jsonl"]},
         "fields": {"type": "string", "description": "'all' (default), 'compact', or comma-separated columns"},
         "sort": {"type": "string"}, "limit": {"type": "integer"}}, required=["path"])},
]


def _handlers(client) -> Dict[str, Callable[[Dict[str, Any]], Any]]:
    def search(a):
        a = dict(a)
        a["limit"] = max(1, min(int(a.get("limit") or 20), MAX_LIMIT))
        a.setdefault("fields", "compact")
        return service.search(client, a)

    return {
        "search_listings": search,
        "count_listings": lambda a: service.count(client, a),
        "market_stats": lambda a: service.stats(client, {k: v for k, v in a.items() if k != "by"}, by=a.get("by")),
        "list_filters": lambda a: service.filters(client, {k: v for k, v in a.items() if k != "facet"},
                                                  facet=a.get("facet")),
        "list_categories": lambda a: service.categories(client, a),
        "get_listing": lambda a: service.get_listing(client, a["id"], a.get("target"), a.get("lang") or "en"),
        "export_listings": lambda a: service.export_to_file(
            client, {k: v for k, v in a.items() if k not in ("path", "format")}, a["path"], a.get("format")),
    }


class Server:
    def __init__(self, client):
        self.handlers = _handlers(client)

    def handle(self, msg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:  # notification
            return None
        try:
            if method == "initialize":
                asked = (msg.get("params") or {}).get("protocolVersion")
                result = {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                          "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "dubizzle", "version": __version__},
                          "instructions": INSTRUCTIONS}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = self.call(msg.get("params") or {})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        except Exception as e:  # never kill the server
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def call(self, params: Dict[str, Any]) -> Dict[str, Any]:
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in self.handlers:
            return {"content": [{"type": "text", "text": f"unknown tool {name}"}], "isError": True}
        try:
            data = self.handlers[name](args)
            return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False, default=str)}],
                    "isError": False}
        except (ValueError, AlgoliaError, KeyError) as e:
            return {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
        except Exception as e:
            traceback.print_exc(file=sys.stderr)
            return {"content": [{"type": "text", "text": f"internal error: {e}"}], "isError": True}


def serve(app_id: Optional[str] = None, api_key: Optional[str] = None, delay: float = 0.25,
          stdin=None, stdout=None) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    server = Server(service.make_client(app_id, api_key, delay))
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            batch = msg if isinstance(msg, list) else [msg]
            replies = [r for r in (server.handle(m) for m in batch) if r is not None]
            if not replies:
                continue
            reply = replies if isinstance(msg, list) else replies[0]
        stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        stdout.flush()
    return 0


def main() -> int:
    return serve()


if __name__ == "__main__":
    sys.exit(main())
