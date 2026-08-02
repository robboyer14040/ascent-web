"""static_map.py — Server-side static route map for the tour-blog PDF.

WeasyPrint runs no JavaScript, so the printed book cannot use a live Leaflet map. This
module stitches raster map tiles around a route's bounding box, draws the route polyline
on top with Pillow, and returns PNG bytes (embedded as a data: URI in the PDF). Self
contained — no external static-map service or API key required.

Pillow and httpx are already project dependencies.
"""

import io
import math
from typing import List, Optional, Sequence, Tuple

import httpx
from PIL import Image, ImageDraw

TILE_SIZE = 256
_UA = {"User-Agent": "AscentWeb/1.0 (tour blog PDF export)"}
_OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _lonlat_to_world_px(lon: float, lat: float, z: int) -> Tuple[float, float]:
    """Web-Mercator world-pixel coordinates at zoom z."""
    n = TILE_SIZE * (2 ** z)
    x = (lon + 180.0) / 360.0 * n
    lat = max(min(lat, 85.05112878), -85.05112878)
    lat_rad = math.radians(lat)
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def _pick_zoom(pts: Sequence[Tuple[float, float]], w: int, h: int, pad: float) -> int:
    """Largest zoom (≤16) at which the padded route fits inside a w×h canvas."""
    lats = [p[0] for p in pts]
    lons = [p[1] for p in pts]
    avail_w = w * (1.0 - 2 * pad)
    avail_h = h * (1.0 - 2 * pad)
    for z in range(16, 0, -1):
        xs = [_lonlat_to_world_px(lon, lat, z) for lat, lon in zip(lats, lons)]
        span_x = max(x for x, _ in xs) - min(x for x, _ in xs)
        span_y = max(y for _, y in xs) - min(y for _, y in xs)
        if span_x <= avail_w and span_y <= avail_h:
            return z
    return 1


def render_route_png(points: Sequence[Sequence[float]],
                     width: int = 1000, height: int = 1000,
                     line_color: Tuple[int, int, int] = (22, 163, 74),
                     tile_url: str = _OSM,
                     pad: float = 0.08,
                     tile_cache: Optional[dict] = None) -> bytes:
    """Render a route (list of [lat, lon, ...]) onto a stitched tile map. Returns PNG bytes.
    An optional tile_cache dict (shared across calls building one PDF) dedupes tile fetches."""
    pts = [(float(p[0]), float(p[1])) for p in points if p and p[0] is not None and p[1] is not None]
    canvas = Image.new("RGB", (width, height), (233, 231, 225))
    if len(pts) < 2:
        return _to_png(canvas)

    z = _pick_zoom(pts, width, height, pad)
    world = [_lonlat_to_world_px(lon, lat, z) for lat, lon in pts]
    cx = (min(x for x, _ in world) + max(x for x, _ in world)) / 2.0
    cy = (min(y for _, y in world) + max(y for _, y in world)) / 2.0

    # Top-left world pixel of the canvas.
    origin_x = cx - width / 2.0
    origin_y = cy - height / 2.0

    max_tile = 2 ** z
    tx0 = math.floor(origin_x / TILE_SIZE)
    ty0 = math.floor(origin_y / TILE_SIZE)
    tx1 = math.floor((origin_x + width) / TILE_SIZE)
    ty1 = math.floor((origin_y + height) / TILE_SIZE)

    if tile_cache is None:
        tile_cache = {}
    with httpx.Client(timeout=8, headers=_UA) as client:
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                if ty < 0 or ty >= max_tile:
                    continue
                wx = tx % max_tile                      # horizontal wrap
                key = (z, wx, ty)
                tile = tile_cache.get(key)
                if tile is None:
                    tile = _fetch_tile(client, tile_url, z, wx, ty)
                    tile_cache[key] = tile
                if tile is None:
                    continue
                px = int(round(tx * TILE_SIZE - origin_x))
                py = int(round(ty * TILE_SIZE - origin_y))
                canvas.paste(tile, (px, py))

    # Draw the route polyline (with a subtle white casing for contrast).
    line = [(x - origin_x, y - origin_y) for x, y in world]
    draw = ImageDraw.Draw(canvas)
    draw.line(line, fill=(255, 255, 255), width=7, joint="curve")
    draw.line(line, fill=line_color, width=4, joint="curve")
    _dot(draw, line[0], (22, 163, 74))
    _dot(draw, line[-1], (220, 38, 38))
    return _to_png(canvas)


def _fetch_tile(client: httpx.Client, tmpl: str, z: int, x: int, y: int):
    try:
        r = client.get(tmpl.format(z=z, x=x, y=y))
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception:
        pass
    return None


def _dot(draw: ImageDraw.ImageDraw, xy, color, r: int = 6):
    x, y = xy
    draw.ellipse([x - r, y - r, x + r, y + r], fill=color, outline=(255, 255, 255), width=2)


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def elevation_svg(points: Sequence[Sequence[float]], metric: bool,
                  width: int = 760, height: int = 150) -> str:
    """Inline SVG elevation profile from [[lat, lon, alt_ft], ...]. WeasyPrint renders
    SVG natively, so no raster step is needed. X = cumulative distance, Y = altitude."""
    pts = [p for p in points if p and len(p) >= 3]
    if len(pts) < 2:
        return ""

    # Cumulative distance (miles), altitude (feet).
    R = 3958.8
    cum = 0.0
    xs: List[float] = []
    ys: List[float] = []
    for i, p in enumerate(pts):
        if i > 0:
            dlat = math.radians(pts[i][0] - pts[i - 1][0])
            dlon = math.radians(pts[i][1] - pts[i - 1][1])
            a = (math.sin(dlat / 2) ** 2 +
                 math.cos(math.radians(pts[i - 1][0])) * math.cos(math.radians(pts[i][0])) *
                 math.sin(dlon / 2) ** 2)
            cum += R * 2 * math.asin(math.sqrt(a))
        xs.append(cum * 1.60934 if metric else cum)
        alt_ft = p[2] or 0
        ys.append(alt_ft * 0.3048 if metric else alt_ft)

    pad = 6
    x_max = xs[-1] or 1
    y_min, y_max = min(ys), max(ys)
    y_span = (y_max - y_min) or 1

    def sx(x): return pad + (x / x_max) * (width - 2 * pad)
    def sy(y): return height - pad - ((y - y_min) / y_span) * (height - 2 * pad)

    line = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    area = f"{pad},{height - pad} {line} {sx(x_max):.1f},{height - pad}"
    x_unit = "km" if metric else "mi"
    y_unit = "m" if metric else "ft"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" preserveAspectRatio="none" class="elev-svg">'
        f'<polygon points="{area}" fill="rgba(59,130,246,0.28)"/>'
        f'<polyline points="{line}" fill="none" stroke="#3b82f6" stroke-width="1.5"/>'
        f'<text x="{pad}" y="14" font-size="10" fill="#64748b">{round(y_max)} {y_unit}</text>'
        f'<text x="{width - pad}" y="{height - 4}" font-size="10" fill="#64748b" '
        f'text-anchor="end">{x_max:.1f} {x_unit}</text>'
        f'</svg>'
    )
