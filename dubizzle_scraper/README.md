# dubizzle-scraper

A search, filter, analysis and export tool for **dubizzle UAE** that uses the site's own
**Algolia search API**. It works as a command-line tool, a Python library, and an
**MCP server** for agents such as Nous Hermes. It needs only the Python standard library.

It covers every section: used and rental cars, motorcycles, boats, heavy vehicles, number plates,
auto parts, residential and commercial property (rent and sale), classifieds, jobs and community.
It supports every filter the site offers:

- make and model
- city and neighbourhood
- price, year, km, bedrooms, bathrooms and size ranges
- furnished and rent period
- seller type
- the site's spec filters: body type, fuel, colours, regional specs, features, phone storage and condition, and more
- a generic filter on any other facet
- a date window and keyword exclusion

It also sorts results, computes market stats, and exports full data sets past Algolia's 10,000-result limit.

It uses the public search-only key that dubizzle's website sends to every browser. It never
loads dubizzle's HTML pages, which sit behind bot protection.

## Install

```bash
cd dubizzle_scraper
./install_hermes.sh          # venv + tests + live check + Hermes MCP registration + skill
# or manually:
python3 -m venv .venv && . .venv/bin/activate && pip install -e ".[test]"
```

Deploying for a Hermes agent is covered in [`../docs/DUBIZZLE_HERMES_DEPLOY.md`](../docs/DUBIZZLE_HERMES_DEPLOY.md).

## Commands

```
dubizzle-scrape search      TARGET [filters] [--sort ..] [-n N] [-o file | --format table|csv|json|jsonl] [--fields ..]
dubizzle-scrape count       TARGET [filters]
dubizzle-scrape stats       TARGET [filters] [--by COLUMN]
dubizzle-scrape filters     TARGET [filters] [--facet NAME]    # discover every filter + values (JSON)
dubizzle-scrape categories  TARGET [filters]                   # sub-categories with counts
dubizzle-scrape get         ID_OR_URL [TARGET]
dubizzle-scrape mcp                                            # MCP server on stdio (also: dubizzle-mcp)
```

`search` is the default command, so `dubizzle-scrape cars --make bmw` works too.

**Targets:** `cars`, `rental-cars`, `motorcycles`, `boats`, `heavy-vehicles`, `number-plates`,
`auto-parts`, `motors` (all of them), `property-rent`, `property-sale`, `commercial-rent`,
`commercial-sale`, `classifieds`, `jobs`, `community`. Any other value is used as a raw Algolia index name.

## Filters

Ranges accept `2021`, `2019..2022`, `3+` and `..60000`.

| option | example | notes |
|---|---|---|
| `-q` | `-q "land cruiser gxr"` | free text |
| `--make`, `--model` | `--make lexus --model rx` | fuzzy: `rx` → `rx-series` (reported as a note) |
| `-c` | `-c motors/used-cars/toyota`, `-c villahouse` | a short slug is expanded under the target |
| `--city` | `dubai`, `"abu dhabi"`, `sharjah`, `ajman`, `rak`, `fujairah`, `uaq`, `"al ain"`, `dubai\|sharjah` | |
| `--area` | `"dubai marina"`, `jvc`, `"business bay"` | property: exact neighbourhood; elsewhere a text search |
| `--price` / `--min-price` / `--max-price` | `--price 50000..90000` | AED |
| `--year` | `--year 2021`, `--year 2020+` | model year (cars), handover year (property) |
| `--km` | `--km ..60000` | cars |
| `--beds`, `--baths`, `--size` | `--beds 2`, `--beds 0` (studio), `--size 1500+` | property; size in sqft |
| `--furnished`, `--rent-period` | `--furnished yes --rent-period yearly` | property |
| `--seller` | `dealer`, `owner`, `certified` / `agent`, `landlord` | cars / property |
| `--spec KEY=VALUE` | `--spec regional-specs=gcc --spec fuel-type=hybrid\|electric --spec body-type=suv` | the site's own filters (cars, classifieds); see `filters` |
| `-w EXPR` | `-w 'bedrooms>=2' -w 'amenities_v2.value=private_pool' -w 'details.Employment Type.en.value=Full Time'` | any facet or numeric field: `=`, `!=`, `>`, `>=`, `<`, `<=`, `a\|b`, `lo..hi` |
| `-f EXPR` | `-f 'year >= 2020 AND kilometers < 50000'` | raw Algolia filter |
| `--since DAYS` | `--since 7` | added in the last N days |
| `--exclude WORD` | `--exclude export --exclude accident` | drops listings with WORD in the title or description (client-side) |

If a spec key or value is wrong, the error lists the valid ones. `filters` shows everything that
is available for the current selection, with counts:

```bash
dubizzle-scrape filters cars --make toyota --model "land cruiser"   # specs, trims, facets, numeric fields
dubizzle-scrape filters property-rent --facet amenities_v2.value     # every value of one facet
```

## Output, sorting and stats

```bash
# pretty table, cheapest first
dubizzle-scrape search cars --make lexus --model rx --year 2021 --sort cheapest --format table

# full export (all columns) - no 10k cap
dubizzle-scrape search property-sale --area jvc --beds 1 -o jvc_1br.csv

# compact columns (per-section preset) or your own
dubizzle-scrape search cars --make nissan --model patrol --fields compact -o patrol.csv
dubizzle-scrape search cars --make nissan --fields id,name,price,year,kilometers,url --format jsonl

# market stats, broken down by a column
dubizzle-scrape stats cars --make nissan --model patrol --year 2020..2022 --by year
dubizzle-scrape stats property-rent --area "dubai marina" --beds 1 --rent-period yearly --by furnished
```

- `--sort` accepts `price`, `-price` (write `--sort=-price`), `price:desc`, `year:desc`, `kilometers`, `newest`, `oldest`, `cheapest` and `expensive`.
- Sorting and `stats` read every match, capped by `--max-scan` (default 20,000).
- CSV and `--fields` output is flattened: bilingual values follow `--lang en|ar`, and spec sheets become columns such as `details.Body Type`.
- Every row gets `added_date` and `url` columns.

## MCP server (Hermes and other agents)

`dubizzle-mcp` (or `dubizzle-scrape mcp`) speaks MCP over stdio. It exposes these tools:

| tool | purpose |
|---|---|
| `search_listings` | filtered, sorted, compact rows (up to 200) |
| `count_listings` | fast count |
| `market_stats` | price/year/km/size distributions, optional breakdown |
| `list_filters` | discover specs, facets, sub-categories, numeric fields |
| `list_categories` | makes, models and property types with counts |
| `get_listing` | full details by id or URL |
| `export_listings` | scrape everything to CSV/JSON/JSONL on disk |

Hermes config (`~/.hermes/config.yaml`, which `install_hermes.sh` writes for you):

```yaml
mcp_servers:
  dubizzle:
    command: "/path/to/dubizzle_scraper/.venv/bin/dubizzle-mcp"
    args: []
    timeout: 600
```

## Python

```python
from dubizzle_scraper import make_client, service

c = make_client()
res = service.search(c, {"target": "cars", "make": "lexus", "model": "rx", "year": "2021",
                         "spec": {"regional-specs": "gcc-specs"}, "sort": "cheapest", "limit": 10})
for row in res["results"]:
    print(row["price"], row["name"], row["url"])
print(service.stats(c, {"target": "property-rent", "area": "jvc", "beds": 1}, by="furnished"))
```

## How it works

- **Past the 10,000-hit cap:** Algolia pages through only the first 10,000 hits of a query. Larger result
  sets are split by bisecting the `added` timestamp until each window fits. Each window is paged
  through, then results are de-duplicated by `objectID`. Tested live: a 31k-listing category came
  back complete (31,268 unique listings against a facet count of 31,271).
- **Spec filters** use the same `seo_links.attribute_links` values that dubizzle's own filter UI uses.
  When a value exists only per brand (common in classifieds), all of its links are combined with OR.
- **Neighbourhoods** resolve to the site's `seo_links.location_links` values. A whole-word match
  wins, and the broadest area is preferred.

## If it stops working

The search key is public and dubizzle can rotate it. If you get `HTTP 403`, open a dubizzle
listing page in your browser. In DevTools > Network, filter on `algolia`, copy the
`x-algolia-api-key` value, and pass it with `--api-key` or set `DUBIZZLE_ALGOLIA_API_KEY`.
For the MCP server, put it in the server's `env:` block in the Hermes config.

## Notes

Be considerate: keep the default delay (`--delay 0.25`) and scrape only what you need. Check
dubizzle's terms of use before using the data. Listings include seller-related fields, so
handle the output responsibly.

## Tests

```bash
python -m pytest        # offline; uses a fake Algolia transport
```
