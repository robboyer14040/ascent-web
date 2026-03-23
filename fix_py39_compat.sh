#!/usr/bin/env bash
# fix_py39_compat.sh — Fix Python 3.10+ type hint syntax for Python 3.9 compatibility
# Run from the ascent-web project root:
#   bash fix_py39_compat.sh

set -e
cd "$(dirname "$0")"

echo "→  Scanning app/ for Python 3.10+ type hint syntax..."

# Find all .py files in app/ (not venv)
FILES=$(find app -name "*.py" | sort)

FIXED=0
for f in $FILES; do
  # Check if file has any | None patterns
  if grep -qE "\b\w+(\[.*\])?\s*\|\s*None\b" "$f" 2>/dev/null; then
    echo "  Fixing: $f"
    
    # Add 'from typing import Optional' if not already present
    if ! grep -q "from typing import" "$f"; then
      # Add after first import block
      sed -i '' '1s/^/from typing import Optional\n/' "$f"
    elif ! grep -q "Optional" "$f"; then
      # Add Optional to existing typing import
      sed -i '' 's/from typing import /from typing import Optional, /' "$f"
    fi
    
    # Replace X | None with Optional[X] for common types
    sed -i '' 's/\bstr | None\b/Optional[str]/g' "$f"
    sed -i '' 's/\bint | None\b/Optional[int]/g' "$f"
    sed -i '' 's/\bfloat | None\b/Optional[float]/g' "$f"
    sed -i '' 's/\bdict | None\b/Optional[dict]/g' "$f"
    sed -i '' 's/\blist | None\b/Optional[list]/g' "$f"
    sed -i '' 's/\bPath | None\b/Optional[Path]/g' "$f"
    sed -i '' 's/\bbool | None\b/Optional[bool]/g' "$f"
    
    FIXED=$((FIXED + 1))
  fi
done

# Also fix ascent_launcher.py if present
if [ -f "ascent_launcher.py" ]; then
  if grep -qE "\b\w+\s*\|\s*None\b" "ascent_launcher.py" 2>/dev/null; then
    echo "  Fixing: ascent_launcher.py"
    sed -i '' 's/\bstr | None\b/Optional[str]/g' "ascent_launcher.py"
    sed -i '' 's/\bint | None\b/Optional[int]/g' "ascent_launcher.py"
    FIXED=$((FIXED + 1))
  fi
fi

echo ""
if [ $FIXED -eq 0 ]; then
  echo "✓  No files needed fixing."
else
  echo "✓  Fixed $FIXED file(s). Rebuild the app:"
  echo "   bash build_app.sh"
fi
