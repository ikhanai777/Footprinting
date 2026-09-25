#!/usr/bin/env bash
# Full local setup of this repository's tools for a Nous Hermes agent:
#   - img2dxf           (image -> DXF converter, CLI + skill)
#   - dubizzle-scraper  (dubizzle UAE search/scrape, CLI + MCP server + skill)
#
#   ./hermes_setup.sh              # everything
#   ./hermes_setup.sh --only img2dxf | --only dubizzle
#   ./hermes_setup.sh --offline    # skip the live dubizzle API check
#
# No sudo, nothing installed globally. Safe to re-run. HERMES_HOME overrides ~/.hermes.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PY="${PYTHON:-python3}"
ONLY="" OFFLINE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift ;;
    --offline) OFFLINE="--offline" ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

"$PY" -c 'import sys; assert sys.version_info >= (3, 9)' 2>/dev/null \
  || { echo "error: Python 3.9+ is required (set PYTHON=/path/to/python3)" >&2; exit 1; }
mkdir -p "$HOME/.local/bin" "$HERMES_HOME/skills"
RESULTS=()

if [ -z "$ONLY" ] || [ "$ONLY" = "img2dxf" ]; then
  echo "################ img2dxf ################"
  [ -x "$REPO/.venv/bin/python" ] || "$PY" -m venv "$REPO/.venv"
  "$REPO/.venv/bin/python" -m pip install -q --upgrade pip
  "$REPO/.venv/bin/python" -m pip install -q -e "$REPO[test]"
  (cd "$REPO" && "$REPO/.venv/bin/python" -m pytest -q tests)
  out="$("$REPO/.venv/bin/img2dxf" "$REPO/examples/plate.png" -o "${TMPDIR:-/tmp}/img2dxf_check.dxf" --width 150 2>&1)"
  echo "$out" | tail -4
  echo "$out" | grep -q "DXF audit: 0 errors" || { echo "error: img2dxf self-check failed" >&2; exit 1; }
  ln -sf "$REPO/.venv/bin/img2dxf" "$HOME/.local/bin/img2dxf"
  # The img2dxf skill refers to ~/img2dxf/.venv; point that at this checkout if it's free.
  [ -e "$HOME/img2dxf" ] || ln -s "$REPO" "$HOME/img2dxf"
  rm -rf "$HERMES_HOME/skills/img2dxf" && cp -r "$REPO/skills/img2dxf" "$HERMES_HOME/skills/img2dxf"
  RESULTS+=("img2dxf: OK  (CLI ~/.local/bin/img2dxf, skill $HERMES_HOME/skills/img2dxf)")
fi

if [ -z "$ONLY" ] || [ "$ONLY" = "dubizzle" ]; then
  echo "################ dubizzle-scraper ################"
  HERMES_HOME="$HERMES_HOME" "$REPO/dubizzle_scraper/install_hermes.sh" $OFFLINE
  ln -sf "$REPO/dubizzle_scraper/.venv/bin/dubizzle-scrape" "$HOME/.local/bin/dubizzle-scrape"
  RESULTS+=("dubizzle: OK  (CLI ~/.local/bin/dubizzle-scrape, MCP server 'dubizzle', skill $HERMES_HOME/skills/dubizzle)")
fi

echo
echo "################ summary ################"
printf '%s\n' "${RESULTS[@]}"
case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to PATH to run the CLIs by name";; esac
echo "Next: restart Hermes (or run /reload-mcp) so it loads the 'dubizzle' MCP tools and the skills."
