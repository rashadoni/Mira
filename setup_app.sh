#!/bin/bash
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"

echo "Creating Mira.app..."
mkdir -p "$DIR/Mira.app/Contents/MacOS"
mkdir -p "$DIR/Mira.app/Contents/Resources"

cat > "$DIR/Mira.app/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>Mira</string>
    <key>CFBundleExecutable</key><string>mira</string>
    <key>CFBundleIdentifier</key><string>com.leaddrive.mira</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleVersion</key><string>1.0.0</string>
    <key>NSMicrophoneUsageDescription</key><string>Mira needs microphone access.</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

cat > "$DIR/Mira.app/Contents/MacOS/mira" << 'LAUNCHER'
#!/bin/bash
cd /Users/rashadrahimov/Documents/Mira
exec ./venv/bin/python main.py
LAUNCHER

chmod +x "$DIR/Mira.app/Contents/MacOS/mira"
echo "Done! Double-click Mira.app to launch."
