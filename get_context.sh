cd /Volumes/Lion2/projects/ascent-web
find . -type f \( -name "*.html" -o -name "*.py" -o -name "*.css" \) \
  | sort \
  | while read f; do echo "===== $f ====="; cat "$f"; echo; done \
  > /tmp/ascent-context.txt