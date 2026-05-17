"""Data sources: weather forecast providers + Home Assistant client.

`base.py` declares the WeatherSource Protocol and the NormalizedForecast
dataclass. Concrete providers (one per module) implement WeatherSource
and translate their wire format into the normalized shape.

`factory.make_weather_source(cfg)` picks the right concrete class based
on the user's config.weather.provider choice.
"""
