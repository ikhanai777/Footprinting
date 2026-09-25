---
name: dubizzle
description: Search, filter, price-check and export dubizzle UAE listings (used cars, rental cars, bikes, boats, number plates, residential and commercial property for rent or sale, classifieds, jobs, community) with every filter the site has. Use when the user asks about UAE cars, apartments, villas, rentals, second-hand items or jobs on dubizzle, wants market prices, or wants listings scraped to a file.
---

# dubizzle: search and scrape UAE listings

Two ways to run it. Prefer the MCP tools when they are available.

- **MCP tools** (server `dubizzle`): `mcp__dubizzle__search_listings`, `count_listings`, `market_stats`,
  `list_filters`, `list_categories`, `get_listing`, `export_listings`.
- **CLI**: `~/Footprinting/dubizzle_scraper/.venv/bin/dubizzle-scrape` (or `dubizzle-scrape` if on PATH).

## Procedure
1. **Pick the target.** Options: `cars` (used cars), `rental-cars`, `motorcycles`, `boats`, `heavy-vehicles`,
   `number-plates`, `auto-parts`, `property-rent`, `property-sale`, `commercial-rent`, `commercial-sale`,
   `classifieds`, `jobs`, `community`.
2. **Discover filters before you guess.** Call `list_filters` with the target and any make, model or category.
   It returns:
   - `specs`: site filters such as body type, fuel, colours, regional specs, features, and phone storage or condition.
   - `subcategories`: models or property types.
   - `facets_for_where`: other filterable fields and their values.
   - `numeric_attributes`.
   To see every value of one facet, pass `facet`.
3. **Size the result** with `count_listings`, then:
   - For "show me" or "cheapest" requests, use `search_listings` with `sort` (`cheapest`, `newest`, `price:desc`,
     `kilometers`, `year:desc`) and a `limit` of 10 to 30.
   - For "what's the market price" requests, use `market_stats`, optionally with `by` (for example `year`,
     `details.Regional Specs`, `neighborhoods.name`, `bedrooms`, `furnished`).
   - For "scrape everything" or "give me a spreadsheet" requests, use `export_listings` with a `path` such as
     `~/dubizzle_exports/<name>.csv`. It has no 10,000-listing cap. Give the user the path and the row count.
4. **Report** the prices (AED), key specs and each listing's `url`. Read the `search.notes` field and mention
   any fuzzy matches it reports, for example "'rx' matched rx-series".

## Filter options (same names in MCP and CLI)
| option | example | applies to |
|---|---|---|
| query | "land cruiser gxr" | all |
| make / model | lexus / rx | cars and other motors |
| category | motors/used-cars/toyota, villahouse, classified/mobile-phones-pdas | all |
| city | dubai, "abu dhabi", sharjah, ajman, rak, fujairah, uaq, "al ain"; `dubai\|sharjah` for several | all |
| area | "dubai marina", jvc, "business bay" | property (other targets fall back to a text search) |
| price, year, km, beds, baths, size | "2021", "2019..2022", "3+", "..60000" | price everywhere; year and km for cars; beds, baths, size and year (handover) for property |
| furnished, rent_period | true, yearly | property |
| seller | dealer, owner, certified (cars); agent, landlord (property) | |
| spec | {"regional-specs": "gcc-specs", "fuel-type": "hybrid\|electric", "body-type": "suv"} | cars, classifieds |
| where | ["bedrooms>=2", "amenities_v2.value=private_pool", "details.Employment Type.en.value=Full Time"] | any facet or numeric field |
| since_days | 7 | all |
| exclude | ["export", "accident"] | all (checks title and description) |

## CLI equivalents
```bash
dubizzle-scrape search cars --make lexus --model rx --year 2021 --spec regional-specs=gcc --sort cheapest -n 10 --format table
dubizzle-scrape stats property-rent --area "dubai marina" --beds 2 --rent-period yearly --by furnished
dubizzle-scrape filters cars --make toyota --model "land cruiser"
dubizzle-scrape search property-sale --city dubai --area jvc --beds 1 --price ..900000 -o ~/dubizzle_exports/jvc_1br.csv
dubizzle-scrape get 16927484
```

## Tips and limits
- `sort` and `market_stats` read every match. They refuse above 20,000 matches (`max_scan`), so narrow the search first.
- If a filter errors, the message lists the valid keys or values. Retry with one of them.
- An `HTTP 403` means the public search key rotated. See the README section "If it stops working".
- Be considerate: don't run large exports in a loop. Listings include seller details, so handle the data responsibly.
