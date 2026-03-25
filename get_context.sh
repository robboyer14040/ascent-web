cd /Volumes/Lion2/projects/ascent-web
find . -type d -name 'venv*' -prune -o -type f \( -name "*.html" -o -name "*.py" -o -name "*.css" \) -print \
  | sort \
  | while read f; do echo "===== $f ====="; cat "$f"; echo; done \
  > /tmp/ascent-context.txt