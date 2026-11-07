#!/usr/bin/env bash
# Export a reveal.js deck to PDF, one 1280x720 page per slide with every fragment shown.
#
# Headless `--print-to-pdf` on `?print-pdf` does not work with the current Chromium: it
# prints before reveal.js builds its print layout, so the deck comes out blank or paginated
# like a web page. Screenshotting each slide and binding the images sidesteps the print
# path entirely.
#
#   ./export-pdf.sh defesa.html defesa.pdf
set -euo pipefail

deck="${1:-defesa.html}"
out="$(realpath -m "${2:-${deck%.html}.pdf}")"
here="$(cd "$(dirname "$0")" && pwd)"
browser="$(command -v chromium || command -v chromium-browser || command -v google-chrome-stable || true)"
[ -n "$browser" ] || { echo "needs chromium or google-chrome on PATH" >&2; exit 1; }

shots="$(mktemp -d)"
trap 'rm -rf "$shots"' EXIT

# Top-level <section>s are the slides; reveal's hash URLs (#/n) address them from 0.
count="$(grep -c '^<section' "$here/$deck" || true)"
[ "$count" -gt 0 ] || { echo "no top-level <section> found in $deck" >&2; exit 1; }

for ((i = 0; i < count; i++)); do
  "$browser" --headless=new --disable-gpu --no-sandbox --hide-scrollbars \
    --window-size=1280,720 --virtual-time-budget=4000 \
    --screenshot="$shots/$(printf '%03d' "$i").png" \
    "file://$here/$deck?allfrags#/$i" >/dev/null 2>&1
done

uv run --quiet --with pillow python - "$shots" "$out" <<'EOF'
import sys
from pathlib import Path

from PIL import Image

shots, out = Path(sys.argv[1]), sys.argv[2]
pages = [Image.open(p).convert("RGB") for p in sorted(shots.glob("*.png"))]
pages[0].save(out, save_all=True, append_images=pages[1:], resolution=96)
print(f"{out}: {len(pages)} pages")
EOF
