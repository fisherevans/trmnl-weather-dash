"""Layout composition: walk a Layout tree, render each panel into its
allocated rectangle, draw separators between siblings, return the
final composed PIL.Image.

The compose pass takes a `build_panel` callback - given a PanelSlot
and the slot's allocated width/height in pixels, return that panel's
rendered image. The indirection lets callers control caching, mocking,
and timing without baking those concerns into the layout walk.

Single-panel layouts (a bare PanelSlot at the root) fill the whole
canvas; the slot's `size:` is ignored at the root since there's no
parent to be a fraction of.

Mixed fr + px sizing is supported: fixed-px children consume their
declared pixels first, then fr children split what's left. The current
deployed weather dashboard exercises only the root-PanelSlot path;
vstack/hstack composition is in here for new dashboards (OG combined,
etc.) but is unexercised today and may want polish when first used.
"""
from __future__ import annotations

from typing import Callable

from PIL import Image, ImageDraw, ImageColor

from .layout import HStack, Layout, PanelSlot, Separator, Size, StackBody, VStack


BuildPanel = Callable[[PanelSlot, int, int], Image.Image]


def render_layout(
    layout: Layout,
    *,
    canvas_size: tuple[int, int],
    build_panel: BuildPanel,
    background: int = 255,
) -> Image.Image:
    """Compose `layout` onto a canvas of `canvas_size` (width, height).

    `background` is the L-mode fill value behind any uncovered pixels
    (padding/gaps). Default 255 (white) so the canvas matches the
    typical e-ink panel background.
    """
    w, h = canvas_size
    canvas = Image.new("RGB", (w, h), (background, background, background))
    _render_node(layout, canvas, (0, 0, w, h), build_panel)
    return canvas


def _render_node(node: Layout, canvas: Image.Image, rect: tuple[int, int, int, int],
                 build_panel: BuildPanel) -> None:
    x, y, w, h = rect
    if isinstance(node, PanelSlot):
        img = build_panel(node, w, h)
        # Panels can return any image mode; convert to canvas mode for paste.
        canvas.paste(img.convert(canvas.mode), (x, y))
        return
    if isinstance(node, VStack):
        _render_stack(node.vstack, canvas, rect, axis="y", build_panel=build_panel)
        return
    if isinstance(node, HStack):
        _render_stack(node.hstack, canvas, rect, axis="x", build_panel=build_panel)
        return
    raise TypeError(f"unsupported layout node: {type(node).__name__}")


def _render_stack(body: StackBody, canvas: Image.Image, rect: tuple[int, int, int, int],
                  *, axis: str, build_panel: BuildPanel) -> None:
    x, y, w, h = rect
    pad = body.padding
    # Inset by padding.
    ix, iy = x + pad, y + pad
    iw, ih = w - 2 * pad, h - 2 * pad

    n = len(body.children)
    if n == 0:
        return

    # Reserve space for separators between siblings; gaps stack on top
    # of any separator thickness. A separator is drawn centered in its
    # reservation, so the visual padding around it on each side equals
    # half the gap.
    sep_thickness = body.separator.thickness if body.separator else 0
    gap_total = body.gap * (n - 1)
    sep_total = sep_thickness * (n - 1)
    available = (iw if axis == "x" else ih) - gap_total - sep_total

    # Two-pass sizing: fixed-px children first, then fr children split
    # what's left. Percentages resolve against the original `available`
    # before subtracting fixed children, matching how flexbox / css
    # grid treat % within a container.
    parsed_sizes = [c.parsed_size if isinstance(c, PanelSlot) else Size("fr", 1.0) for c in body.children]
    fixed_px = 0
    pct_px = 0
    fr_total = 0.0
    for s in parsed_sizes:
        if s.kind == "px":
            fixed_px += int(s.value)
        elif s.kind == "%":
            pct_px += int(round(available * s.value / 100.0))
        else:  # fr
            fr_total += s.value
    fr_remaining = max(0, available - fixed_px - pct_px)
    fr_per_unit = fr_remaining / fr_total if fr_total > 0 else 0.0

    sizes: list[int] = []
    for s in parsed_sizes:
        if s.kind == "px":
            sizes.append(int(s.value))
        elif s.kind == "%":
            sizes.append(int(round(available * s.value / 100.0)))
        else:
            sizes.append(int(round(s.value * fr_per_unit)))

    cursor = ix if axis == "x" else iy
    draw = ImageDraw.Draw(canvas) if body.separator else None
    for i, (child, size) in enumerate(zip(body.children, sizes)):
        if axis == "x":
            child_rect = (cursor, iy, size, ih)
        else:
            child_rect = (ix, cursor, iw, size)
        _render_node(child, canvas, child_rect, build_panel)
        cursor += size
        if i < n - 1:
            # Half-gap before separator, separator, half-gap after.
            cursor += body.gap // 2
            if body.separator and draw is not None:
                _draw_separator(draw, body.separator, cursor, axis, ix, iy, iw, ih)
                cursor += sep_thickness
            cursor += body.gap - body.gap // 2


def _draw_separator(draw: ImageDraw.ImageDraw, sep: Separator, cursor: int, axis: str,
                    ix: int, iy: int, iw: int, ih: int) -> None:
    color = ImageColor.getrgb(sep.color)
    if axis == "x":
        # Vertical line across the cross-axis.
        draw.rectangle((cursor, iy, cursor + sep.thickness - 1, iy + ih - 1), fill=color)
    else:
        # Horizontal line across the cross-axis.
        draw.rectangle((ix, cursor, ix + iw - 1, cursor + sep.thickness - 1), fill=color)
