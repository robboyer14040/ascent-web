"""static_map.py — Server-side static route map for the tour-blog PDF.

WeasyPrint runs no JavaScript, so the printed book cannot use a live Leaflet map. This
module stitches raster map tiles around a route's bounding box, draws the route polyline
on top with Pillow, and returns PNG bytes (embedded as a data: URI in the PDF). Self
contained — no external static-map service or API key required.

Pillow and httpx are already project dependencies.
"""

import io
import math
import os
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Sequence, Tuple

import httpx
from PIL import Image, ImageDraw

TILE_SIZE = 256
_UA = {"User-Agent": "AscentWeb/1.0 (tour blog PDF export; +https://ascent.fly.dev)"}
_OSM = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def default_tile_url() -> str:
    """Prefer Stadia's light basemap when keyed (allowed server-side and not
    throttled like OSM-from-cloud); fall back to OSM otherwise."""
    key = os.environ.get("STADIA_API_KEY", "")
    if key:
        return ("https://tiles.stadiamaps.com/tiles/alidade_smooth"
                "/{z}/{x}/{y}.png?api_key=" + key)
    return _OSM


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
                     width: int = 960, height: int = 540,
                     line_color: Tuple[int, int, int] = (22, 163, 74),
                     tile_url: Optional[str] = None,
                     pad: float = 0.08,
                     tile_cache: Optional[dict] = None) -> bytes:
    """Render a route (list of [lat, lon, ...]) onto a stitched tile map. Returns PNG bytes.
    Tiles are fetched concurrently (and deduped via the optional shared tile_cache) so a
    photo-heavy, many-stage book renders in seconds rather than stalling on sequential,
    possibly-throttled requests from a cloud host."""
    tile_url = tile_url or default_tile_url()
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

    # Collect the tiles this canvas needs, fetch the uncached ones concurrently.
    placements = []          # (px, py, cache_key)
    to_fetch = {}            # cache_key -> (z, wx, ty)
    for tx in range(tx0, tx1 + 1):
        for ty in range(ty0, ty1 + 1):
            if ty < 0 or ty >= max_tile:
                continue
            wx = tx % max_tile                          # horizontal wrap
            key = (z, wx, ty)
            placements.append((int(round(tx * TILE_SIZE - origin_x)),
                               int(round(ty * TILE_SIZE - origin_y)), key))
            if key not in tile_cache and key not in to_fetch:
                to_fetch[key] = (z, wx, ty)

    if to_fetch:
        with httpx.Client(timeout=5, headers=_UA) as client:
            def _grab(item):
                key, (zz, xx, yy) = item
                return key, _fetch_tile(client, tile_url, zz, xx, yy)
            with ThreadPoolExecutor(max_workers=8) as pool:
                for key, tile in pool.map(_grab, list(to_fetch.items())):
                    tile_cache[key] = tile

    for px, py, key in placements:
        tile = tile_cache.get(key)
        if tile is not None:
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


# Gradient % → color, matching elev_gradient.js `gradientColor` (light-theme stops):
# blue (downhill) → green (flat) → yellow → orange → red (steep).
_GRAD_STOPS = [(-20, 29, 78, 216), (-5, 96, 165, 250), (0, 21, 128, 61),
               (5, 161, 98, 7), (10, 194, 65, 12), (20, 185, 28, 28)]


def _grad_color(pct: float) -> str:
    stops = _GRAD_STOPS
    c = max(stops[0][0], min(stops[-1][0], pct))
    for i in range(len(stops) - 1):
        t0, r0, g0, b0 = stops[i]
        t1, r1, g1, b1 = stops[i + 1]
        if c <= t1:
            f = (c - t0) / (t1 - t0)
            return (f"rgb({round(r0 + (r1 - r0) * f)},"
                    f"{round(g0 + (g1 - g0) * f)},{round(b0 + (b1 - b0) * f)})")
    return "rgb(185,28,28)"


def _point_grades(pts: Sequence[Sequence[float]]) -> List[float]:
    """Per-point slope % using a ~25 m half-window, mirroring
    stage_elev_chart.js `computeStageGradFromRawPts` so page and PDF agree."""
    n = len(pts)
    R = 3958.8
    cum_mi = [0.0] * n
    for i in range(1, n):
        dlat = math.radians(pts[i][0] - pts[i - 1][0])
        dlon = math.radians(pts[i][1] - pts[i - 1][1])
        a = (math.sin(dlat / 2) ** 2 +
             math.cos(math.radians(pts[i - 1][0])) * math.cos(math.radians(pts[i][0])) *
             math.sin(dlon / 2) ** 2)
        cum_mi[i] = cum_mi[i - 1] + R * 2 * math.asin(math.sqrt(a))
    half = 0.016                                # ≈ 25 m in miles
    grads = [0.0] * n
    for i in range(n):
        lo = hi = i
        while lo > 0 and cum_mi[i] - cum_mi[lo - 1] <= half:
            lo -= 1
        while hi < n - 1 and cum_mi[hi + 1] - cum_mi[i] <= half:
            hi += 1
        if lo == hi:
            if lo > 0:
                lo -= 1
            elif hi < n - 1:
                hi += 1
        d_alt = (pts[hi][2] or 0) - (pts[lo][2] or 0)          # feet
        d_dist = (cum_mi[hi] - cum_mi[lo]) * 5280               # feet
        grads[i] = (d_alt / d_dist * 100) if d_dist > 1 else 0.0
    return grads


def elevation_svg(points: Sequence[Sequence[float]], metric: bool,
                  width: int = 760, height: int = 150) -> str:
    """Inline SVG elevation profile from [[lat, lon, alt_ft], ...]. WeasyPrint renders
    SVG natively, so no raster step is needed. X = cumulative distance, Y = altitude.
    The area is filled per-segment with a slope-gradient color (green→red) matching the
    interactive chart, rather than a flat blue."""
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

    grads = _point_grades(pts)

    pad = 6
    x_max = xs[-1] or 1
    y_min, y_max = min(ys), max(ys)
    y_span = (y_max - y_min) or 1

    def sx(x): return pad + (x / x_max) * (width - 2 * pad)
    def sy(y): return height - pad - ((y - y_min) / y_span) * (height - 2 * pad)

    base = height - pad
    # One filled trapezoid per segment, colored by the segment's slope.
    segs = []
    for i in range(len(xs) - 1):
        x0, x1 = sx(xs[i]), sx(xs[i + 1])
        y0, y1 = sy(ys[i]), sy(ys[i + 1])
        color = _grad_color(grads[i])
        segs.append(
            f'<polygon points="{x0:.1f},{base:.1f} {x0:.1f},{y0:.1f} '
            f'{x1:.1f},{y1:.1f} {x1:.1f},{base:.1f}" fill="{color}" fill-opacity="0.82"/>'
        )
    line = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in zip(xs, ys))
    x_unit = "km" if metric else "mi"
    y_unit = "m" if metric else "ft"
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="100%" preserveAspectRatio="none" class="elev-svg">'
        f'{"".join(segs)}'
        f'<polyline points="{line}" fill="none" stroke="rgba(255,255,255,0.55)" stroke-width="1"/>'
        f'<text x="{pad}" y="14" font-size="10" fill="#64748b">{round(y_max)} {y_unit}</text>'
        f'<text x="{width - pad}" y="{height - 4}" font-size="10" fill="#64748b" '
        f'text-anchor="end">{x_max:.1f} {x_unit}</text>'
        f'</svg>'
    )
