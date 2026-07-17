#!/bin/bash
set -euo pipefail

# Build Mira.app bundle
# Usage: bash build_app.sh [--install]

DIR="$(cd "$(dirname "$0")" && pwd)"
APP="$DIR/Mira.app"

echo "Building Mira.app..."

# Create bundle structure
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources"

# Info.plist
cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Mira</string>
    <key>CFBundleDisplayName</key>
    <string>Mira</string>
    <key>CFBundleIdentifier</key>
    <string>com.leaddrive.mira</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>mira</string>
    <key>CFBundleIconFile</key>
    <string>mira</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>13.0</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>Mira needs microphone access to record meeting audio.</string>
