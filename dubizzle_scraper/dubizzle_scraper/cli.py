"""Command-line interface: dubizzle-scrape."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import List, Optional

from . import export
from .client import DEFAULT_API_KEY, DEFAULT_APP_ID, AlgoliaClient, AlgoliaError
from .scraper import ALIASES, build_filters, categories, count, iter_listings, resolve


def _parse_args(argv: Optional[List[str]]) -> argparse.Namespace:
    aliases = ", ".join(f"{k} ({v[0]}{' ' + v[1] if v[1] else ''})" for k, v in ALIASES.items())
    p = argparse.ArgumentParser(
        prog="dubizzle-scrape",
        description="Scrape dubizzle UAE listings through its Algolia search API.",
        epilog=f"Targets: {aliases}. Any other value is used as a raw Algolia index name.",
    )
    p.add_argument("target", help="alias (e.g. cars, property-rent) or Algolia index name")
    p.add_argument("-q", "--query", default="", help="free-text search, e.g. 'land cruiser'")
    p.add_argument("-c", "--category", help="category path, e.g. motors/used-cars/toyota (see --categories)")
    p.add_argument("-f", "--filters", help='extra Algolia filter expression, e.g. \'year >= 2020\'')
    p.add_argument("--min-price", type=float)
    p.add_argument("--max-price", type=float)
    p.add_argument("-n", "--limit", type=int, help="stop after N listings")
    p.add_argument("-o", "--output", help="output file (default: stdout). Format is taken from the extension")
    p.add_argument("--format", choices=["csv", "json", "jsonl"], help="output format (default: jsonl, or from -o)")
    p.add_argument("--fields", help="comma-separated flattened columns to keep, e.g. id,name,price,absolute_url")
    p.add_argument("--lang", choices=["en", "ar"], default="en", help="language for localized fields")
    p.add_argument("--count", action="store_true", help="only print the number of matching listings")
    p.add_argument("--categories", action="store_true", help="list category paths with listing counts")
    p.add_argument("--delay", type=float, default=0.25, help="seconds between requests (default 0.25)")
    p.add_argument("--app-id", default=os.environ.get("DUBIZZLE_ALGOLIA_APP_ID", DEFAULT_APP_ID))
    p.add_argument("--api-key", default=os.environ.get("DUBIZZLE_ALGOLIA_API_KEY", DEFAULT_API_KEY))
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(message)s", stream=sys.stderr)

    index, alias_category = resolve(args.target)
    filters = build_filters(args.category or alias_category, args.filters)
    numeric = []
    if args.min_price is not None:
        numeric.append(f"price >= {args.min_price:g}")
    if args.max_price is not None:
        numeric.append(f"price <= {args.max_price:g}")
    client = AlgoliaClient(args.app_id, args.api_key, delay=args.delay)

    try:
        if args.count:
            print(count(client, index, query=args.query, filters=filters, numeric_filters=numeric))
            return 0
        if args.categories:
            for path, n in categories(client, index, query=args.query, filters=filters).items():
                print(f"{n:>8}  {path}")
            return 0

        fmt = args.format
        if not fmt and args.output:
            ext = os.path.splitext(args.output)[1].lstrip(".").lower()
            fmt = ext if ext in ("csv", "json", "jsonl") else None
        fmt = fmt or "jsonl"
        fields = [f.strip() for f in args.fields.split(",") if f.strip()] if args.fields else None

        hits = iter_listings(client, index, query=args.query, filters=filters,
                             numeric_filters=numeric, limit=args.limit)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="") as out:
                n = export.write(hits, out, fmt, lang=args.lang, fields=fields)
            print(f"wrote {n} listings to {args.output}", file=sys.stderr)
        else:
            export.write(hits, sys.stdout, fmt, lang=args.lang, fields=fields)
    except AlgoliaError as e:
        print(f"error: {e}", file=sys.stderr)
        if "403" in str(e):
            print("The public search key may have rotated or this index is not allowed; "
                  "pass a fresh one with --api-key.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
