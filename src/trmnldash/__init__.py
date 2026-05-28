"""TRMNL dashboard suite.

A toolkit for rendering TRMNL e-ink dashboards. The engine is panel-agnostic:
a panel supplies a template + assets + context-building logic; the engine
turns it into a screenshot and palette-quantizes it for the target device.

Layout:
  trmnldash.engine               panel-agnostic render + quantize + serve
  trmnldash.panels.<name>        one self-contained panel per device target
  trmnldash.sources              data providers (weather, HA, ...) shared
                                 across panels
  trmnldash.config               YAML config schema + loader
  trmnldash.cli                  console entrypoint
"""
