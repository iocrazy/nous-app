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

# CFT_MIRROR is the Chrome-for-Testing counterpart of UBUNTU_MIRROR — it feeds
# the `patchright install chromium` step below (see the long note there for why
# a plain PLAYWRIGHT_DOWNLOAD_HOST can't reach npmmirror). npmmirror serves the
# CfT zips at ~13MB/s vs gpupc's flaky ~0.9MB/s direct to Google's bucket.
# Overridable for overseas builds:
#   docker build --build-arg CFT_MIRROR=https://storage.googleapis.com/chrome-for-testing-public .
#   docker build --build-arg CFT_MIRROR= .   # empty => patchright's default CDN
ARG CFT_MIRROR=https://cdn.npmmirror.com/binaries/chrome-for-testing

# Playwright's own image already carries Chromium plus every shared library it
# needs. The tag and the `playwright` pin in pyproject.toml must move together -
# a mismatch fails at runtime with "Executable doesn't exist".
#
# Pinned by digest (CLAUDE.md build-reproducibility rule #1): a bare
# `:v1.52.0-noble` tag is still MUTABLE — mcr can repush it with a security
# snapshot, which silently invalidates every layer below (the ~950MB base plus
# the chromium/ffmpeg install layer) and forces a from-scratch re-download on
# the next deploy. The tag is kept alongside the digest for human readability;
# to move the base image, resolve a new digest deliberately:
#   docker buildx imagetools inspect mcr.microsoft.com/playwright/python:v1.52.0-noble --format '{{.Manifest.Digest}}'
# and bump it together with the `playwright` pin in pyproject.toml.
FROM mcr.microsoft.com/playwright/python:v1.52.0-noble@sha256:a1d2b48b65f41f34e5e1d7690f385f1376397b7208691cba359e4078edbf86cb

# ARGs declared before the first FROM are global; a stage that wants one has to
# re-declare it (Docker semantics, not a typo).
ARG UBUNTU_MIRROR
ARG CFT_MIRROR

# Xvfb, because this service runs Chromium HEADED (design doc 2.4). Headless
# leaves detectable traces and Douyin bounces the upload page to login, which
# reads as an expired session. Server-side != headless.
#
# Ubuntu 24.04 uses the deb822 format, so the file to rewrite is
# `/etc/apt/sources.list.d/ubuntu.sources` — NOT the classic
# `/etc/apt/sources.list`, which does not exist here. Both URIs need it: the
# archive one AND security.ubuntu.com (they are separate stanzas, and leaving
# security on the slow host reintroduces the same timeout).
# BuildKit 缓存挂载（2026-09-06 加，构建机走 5G 计费网络）。删 docker-clean、
# 去掉 `rm -rf /var/lib/apt/lists/*`，否则缓存永远是空的；缓存挂载不进镜像层，
# 去掉清理反而让镜像更小。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    if [ -n "$UBUNTU_MIRROR" ]; then \
        sed -i \
            -e "s|http://archive.ubuntu.com/ubuntu/|http://${UBUNTU_MIRROR}/ubuntu/|g" \
            -e "s|http://security.ubuntu.com/ubuntu/|http://${UBUNTU_MIRROR}/ubuntu/|g" \
            /etc/apt/sources.list.d/ubuntu.sources; \
    fi \
    && apt-get update \
    && apt-get install -y --no-install-recommends xvfb

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
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# patchright 自带**另一份** Chromium，跟基础镜像里的不是同一个。实测（构建
# 期探测，2026-08-08）：装完 patchright 后 executable_path 指向
# /ms-playwright/chromium-1208/，而 v1.52.0-noble 镜像里只有 chromium-1169，
# 该路径 EXISTS=False。
#
# ⚠️ 漏掉这一步，**构建照样通过**，直到运行时第一次启浏览器才炸
# "Executable doesn't exist" —— 而那时已经是线上发布任务在跑。
#
# 装到 PLAYWRIGHT_BROWSERS_PATH（镜像已设为 /ms-playwright）下，patchright
# 认这个变量（同一次探测确认）。只装 chromium：patchright 明确不支持
# Firefox / WebKit，装了也是白占体积。
#
# ⚠️ 下载源（2026-08-11 修，此前连续挂 deploy-gpu 三次的真正病根）：
# patchright 1.58.2 在 ubuntu24.04-x64（本 noble 基础镜像）上装的是 **Chrome
# for Testing**，不是标准 playwright chromium build —— 下载路径在 driver 里写死为
#   builds/cft/<browserVersion>/linux64/{chrome-linux64,chrome-headless-shell-linux64}.zip
# 默认走 cdn.playwright.dev → 307 → storage.googleapis.com。gpupc 直连那个 bucket
# 时快时慢（实测全量 175MB 只有 ~0.9MB/s，间歇 `Download failure, code=1`），缓存
# 一失效这层就重下、部署就红。
#
# npmmirror 有这份内容，但在 **chrome-for-testing** 命名空间下
#   cdn.npmmirror.com/binaries/chrome-for-testing/<v>/linux64/...  （实测 ~13MB/s）
# 路径前缀跟 patchright 硬拼的 `builds/cft/<v>/` 不同 —— 而 PLAYWRIGHT_DOWNLOAD_HOST
# 只换 host、改不了中间那段路径，直接指 npmmirror 会 404（已实测两个 npmmirror
# host 都 404）。
#
# 解法：从 CFT_MIRROR 预下两个 zip，摆成 patchright 期望的 `builds/cft/<v>/linux64/`
# 布局，起一个只绑 127.0.0.1 的临时 http server，把 PLAYWRIGHT_DOWNLOAD_HOST 指向它。
# patchright 用**自己**的安装逻辑解压 + 登记 registry，我们只负责供字节。<v> 从
# patchright 的 browsers.json 动态读取（patchright 一升级自动跟着走，不写死版本号）。
# CFT_MIRROR 空串时回退 patchright 默认 CDN（海外构建用，见文件顶部 ARG 注释）。
# --mount=type=cache：两个 CfT zip 合计约 175MB，原先下到 /tmp/cft 且用完就
# `rm -rf`，这一层每失效一次就重下一次。改成落在 builder 缓存里，按版本号
# 分目录，patchright 升级后 V 变了自然会下新版，旧版留着由 builder 缓存的
# 容量上限兜底回收。下载先写 .part 再改名：中途失败不会留下一个"看起来
# 存在"的半截 zip，下次仍会重下。
RUN --mount=type=cache,target=/var/cache/cft,sharing=locked \
    set -eux; \
    if [ -n "${CFT_MIRROR}" ]; then \
        V="$(python3 -c 'import json,os,patchright; d=json.load(open(os.path.join(os.path.dirname(patchright.__file__),"driver/package/browsers.json"))); print(next(b["browserVersion"] for b in d["browsers"] if b["name"]=="chromium"))')"; \
        dst="/var/cache/cft/builds/cft/${V}/linux64"; \
        mkdir -p "${dst}"; \
        for z in chrome-linux64.zip chrome-headless-shell-linux64.zip; do \
            if [ -s "${dst}/${z}" ]; then \
                echo "cft cache hit: ${z}"; \
            else \
                curl -fSL --retry 3 --retry-delay 2 -o "${dst}/${z}.part" "${CFT_MIRROR}/${V}/linux64/${z}"; \
                mv "${dst}/${z}.part" "${dst}/${z}"; \
            fi; \
        done; \
        python3 -m http.server 8909 --bind 127.0.0.1 --directory /var/cache/cft >/tmp/cft-httpd.log 2>&1 & \
        srv_pid=$!; \
        for _ in $(seq 1 40); do \
            curl -sf -r 0-0 "http://127.0.0.1:8909/builds/cft/${V}/linux64/chrome-linux64.zip" -o /dev/null && break; \
            sleep 0.25; \
        done; \
        PLAYWRIGHT_DOWNLOAD_HOST=http://127.0.0.1:8909 patchright install chromium; \
        kill "${srv_pid}" 2>/dev/null || true; \
        rm -f /tmp/cft-httpd.log; \
    else \
        patchright install chromium; \
    fi; \
    test -x "$(python3 -c 'from patchright.sync_api import sync_playwright; p=sync_playwright().start(); print(p.chromium.executable_path); p.stop()')" \
    || (echo "patchright chromium missing after install" && exit 1)

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
