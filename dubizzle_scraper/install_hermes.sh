#!/usr/bin/env bash
# Install dubizzle-scraper into a local venv and register it with Hermes Agent.
#
#   ./install_hermes.sh               # venv + checks + MCP server in ~/.hermes/config.yaml + skill
#   ./install_hermes.sh --no-config   # don't touch ~/.hermes/config.yaml
#   ./install_hermes.sh --no-skill    # don't copy the skill
#   ./install_hermes.sh --offline     # skip the live API check
#
# Safe to re-run. HERMES_HOME overrides ~/.hermes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DO_CONFIG=1 DO_SKILL=1 DO_LIVE=1
for arg in "$@"; do
  case "$arg" in
    --no-config) DO_CONFIG=0 ;;
    --no-skill) DO_SKILL=0 ;;
    --offline) DO_LIVE=0 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

PY="${PYTHON:-python3}"
"$PY" -c 'import sys; assert sys.version_info >= (3, 9), "Python 3.9+ required"' \
  || { echo "error: needs Python 3.9 or newer (set PYTHON=/path/to/python3)" >&2; exit 1; }

echo "==> creating venv in $HERE/.venv"
[ -x "$HERE/.venv/bin/python" ] || "$PY" -m venv "$HERE/.venv"
VPY="$HERE/.venv/bin/python"
"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -e "$HERE[test]"

echo "==> running offline tests"
(cd "$HERE" && "$VPY" -m pytest -q)

if [ "$DO_LIVE" = 1 ]; then
  echo "==> live check against dubizzle's search API"
  "$HERE/.venv/bin/dubizzle-scrape" count cars
fi

echo "==> checking the MCP server handshake"
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18"}}' \
              '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' \
  | "$HERE/.venv/bin/dubizzle-mcp" \
  | "$VPY" -c 'import json,sys; r=[json.loads(l) for l in sys.stdin]; print("   tools:", ", ".join(t["name"] for t in r[1]["result"]["tools"]))'

if [ "$DO_CONFIG" = 1 ]; then
  echo "==> registering MCP server 'dubizzle' in $HERMES_HOME/config.yaml"
  mkdir -p "$HERMES_HOME"
  "$VPY" - "$HERMES_HOME/config.yaml" "$HERE/.venv/bin/dubizzle-mcp" <<'PYEOF'
import os, re, shutil, sys, time
path, command = sys.argv[1], sys.argv[2]
text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
section = re.search(r"^mcp_servers:[ \t]*(\{\})?[ \t]*$", text, re.M)
if section and re.search(r"^[ \t]+command:.*dubizzle-mcp", text, re.M):
    print("   already registered; leaving config unchanged")
    sys.exit(0)
if os.path.exists(path):
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(path, backup)
    print(f"   backup: {backup}")
def block(ind):
    i2 = ind * 2
    return (f"{ind}dubizzle:\n{i2}command: \"{command}\"\n{i2}args: []\n"
            f"{i2}timeout: 600\n{i2}connect_timeout: 60\n")
if section and section.group(1):          # "mcp_servers: {}"
    text = text[:section.start()] + "mcp_servers:\n" + block("  ") + text[section.end():].lstrip("\n")
elif section:                             # existing mapping: match its indentation
    after = text[section.end():]
    m = re.search(r"^([ \t]+)\S", after, re.M)
    ind = m.group(1) if m else "  "
    text = text[:section.end()] + "\n" + block(ind).rstrip("\n") + after
else:
    text = text.rstrip("\n") + ("\n\n" if text.strip() else "") + "mcp_servers:\n" + block("  ")
open(path, "w", encoding="utf-8").write(text)
print("   added mcp_servers.dubizzle")
PYEOF
fi

if [ "$DO_SKILL" = 1 ]; then
  SKILL_SRC="$HERE/../skills/dubizzle"
  if [ -d "$SKILL_SRC" ]; then
    echo "==> installing skill to $HERMES_HOME/skills/dubizzle"
    mkdir -p "$HERMES_HOME/skills"
    rm -rf "$HERMES_HOME/skills/dubizzle"
    cp -r "$SKILL_SRC" "$HERMES_HOME/skills/dubizzle"
  fi
fi

cat <<EOF

Done.
  CLI:        $HERE/.venv/bin/dubizzle-scrape --help
  MCP server: $HERE/.venv/bin/dubizzle-mcp
Restart Hermes (or run /reload-mcp) so it picks up the 'dubizzle' tools.
EOF
