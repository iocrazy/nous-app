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
# PIP_MIRROR is the PyPI counterpart of the two mirrors above, and it exists
# for exactly the same reason. 2026-08-11: `pip install yt-dlp faster-whisper`
# (line ~140) died with
#   ProtocolError: Connection broken: IncompleteRead(3029067 bytes read,
#                                                    147657 more expected)
# — pypi.org cut the transfer mid-wheel. That layer had been a cache hit for
# months; it only became visible after a `docker image prune` cleared the build
# cache, which is the same way the chromium download failure surfaced. A layer
# that only works while cached is not "working", it is untested.
# Set it empty to use pypi.org directly (overseas builds):
#   docker build --build-arg PIP_MIRROR= .
ARG PYTHON_BASE=python:3.13-slim@sha256:99569264a52f7665899b7bc0fb48e72a2712b850b129f63c4733af1e939accfb
ARG APT_MIRROR=mirrors.aliyun.com
ARG CARGO_MIRROR=sparse+https://rsproxy.cn/index/
ARG PIP_MIRROR=https://mirrors.aliyun.com/pypi/simple/

# ============================================
# Stage 1: Build Rust nous-core module
# Use python:3.13 as base so maturin builds cp313 wheels
# ============================================
FROM ${PYTHON_BASE} AS rust-builder

# ARGs declared before the first FROM are global; a stage that wants one has to
# re-declare it (Docker semantics, not a typo).
ARG APT_MIRROR
ARG PIP_MIRROR
# Set once per stage instead of per `pip install` — there are five of them
# across the two stages and a per-call flag is one `RUN` away from being
# forgotten. `${VAR:-default}` keeps `--build-arg PIP_MIRROR=` (empty) working
# as the documented overseas escape hatch; an empty PIP_INDEX_URL would make
# pip fail to resolve anything at all.
ENV PIP_INDEX_URL=${PIP_MIRROR:-https://pypi.org/simple/}
# uv reads its own variable and ignores PIP_INDEX_URL (`uv sync`, line ~165).
ENV UV_INDEX_URL=${PIP_MIRROR:-https://pypi.org/simple/}

# Debian 13 ships deb822 sources, so this rewrites debian.sources rather than
# the classic sources.list. Both URIs (debian + debian-security) are covered by
# the single host substitution.
# BuildKit 缓存挂载（2026-09-06 加，构建机走 5G 计费网络）：把 .deb 与 apt
# 索引留在 builder 缓存里，这一层失效时只补下变化的包，不再整层重下。
# 三点必须一起做，少一个缓存就永远是空的：
#   1) 删 docker-clean —— 基础镜像里"装完即删 .deb"的钩子；
#   2) 不再 `rm -rf /var/lib/apt/lists/*` —— 那清的正是缓存本身；
#   3) 缓存挂载的内容不进镜像层，所以去掉清理反而让镜像更小。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y \
    curl \
    build-essential \
    --no-install-recommends \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain 1.90.0

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

RUN --mount=type=cache,target=/root/.cache/pip pip install maturin

WORKDIR /rust
COPY nous-core/ .
# crates 注册表与 git 源缓存复用，crates.io/rsproxy 不再每次重拉。
# ⚠️ /rust/target 故意**不**缓存：产物 wheel 要靠下面的 `COPY --from` 取走，
# 而缓存挂载的内容不属于镜像层，缓存了就 COPY 不到。
RUN --mount=type=cache,target=/root/.cargo/registry,sharing=locked \
    --mount=type=cache,target=/root/.cargo/git,sharing=locked \
    maturin build --release

# ============================================
# Stage 2: Final Python application
# ============================================
FROM ${PYTHON_BASE}

ARG APT_MIRROR
ARG PIP_MIRROR
# Same reasoning as the rust-builder stage above.
ENV PIP_INDEX_URL=${PIP_MIRROR:-https://pypi.org/simple/}
ENV UV_INDEX_URL=${PIP_MIRROR:-https://pypi.org/simple/}
# uv 的缓存挂载在另一个文件系统上，默认的硬链接策略会失败并退化成拷贝，
# 每次刷一堆警告。显式设成 copy，与 browser/Dockerfile 一致。
ENV UV_LINK_MODE=copy

# Install Chrome, ffmpeg, Node.js, build tools and dependencies.
# Node.js is required by the ABogus parser tier (services/douyin_parse/env.js)
# which runs douyin_bdms.js to compute the a_bogus request signature.
#
# This is the layer that hurts when the cache misses — several hundred MB of
# chromium + ffmpeg + fonts. See the APT_MIRROR note at the top.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean \
    && sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
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
    --no-install-recommends

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

# ── gpt-image-2-skill (Codex / GPT Image 2) CLI ──────────────────────────
# CodexCliProvider drives this binary as a subprocess for image generation
# over the local Codex OAuth session (bind-mounted read-write at /app/.codex,
# see docs/runbook/codex-image.md). The npm package is only a JS launcher; the
# real program is a single static Rust binary shipped in the
# `…-linux-x64-static` platform package, so we pull that tarball straight from
# the registry and skip node/npm entirely. Pinned by version + sha256 —
# upgrading is an explicit, reviewable change (same policy as base-image
# digest pins). NPM_REGISTRY follows the APT_MIRROR convention: CN mirror by
# default, override with https://registry.npmjs.org for builds abroad (the
# tarball is byte-identical, the sha256 pin still holds).
# NB: the CLI's -V/--help exit non-zero by design, so the sanity check greps
# the version string instead of trusting the exit code.
ARG NPM_REGISTRY=https://registry.npmmirror.com
ARG GPT_IMAGE_2_SKILL_VERSION=0.7.3
ARG GPT_IMAGE_2_SKILL_SHA256=9ff833f643736cd317e91c31ef976ff74356340c59ec95c343b112d98de92cc9
RUN curl -fsSL -o /tmp/gis.tgz "${NPM_REGISTRY}/gpt-image-2-skill-linux-x64-static/-/gpt-image-2-skill-linux-x64-static-${GPT_IMAGE_2_SKILL_VERSION}.tgz" \
    && echo "${GPT_IMAGE_2_SKILL_SHA256}  /tmp/gis.tgz" | sha256sum -c - \
    && tar xzf /tmp/gis.tgz -C /tmp package/bin/gpt-image-2-skill \
    && install -m 0755 /tmp/package/bin/gpt-image-2-skill /usr/local/bin/gpt-image-2-skill \
    && rm -rf /tmp/gis.tgz /tmp/package \
    && gpt-image-2-skill -V 2>&1 | grep -q "${GPT_IMAGE_2_SKILL_VERSION}"

# Install uv package manager
RUN --mount=type=cache,target=/root/.cache/pip pip install uv

# Install yt-dlp and faster-whisper
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install yt-dlp==2024.12.23 faster-whisper==1.1.0

# py-spy: sample a live process's Python stacks from outside the
# interpreter. Kept in the image so an event-loop freeze (2026-07-06 P0)
# can be diagnosed BEFORE the restart destroys the evidence:
#   docker exec mediahub-app-backend py-spy dump --pid 1
RUN --mount=type=cache,target=/root/.cache/pip pip install py-spy==0.4.0

# Set working directory
WORKDIR /app

# ── 依赖层与源码层分离（2026-09-06）──────────────────────────────
# 改之前：`COPY backend/ .` 排在 `uv sync` 前面，于是 backend 下任何一个 .py
# 改动都让 COPY 层失效，`uv sync` 跟着失效，177 个依赖从 mirrors.aliyun.com
# 整包重下。实测近 30 天 backend 有 203 次提交，而 uv.lock 只变了 1 次 ——
# 202 次重下是纯浪费。这台自托管构建机走 5G 计费网络，每次约 0.4GB。
# 拆开之后：只有 pyproject.toml / uv.lock 真的变了才会重下依赖。
# 写法与 browser/Dockerfile 的两段式保持一致。
#
# --frozen：lock 过期时直接报错，而不是静默联网重解析。重解析既费流量又会
#   让依赖悄悄漂移。2026-09-06 用 `uv lock --check` 离线验证过 lock 是最新的。
# --no-install-project：pyproject.toml 里 packages = ["app"]，而 app/ 要到
#   下一层才进来，这一层装项目自身必然失败；这里只装第三方依赖。
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# 源码层。改代码只失效这一层往后，上面的依赖层原样命中。
#
# ⚠️ 这里原本跟着一句 `rm -rf .venv`，用来删掉被 COPY 带进来的宿主机 venv
# （宿主机 backend/.venv 实测 738MB）。现在改成在 .dockerignore 里用
# `**/.venv` 递归排除 —— 裸写 `.venv` 只匹配上下文根目录，匹配不到
# backend/.venv，这是原来那句 rm 存在的真正原因。
# 若把 .dockerignore 里那行删了，宿主机 venv 会覆盖上一层建好的 /app/.venv，
# 这里的分层优化即刻失效。两处是绑定的。
COPY backend/ .

# 把项目自身装进 venv。第三方依赖已在上面就位，这一步不联网。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

# nous-core Rust module from the build stage.
# ⚠️ 这个 COPY 原先在上面三次 pip install 之前（2026-09-06 下移）。放在前面时，
# 只要 nous-core/ 的 Rust 代码一改，wheel 就变，COPY 层失效，后面
# yt-dlp / faster-whisper / py-spy 三次 pip install 全部跟着重下。
# 挪到真正用它的地方之前，Rust 改动就只影响这一层往后。
COPY --from=rust-builder /rust/target/wheels/*.whl /tmp/

# Install the nous-core wheel INTO THE VENV, not the system interpreter.
# The app runs as /app/.venv/bin/python and that venv is built by `uv sync`
# with include-system-site-packages = false, so anything pip-installed into
# /usr/local/lib/pythonX/site-packages is invisible to it. This install must
# therefore come AFTER `rm -rf .venv && uv sync` — before it, the venv either
# does not exist yet or is about to be deleted.
# (yt-dlp / faster-whisper stay on the system interpreter above: they are used
# as CLI binaries on PATH, not imported by the app.)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv/bin/python /tmp/nous_core*.whl \
    && rm -f /tmp/nous_core*.whl \
    && /app/.venv/bin/python -c "import nous_core; assert hasattr(nous_core, 'fetch_to_file')"

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
