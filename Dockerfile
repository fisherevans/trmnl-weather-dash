# ─── builder ──────────────────────────────────────────────────────────────
# uv resolves and installs Python deps into a venv we copy into the runtime
# image. Same Python version (3.12) as the runtime base so the venv runs
# without rebuilds.
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
# Playwright's official Python image ships Chromium + the long list of GTK/
# audio/font system libraries it needs. We just drop our venv on top.
FROM mcr.microsoft.com/playwright/python:v1.50.0-noble AS runtime
WORKDIR /app

COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    WEATHERDASH_CONFIG=/etc/weatherdash/config.yaml

# Non-root user. Browsers under /ms-playwright are world-executable in the
# base image, so an unprivileged user can launch Chromium.
RUN groupadd --system app && useradd --system --gid app --create-home app && \
    chown -R app:app /app

USER app
VOLUME ["/data"]
EXPOSE 8080

CMD ["weatherdash", "serve"]
