"""TRMNL X weather dashboard renderer.

Generates a 1872x1404, 16-grey PNG for the TRMNL X 10.3" e-ink panel.
Pipeline: data dict -> Jinja2 template -> headless Chromium screenshot
-> Pillow 4-bit grayscale quantization.

Top-level surface:
  weatherdash.render.render_to_png(data, out_path, quantize=True, keep_html=False)
  weatherdash.cli.main()  # console entrypoint
"""
