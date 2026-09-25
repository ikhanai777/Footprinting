# Deploying dubizzle-scraper locally with a Nous Hermes agent

This file is written to be handed to a Nous Hermes agent, or any agent with shell access.
The agent installs `dubizzle-scraper` into an isolated virtual environment, registers it as an MCP
server so Hermes gets native `dubizzle` tools, installs the skill, and verifies everything.

## Prompt to give the agent

```text
Deploy the dubizzle scraper on this machine by following docs/DUBIZZLE_HERMES_DEPLOY.md from
https://github.com/ikhanai777/Footprinting. Work through the steps in order, run the verification
step, and report the install path and the verification output. Stop and ask me if a step fails
after its listed fix. Do not use sudo and do not install packages globally.
```

## Steps for the agent

Run every command from a shell. Check each step's result before you continue.

### 1. Check prerequisites

```bash
python3 --version   # needs 3.9 or newer
git --version
```

If Python is older than 3.9 or missing, stop and ask the user to install Python 3.9+.
There are no other dependencies: the tool uses only the Python standard library.

### 2. Get the code

```bash
REPO_DIR="$HOME/Footprinting"
git clone https://github.com/ikhanai777/Footprinting.git "$REPO_DIR" || git -C "$REPO_DIR" pull
cd "$REPO_DIR"
git checkout main 2>/dev/null && [ -d dubizzle_scraper ] || git checkout claude/dubizzle-algolia-scraper-pyh9ce
```

Use `main` once the pull request is merged. Before that, the code is on
`claude/dubizzle-algolia-scraper-pyh9ce`.

If the clone fails with an authentication error, the repository is private. Ask the user
to run `gh auth login`, or for a GitHub token, then retry. Never print the token.

### 3. Install, verify and register with Hermes (Linux / macOS)

```bash
cd "$REPO_DIR/dubizzle_scraper"
./install_hermes.sh
```

The script does the following:

1. Creates `.venv` and installs the package.
2. Runs the offline tests, which should report `36 passed`.
3. Makes one live API call, which prints the number of used-car listings.
4. Checks the MCP handshake, which lists 7 tools.
5. Adds this block to `~/.hermes/config.yaml`, after backing the file up:
   ```yaml
   mcp_servers:
     dubizzle:
       command: "<REPO_DIR>/dubizzle_scraper/.venv/bin/dubizzle-mcp"
       args: []
       timeout: 600
       connect_timeout: 60
   ```
6. Copies `skills/dubizzle` to `~/.hermes/skills/dubizzle`.

Options:

- `--no-config`: leave the Hermes config alone and add the block yourself.
- `--no-skill`: don't copy the skill.
- `--offline`: skip the live check.
- `HERMES_HOME=/path`: use a Hermes home directory other than `~/.hermes`.

If the live check fails with `HTTP 403`, the public search key has rotated. See
"If it stops working" in `dubizzle_scraper/README.md`, then set the new key in the server's
`env:` block as `DUBIZZLE_ALGOLIA_API_KEY: "<key>"`.
If it fails with a network error, the machine has no internet access to `*.algolia.net`. Tell the user.

#### Windows (PowerShell)

```powershell
cd $HOME\Footprinting\dubizzle_scraper
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[test]"
.venv\Scripts\python -m pytest -q
.venv\Scripts\dubizzle-scrape count cars
```

Then add the `mcp_servers.dubizzle` block by hand, with
`command: "C:\\Users\\<you>\\Footprinting\\dubizzle_scraper\\.venv\\Scripts\\dubizzle-mcp.exe"`,
and copy `skills\dubizzle` into the Hermes skills folder.

### 4. Load it in Hermes (required)

Restart Hermes, or run `/reload-mcp` in a running session. Then ask Hermes:

> How many 2021 Lexus RX are for sale on dubizzle, and what's the cheapest GCC-spec one?

The agent should call `mcp__dubizzle__count_listings` and `mcp__dubizzle__search_listings`.
Report the answer to the user.

### 5. Make the CLI available (optional)

```bash
mkdir -p "$HOME/.local/bin"
ln -sf "$REPO_DIR/dubizzle_scraper/.venv/bin/dubizzle-scrape" "$HOME/.local/bin/dubizzle-scrape"
```

## Using it after deployment

| Ask | Tool call (MCP) | CLI |
|---|---|---|
| Find cars | `search_listings {target: cars, make, model, year, spec, sort}` | `dubizzle-scrape search cars --make lexus --model rx --year 2021 --sort cheapest --format table` |
| What filters exist? | `list_filters {target, make/category}` | `dubizzle-scrape filters cars --make toyota` |
| Market price | `market_stats {..., by: year}` | `dubizzle-scrape stats cars --make nissan --model patrol --by year` |
| Rent in an area | `search_listings {target: property-rent, area: "dubai marina", beds: "2", furnished: true}` | `dubizzle-scrape search property-rent --area "dubai marina" --beds 2 --furnished yes` |
| Full data dump | `export_listings {..., path: ~/dubizzle_exports/x.csv}` | `dubizzle-scrape search ... -o x.csv` |
| One listing | `get_listing {id}` | `dubizzle-scrape get 16927484` |

## Updating and uninstalling

```bash
cd "$HOME/Footprinting" && git pull && dubizzle_scraper/.venv/bin/pip install -e dubizzle_scraper
rm -rf "$HOME/Footprinting/dubizzle_scraper/.venv" ~/.hermes/skills/dubizzle   # uninstall
# then delete the mcp_servers.dubizzle block from ~/.hermes/config.yaml
```
