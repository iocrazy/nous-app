# Playwright's own image already carries Chromium plus every shared library it
# needs. The tag and the `playwright` pin in pyproject.toml must move together -
# a mismatch fails at runtime with "Executable doesn't exist".
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# Xvfb, because this service runs Chromium HEADED (design doc 2.4). Headless
# leaves detectable traces and Douyin bounces the upload page to login, which
# reads as an expired session. Server-side != headless.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xvfb \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    VIRTUAL_ENV=/app/.venv \
    PATH=/app/.venv/bin:$PATH \
    DISPLAY=:99 \
    XVFB_SCREEN=1920x1080x24

WORKDIR /app

# Dependency layer first so app edits do not reinstall the world.
#
# uv.lock is REQUIRED, not optional. Without it every rebuild re-resolves and
# can silently drift patchright/playwright to a new patch release - which
# changes the browser fingerprint and the DOM these validators read. The
# failure mode is the worst kind: no code changed, CI is green, and session
# validation starts misjudging live accounts as dead. --frozen makes a stale
# lock a build error instead of a silent re-resolve.
# --no-install-project: `app/` is copied AFTER this layer (that is the whole
# point of the split), so building the project itself here would fail on
# hatchling's packages = ["app"]. Only third-party deps belong in this layer.
# Dev extras are opt-in under [project.optional-dependencies] and are never
# installed by a plain sync, so the image stays free of pytest/httpx.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project

COPY app ./app
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

# pwuser ships with the base image and owns the browser cache. Running the
# service as a non-root user keeps a compromised page from owning the container.
RUN chown -R pwuser:pwuser /app
USER pwuser

EXPOSE 8090

# Probes the real thing: /healthz launches a Chromium (TTL-cached) and returns
# 503 when the browser or Xvfb is not actually usable.
HEALTHCHECK --interval=30s --timeout=20s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/healthz', timeout=15).status == 200 else 1)"

ENTRYPOINT ["./entrypoint.sh"]
