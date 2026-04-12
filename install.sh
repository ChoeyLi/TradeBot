#!/bin/bash
# sectornews installer for macOS
# No dependencies — pure Python stdlib only

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_PATH="/usr/local/bin/sectornews"

echo ""
echo "  sectornews — sector intelligence dashboard"
echo "  ─────────────────────────────────────────"
echo ""

# Check Python 3
if ! command -v python3 &>/dev/null; then
    echo "  ✗ Python 3 not found. Install from https://python.org"
    exit 1
fi

PY_VER=$(python3 -c "import sys; print(sys.version_info.major, sys.version_info.minor)")
PY_MAJ=$(echo $PY_VER | cut -d' ' -f1)
PY_MIN=$(echo $PY_VER | cut -d' ' -f2)

if [ "$PY_MAJ" -lt 3 ] || ([ "$PY_MAJ" -eq 3 ] && [ "$PY_MIN" -lt 8 ]); then
    echo "  ✗ Python 3.8+ required (you have 3.$PY_MIN)"
    exit 1
fi

echo "  ✓ Python 3.$PY_MIN found"

# Create wrapper script
cat > /tmp/sectornews_wrapper << WRAPPER
#!/bin/bash
python3 "$SCRIPT_DIR/sectornews.py" "\$@"
WRAPPER

# Install
if [ -w "/usr/local/bin" ]; then
    cp /tmp/sectornews_wrapper "$INSTALL_PATH"
    chmod +x "$INSTALL_PATH"
else
    sudo cp /tmp/sectornews_wrapper "$INSTALL_PATH"
    sudo chmod +x "$INSTALL_PATH"
fi

echo "  ✓ Installed to $INSTALL_PATH"
echo ""
echo "  Get started:"
echo "    sectornews          # launch dashboard"
echo "    sectornews --help   # help"
echo ""
echo "  Controls inside the dashboard:"
echo "    [1] News   [2] Sectors   [3] Watchlist   [4] Chart"
echo "    [↑↓] navigate   [R] refresh   [Q] quit"
echo ""
