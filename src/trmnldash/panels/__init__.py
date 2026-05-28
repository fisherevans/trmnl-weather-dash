"""Panels: atomic dashboard units.

Each panel is a self-contained renderable thing - own template, own
context-building logic, own assets, own target dimensions. Panels render
to a PIL.Image; the dashboard layer composes panels onto a final canvas
and runs the result through `engine.quantize`.

Panel module convention (phase 2 - a registry/manifest lands in phase 3):
- `RENDER_SPEC` - a RenderSpec(width, height, palette) declaring the
  panel's natural size and the device palette it targets.
- `render(data) -> PIL.Image` - builds the panel's HTML from `data` and
  returns the screenshot. The shape of `data` is panel-specific.
"""
