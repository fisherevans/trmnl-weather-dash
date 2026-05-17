# ─── builder ──────────────────────────────────────────────────────────────
# uv resolves and installs Python deps into a venv we copy into the runtime
# image. Slim base; same Python version (3.12) on both stages so the venv
# carries over without rebuilds.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install deps first (without project source) so this layer caches across
# source-only changes.
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen --no-install-project

# Add the package and install it on top of the cached deps.
COPY README.md ./
COPY src/ ./src/
RUN uv sync --no-dev --frozen


# ─── runtime ──────────────────────────────────────────────────────────────
# python:3.12-slim is ~150 MB. We install Chromium (only) via Playwright's
# CLI, which pulls the right browser version for our playwright package
# and `--with-deps` brings in the GTK/audio/font system libs it needs.
# This shaves ~3 GB vs starting from mcr.microsoft.com/playwright/python
# (which ships chromium + firefox + webkit + edge — we only need chromium).
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    WEATHERDASH_CONFIG=/etc/weatherdash/config.yaml \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install Chromium + its system deps. --with-deps invokes apt-get under
# the hood; the playwright CLI knows the exact package list.
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates && \
    playwright install --with-deps chromium && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /root/.cache/pip /root/.cache/uv

# Non-root user. We deliberately do NOT chown -R the /app or /ms-playwright
# trees — each chown rewrites every file into a fresh image layer (1+ GB
# of duplicated bytes). The files land as root with world-read + world-exec
# bits from the default umask, so the unprivileged user can still launch
# chromium and read the venv.
RUN groupadd --system app && useradd --system --gid app --create-home app

# /data is the writable surface for the rendered PNG. Pre-creating it with
# `app` ownership in the image means a fresh named volume inherits that
# ownership when Docker initializes it. (A bind mount would still need the
# host dir to be writable for the runtime UID.)
RUN mkdir -p /data && chown app:app /data
VOLUME ["/data"]

USER app
EXPOSE 8080

CMD ["weatherdash", "serve"]
