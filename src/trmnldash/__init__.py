"""TRMNL dashboard suite.

A small toolkit for rendering TRMNL e-ink dashboards. First dashboard is a
1872x1404 16-grey weather panel for the TRMNL X 10.3" device. Pipeline:
data dict -> Jinja2 template -> headless Chromium screenshot -> Pillow
palette quantization.

Top-level surface:
  trmnldash.render.render_to_png(data, out_path, quantize=True, keep_html=False)
  trmnldash.cli.main()  # console entrypoint
"""
