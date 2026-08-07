# UBUNTU_MIRROR is the Ubuntu counterpart of the root Dockerfile's APT_MIRROR.
# The base image below is Ubuntu (noble), not Debian, so it points at
# archive.ubuntu.com and the Debian mirror arg does not apply to it.
#
# 2026-08-07 measured from this machine:
#   archive.ubuntu.com    ~26 KB/s
#   mirrors.aliyun.com   ~952 KB/s   (36x)
# At 26 KB/s `apt-get update` does not merely crawl, it *corrupts*: the index
# download times out half-read and apt reports `Hash Sum mismatch`, which reads
# like a compromised mirror rather than a slow one. That is what failed the
# deploy — and because this stage is `target browser`, the whole deploy-gpu run
# goes red and blocks everyone else's merges too.
#
# Overridable so a build outside China is not forced through a Chinese mirror:
#   docker build --build-arg UBUNTU_MIRROR=archive.ubuntu.com .
ARG UBUNTU_MIRROR=mirrors.aliyun.com

# Playwright's own image already carries Chromium plus every shared library it
# needs. The tag and the `playwright` pin in pyproject.toml must move together -
# a mismatch fails at runtime with "Executable doesn't exist".
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

# ARGs declared before the first FROM are global; a stage that wants one has to
# re-declare it (Docker semantics, not a typo).
ARG UBUNTU_MIRROR

# Xvfb, because this service runs Chromium HEADED (design doc 2.4). Headless
# leaves detectable traces and Douyin bounces the upload page to login, which
# reads as an expired session. Server-side != headless.
#
# Ubuntu 24.04 uses the deb822 format, so the file to rewrite is
# `/etc/apt/sources.list.d/ubuntu.sources` — NOT the classic
# `/etc/apt/sources.list`, which does not exist here. Both URIs need it: the
# archive one AND security.ubuntu.com (they are separate stanzas, and leaving
# security on the slow host reintroduces the same timeout).
RUN if [ -n "$UBUNTU_MIRROR" ]; then \
        sed -i \
            -e "s|http://archive.ubuntu.com/ubuntu/|http://${UBUNTU_MIRROR}/ubuntu/|g" \
            -e "s|http://security.ubuntu.com/ubuntu/|http://${UBUNTU_MIRROR}/ubuntu/|g" \
            /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
    && apt-get update \
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
