#!/bin/bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

APP_NAME="Mira"
VERSION="1.0.0"
VENV="$DIR/venv"
DIST="$DIR/dist"
ICNS="$DIR/Mira.app/Contents/Resources/mira.icns"

echo "=========================================="
echo "  Building $APP_NAME v$VERSION"
echo "=========================================="

# Step 1: Activate venv
echo ""
echo "[1/4] Activating virtual environment..."
source "$VENV/bin/activate"

# Step 2: PyInstaller build
echo ""
echo "[2/4] Building .app with PyInstaller..."
pyinstaller \
    --noconfirm \
    --windowed \
    --name "$APP_NAME" \
    --icon "$ICNS" \
    --add-data ".env.example:." \
    --hidden-import "faster_whisper" \
    --hidden-import "mlx_whisper" \
    --hidden-import "google.genai" \
    --hidden-import "anthropic" \
    --hidden-import "openai" \
    --hidden-import "sounddevice" \
    --hidden-import "soundfile" \
    --hidden-import "customtkinter" \
    --hidden-import "dotenv" \
    --collect-all "customtkinter" \
    --collect-all "faster_whisper" \
    main.py

echo "  .app created at $DIST/$APP_NAME.app"

# Step 3: Remove quarantine
echo ""
echo "[3/4] Removing quarantine flags..."
xattr -cr "$DIST/$APP_NAME.app"

# Step 4: Create DMG
echo ""
echo "[4/4] Creating DMG installer..."
DMG_NAME="${APP_NAME}-${VERSION}.dmg"
rm -f "$DIST/$DMG_NAME"

create-dmg \
    --volname "$APP_NAME" \
    --volicon "$ICNS" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "$APP_NAME.app" 150 190 \
    --hide-extension "$APP_NAME.app" \
    --app-drop-link 450 190 \
    --no-internet-enable \
    "$DIST/$DMG_NAME" \
    "$DIST/$APP_NAME.app"

echo ""
echo "=========================================="
echo "  BUILD COMPLETE"
echo "  DMG: $DIST/$DMG_NAME"
echo "  Size: $(du -h "$DIST/$DMG_NAME" | cut -f1)"
echo "=========================================="
echo ""
echo "To install on another Mac:"
echo "  1. Copy $DMG_NAME to the other Mac"
echo "  2. Open DMG → drag Mira to Applications"
echo "  3. Right-click → Open (first time, to bypass Gatekeeper)"
echo "  4. Add API keys in ~/Documents/Mira/.env"
