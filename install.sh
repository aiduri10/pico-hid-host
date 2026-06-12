#!/usr/bin/env bash
# pico-hid installer for Linux / macOS
# Usage: bash install.sh
set -euo pipefail

INSTALL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/pico-hid"
BIN_DIR="$HOME/.local/bin"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ── colour helpers ────────────────────────────────────────────────────────────
GRN='\033[0;32m'; YLW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GRN}[pico-hid]${NC} $*"; }
warn()  { echo -e "${YLW}[pico-hid]${NC} $*"; }
die()   { echo -e "${RED}[pico-hid] ERROR:${NC} $*" >&2; exit 1; }

# ── 1. find Python 3.10+ ──────────────────────────────────────────────────────
PYTHON=""
for py in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$py" &>/dev/null 2>&1; then
        ok=$("$py" -c "import sys; print(sys.version_info >= (3,10))" 2>/dev/null || echo False)
        if [[ "$ok" == "True" ]]; then
            PYTHON="$py"; break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    warn "Python 3.10+ not found — installing..."
    if   command -v apt-get &>/dev/null; then sudo apt-get install -y python3 python3-venv python3-pip
    elif command -v dnf     &>/dev/null; then sudo dnf install -y python3 python3-pip
    elif command -v pacman  &>/dev/null; then sudo pacman -S --noconfirm python
    elif command -v brew    &>/dev/null; then brew install python@3.12
    else die "Cannot install Python automatically.\nPlease install Python 3.10+ from https://python.org and re-run this script."
    fi
    PYTHON=python3
fi

info "Python: $("$PYTHON" --version)"

# ── 2. install source files ───────────────────────────────────────────────────
info "Installing to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
cp "$SCRIPT_DIR/core.py" "$SCRIPT_DIR/linux.py" "$INSTALL_DIR/"

# ── 3. virtual environment + dependencies ────────────────────────────────────
if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$INSTALL_DIR/venv"
fi

info "Installing dependencies (bleak, cryptography)..."
"$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -q bleak cryptography

# ── 4. create launcher ────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/pico-hid" << EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/venv/bin/python" "$INSTALL_DIR/linux.py" "\$@"
EOF
chmod +x "$BIN_DIR/pico-hid"
info "Launcher created: $BIN_DIR/pico-hid"

# ── 5. PATH check ────────────────────────────────────────────────────────────
if [[ ":${PATH}:" != *":${BIN_DIR}:"* ]]; then
    warn "$BIN_DIR is not in your PATH yet."
    warn "Add the following line to your ~/.bashrc or ~/.zshrc, then restart your terminal:"
    echo
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo
else
    echo
    info "All done!  Run:  pico-hid"
fi
