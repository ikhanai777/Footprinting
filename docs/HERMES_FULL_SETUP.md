# Full setup for a Nous Hermes agent

This runbook is written for a Nous Hermes agent, or any agent with shell access. It deploys
everything in this repository on the local machine and connects it to Hermes:

| Tool | What Hermes gets |
|---|---|
| **dubizzle-scraper** | 7 native MCP tools (`mcp__dubizzle__*`) to search, filter, price-check and export dubizzle UAE listings, a CLI, and a skill |
| **img2dxf** | a CLI that converts images into editable DXF drawings, and a skill |

The whole setup runs without sudo and installs nothing globally. Each tool gets its own virtual environment.

## Prompt to give the agent

Paste this into Hermes:

```text
Set up the tools from https://github.com/ikhanai777/Footprinting on this machine by following
docs/HERMES_FULL_SETUP.md on the branch claude/dubizzle-algolia-scraper-pyh9ce (use main instead
if main contains dubizzle_scraper/). Do the steps in order and check every "Expect" line before
moving on. Do not use sudo, do not install packages globally, and never print tokens or keys.
If a step still fails after its listed fix, stop and tell me the exact command and output.
When done, report the summary block from step 3 and the answers to the step 5 checks.
```

---

## Steps for the agent

### 1. Check prerequisites

```bash
python3 --version    # Expect: Python 3.9 or newer
git --version        # Expect: any version
python3 -m venv --help >/dev/null && echo venv-ok   # Expect: venv-ok
```

- **Python is missing or older than 3.9:** stop and ask the user to install Python 3.9+.
- **`venv` is missing** (Debian/Ubuntu): ask the user to install the `python3-venv` package. You cannot use sudo.
- **Network access:** the machine needs HTTPS access to `github.com`, `pypi.org` and `*.algolia.net`.

### 2. Get the code

```bash
REPO_DIR="$HOME/Footprinting"
if [ -d "$REPO_DIR/.git" ]; then git -C "$REPO_DIR" fetch origin; else git clone https://github.com/ikhanai777/Footprinting.git "$REPO_DIR"; fi
cd "$REPO_DIR"
if git ls-tree -d origin/main dubizzle_scraper >/dev/null 2>&1 && [ -n "$(git ls-tree -d origin/main dubizzle_scraper)" ]; then
  git checkout main && git pull --ff-only
else
  git checkout claude/dubizzle-algolia-scraper-pyh9ce && git pull --ff-only
fi
ls   # Expect: dubizzle_scraper  img2dxf  skills  docs  hermes_setup.sh ...
```

- **Authentication error:** the repository is private. Ask the user to run `gh auth login`, or to give you a GitHub token, then retry. Never echo the token.
- **Local changes block the checkout:** stop and ask the user. Do not discard their changes.

### 3. Install everything (Linux / macOS)

```bash
cd "$HOME/Footprinting"
chmod +x hermes_setup.sh dubizzle_scraper/install_hermes.sh
./hermes_setup.sh
```

The script does the following:

**img2dxf**
1. Creates `~/Footprinting/.venv` and installs the package.
2. Runs its tests.
3. Converts `examples/plate.png` as a check.
4. Links the `img2dxf` command into `~/.local/bin`.
5. Links `~/img2dxf` to the checkout, because the skill looks for that path.
6. Copies the skill to `~/.hermes/skills/img2dxf`.

**dubizzle-scraper**
1. Creates `~/Footprinting/dubizzle_scraper/.venv`.
2. Runs 36 offline tests.
3. Makes one live search.
4. Checks the MCP handshake.
5. Registers the MCP server in `~/.hermes/config.yaml`, after backing the file up. If it's already registered, the config is left alone.
6. Copies the skill to `~/.hermes/skills/dubizzle`.
7. Links the `dubizzle-scrape` command into `~/.local/bin`.

Expect this output:
- `... passed` twice (once per test suite).
- `DXF audit: 0 errors`.
- A number for the live check, roughly 40,000 used cars.
- `tools: search_listings, count_listings, market_stats, list_filters, list_categories, get_listing, export_listings`.
- A final summary block:
  ```
  img2dxf: OK  (...)
  dubizzle: OK  (...)
  ```

Fixes:
- **pip can't download packages:** the machine has no access to PyPI, or it needs a proxy. Tell the user.
  To set up only the dubizzle scraper, which needs nothing from PyPI except pytest, run
  `./hermes_setup.sh --only dubizzle`.
- **The live check fails with `HTTP 403`:** dubizzle rotated its public search key. Finish with
  `./hermes_setup.sh --only dubizzle --offline`, then follow "If it stops working" in
  `dubizzle_scraper/README.md` to get the new key. Add it to the server's config (step 4).
- **The live check fails with a network error:** `*.algolia.net` is blocked. Tell the user.
- **A different Hermes home:** prefix the command with `HERMES_HOME=/path/to/.hermes`.

#### Windows (PowerShell)

The script is bash-only. On Windows, run these commands, then add the config block from step 4 by hand:

```powershell
cd $HOME\Footprinting
py -3 -m venv .venv; .venv\Scripts\python -m pip install -e ".[test]"; .venv\Scripts\python -m pytest -q tests
cd dubizzle_scraper
py -3 -m venv .venv; .venv\Scripts\python -m pip install -e ".[test]"; .venv\Scripts\python -m pytest -q
.venv\Scripts\dubizzle-scrape count cars
Copy-Item -Recurse -Force ..\skills\img2dxf, ..\skills\dubizzle $HOME\.hermes\skills\
```

### 4. Confirm the Hermes config

```bash
grep -n -A5 "dubizzle:" ~/.hermes/config.yaml
```

Expect a block like this under `mcp_servers:`:

```yaml
mcp_servers:
  dubizzle:
    command: "/home/<user>/Footprinting/dubizzle_scraper/.venv/bin/dubizzle-mcp"
    args: []
    timeout: 600          # large exports can take several minutes
    connect_timeout: 60
```

If you ran with `--no-config`, or you're on Windows, add the block yourself. On Windows, the command is
`C:\\Users\\<you>\\Footprinting\\dubizzle_scraper\\.venv\\Scripts\\dubizzle-mcp.exe`. Keep the file's
existing indentation. There must be only one `mcp_servers:` key.

If a new search key is needed (the `HTTP 403` fix in step 3), add it to the same block. Never put the key in `args`:

```yaml
    env:
      DUBIZZLE_ALGOLIA_API_KEY: "<new key>"
```

### 5. Load and verify in Hermes (required)

1. Restart Hermes, or run `/reload-mcp` in the running session.
2. Check that the tools are listed. `hermes mcp list` (or the `/tools` view) should show the `dubizzle` server with 7 tools.
3. Run these checks and report the answers to the user.

   a. **dubizzle:** "How many used 2021 Lexus RX are on dubizzle, and which is the cheapest GCC-spec one? Give the link."

      Expect calls to `mcp__dubizzle__count_listings` and `mcp__dubizzle__search_listings`, with
      `make=lexus, model=rx, year=2021, spec={regional-specs: gcc-specs}, sort=cheapest`. The answer should include a price in AED and a dubizzle URL.

   b. **dubizzle stats:** "What's the median yearly rent for a 1-bed in Dubai Marina, furnished vs unfurnished?"

      Expect `mcp__dubizzle__market_stats` with `target=property-rent, area="dubai marina", beds=1, rent_period=yearly, by=furnished`.

   c. **img2dxf:** from a shell, run the command below.
      ```bash
      img2dxf ~/Footprinting/examples/strokes.png -o /tmp/strokes.dxf --mode centerline
      ```
      Expect: `DXF audit: 0 errors`.

### 6. Report to the user

Tell the user:
- the install path (`~/Footprinting`);
- the summary block from step 3;
- the answers to 5a and 5b;
- the location of the config backup (`~/.hermes/config.yaml.bak-*`), if one was made.

---

## Using the tools afterwards

**dubizzle.** The skill explains the workflow. The usual order is:
1. `list_filters`: see which specs, facets and sub-categories exist.
2. `count_listings`: size the result.
3. `search_listings`, `market_stats` or `export_listings`, depending on the request.

Filters include make and model (loosely matched), city, neighbourhood, and ranges for price, year, km,
beds, baths and size, written like `2021`, `2019..2022`, `3+` or `..60000`. There are also furnished,
rent period, seller, the site's own specs (body type, fuel, colours, regional specs, phone
storage and condition...), a `where` filter on any other field, `since_days` and `exclude`.

Exports go to a file path the agent chooses, for example `~/dubizzle_exports/lexus_rx.csv`.

CLI equivalent:
```bash
dubizzle-scrape search cars --make lexus --model rx --year 2021 --spec regional-specs=gcc --sort cheapest --format table
```

**img2dxf:** `img2dxf in.png -o out.dxf --width 120 --preview`. See `skills/img2dxf/SKILL.md`.

## Updating

```bash
cd ~/Footprinting && git pull --ff-only && ./hermes_setup.sh
```

Then run `/reload-mcp` in Hermes.

## Uninstalling

```bash
rm -rf ~/Footprinting/.venv ~/Footprinting/dubizzle_scraper/.venv \
       ~/.hermes/skills/img2dxf ~/.hermes/skills/dubizzle \
       ~/.local/bin/img2dxf ~/.local/bin/dubizzle-scrape
[ -L ~/img2dxf ] && rm ~/img2dxf
```

Then delete the `dubizzle:` block under `mcp_servers:` in `~/.hermes/config.yaml`, and run `/reload-mcp`.
To remove the code as well, run `rm -rf ~/Footprinting`.
