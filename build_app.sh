#!/usr/bin/env bash
# build_app.sh — Build Ascent.app for distribution
#
# Usage:
#   cd /Volumes/Lion2/projects/ascent-web
#   bash build_app.sh

set -e
cd "$(dirname "$0")"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Ascent.app builder"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f "app/main.py" ]; then
    echo "❌  Run this script from the ascent-web project root."
    exit 1
fi

# Activate venv
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -d "venv" ]; then
        echo "→  Activating venv…"
        source venv/bin/activate
    else
        echo "❌  No venv found. Create one first:"
        echo "    python3 -m venv venv && source venv/bin/activate && pip install -r requirements.txt"
        exit 1
    fi
fi

# Install build dependencies
echo "→  Installing build dependencies…"
pip install --quiet --upgrade pyinstaller rumps

# Clean previous build (stale bundles cause hard-to-debug issues)
echo "→  Cleaning previous build…"
rm -rf build dist

# Build
echo "→  Running PyInstaller…"
pyinstaller ascent.spec --noconfirm

if [ -d "dist/Ascent.app" ]; then
    SIZE=$(du -sh dist/Ascent.app | cut -f1)
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  ✅  Built:  dist/Ascent.app  ($SIZE)"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""
    echo "  To send to your friend:"
    echo "    cd dist && zip -r Ascent.zip Ascent.app && cd .."
    echo ""
    echo "  Your friend:"
    echo "    1. Unzip and drag Ascent.app to Applications"
    echo "    2. Right-click → Open the first time (Gatekeeper bypass)"
    echo "    3. Choose their .ascentdb file when prompted"
    echo ""
    echo "  To set ANTHROPIC_API_KEY / Strava keys:"
    echo "    Use Settings inside the app, or add to ~/.ascent_config directly"
    echo ""
else
    echo "❌  Build failed — check output above for errors."
    exit 1
fi
