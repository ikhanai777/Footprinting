"""Command-line interface: dubizzle-scrape <command> <target> [filters]."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

from . import export, service
from .client import AlgoliaError
from .sources import TARGETS

COMMANDS = ("search", "count", "stats", "filters", "categories", "get", "mcp")


def _add_filters(p: argparse.ArgumentParser) -> None:
    p.add_argument("target", nargs="?", default="cars",
                   help=f"one of: {', '.join(TARGETS)}; or a raw Algolia index name (default: cars)")
    g = p.add_argument_group("filters (ranges: 5, 3..7, 3+, ..7)")
    g.add_argument("-q", "--query", default="", help="free-text search, e.g. 'land cruiser'")
    g.add_argument("-c", "--category", help="category path or child slug, e.g. motors/used-cars/toyota")
    g.add_argument("--make", help="car make, fuzzy (e.g. 'mercedes')")
    g.add_argument("--model", help="car model, fuzzy (e.g. 'rx' -> rx-series)")
    g.add_argument("--city", help="dubai, abu-dhabi, sharjah, ajman, rak, fujairah, uaq, al-ain (a|b for several)")
    g.add_argument("--area", help="neighbourhood, e.g. 'dubai marina' (property; otherwise a text search)")
    g.add_argument("--price", help="price range")
    g.add_argument("--min-price", type=float)
    g.add_argument("--max-price", type=float)
    g.add_argument("--year", help="model year (cars) or handover year (property)")
    g.add_argument("--km", help="kilometers range (cars)")
    g.add_argument("--beds", help="bedrooms (property; 0 = studio)")
    g.add_argument("--baths", help="bathrooms (property)")
    g.add_argument("--size", help="size in sqft (property)")
    g.add_argument("--furnished", help="yes/no (property)")
    g.add_argument("--seller", help="cars: dealer|owner|certified; property: agent|landlord")
    g.add_argument("--rent-period", help="yearly|monthly|quarterly (property for rent)")
    g.add_argument("--spec", action="append", metavar="KEY=VALUE",
                   help="site filter, e.g. fuel-type=hybrid, regional-specs=gcc, body-type=suv|coupe "
                        "(see `filters`); repeatable")
    g.add_argument("-w", "--where", action="append", metavar="EXPR",
                   help="any facet/numeric filter: 'bedrooms>=2', 'site.en=Dubai|Sharjah', 'year=2019..2022'; "
                        "repeatable")
    g.add_argument("-f", "--filters", action="append", help="raw Algolia filter expression (repeatable)")
    g.add_argument("--since", type=float, metavar="DAYS", help="only listings added in the last N days")
    g.add_argument("--exclude", action="append", metavar="WORD", help="drop listings whose title/description "
                   "contains WORD (client-side; repeatable)")
    g.add_argument("--lang", choices=["en", "ar"], default="en")
    g.add_argument("--max-scan", type=int, help=f"cap for sort/stats scans (default {service.DEFAULT_MAX_SCAN})")


def _opts(a: argparse.Namespace) -> Dict[str, Any]:
    price = a.price
    if price is None and (a.min_price is not None or a.max_price is not None):
        price = [a.min_price, a.max_price]
    return {
        "target": a.target, "query": a.query, "category": a.category, "make": a.make, "model": a.model,
        "city": a.city, "area": a.area, "price": price, "year": a.year, "km": a.km, "beds": a.beds,
        "baths": a.baths, "size": a.size, "furnished": a.furnished, "seller": a.seller,
        "rent_period": a.rent_period, "spec": a.spec, "where": (a.where or []) + (a.filters or []),
        "since_days": a.since, "exclude": a.exclude, "lang": a.lang, "max_scan": a.max_scan,
        "sort": getattr(a, "sort", None), "limit": getattr(a, "limit", None),
        "fields": getattr(a, "fields", None),
    }


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dubizzle-scrape",
                                description="Search, filter, analyse and export dubizzle UAE listings "
                                            "through its Algolia search API.")
    p.add_argument("--app-id", default=os.environ.get("DUBIZZLE_ALGOLIA_APP_ID"))
    p.add_argument("--api-key", default=os.environ.get("DUBIZZLE_ALGOLIA_API_KEY"))
    p.add_argument("--delay", type=float, default=0.25, help="seconds between API calls (default 0.25)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("search", help="fetch listings (default command)")
    _add_filters(s)
    s.add_argument("--sort", help="price, -price, year, -year, kilometers, -added, newest, cheapest ...")
    s.add_argument("-n", "--limit", type=int, help="stop after N listings")
    s.add_argument("-o", "--output", help="output file; .csv/.json/.jsonl picks the format")
    s.add_argument("--format", choices=["csv", "json", "jsonl", "table"],
                   help="default: jsonl to stdout, or from the -o extension")
    s.add_argument("--fields", help="'compact' (per-section preset), 'all', or comma-separated columns")
    s.add_argument("--count", action="store_true", help=argparse.SUPPRESS)       # legacy
    s.add_argument("--categories", action="store_true", help=argparse.SUPPRESS)  # legacy

    c = sub.add_parser("count", help="number of matching listings")
    _add_filters(c)

    st = sub.add_parser("stats", help="price / year / km / size distribution of every match")
    _add_filters(st)
    st.add_argument("--by", help="break down by a column, e.g. 'details.Regional Specs', year, site")

    f = sub.add_parser("filters", help="show every filter available for a target/category (JSON)")
    _add_filters(f)
    f.add_argument("--facet", help="list every value of one facet, e.g. amenities_v2.value")

    cat = sub.add_parser("categories", help="list sub-categories with counts")
    _add_filters(cat)

    g = sub.add_parser("get", help="fetch one listing by id or URL")
    g.add_argument("id")
    g.add_argument("target", nargs="?", help="speeds up the lookup")
    g.add_argument("--lang", choices=["en", "ar"], default="en")

    sub.add_parser("mcp", help="run the MCP server on stdio (for Hermes and other agents)")
    return p


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=1, default=str))


def _table(rows: List[Dict[str, Any]], out) -> None:
    if not rows:
        print("(no results)", file=out)
        return
    cols = list(rows[0].keys())
    cell = lambda v: "" if v is None else (f"{v:,.0f}" if isinstance(v, float) else str(v))  # noqa: E731
    widths = {c: min(60, max(len(c), *(len(cell(r.get(c))) for r in rows))) for c in cols}
    print("  ".join(c[:widths[c]].ljust(widths[c]) for c in cols), file=out)
    for r in rows:
        print("  ".join(cell(r.get(c))[:widths[c]].ljust(widths[c]) for c in cols), file=out)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Legacy form: `dubizzle-scrape cars --count` -> `search cars --count`.
    globals_with_value = {"--app-id", "--api-key", "--delay"}
    i = 0
    while i < len(argv) and argv[i].startswith("-") and argv[i] not in ("-h", "--help"):
        i += 2 if argv[i] in globals_with_value else 1
    if i < len(argv) and argv[i] not in COMMANDS and argv[i] not in ("-h", "--help"):
        argv.insert(i, "search")
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s", stream=sys.stderr)
    if args.command is None:
        _parser().print_help()
        return 0
    if args.command == "mcp":
        from .mcp_server import serve
        return serve(app_id=args.app_id, api_key=args.api_key, delay=args.delay)

    client = service.make_client(args.app_id, args.api_key, args.delay)
    try:
        if args.command == "get":
            _print_json(service.get_listing(client, args.id, args.target, args.lang))
            return 0
        opts = _opts(args)
        if args.command == "count" or getattr(args, "count", False):
            res = service.count(client, opts)
            print(res["count"])
            for note in res["search"]["notes"]:
                print(f"note: {note}", file=sys.stderr)
        elif args.command == "categories" or getattr(args, "categories", False):
            for path, n in sorted(service.categories(client, opts).items(), key=lambda kv: -kv[1]):
                print(f"{n:>8}  {path}")
        elif args.command == "stats":
            _print_json(service.stats(client, opts, by=args.by))
        elif args.command == "filters":
            _print_json(service.filters(client, opts, facet=args.facet))
        else:
            _search(client, args, opts)
    except (AlgoliaError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        if "403" in str(e):
            print("The public search key may have rotated; pass a fresh one with --api-key "
                  "(see README).", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    except BrokenPipeError:
        sys.stderr.close()
        return 0
    return 0


def _search(client, args: argparse.Namespace, opts: Dict[str, Any]) -> None:
    fmt = args.format
    if not fmt and args.output:
        ext = os.path.splitext(args.output)[1].lstrip(".").lower()
        fmt = ext if ext in ("csv", "json", "jsonl") else None
    if args.output:
        res = service.export_to_file(client, opts, args.output, fmt)
        print(f"wrote {res['written']} listings to {res['path']}", file=sys.stderr)
    elif fmt == "table":
        res = service.search(client, {**opts, "fields": opts.get("fields") or "compact"}, default_limit=50)
        _table(res["results"], sys.stdout)
        print(f"-- {res['returned']} of {res['total_matches']} matches", file=sys.stderr)
    else:
        r, hits = service.iter_hits(client, opts)
        fields = service._fields_for(r, opts.get("fields"))
        export.write(hits, sys.stdout, fmt or "jsonl", lang=opts.get("lang") or "en", fields=fields)
    for note in (res["search"]["notes"] if args.output or fmt == "table" else r.notes):
        print(f"note: {note}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
