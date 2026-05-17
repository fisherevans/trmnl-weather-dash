#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["shapely>=2.0", "svgpathtools>=1.6"]
# ///
"""Generate the chart background tiles (cloud + rain, day + night) from the
artist-supplied silhouettes.

- shape-cloud-a.svg, shape-cloud-b.svg, shape-drop.svg are the source shapes.
- Placement uses Mitchell's best-candidate with exact silhouette collision.
  For each shape we parse its `<path d=...>` into a shapely Polygon (in the
  symbol's viewBox coordinates), apply scale/flip/rotate/translate to get
  the candidate's actual on-tile geometry, and reject candidates whose
  polygon intersects any already-placed polygon. Toroidal wrap is handled
  by testing the candidate at its base position plus 8 edge translates.
  Among non-overlapping candidates, the one farthest from existing centres
  (toroidal distance) is kept. Result is seeded, evenly distributed, and
  silhouette-overlap-free (rect bounds can still overlap, since the
  collision shape is the actual silhouette, not its bounding rect).
- Day pattern fills use a mid-light grey; night uses a lighter grey so the
  shapes read against the night-shade base.

Writes pattern-cloud.svg, pattern-cloud-night.svg, pattern-rain.svg,
pattern-rain-night.svg in the current directory.
"""
from __future__ import annotations
import functools
import math
import random
import re
from pathlib import Path

from shapely.geometry import Polygon, box
from shapely.strtree import STRtree
from shapely import affinity
from svgpathtools import parse_path

ROOT = Path(__file__).parent


def read_shape(path: Path) -> tuple[str, tuple[float, float, float, float]]:
    """Return (inner_svg_markup, viewBox_xywh) for the given source SVG.

    Any explicit fill on inner elements is rewritten to `currentColor` so
    that whatever color the surrounding `<g>` sets in the pattern cascades
    down to the actual shape paths.
    """
    text = path.read_text()
    vb = re.search(r'viewBox="([\d.\-\s]+)"', text)
    if not vb:
        raise ValueError(f"no viewBox in {path}")
    x, y, w, h = (float(p) for p in vb.group(1).split())
    inner = re.search(r"<svg[^>]*>(.*)</svg>", text, re.S).group(1).strip()
    inner = re.sub(r"<!--.*?-->", "", inner, flags=re.S)
    inner = re.sub(r"<metadata>.*?</metadata>", "", inner, flags=re.S)
    # Drop any explicit fill so the parent <g fill="..."> in the pattern wins.
    inner = re.sub(r'\s+fill="[^"]*"', "", inner)
    return inner, (x, y, w, h)


_POTRACE_TRANSFORM_RE = re.compile(
    r'transform="translate\(([\-\d.]+),([\-\d.]+)\)\s+scale\(([\-\d.]+),([\-\d.]+)\)"'
)


@functools.lru_cache(maxsize=None)
def read_shape_polygon(path: Path, samples: int = 160) -> tuple[Polygon, tuple[float, float, float, float]]:
    """Parse the artist-supplied SVG into a shapely Polygon expressed in the
    symbol's viewBox coordinates, centred at the viewBox centre.

    The source SVGs are Potrace output: a single `<path d="...">` inside a
    `<g transform="translate(tx,ty) scale(sx,sy)">`. The transform maps raw
    Potrace coordinates into viewBox coordinates (typically with sy < 0 to
    flip Y). We sample the path's Béziers to a polyline, apply the
    Potrace-style transform, then re-centre.
    """
    text = path.read_text()
    vb = re.search(r'viewBox="([\d.\-\s]+)"', text)
    if not vb:
        raise ValueError(f"no viewBox in {path}")
    vx, vy, vw, vh = (float(p) for p in vb.group(1).split())

    tm = _POTRACE_TRANSFORM_RE.search(text)
    if tm:
        tx, ty, sx, sy = (float(g) for g in tm.groups())
    else:
        tx, ty, sx, sy = 0.0, 0.0, 1.0, 1.0

    d = re.search(r'<path[^>]*\bd="([^"]+)"', text, re.S).group(1)
    parsed = parse_path(d)
    rings: list[Polygon] = []
    for sub in parsed.continuous_subpaths():
        if sub.length() == 0:
            continue
        pts = []
        for i in range(samples):
            p = sub.point(i / samples)
            rx, ry = p.real, p.imag
            pts.append((sx * rx + tx, sy * ry + ty))
        if len(pts) < 3:
            continue
        poly = Polygon(pts)
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        rings.append(poly)
    if not rings:
        raise ValueError(f"no closed subpaths parsed from {path}")
    # Take the largest ring as the silhouette. Source shapes are single
    # closed paths in practice; smaller rings (if any) are dropped.
    rings.sort(key=lambda p: p.area, reverse=True)
    poly = rings[0]
    return affinity.translate(poly, -vx - vw / 2, -vy - vh / 2), (vx, vy, vw, vh)


_TOROIDAL_OFFSETS = [(dx, dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1)]


def _toroidal_dist(p1: tuple[float, float], p2: tuple[float, float],
                   tile_w: float, tile_h: float) -> float:
    dx = abs(p1[0] - p2[0]); dy = abs(p1[1] - p2[1])
    dx = min(dx, tile_w - dx); dy = min(dy, tile_h - dy)
    return math.hypot(dx, dy)


def poisson_place(rnd: random.Random, tile_w: float, tile_h: float,
                  count: int, pick_shape, max_candidates: int = 32):
    """Mitchell's best-candidate with exact silhouette non-overlap.

    `pick_shape(rnd) -> (poly, payload)` returns a shapely Polygon already
    scaled/flipped/rotated and centred on the origin, plus an opaque
    payload describing how to render the same shape later.

    Returns a list of (cx, cy, payload). Shapes that can't find a
    non-overlapping spot within `max_candidates` tries get dropped; raise
    `max_candidates`, lower `count`, or shrink the shapes if that happens
    more than expected.

    Hot-path optimisations: placed polygons go into an STRtree so each
    candidate test is an O(log N) bbox query against the spatial index
    rather than a linear scan, and toroidal wrap copies are skipped when
    their bbox doesn't touch the tile.
    """
    tile_bbox = box(0, 0, tile_w, tile_h)
    placed_polys: list[Polygon] = []
    placed_centres: list[tuple[float, float]] = []
    result: list[tuple[float, float, object]] = []
    tree = STRtree([])
    for _ in range(count):
        base_poly, payload = pick_shape(rnd)
        bx0, by0, bx1, by1 = base_poly.bounds  # bbox of the centred base polygon
        best_pt = None
        best_spread = -math.inf
        for _ in range(max_candidates):
            cx = rnd.uniform(0, tile_w)
            cy = rnd.uniform(0, tile_h)
            collides = False
            for ox, oy in _TOROIDAL_OFFSETS:
                dx = cx + ox * tile_w
                dy = cy + oy * tile_h
                # Quick bbox-vs-tile reject: if the translated shape's bbox
                # doesn't touch the tile, no placed shape inside the tile
                # can intersect it either.
                if bx1 + dx < 0 or bx0 + dx > tile_w or by1 + dy < 0 or by0 + dy > tile_h:
                    continue
                cand = affinity.translate(base_poly, dx, dy)
                hits = tree.query(cand)
                if len(hits) and any(cand.intersects(placed_polys[i]) for i in hits):
                    collides = True
                    break
            if collides:
                continue
            if not placed_centres:
                best_pt = (cx, cy)
                best_spread = math.inf
                break
            spread = min(_toroidal_dist((cx, cy), p, tile_w, tile_h) for p in placed_centres)
            if spread > best_spread:
                best_spread = spread
                best_pt = (cx, cy)
        if best_pt is None:
            continue
        placed_polys.append(affinity.translate(base_poly, best_pt[0], best_pt[1]))
        placed_centres.append(best_pt)
        tree = STRtree(placed_polys)
        result.append((best_pt[0], best_pt[1], payload))
    return result


def _edge_offsets(x: float, y: float, w: float, h: float, tile_w: float, tile_h: float):
    """Return the set of (dx, dy) offsets needed so a shape at (x, y, w, h)
    tiles seamlessly — if it crosses an edge, place a wrapped copy on the
    opposite side so the neighbour tile picks up the missing part."""
    offsets = [(0.0, 0.0)]
    L, R = x < 0, x + w > tile_w
    T, B = y < 0, y + h > tile_h
    if L: offsets.append((tile_w, 0))
    if R: offsets.append((-tile_w, 0))
    if T: offsets.append((0, tile_h))
    if B: offsets.append((0, -tile_h))
    if L and T: offsets.append((tile_w, tile_h))
    if R and T: offsets.append((-tile_w, tile_h))
    if L and B: offsets.append((tile_w, -tile_h))
    if R and B: offsets.append((-tile_w, -tile_h))
    return offsets


def build_cloud_svg(
    fill: str = "#bdbdbd",
    *,
    tile_w: int = 720,
    tile_h: int = 460,
    count: int = 6,
    base_width_frac: float = 0.30,     # base cloud width as fraction of tile_w
    min_scale: float = 0.70,
    max_scale: float = 1.25,
    max_candidates: int = 32,
    seed: int = 7,
) -> str:
    """Build a cloud pattern tile and return the SVG as a string."""
    rnd = random.Random(seed)
    cloud_a, va = read_shape(ROOT / "shape-cloud-a.svg")
    cloud_b, vb = read_shape(ROOT / "shape-cloud-b.svg")
    poly_a, _ = read_shape_polygon(ROOT / "shape-cloud-a.svg")
    poly_b, _ = read_shape_polygon(ROOT / "shape-cloud-b.svg")

    base_w = tile_w * base_width_frac
    base_h_a = base_w * va[3] / va[2]
    base_h_b = base_w * vb[3] / vb[2]

    def pick_cloud(r: random.Random):
        scale = r.uniform(min_scale, max_scale)
        pick_a = r.random() < 0.5
        flip = -1 if r.random() < 0.5 else 1
        w = base_w * scale
        h = (base_h_a if pick_a else base_h_b) * scale
        base_poly = poly_a if pick_a else poly_b
        vw = va[2] if pick_a else vb[2]
        s = w / vw  # uniform scale (viewBox aspect matches rendered aspect)
        poly = affinity.scale(base_poly, xfact=s * flip, yfact=s, origin=(0, 0))
        return poly, (pick_a, w, h, flip)

    uses = []
    for cx, cy, payload in poisson_place(rnd, tile_w, tile_h, count, pick_cloud, max_candidates):
        pick_a, w, h, flip = payload
        sym = "cloud-a" if pick_a else "cloud-b"
        x = cx - w / 2
        y = cy - h / 2
        for dx, dy in _edge_offsets(x, y, w, h, tile_w, tile_h):
            xx, yy, ccx, ccy = x + dx, y + dy, cx + dx, cy + dy
            if flip == -1:
                uses.append(
                    f'<g transform="translate({ccx:.2f} {ccy:.2f}) scale(-1 1) translate({-ccx:.2f} {-ccy:.2f})">'
                    f'<use href="#{sym}" x="{xx:.2f}" y="{yy:.2f}" width="{w:.2f}" height="{h:.2f}"/></g>'
                )
            else:
                uses.append(
                    f'<use href="#{sym}" x="{xx:.2f}" y="{yy:.2f}" width="{w:.2f}" height="{h:.2f}"/>'
                )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {tile_w} {tile_h}">\n'
        f"  <defs>\n"
        f'    <symbol id="cloud-a" viewBox="{va[0]} {va[1]} {va[2]} {va[3]}" preserveAspectRatio="xMidYMid meet">{cloud_a}</symbol>\n'
        f'    <symbol id="cloud-b" viewBox="{vb[0]} {vb[1]} {vb[2]} {vb[3]}" preserveAspectRatio="xMidYMid meet">{cloud_b}</symbol>\n'
        f"  </defs>\n"
        f'  <g fill="{fill}">\n    '
        + "\n    ".join(uses)
        + f"\n  </g>\n</svg>\n"
    )


def build_rain_svg(
    fill: str = "#bdbdbd",
    *,
    tile_w: int = 260,
    tile_h: int = 320,
    count: int = 35,
    angle_min: float = 10,
    angle_max: float = 20,
    base_drop_w: int = 26,
    drop_stretch: float = 1.35,
    min_scale: float = 0.85,
    max_scale: float = 1.25,
    max_candidates: int = 32,
    seed: int = 13,
) -> str:
    """Build a rain pattern tile and return the SVG as a string."""
    rnd = random.Random(seed)
    drop, vd = read_shape(ROOT / "shape-drop.svg")
    poly_d, _ = read_shape_polygon(ROOT / "shape-drop.svg")
    base_h = base_drop_w * vd[3] / vd[2] * drop_stretch

    def pick_drop(r: random.Random):
        scale = r.uniform(min_scale, max_scale)
        angle = r.uniform(angle_min, angle_max)
        w = base_drop_w * scale
        h = base_h * scale
        sx = w / vd[2]
        sy = h / vd[3]
        poly = affinity.scale(poly_d, xfact=sx, yfact=sy, origin=(0, 0))
        poly = affinity.rotate(poly, angle, origin=(0, 0))
        return poly, (w, h, angle)

    uses = []
    for cx, cy, payload in poisson_place(rnd, tile_w, tile_h, count, pick_drop, max_candidates):
        w, h, angle = payload
        # Include rotation in the wrap-bounds: a drop tilted ~20° extends
        # about +15% in each direction.
        bw, bh = w * 1.2, h * 1.2
        for dx, dy in _edge_offsets(cx - bw/2, cy - bh/2, bw, bh, tile_w, tile_h):
            ccx, ccy = cx + dx, cy + dy
            xx, yy = ccx - w/2, ccy - h/2
            uses.append(
                f'<g transform="rotate({angle:.2f} {ccx:.2f} {ccy:.2f})">'
                f'<use href="#drop" x="{xx:.2f}" y="{yy:.2f}" width="{w:.2f}" height="{h:.2f}"/>'
                f"</g>"
            )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {tile_w} {tile_h}">\n'
        f"  <defs>\n"
        f'    <symbol id="drop" viewBox="{vd[0]} {vd[1]} {vd[2]} {vd[3]}" preserveAspectRatio="xMidYMid meet">{drop}</symbol>\n'
        f"  </defs>\n"
        f'  <g fill="{fill}">\n    '
        + "\n    ".join(uses)
        + f"\n  </g>\n</svg>\n"
    )


DAY_FILL = "#bdbdbd"
NIGHT_FILL = "#a4a4a4"


def write_default_set() -> None:
    """Regenerate the four pattern SVGs using the defaults."""
    (ROOT / "pattern-cloud.svg").write_text(build_cloud_svg(DAY_FILL))
    (ROOT / "pattern-cloud-night.svg").write_text(build_cloud_svg(NIGHT_FILL, seed=11))
    (ROOT / "pattern-rain.svg").write_text(build_rain_svg(DAY_FILL))
    (ROOT / "pattern-rain-night.svg").write_text(build_rain_svg(NIGHT_FILL, seed=17))


if __name__ == "__main__":
    write_default_set()
    print("wrote pattern-cloud{,-night}.svg, pattern-rain{,-night}.svg")
