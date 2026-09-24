# Deploying img2dxf locally with a Nous Hermes agent

This file is written to be handed to a Nous Hermes agent (or any agent with shell
access). Paste the prompt below into the agent, or point it at this file. The agent
installs `img2dxf` into an isolated virtual environment, checks that the install
works, and can then convert images for you.

## Prompt to give the agent

```text
Deploy the img2dxf image-to-DXF converter on this machine by following
docs/HERMES_AGENT_DEPLOY.md from https://github.com/ikhanai777/Footprinting.
Work through the steps in order, run the verification step, and report the
install path and the verification output. Stop and ask me if a step fails
after its listed fix. Do not use sudo and do not install packages globally.
```

## Steps for the agent

Run every command from a shell. Check each step's result before you continue.

### 1. Check prerequisites

```bash
python3 --version   # needs 3.9 or newer (on Windows: py -3 --version)
git --version
```

If Python is older than 3.9 or missing, stop and ask the user to install Python 3.9+.
No system libraries are needed: the package uses `opencv-python-headless`.

### 2. Get the code

```bash
INSTALL_DIR="$HOME/img2dxf"
git clone https://github.com/ikhanai777/Footprinting.git "$INSTALL_DIR"
cd "$INSTALL_DIR"
git checkout main 2>/dev/null || git checkout claude/image-to-dxf-converter-i4wnc7
```

Use `main` once the pull request is merged. Before that, the code is on
`claude/image-to-dxf-converter-i4wnc7`.

If the clone fails with an authentication error, the repository is private. Ask the user
to run `gh auth login`, or for a GitHub token, then retry. Never print the token.
If the directory already exists, run `git -C "$INSTALL_DIR" pull` instead of cloning.

### 3. Create an isolated environment and install

Linux / macOS:
```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[test]"
```

Windows (PowerShell):
```powershell
py -3 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[test]"
```

### 4. Verify (required)

```bash
python -m pytest -q                      # expect: all tests passed
img2dxf examples/plate.png -o /tmp/plate_check.dxf --width 150 --preview
```

The second command must print `DXF audit: 0 errors`. Report both outputs to the user.

### 5. Make the command available (optional)

Point to the venv executable instead of activating the environment each time:

```bash
mkdir -p "$HOME/.local/bin"
ln -sf "$INSTALL_DIR/.venv/bin/img2dxf" "$HOME/.local/bin/img2dxf"   # Linux/macOS
```

On Windows, the executable is `%USERPROFILE%\img2dxf\.venv\Scripts\img2dxf.exe`.

### 6. Install the agent skill (optional)

`skills/img2dxf/SKILL.md` describes how to run conversions. If your Hermes setup
loads skills from a skills directory (commonly `~/.hermes/skills/`), copy the folder there:

```bash
mkdir -p ~/.hermes/skills && cp -r "$INSTALL_DIR/skills/img2dxf" ~/.hermes/skills/
```

## Converting images after deployment

| Situation | Command |
|---|---|
| Filled shapes, logos, parts, silhouettes | `img2dxf in.png -o out.dxf` |
| Known real width of the drawing | `img2dxf in.png -o out.dxf --width 120 --units mm` |
| Scanned at a known DPI | `img2dxf scan.tif -o out.dxf --dpi 600` |
| Phone photo, shading, noise | `img2dxf photo.jpg -o out.dxf --threshold adaptive --denoise 10` |
| Line drawing or sketch (single lines) | `img2dxf sketch.png -o out.dxf --mode centerline` |
| Separate LINE/ARC entities | add `--entities primitives` |
| Visual check of the result | add `--preview` (writes `out.preview.png`) |
| Folder of images | `img2dxf imgs/*.png -o out_dir/` |

After each conversion, read the printed report. `fit deviation` is the accuracy in
pixels and drawing units, and `DXF audit` must show 0 errors. If the fit is too coarse,
lower `-t` (for example `-t 0.3`). If a noisy image produces too many segments, raise `-t`
or add `--denoise`.

## Updating and uninstalling

```bash
cd "$HOME/img2dxf" && git pull && . .venv/bin/activate && pip install -e .
rm -rf "$HOME/img2dxf" "$HOME/.local/bin/img2dxf" ~/.hermes/skills/img2dxf   # uninstall
```
