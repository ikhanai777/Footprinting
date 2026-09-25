# dubizzle-scraper

A small command-line app (standard library only) that pulls dubizzle UAE listings
straight from the **Algolia search API** behind dubizzle's own website. It uses
the same public, search-only key the site sends to every browser. It does not
load dubizzle's HTML pages, which sit behind bot protection.

## Install

```bash
cd dubizzle_scraper
pip install -e .            # installs the `dubizzle-scrape` command
# or run without installing:  python -m dubizzle_scraper ...
```

## Usage

```bash
# how many used cars are listed?
dubizzle-scrape cars --count

# discover category paths (use them with -c)
dubizzle-scrape cars --categories | sort -rn | head

# search + filters -> CSV with chosen columns
dubizzle-scrape cars -q "land cruiser" --max-price 150000 -f 'year >= 2018' \
    --fields id,name,price,year,kilometers,absolute_url -o landcruisers.csv

# every villa for rent, all fields, as JSON lines (raw Algolia hits)
dubizzle-scrape property-rent -c property-for-rent/residential/villahouse -o villas.jsonl

# first 500 classifieds in Arabic
dubizzle-scrape classifieds -n 500 --lang ar -o classifieds.csv
```

| target | Algolia index |
|---|---|
| `cars` | `motors.com`, restricted to `motors/used-cars` |
| `motors` | `motors.com` (used cars, rentals, bikes, boats, parts…) |
| `property-rent` / `property-sale` | `property-for-rent-residential.com` / `property-for-sale-residential.com` |
| `commercial-rent` / `commercial-sale` | `property-for-rent-commercial.com` / `property-for-sale-commercial.com` |
| `classifieds` | `classified.com` |
| `jobs` | `jobs.com` |
| `community` | `community.com` |

Any other target is used as a raw index name.

### Options

| option | meaning |
|---|---|
| `-q` | free-text query |
| `-c` | category path, e.g. `motors/used-cars/toyota/land-cruiser` |
| `-f` | extra [Algolia filter](https://www.algolia.com/doc/api-reference/api-parameters/filters/), e.g. `'year >= 2020 AND kilometers < 50000'`, `'bedrooms = 3'` |
| `--min-price` / `--max-price` | price range |
| `-n` | stop after N listings |
| `-o` | output file; `.csv`, `.json` or `.jsonl` picks the format (stdout defaults to JSONL) |
| `--fields` | keep only these flattened columns (see a CSV header for names) |
| `--lang en\|ar` | language for bilingual fields |
| `--delay` | seconds between API calls (default 0.25) |
| `--app-id` / `--api-key` | override the credentials (also `DUBIZZLE_ALGOLIA_APP_ID` / `DUBIZZLE_ALGOLIA_API_KEY`) |

CSV and `--fields` output is flattened: bilingual `{en, ar}` values collapse to one
language, spec sheets become columns such as `details.Body Type`, and lists are
joined with ` | `. JSON/JSONL without `--fields` keeps the raw hits.

### Getting past the 10,000-hit limit

Algolia only pages through the first 10,000 hits of any query. When a query
matches more than that, the scraper bisects the result set on the `added`
timestamp until each time window is small enough, pages through every window,
and de-duplicates by `objectID`. Tested live: a 31k-listing category came back
complete (31,268 unique listings against a facet count of 31,271).

### From Python

```python
from dubizzle_scraper import AlgoliaClient, build_filters, iter_listings, flatten

client = AlgoliaClient()
for hit in iter_listings(client, "motors.com", query="patrol",
                         filters=build_filters("motors/used-cars/nissan"), limit=100):
    row = flatten(hit)
    print(row["id"], row["name"], row["price"])
```

## If it stops working

The search key is public and can be rotated by dubizzle. If you get `HTTP 403`,
open a dubizzle listing page in your browser. In DevTools > Network, filter on
`algolia`, and copy the `x-algolia-api-key` value into `--api-key`.

## Notes

Be considerate: keep the default delay and scrape only what you need. Check
dubizzle's terms of use before using the data. Listings include seller-related
fields, so handle the output responsibly.

## Tests

```bash
pip install pytest
python -m pytest        # offline; uses a fake Algolia transport
```
