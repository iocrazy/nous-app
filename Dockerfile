# MediaHub Backend Dockerfile
# Frontend is deployed separately to Vercel

# ============================================
# Build inputs — both exist because this image is built in mainland China
# ============================================
#
# PYTHON_BASE is pinned by digest, not by the floating `python:3.13-slim` tag.
# That tag is rebuilt often (Debian security snapshots), and every rebuild
# invalidates EVERY layer below it — including the apt layer that pulls
# chromium + ffmpeg + nodejs + CJK fonts. Two deploys failed this way on
# 2026-08-05: the tag moved, the cache died, and apt spent 986s before giving
# up. Pinning makes that a deliberate, reviewable change instead of a random
# one. To upgrade:
#   docker buildx imagetools inspect python:3.13-slim --format '{{.Manifest.Digest}}'
#
# APT_MIRROR exists because deb.debian.org measures ~0.29 MB/s from this
# machine while mirrors.aliyun.com measures ~8.1 MB/s (28x). Overridable so a
# build outside China is not forced through a Chinese mirror:
#   docker build --build-arg APT_MIRROR=deb.debian.org .
#
# CARGO_MIRROR is the same story one layer down. Fetching a real crate from
# crates.io on this machine returns a 277-byte error body rather than the
# 78 KB file; rsproxy.cn serves it at ~100 KB/s. That is what killed the
# rust-builder stage with `SSL_ERROR_SYSCALL` before apt ever got a turn.
# Set it empty to use crates.io directly:
#   docker build --build-arg CARGO_MIRROR= .
ARG PYTHON_BASE=python:3.13-slim@sha256:99569264a52f7665899b7bc0fb48e72a2712b850b129f63c4733af1e939accfb
ARG APT_MIRROR=mirrors.aliyun.com
ARG CARGO_MIRROR=sparse+https://rsproxy.cn/index/

# ============================================
# Stage 1: Build Rust nous-core module
# Use python:3.13 as base so maturin builds cp313 wheels
# ============================================
FROM ${PYTHON_BASE} AS rust-builder

# ARGs declared before the first FROM are global; a stage that wants one has to
# re-declare it (Docker semantics, not a typo).
ARG APT_MIRROR

# Debian 13 ships deb822 sources, so this rewrites debian.sources rather than
# the classic sources.list. Both URIs (debian + debian-security) are covered by
# the single host substitution.
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y \
    curl \
    build-essential \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.84.0

ENV PATH="/root/.cargo/bin:${PATH}"

# Point cargo at the mirror before any crate is fetched. Empty CARGO_MIRROR
# leaves the default (crates.io) in place, so a build outside China is not
# routed through a Chinese proxy.
ARG CARGO_MIRROR
RUN if [ -n "${CARGO_MIRROR}" ]; then \
      mkdir -p /root/.cargo \
      && printf '[source.crates-io]\nreplace-with = "mirror"\n\n[source.mirror]\nregistry = "%s"\n' \
           "${CARGO_MIRROR}" > /root/.cargo/config.toml \
      && echo "cargo registry -> ${CARGO_MIRROR}"; \
    fi

RUN pip install maturin

WORKDIR /rust
COPY nous-core/ .
RUN maturin build --release

# ============================================
# Stage 2: Final Python application
# ============================================
FROM ${PYTHON_BASE}

ARG APT_MIRROR

# Install Chrome, ffmpeg, Node.js, build tools and dependencies.
# Node.js is required by the ABogus parser tier (services/douyin_parse/env.js)
# which runs douyin_bdms.js to compute the a_bogus request signature.
#
# This is the layer that hurts when the cache misses — several hundred MB of
# chromium + ffmpeg + fonts. See the APT_MIRROR note at the top.
RUN sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y \
    wget \
    curl \
    gnupg \
    chromium \
    chromium-driver \
    fonts-noto-cjk \
    fonts-noto-cjk-extra \
    build-essential \
    ffmpeg \
    nodejs \
    --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*

# ── GPU Transcode Support (uncomment as needed) ──
# NVIDIA: Install CUDA toolkit for h264_nvenc
# RUN apt-get update && apt-get install -y nvidia-cuda-toolkit && rm -rf /var/lib/apt/lists/*
# Intel QSV: Install VA-API drivers for h264_qsv
# RUN apt-get update && apt-get install -y intel-media-va-driver-non-free libmfx1 && rm -rf /var/lib/apt/lists/*

# Set Chrome environment variables
ENV CHROME_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# ── dreamina (即梦) AIGC CLI ──────────────────────────────────────────────
# JimengCliProvider drives this binary as a subprocess for image + video
# generation. The official installer (verified 2026-07-07) auto-detects the
# platform and drops a single static binary; DREAMINA_INSTALL_DIR pins it to
# /usr/local/bin so it lands on PATH and is readable by the non-root `app`
# user. The build FAILS if the binary isn't on PATH + runnable afterward —
# never a silent skip (a missing CLI must break the image, not surface at
# runtime). Auth/login state is written at runtime to $HOME/.dreamina_cli
# (i.e. /app/.dreamina_cli for the `app` user) and persisted via the
# dreamina-auth compose volume — see docs/runbook/jimeng-cli.md.
# NOTE: the installer fetches the binary from a ByteDance CN CDN; a CI runner
# without CN egress will fail this layer (that is the intended hard failure).
RUN DREAMINA_INSTALL_DIR=/usr/local/bin bash -c 'curl -fsSL https://jimeng.jianying.com/cli | bash' \
    && command -v dreamina \
    && dreamina --help >/dev/null

# Install uv package manager
RUN pip install uv

# Install nous-core Rust module from build stage
COPY --from=rust-builder /rust/target/wheels/*.whl /tmp/
RUN pip install /tmp/nous_core*.whl && rm -f /tmp/nous_core*.whl

# Install yt-dlp and faster-whisper
RUN pip install yt-dlp==2024.12.23 faster-whisper==1.1.0

# py-spy: sample a live process's Python stacks from outside the
# interpreter. Kept in the image so an event-loop freeze (2026-07-06 P0)
# can be diagnosed BEFORE the restart destroys the evidence:
#   docker exec mediahub-app-backend py-spy dump --pid 1
RUN pip install py-spy==0.4.0

# Set working directory
WORKDIR /app

# Copy backend code
COPY backend/ .

# Remove any existing local venv (different architecture incompatible)
RUN rm -rf .venv

# Install Python dependencies
RUN uv sync

# Create downloads directory
RUN mkdir -p /app/downloads

# dreamina CLI writes OAuth/login state under $HOME/.dreamina_cli (HOME=/app for
# the app user). Pre-create it so the dreamina-auth named volume inherits the
# app user's ownership (1031:100) on first mount, and `dreamina login` run via
# `docker exec` can write without a permission error.
RUN mkdir -p /app/.dreamina_cli

# Non-root service user.
# UID 1031 / GID 100 matches the 'mediahub' service account created on
# the Synology NAS (`synouser --add mediahub`). The shared UID+GID is
# what lets the container read/write the bind-mounted MediaHub.library
# directory — Docker permissions are purely numeric, so the names
# ('mediahub' on host, 'app' in container) don't matter, only the
# numbers 1031/100 do.
RUN groupadd --gid 100 users 2>/dev/null || true \
    && useradd --uid 1031 --gid 100 --home /app --shell /usr/sbin/nologin app \
    && chown -R 1031:100 /app
USER app

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1


# Start command
CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
