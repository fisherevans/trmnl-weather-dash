# trmnl-weather-dash

A weather dashboard for the [TRMNL X](https://shop.trmnl.com/products/trmnl-x)
10.3" e-ink panel. Pulls hourly forecast from a public weather API, prefers
real Home Assistant sensor readings when available, renders a native-resolution
1872×1404 4-bit grayscale PNG, and serves it over HTTP for TRMNL's Image
Display plugin to poll.

![sample render](docs/screenshot.png)

What's on the panel:

- **OUTSIDE** — current condition icon, temperature + 3-hour trend arrow,
  humidity + trend, wind/gust/direction.
- **TEMPERATURE FORECAST** — next 24h high/low with a `TMRW` pill if the
  peak/trough falls on the next calendar day.
- **INSIDE** — average of your configured indoor HA sensors; renders `--`
  when nothing's configured.
- **PRECIPITATION & CLOUD COVER FORECAST** — back-to-back bar chart. Rain
  bars grow up; cloud bars grow down. Dotted threshold lines mark
  MODERATE/HEAVY rain and PARTLY/OVERCAST cloud cover. The night half of
  the chart is shaded with a parallel night-tinted background pattern so
  the texture continues into evening hours. Sunset/sunrise markers float
  at their actual times.
- A small `Updated HH:MM` stamp in the bottom right tells you at a glance
  if the image is stale.

## Quickstart (Docker)

```bash
git clone https://github.com/fisherevans/trmnl-weather-dash
cd trmnl-weather-dash
cp config.example.yaml config.yaml             # edit for your location + sensors
cp .env.example .env                            # add HA_TOKEN
cp docker-compose.example.yml docker-compose.yml
docker compose up -d
```

The dashboard is served at `http://<host>:8090/<secret_path>/dashboard.png`.
Point a TRMNL Image Display plugin at that URL with a 15-minute refresh
interval and you're done.

Images are published to `ghcr.io/fisherevans/trmnl-weather-dash`. Pin to a
versioned tag (`v0.1.0`) for stable deploys; use `:edge` to track `main`.

## Configuration

Everything's in one `config.yaml`. See
[`config.example.yaml`](config.example.yaml) for the documented schema. Secrets
(HA token, optional weather API key) stay in env vars referenced by name —
keep real values in `.env`.

```bash
# Sanity-check a config before deploying
uv run weatherdash validate --config config.yaml
```

## Weather providers

| Provider | Key required | Coverage | Status |
|---|---|---|---|
| `open-meteo` | none | global | default |
| `nws` | none | US only | tracked in [#11](https://github.com/fisherevans/trmnl-weather-dash/issues/11) |
| `pirate` | yes (free) | global | tracked in [#12](https://github.com/fisherevans/trmnl-weather-dash/issues/12) |

All implement the same `WeatherSource` protocol — switching is one line of
YAML (`weather.provider: nws`). Open-Meteo is the default because it needs
no signup; non-commercial use gets 10k calls/day, well above what a 10-minute
render cadence consumes.

## Home Assistant integration

The dashboard prefers HA sensor readings over the weather API for anything
HA can measure directly:

- **outdoor temp/humidity** — HA when configured, API current observation as
  fallback. The HA sensor is the actual reading at your location; the API
  is a model estimate from 1–30 km away.
- **indoor temp/humidity** — HA only. The API can't measure indoors. List
  several sensors and the dashboard averages them.

```yaml
home_assistant:
  base_url: http://homeassistant.local:8123
  token_env: HA_TOKEN
  sensors:
    outdoor_temp_f: sensor.outdoor_temperature       # single = use as-is
    outdoor_humidity: sensor.outdoor_humidity
    indoor_temp_f:                                    # list = mean
      - sensor.living_room_temp
      - sensor.bedroom_temp
      - sensor.office_temp
    indoor_humidity:
      - sensor.living_room_humidity
      - sensor.bedroom_humidity
```

Every field is optional. A missing or `unavailable` sensor just falls through
to the next fallback; the dashboard never fails because one entity is down.

Create a long-lived access token at `<your-ha-url>/profile/security` and
put it in `.env` as `HA_TOKEN=...` (or whatever name you put in `token_env`).

## Local development

```bash
# Setup (one time) — installs chromium for playwright
uv run weatherdash setup

# Render a static fixture (no API, no HA — useful for UI iteration)
uv run weatherdash render --data data-morning.json --out morning.png

# Render once with live data
uv run weatherdash render-live --config config.yaml

# Run the full service locally (scheduler + HTTP server)
uv run weatherdash serve --config config.yaml
```

`uv run` builds + installs into an ephemeral env on first invocation —
no `pip install` step needed.

The dashboard is one Jinja2 template
([`src/weatherdash/assets/template.html`](src/weatherdash/assets/template.html))
with all CSS inline. No build step, no bundler. Edits go directly there.

## Repo layout

```
src/weatherdash/
├── cli.py                   weatherdash {render,render-live,serve,setup,validate}
├── render.py                html -> chromium -> 4-bit PNG
├── server.py                scheduler loop + aiohttp HTTP server
├── config.py                Pydantic schema + YAML loader
├── aggregate.py             merge weather + HA into render context
├── pipeline.py              shared fetch -> aggregate -> render flow
├── sources/
│   ├── base.py              WeatherSource Protocol + NormalizedForecast
│   ├── openmeteo.py
│   ├── homeassistant.py
│   └── factory.py
└── assets/
    ├── template.html        single Jinja2 template, inline CSS
    ├── bg-{cloud,rain,snow}{,-night}.svg
    └── makin-grey/<58 condition icons>.svg

scripts/                     one-off generators (PEP 723 single-file uv scripts)
data*.json                   dev fixtures for offline rendering
config.example.yaml          documented config schema
Dockerfile                   multi-stage; published to ghcr.io
docker-compose.example.yml   deploy shape
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Issues and PRs welcome.

## Licenses

- Code: [MIT](LICENSE).
- Weather icons under `src/weatherdash/assets/makin-grey/`: MIT,
  [Makin-Things/weather-icons](https://github.com/Makin-Things/weather-icons).
