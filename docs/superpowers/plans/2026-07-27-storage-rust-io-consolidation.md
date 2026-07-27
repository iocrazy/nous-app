# Rust 流式 IO 接入 + 存储函数整合 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把媒体存储读写的字节路径从 Python 移到 Rust，同时收拢重复的路径解析函数并拆分过大的 transcode 服务，为后续「全量 S3 化」铺好地基。

**Architecture:** Python 保留鉴权与路由决策（算签名 URL、选 bucket、编排任务），Rust 新增 `fetch_to_file` / `put_file` 承担纯字节搬运，两者通过 URL + headers 这一层窄接口交接。存储位置本身完全不变，Rust 路径由 `FEATURE_RUST_STREAM_IO` 开关控制，关掉即回退纯 Python。

**Tech Stack:** Python 3.13 / FastAPI / DBOS · Rust 1.84 + PyO3 0.22 + maturin · reqwest(rustls-tls) + tokio · pytest(asyncio_mode=auto)

## Global Constraints

- 设计依据：`docs/superpowers/specs/2026-07-27-storage-rust-io-consolidation-design.md`
- Rust toolchain 固定 `1.84.0`（`Dockerfile:15` 已锁），PyO3 `0.22`
- `reqwest` 必须启用 `rustls-tls` 且 `default-features = false` —— slim 镜像无 OpenSSL
- 所有暴露给 Python 的 Rust 函数必须用 `py.allow_threads()` 释放 GIL
- 测试命令一律 `cd backend && uv run pytest`（`asyncio_mode = "auto"`，async 测试无需 `@pytest.mark.asyncio`）
- UI 文案一律英文；代码注释与文档中文（CLAUDE.md 规范）
- 提交信息结尾附 `Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA`
- **禁止改动**：`public.mediahub_models` 表、`mediahub_model_repository` 及其标识符、`deploy/nas/` 与 `docker/` 中的 `mediahub-*` 容器名、`MEDIAHUB_ROLE` / `MEDIAHUB_TOKEN` 环境变量
- 性能测量方法学：字节路径中不得有 Python 或额外管道；每次换不同文件并 `posix_fadvise(DONTNEED)`；必须扫并发（工具见 `scripts/bench-storage.py`）

---

## 文件结构

### PR1 `chore/brand-nous`

| 动作 | 路径 | 职责 |
|------|------|------|
| 重命名 | `mediahub-core/` → `nous-core/` | Rust crate 目录 |
| 修改 | `nous-core/Cargo.toml` | `name = "nous_core"` |
| 修改 | `nous-core/pyproject.toml` | 包名 |
| 修改 | `nous-core/src/lib.rs` | `#[pymodule] fn nous_core` |
| 修改 | `Dockerfile:22`、`77` | build stage 路径与 wheel 名 |
| 修改 | `backend/pyproject.toml:91-93` | 修正 github URL |
| 修改 | docs 共 181 个 markdown | 文案改名 |

### PR2 `refactor/storage-consolidation`

| 动作 | 路径 | 职责 |
|------|------|------|
| 新建 | `backend/app/services/media/audio_source.py` | `AudioSourceResolver`：音频路径解析唯一入口 |
| 新建 | `backend/app/services/library/derived_paths.py` | `DerivedArtifactPaths`：派生产物落位唯一入口 |
| 新建 | `backend/app/services/media/transcode/transcode_probe.py` | ffprobe / 编码器探测 |
| 新建 | `backend/app/services/media/transcode/hls_publisher.py` | HLS 产物发布与清理 |
| 修改 | `backend/app/services/media/transcode/transcode_service.py` | 只留编排 |
| 修改 | `backend/app/workflows/ai_transcription.py` | 改用 `AudioSourceResolver` |
| 修改 | `backend/app/services/ai/transcribe/whisper_service.py` | 改用 `AudioSourceResolver` |
| 修改 | `backend/app/services/media/render/thumbnail_service.py` | 改用 `DerivedArtifactPaths` |

### PR3 `feature/nous-core-stream-io`

| 动作 | 路径 | 职责 |
|------|------|------|
| 新建 | `nous-core/src/http_io.rs` | reqwest 流式拉/推 |
| 修改 | `nous-core/src/lib.rs` | 导出 `fetch_to_file` / `put_file` |
| 修改 | `nous-core/src/errors.rs` | 新增 IO 错误变体 |
| 修改 | `backend/app/core/config.py` | `FEATURE_RUST_STREAM_IO` |
| 修改 | `backend/app/services/library/media_storage.py` | `materialize` / `put_file` 分支 |

---

# PR1 — `chore/brand-nous`（纯改名，零逻辑）

### Task 1: Rust crate 重命名为 nous_core

**Files:**
- Rename: `mediahub-core/` → `nous-core/`
- Modify: `nous-core/Cargo.toml`
- Modify: `nous-core/pyproject.toml`
- Modify: `nous-core/src/lib.rs:151-152`
- Modify: `Dockerfile:22`, `Dockerfile:77`

**Interfaces:**
- Consumes: 无
- Produces: Python 模块名 `nous_core`（PR3 的 `import nous_core` 依赖它）

- [ ] **Step 1: 建分支并重命名目录**

```bash
cd /media/heygo/program/projects-code/repos/nous-app
git checkout master && git pull --rebase
git checkout -b chore/brand-nous
git mv mediahub-core nous-core
```

- [ ] **Step 2: 改 Cargo.toml 的包名与库名**

`nous-core/Cargo.toml` —— 把 `[package]` 的 `name` 与 `[lib]` 的 `name` 都改掉：

```toml
[package]
name = "nous-core"

[lib]
name = "nous_core"
crate-type = ["cdylib"]
```

- [ ] **Step 3: 改 pyproject.toml 的模块名**

`nous-core/pyproject.toml`：

```toml
[project]
name = "nous-core"

[tool.maturin]
module-name = "nous_core"
```

- [ ] **Step 4: 改 pymodule 函数名**

`nous-core/src/lib.rs`，把 `#[pymodule]` 下的函数名改成与 `module-name` 一致（PyO3 要求两者相同，不一致会在 `import` 时报 `ImportError: dynamic module does not define module export function`）：

```rust
#[pymodule]
fn nous_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
```

- [ ] **Step 5: 改 Dockerfile 的构建路径与 wheel 名**

`Dockerfile:22`：

```dockerfile
COPY nous-core/ .
```

`Dockerfile:77`：

```dockerfile
RUN pip install /tmp/nous_core*.whl && rm -f /tmp/nous_core*.whl
```

- [ ] **Step 6: 本地验证 crate 能编译并导入**

```bash
cd nous-core
cargo build --release
```

Expected: 编译通过，无 error。

- [ ] **Step 7: 验证仓库里没有残留引用**

```bash
cd /media/heygo/program/projects-code/repos/nous-app
git grep -n "mediahub-core\|mediahub_core" -- . ':!docs' ':!*.lock'
```

Expected: 无输出。若有输出，逐个改掉后重跑。

- [ ] **Step 8: 提交**

```bash
git add -A
git commit -m "chore(core): mediahub-core → nous-core（crate/模块/Dockerfile）

当前无任何 Python 调用方 import 它,故重命名的调用方风险为零。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 2: 文档改名与修正 github URL

**Files:**
- Modify: `backend/pyproject.toml:91-93`
- Modify: docs 及根目录 markdown 共 181 个文件

**Interfaces:**
- Consumes: 无
- Produces: 无（纯文案）

- [ ] **Step 1: 修正 pyproject.toml 里写错的仓库地址**

真实仓库是 `iocrazy/nous-app`，`backend/pyproject.toml:91-93` 指向已不存在的 `iocrazy/mediahub`：

```toml
Homepage = "https://github.com/iocrazy/nous-app"
Repository = "https://github.com/iocrazy/nous-app"
Issues = "https://github.com/iocrazy/nous-app/issues"
```

- [ ] **Step 2: 先看清将要改动的文件范围**

```bash
git grep -il "mediahub" -- docs '*.md' | wc -l
```

Expected: 181

- [ ] **Step 3: 批量替换文档中的品牌名**

大小写各自替换，保持原有大小写风格：

```bash
git grep -il "mediahub" -- docs '*.md' \
  | xargs sed -i -e 's/MediaHub/Nous/g' -e 's/mediahub/nous/g' -e 's/MEDIAHUB/NOUS/g'
```

- [ ] **Step 4: 回滚被误伤的禁改项**

上一步会连带改掉文档里引用的**活表名、容器名、环境变量名**——那些必须保持原样，否则文档会与生产不符：

```bash
git grep -il "nous_models\|nous-sb\|nous-app-backend\|nous-worker\|NOUS_ROLE\|NOUS_TOKEN\|iocrazy/nous" -- docs '*.md' \
  | xargs -r sed -i \
      -e 's/nous_models/mediahub_models/g' \
      -e 's/nous-sb/mediahub-sb/g' \
      -e 's/nous-app-backend/mediahub-app-backend/g' \
      -e 's/NOUS_ROLE/MEDIAHUB_ROLE/g' \
      -e 's/NOUS_TOKEN/MEDIAHUB_TOKEN/g' \
      -e 's#github.com/iocrazy/nous\([^-]\|$\)#github.com/iocrazy/nous-app\1#g'
```

**为什么最后一条必须有：** 3 个文档里写着 `github.com/iocrazy/mediahub`，上一步的 `s/mediahub/nous/g` 会把它变成 `iocrazy/nous` —— 而真实仓库是 `iocrazy/nous-app`。`\([^-]\|$\)` 的作用是不去动已经正确的 `nous-app`。

验证：

```bash
git grep -n "github.com/iocrazy" -- docs '*.md' | grep -v "nous-app" 
```

Expected: 无输出。

- [ ] **Step 5: 人工复核 diff 中所有代码块**

```bash
git diff -- docs '*.md' | grep -E "^\+.*(nous-worker|nous_models|docker |psql |SELECT )" | head -40
```

Expected: 输出中不应出现指向生产资源的改动。逐条确认；凡是命令、SQL、容器名，一律改回。

- [ ] **Step 6: 确认没碰到禁改区**

```bash
git diff --name-only | grep -E "^(deploy/nas|docker/|\.github/)" 
git diff --name-only | grep -E "supabase/migrations"
```

Expected: 两条命令均无输出。

- [ ] **Step 7: 提交**

```bash
git add -A
git commit -m "chore(docs): 品牌 mediahub → nous（181 个文档）+ 修正 github URL

排除:活表 mediahub_models、NAS 老栈容器名、仓库外环境变量
—— 它们在文档中保持原样,否则与生产不符。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

- [ ] **Step 8: 开 PR**

```bash
git push -u origin chore/brand-nous
gh pr create --base master --title "chore: 品牌统一 mediahub → nous（改名，零逻辑）" \
  --body "纯改名 PR,零行为变化。

- mediahub-core → nous-core（crate 名 / 模块名 / Dockerfile）
- docs × 181 文案改名
- 修正 backend/pyproject.toml 写错的 github URL（iocrazy/mediahub → iocrazy/nous-app）

明确排除（见 spec §范围）:
- public.mediahub_models（活表,需 migration + view 过渡）
- mediahub_model_repository（30 文件/98 行,26 个测试用字符串 monkeypatch）
- deploy/nas/ 与 docker/ 的容器名（NAS 恢复依赖它们）
- MEDIAHUB_ROLE / MEDIAHUB_TOKEN（值在仓库外 secrets/backend.env）

https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

# PR2 — `refactor/storage-consolidation`（纯结构，零行为变化）

> CLAUDE.md 要求纯 refactor PR 24 小时内合入。开工前先 `git checkout master && git pull --rebase`。

### Task 3: 抽出 AudioSourceResolver

当前音频路径解析散在 4 处，且**两条链行为并不一致**：`whisper_service` 有 glob 兜底（处理 `audio.m4a` vs `audio.mp3` 后缀不符），`ai_transcription` 没有；`ai_transcription` 有 isdir/size 守卫，`whisper_service` 没有。合并时必须取两者的**并集**，否则会静默丢掉一边的保护。

**Files:**
- Create: `backend/app/services/media/audio_source.py`
- Create: `backend/tests/test_audio_source_resolver.py`
- Modify: `backend/app/workflows/ai_transcription.py:121-141`, `:265-268`, `:292-296`
- Modify: `backend/app/services/ai/transcribe/whisper_service.py:62-78`

**Interfaces:**
- Consumes: `app.core.config.settings.DOWNLOAD_PATH`
- Produces:
  - `AudioSourceResolver.resolve(audio_path: str) -> str` — 返回绝对路径；解析不到抛 `FileNotFoundError`
  - `AudioSourceResolver.assert_playable(audio_path: str) -> str` — 校验是文件且非空，返回**原始入参**（不是绝对路径）；失败抛 `RuntimeError`
  - `AudioSourceResolver.to_relative(abs_path: str) -> str` — 去掉 `DOWNLOAD_PATH` 前缀，用于拼 URL

- [ ] **Step 1: 写失败测试**

`backend/tests/test_audio_source_resolver.py`：

```python
"""AudioSourceResolver —— 合并 ai_transcription 与 whisper_service 的两条解析链。

关键:必须取两者并集。whisper 侧有 glob 兜底,ai_transcription 侧有
isdir/size 守卫,任一丢失都会让某条 provider 路径回退到旧 bug。
"""

import os

import pytest

from app.services.media.audio_source import AudioSourceResolver


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    return AudioSourceResolver()


def test_resolve_absolute_path_passthrough(resolver, tmp_path):
    f = tmp_path / "a.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.resolve(str(f)) == str(f)


def test_resolve_relative_joins_download_path(resolver, tmp_path):
    (tmp_path / "web").mkdir()
    f = tmp_path / "web" / "audio.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.resolve("web/audio.m4a") == str(f)


def test_resolve_globs_when_extension_differs(resolver, tmp_path):
    """downloader 存 .mp3 但 DB 记的是 .m4a —— whisper 侧原有的兜底。"""
    (tmp_path / "web").mkdir()
    actual = tmp_path / "web" / "audio.mp3"
    actual.write_bytes(b"x" * 16)
    assert resolver.resolve("web/audio.m4a") == str(actual)


def test_resolve_missing_raises(resolver):
    with pytest.raises(FileNotFoundError):
        resolver.resolve("web/nope.m4a")


def test_assert_playable_rejects_directory(resolver, tmp_path):
    """图集没下载背景音乐时 path 是目录,旧 exists() 放行导致 ASR 报
    opaque 'Invalid audio URI' —— ai_transcription 侧原有的守卫。"""
    d = tmp_path / "dir.m4a"
    d.mkdir()
    with pytest.raises(RuntimeError, match="directory"):
        resolver.assert_playable("dir.m4a")


def test_assert_playable_rejects_empty_file(resolver, tmp_path):
    f = tmp_path / "empty.m4a"
    f.write_bytes(b"")
    with pytest.raises(RuntimeError, match="missing or empty"):
        resolver.assert_playable("empty.m4a")


def test_assert_playable_returns_original_arg(resolver, tmp_path):
    """调用方把返回值原样存回 DB,必须是相对路径而非解析后的绝对路径。"""
    f = tmp_path / "ok.m4a"
    f.write_bytes(b"x" * 16)
    assert resolver.assert_playable("ok.m4a") == "ok.m4a"


def test_to_relative_strips_download_root(resolver, tmp_path):
    assert resolver.to_relative(str(tmp_path / "web" / "a.m4a")) == "web/a.m4a"


def test_to_relative_passthrough_when_outside_root(resolver):
    assert resolver.to_relative("/elsewhere/a.m4a") == "/elsewhere/a.m4a"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/test_audio_source_resolver.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.media.audio_source'`

- [ ] **Step 3: 实现 AudioSourceResolver**

`backend/app/services/media/audio_source.py`：

```python
"""音频源路径解析的唯一入口。

合并前分散在四处,且两条链行为不一致:

- ``ai_transcription.py`` —— isabs → join(DOWNLOAD_PATH);带 isdir/size 守卫
- ``whisper_service.py``  —— exists → join → glob ``<stem>.*`` 兜底

``ai_transcription.py:263`` 的注释自己写着 "whisper_service has the same
resolution chain; mirror it here",但实际两边并不相同。本类取并集:解析
链带 glob 兜底,校验带 isdir/size 守卫。
"""

from __future__ import annotations

import glob
import os

from loguru import logger


class AudioSourceResolver:
    """把 DB 里存的 audio_path（通常是相对路径）解析成磁盘上的真实文件。"""

    def _download_root(self) -> str:
        from app.core.config import settings

        return settings.DOWNLOAD_PATH.rstrip("/")

    def resolve(self, audio_path: str) -> str:
        """返回绝对路径。三级链:as-is → join(DOWNLOAD_PATH) → glob 同名不同后缀。

        Raises:
            FileNotFoundError: 三级都没命中。
        """
        if os.path.exists(audio_path):
            return audio_path

        joined = os.path.join(self._download_root(), audio_path)
        if os.path.exists(joined):
            logger.info(f"Audio path {audio_path} relative; resolved to {joined}")
            return joined

        # downloader 可能存成 audio.mp3 而 DB 记的是 audio.m4a（或反之）
        parent = os.path.dirname(joined) or "."
        stem = os.path.basename(audio_path).rsplit(".", 1)[0]
        candidates = sorted(glob.glob(os.path.join(parent, f"{stem}.*")))
        if candidates:
            logger.info(f"Audio path {audio_path} matched by glob: {candidates[0]}")
            return candidates[0]

        raise FileNotFoundError(f"audio file not found: {audio_path}")

    def assert_playable(self, audio_path: str) -> str:
        """校验解析结果是非空文件,返回**原始入参**（调用方要原样存回 DB）。

        用 isfile 而非 exists:图集若从未下载背景音乐,路径会回退成一个
        目录,exists() 放行后 ASR provider 才炸,报的是不可诊断的
        "Invalid audio URI"。这里 fast-fail 并给出可操作的信息。
        """
        try:
            full_path = self.resolve(audio_path)
        except FileNotFoundError:
            raise RuntimeError(
                f"audio file missing or empty at dispatch time: {audio_path}"
            )

        try:
            if os.path.isdir(full_path):
                raise RuntimeError(
                    f"audio path is a directory (gallery without downloaded "
                    f"music?): {audio_path}"
                )
            if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                return audio_path
        except OSError:
            pass
        raise RuntimeError(f"audio file missing or empty at dispatch time: {audio_path}")

    def to_relative(self, abs_path: str) -> str:
        """去掉 DOWNLOAD_PATH 前缀 —— /media 路由按相对路径提供文件。"""
        root = self._download_root()
        if abs_path.startswith(root + "/"):
            return abs_path[len(root) + 1 :]
        return abs_path
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/test_audio_source_resolver.py -v
```

Expected: 9 passed

- [ ] **Step 5: 把 ai_transcription.py 的第一处改为调用解析器**

`backend/app/workflows/ai_transcription.py`，删除 `:119-141` 的整段内联逻辑，替换为：

```python
    from app.services.media.audio_source import AudioSourceResolver

    return AudioSourceResolver().assert_playable(audio_path)
```

- [ ] **Step 6: 把 ai_transcription.py 的 volcengine 分支改为调用解析器**

`backend/app/workflows/ai_transcription.py:262-268` 那段 `if not os.path.exists(...)` 替换为：

```python
    # 解析到磁盘路径,以便下面推导对外 URL。
    from app.services.media.audio_source import AudioSourceResolver

    _resolver = AudioSourceResolver()
    audio_path = _resolver.resolve(audio_path)
```

再把 `:292-296` 的相对路径推导替换为：

```python
    rel_path = _resolver.to_relative(audio_path)
```

- [ ] **Step 7: 把 whisper_service.py 改为调用解析器**

`backend/app/services/ai/transcribe/whisper_service.py`，删除 `:53-78` 的整段解析链（含注释），替换为：

```python
        from app.services.media.audio_source import AudioSourceResolver

        audio_path = AudioSourceResolver().resolve(audio_path)
```

注意原代码在解析失败时抛 `FileNotFoundError`，`AudioSourceResolver.resolve` 抛的也是 `FileNotFoundError`，docstring 承诺不变。

- [ ] **Step 8: 跑转录相关全部回归**

```bash
cd backend && uv run pytest tests/test_audio_source_resolver.py \
  tests/test_transcription_resolver.py \
  tests/test_transcribe_whisper_model_assignment.py \
  tests/test_ai_transcription_sql.py \
  tests/test_openai_transcribe_minimal_shape.py -v
```

Expected: 全部 passed

- [ ] **Step 9: 确认没有残留的内联解析**

```bash
cd backend && grep -n "DOWNLOAD_PATH" app/workflows/ai_transcription.py \
  app/services/ai/transcribe/whisper_service.py
```

Expected: 无输出（两个文件都不该再直接碰 `DOWNLOAD_PATH`）。

- [ ] **Step 10: 提交**

```bash
git add backend/app/services/media/audio_source.py \
        backend/tests/test_audio_source_resolver.py \
        backend/app/workflows/ai_transcription.py \
        backend/app/services/ai/transcribe/whisper_service.py
git commit -m "refactor(audio): 抽出 AudioSourceResolver,合并四处重复解析

两条链原本行为不一致:whisper 有 glob 兜底、ai_transcription 有
isdir/size 守卫。合并取并集,两条 provider 路径行为对齐。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 4: 抽出 DerivedArtifactPaths

派生产物（缩略图 / sprite）的落位规则目前内联在 `thumbnail_service.py:87-102`：文件系统源存在源文件旁，`sb://` 源存进 `DOWNLOAD_PATH/derived/thumbnails/<resource_id>/`。spec #2 要把派生产物改写 S3，届时只需改这一个类。

**Files:**
- Create: `backend/app/services/library/derived_paths.py`
- Create: `backend/tests/test_derived_paths.py`
- Modify: `backend/app/services/media/render/thumbnail_service.py:87-107`

**Interfaces:**
- Consumes: `app.services.library.media_storage.resolve_media_source`
- Produces:
  - `DerivedArtifactPaths.thumbnail_dir(file_path: str, resource_id: str, local_path: Path) -> Path` — 返回派生产物应落的目录，并已 `mkdir(parents=True, exist_ok=True)`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_derived_paths.py`：

```python
"""DerivedArtifactPaths —— 派生产物落位的唯一入口。

spec #2 要把缩略图/sprite 改写 S3,届时只改这个类,不用再翻 thumbnail_service。
"""

from pathlib import Path

import pytest

from app.services.library.derived_paths import DerivedArtifactPaths


@pytest.fixture
def paths(tmp_path, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "DOWNLOAD_PATH", str(tmp_path))
    return DerivedArtifactPaths()


def test_filesystem_source_lands_next_to_source(paths, tmp_path):
    src = tmp_path / "web" / "vid" / "video.mp4"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("web/vid/video.mp4", "123", src)
    assert out == src.parent


def test_object_store_source_lands_in_derived_tree(paths, tmp_path):
    """sb:// 源在 DOWNLOAD_PATH 下没有"旁边"可言,落 resource_id 键控的树。"""
    src = tmp_path / "tmpXYZ.mp4"
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("sb://library/ab/cd/deadbeef", "456", src)
    assert out == tmp_path / "derived" / "thumbnails" / "456"


def test_object_store_dir_is_created(paths, tmp_path):
    src = tmp_path / "tmpXYZ.mp4"
    src.write_bytes(b"x")
    out = paths.thumbnail_dir("sb://library/ab/cd/deadbeef", "789", src)
    assert out.is_dir()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/test_derived_paths.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.library.derived_paths'`

- [ ] **Step 3: 实现 DerivedArtifactPaths**

`backend/app/services/library/derived_paths.py`：

```python
"""派生产物（缩略图 / sprite / 未来的 HLS）落位的唯一入口。

当前规则:派生产物一律留在文件系统,只有原件进对象存储
（gallery PR #1491 定下的 "storage unification: only originals go to
object storage"）。实测支持这个选择 —— 小文件读取 CIFS 0.6 ms/个
优于 S3 1.4 ms/个。

spec #2 若要把派生产物改写 S3,只改这个类即可,调用方无需变动。
"""

from __future__ import annotations

from pathlib import Path


class DerivedArtifactPaths:
    """决定缩略图 / sprite 落在哪个目录。"""

    def thumbnail_dir(
        self, file_path: str, resource_id: str, local_path: Path
    ) -> Path:
        """返回派生产物目录（已创建）。

        Args:
            file_path: 资源的存储路径,``sb://`` 或 DOWNLOAD_PATH 相对路径。
            resource_id: 资源 ID,对象存储源的目录键。
            local_path: ``materialize()`` 给出的真实本地路径。对文件系统
                源它就是源文件本身,派生产物存在它旁边;对 ``sb://`` 源它
                是个临时文件,"旁边"没有意义。

        Returns:
            已经 mkdir 好的目录路径。
        """
        from app.core.config import settings
        from app.services.library.media_storage import resolve_media_source

        loc = resolve_media_source(file_path)
        if not loc.is_object_store:
            return local_path.parent

        out_dir = (
            Path(settings.DOWNLOAD_PATH) / "derived" / "thumbnails" / str(resource_id)
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/test_derived_paths.py -v
```

Expected: 3 passed

- [ ] **Step 5: 改 thumbnail_service 调用它**

`backend/app/services/media/render/thumbnail_service.py`，把 `:79` 的 `loc = resolve_media_source(file_path)` 与 `:87-102` 的 if/else 整段替换为：

```python
            from app.services.library.derived_paths import DerivedArtifactPaths

            async with materialize(file_path) as local_path:
                if not local_path.exists():
                    logger.warning(
                        f"Thumbnail skipped: source file not found at {local_path}"
                    )
                    return None

                out_dir = DerivedArtifactPaths().thumbnail_dir(
                    file_path, str(resource_id), local_path
                )
```

同时把文件顶部 `from app.services.library.media_storage import materialize, resolve_media_source` 中不再使用的 `resolve_media_source` 删掉（若该文件其他地方仍用到则保留）。

- [ ] **Step 6: 跑缩略图回归**

```bash
cd backend && uv run pytest tests/test_derived_paths.py \
  tests/test_thumbnail_lazy.py \
  tests/test_transcode_thumbnail_scope_wiring.py -v
```

Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/library/derived_paths.py \
        backend/tests/test_derived_paths.py \
        backend/app/services/media/render/thumbnail_service.py
git commit -m "refactor(storage): 抽出 DerivedArtifactPaths

派生产物落位收拢到一处,spec #2 改写 S3 时只需改这个类。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 5: 抽出 MediaKeyBuilder

**Files:**
- Create: `backend/app/services/library/media_keys.py`
- Create: `backend/tests/test_media_keys.py`
- Modify: `backend/app/services/library/media_storage.py:79-160`

**Interfaces:**
- Consumes: 无
- Produces:
  - `MediaKeyBuilder.content_key(scope_id, sha, mime, filename) -> str`
  - `MediaKeyBuilder.hls_prefix(resource_id, version_id) -> str`
  - `MediaKeyBuilder.hls_key(resource_id, version_id, rel_path) -> str`
  - `MediaKeyBuilder.to_file_path(bucket, key) -> str`
  - `media_storage` 中原有的模块级函数保留为薄转发，避免一次性改 12 个调用方

- [ ] **Step 1: 写失败测试**

`backend/tests/test_media_keys.py`：

```python
"""MediaKeyBuilder —— 对象键构造集中化。

保留 media_storage 里的模块级函数作为薄转发,所以本任务对调用方零影响;
spec #4 迁移校验需要一个可单独测试的键构造器。
"""

import pytest

from app.services.library.media_keys import MediaKeyBuilder


@pytest.fixture
def keys():
    return MediaKeyBuilder()


def test_content_key_is_scope_scoped_and_sharded(keys):
    k = keys.content_key(
        scope_id=310812366953241,
        sha="0bde134795d32e26fbee0100112233445566778899aabbccddeeff0011223344",
        mime="video/mp4",
        filename="clip.mp4",
    )
    assert k.startswith("t310812366953241/0b/de/")
    assert k.endswith(".mp4")


def test_content_key_is_deterministic(keys):
    args = dict(
        scope_id=1,
        sha="a" * 64,
        mime="image/webp",
        filename="x.webp",
    )
    assert keys.content_key(**args) == keys.content_key(**args)


def test_hls_prefix_shape(keys):
    assert keys.hls_prefix("123", "456") == "hls/123/456"


def test_hls_key_joins_relative_path(keys):
    assert keys.hls_key("123", "456", "720p/seg0.ts") == "hls/123/456/720p/seg0.ts"


def test_hls_key_rejects_traversal(keys):
    with pytest.raises(ValueError):
        keys.hls_key("123", "456", "../escape.ts")


def test_to_file_path_builds_sb_scheme(keys):
    assert keys.to_file_path("library", "ab/cd/ef") == "sb://library/ab/cd/ef"


def test_module_level_functions_still_work():
    """薄转发:12 个既有调用方不需要改。"""
    from app.services.library import media_storage

    assert media_storage.to_file_path("library", "k") == "sb://library/k"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/test_media_keys.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.library.media_keys'`

- [ ] **Step 3: 实现 MediaKeyBuilder**

`backend/app/services/library/media_keys.py` —— 把 `media_storage.py` 中 `_ext_for` / `_object_key` / `content_key` / `content_key_from_sha` / `to_file_path` / `hls_key_prefix` / `hls_key` 的**函数体原样搬过来**（不改逻辑，包括正则常量 `_HLS_ID_RE` / `_HLS_REL_RE` 与 `_HLS_PREFIX`），包成类方法：

```python
"""对象键构造。从 media_storage.py 原样搬出,逻辑未改。

media_storage 保留同名模块级函数作为薄转发,故本次改动对 12 个既有
调用方零影响。
"""

from __future__ import annotations

import re
from typing import Optional

_HLS_PREFIX = "hls"
_HLS_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_HLS_REL_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]*(/[A-Za-z0-9_][A-Za-z0-9._-]*)*$")


class MediaKeyBuilder:
    """构造对象存储键与 sb:// 路径。无状态,可自由实例化。"""

    def ext_for(self, mime: str, filename: Optional[str]) -> str:
        ...  # 从 media_storage._ext_for 原样搬入

    def content_key(
        self, scope_id: int, sha: str, mime: str, filename: Optional[str] = None
    ) -> str:
        ...  # 从 media_storage.content_key_from_sha 原样搬入

    def hls_prefix(self, resource_id: str, version_id: str) -> str:
        ...  # 从 media_storage.hls_key_prefix 原样搬入

    def hls_key(self, resource_id: str, version_id: str, rel_path: str) -> str:
        ...  # 从 media_storage.hls_key 原样搬入

    def to_file_path(self, bucket: str, key: str) -> str:
        ...  # 从 media_storage.to_file_path 原样搬入
```

**实现要求：** 上面的 `...` 必须替换为 `media_storage.py` 中对应函数的真实函数体。执行本步骤时先 `Read backend/app/services/library/media_storage.py:79-160`，逐个复制，不得改写逻辑 —— 本任务是纯搬迁，任何行为变化都属于超范围。

- [ ] **Step 4: 把 media_storage 的原函数改成薄转发**

`backend/app/services/library/media_storage.py`，保留原有函数名与签名，函数体改为委托：

```python
from app.services.library.media_keys import MediaKeyBuilder

_KEYS = MediaKeyBuilder()


def content_key_from_sha(scope_id: int, sha: str, mime: str, filename=None) -> str:
    return _KEYS.content_key(scope_id, sha, mime, filename)


def to_file_path(bucket: str, key: str) -> str:
    return _KEYS.to_file_path(bucket, key)


def hls_key_prefix(resource_id: str, version_id: str) -> str:
    return _KEYS.hls_prefix(resource_id, version_id)


def hls_key(resource_id: str, version_id: str, rel_path: str) -> str:
    return _KEYS.hls_key(resource_id, version_id, rel_path)
```

`content_key` 若签名与 `content_key_from_sha` 不同（前者自己算 sha），保留其原有实现，仅把内部的键拼接换成 `_KEYS.content_key`。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/test_media_keys.py -v
```

Expected: 7 passed

- [ ] **Step 6: 跑存储层全部回归**

```bash
cd backend && uv run pytest tests/test_media_storage.py \
  tests/test_media_storage_unified.py \
  tests/test_storage_object_store_smoke.py \
  tests/test_storage_migration.py \
  tests/test_resources_unified_storage.py \
  tests/test_projects_unified_storage.py \
  tests/test_derive_unified_storage.py -v
```

Expected: 全部 passed

- [ ] **Step 7: 提交**

```bash
git add backend/app/services/library/media_keys.py \
        backend/tests/test_media_keys.py \
        backend/app/services/library/media_storage.py
git commit -m "refactor(storage): 抽出 MediaKeyBuilder,原函数改薄转发

纯搬迁,逻辑未改;12 个既有调用方零影响。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 6: 从 TranscodeService 拆出 transcode_probe

`transcode_service.py` 现有 1112 行、单个 `TranscodeService` 类含 20 个方法。先拆探测部分 —— 它们都是无状态的 ffprobe 包装，与编排无耦合。

**Files:**
- Create: `backend/app/services/media/transcode/transcode_probe.py`
- Modify: `backend/app/services/media/transcode/transcode_service.py`（移除 `_detect_encoder`、`_probe_encoder`、`_map_nvenc_preset`、`_probe_resolution`、`_probe_duration`、`_probe_codecs`、`_probe_bitrate`）

**Interfaces:**
- Consumes: 无
- Produces:
  - `TranscodeProbe.detect_encoder() -> tuple[str, list[str], str]`
  - `TranscodeProbe.probe_resolution(filepath) -> tuple[Optional[int], Optional[int]]`
  - `TranscodeProbe.probe_duration(filepath) -> Optional[float]`
  - `TranscodeProbe.probe_codecs(filepath) -> tuple[Optional[str], Optional[str]]`
  - `TranscodeProbe.probe_bitrate(filepath) -> Optional[int]`
  - `TranscodeProbe.map_nvenc_preset(cpu_preset: str) -> str`

- [ ] **Step 1: 先确认既有测试覆盖，作为搬迁的安全网**

```bash
cd backend && uv run pytest tests/test_transcode_probe.py tests/test_transcode_db_setting_coerce.py -v
```

Expected: 全部 passed。**记录通过数量** —— 搬迁后必须一模一样。

- [ ] **Step 2: 新建 transcode_probe.py 并搬入七个方法**

`backend/app/services/media/transcode/transcode_probe.py`：

```python
"""ffprobe / 编码器探测。从 TranscodeService 原样搬出。

拆分理由:这七个方法全部无状态、只读,与转码编排无耦合,却占了
transcode_service.py 相当篇幅。搬出后编排层只留真正的流程控制。
"""

from __future__ import annotations

from typing import List, Optional


class TranscodeProbe:
    """ffprobe 包装 + 编码器能力探测。无状态。"""

    async def detect_encoder(self) -> tuple[str, list[str], str]:
        ...  # 从 TranscodeService._detect_encoder 原样搬入

    @staticmethod
    async def probe_encoder(encoder_name: str) -> bool:
        ...  # 从 TranscodeService._probe_encoder 原样搬入

    @staticmethod
    def map_nvenc_preset(cpu_preset: str) -> str:
        ...  # 从 TranscodeService._map_nvenc_preset 原样搬入

    async def probe_resolution(self, filepath: str):
        ...  # 从 TranscodeService._probe_resolution 原样搬入

    async def probe_duration(self, filepath: str) -> Optional[float]:
        ...  # 从 TranscodeService._probe_duration 原样搬入

    async def probe_codecs(self, filepath: str):
        ...  # 从 TranscodeService._probe_codecs 原样搬入

    async def probe_bitrate(self, filepath: str) -> Optional[int]:
        ...  # 从 TranscodeService._probe_bitrate 原样搬入
```

**实现要求：** `...` 必须替换为 `transcode_service.py` 中对应方法的真实函数体。执行时先 Read `transcode_service.py:74-160` 与 `:603-721`，逐个复制。方法内若引用 `self._xxx` 属性（如缓存的编码器结果），一并把该属性搬进 `TranscodeProbe.__init__`。**不得改写逻辑。**

- [ ] **Step 3: 在 TranscodeService 中改为委托**

`transcode_service.py` 的 `TranscodeService.__init__` 增加：

```python
        from app.services.media.transcode.transcode_probe import TranscodeProbe

        self._probe = TranscodeProbe()
```

删除被搬走的七个方法定义，把类内所有 `self._probe_duration(...)`、`self._detect_encoder()` 等调用改为 `self._probe.probe_duration(...)`、`self._probe.detect_encoder()`。

- [ ] **Step 4: 确认没有残留调用**

```bash
cd backend && grep -nE "self\._(detect_encoder|probe_encoder|map_nvenc_preset|probe_resolution|probe_duration|probe_codecs|probe_bitrate)" \
  app/services/media/transcode/transcode_service.py
```

Expected: 无输出。

- [ ] **Step 5: 跑转码全部回归**

```bash
cd backend && uv run pytest tests/test_transcode_probe.py \
  tests/test_transcode_db_setting_coerce.py \
  tests/test_transcode_materialize.py \
  tests/test_transcode_size_gate.py \
  tests/test_transcode_thumbnail_scope_wiring.py \
  tests/test_admin_transcode_repository.py -v
```

Expected: 全部 passed，且 `test_transcode_probe.py` 的通过数与 Step 1 记录一致。

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/media/transcode/transcode_probe.py \
        backend/app/services/media/transcode/transcode_service.py
git commit -m "refactor(transcode): 拆出 TranscodeProbe（七个无状态探测方法）

纯搬迁,逻辑未改。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 7: 从 TranscodeService 拆出 hls_publisher

**Files:**
- Create: `backend/app/services/media/transcode/hls_publisher.py`
- Modify: `backend/app/services/media/transcode/transcode_service.py`（移除 `_publish_hls`、`_clear_published_hls`、`_write_master_playlist`）

**Interfaces:**
- Consumes: `MediaKeyBuilder.hls_prefix` / `hls_key`（Task 5）、`ObjectStore.put_dir` / `remove_prefix`
- Produces:
  - `HlsPublisher.publish(resource_id: str, version_id: str, hls_dir: Path) -> None`
  - `HlsPublisher.clear(resource_id: str, version_id: str) -> None`
  - `HlsPublisher.write_master_playlist(...)` —— 签名与原 `_write_master_playlist` 一致

- [ ] **Step 1: 先跑既有 HLS 测试建立基线**

```bash
cd backend && uv run pytest tests/ -k "hls or transcode" -v 2>&1 | tail -20
```

**记录通过数量。**

- [ ] **Step 2: 新建 hls_publisher.py 并搬入三个方法**

`backend/app/services/media/transcode/hls_publisher.py`：

```python
"""HLS 产物的发布与清理。从 TranscodeService 原样搬出。

关键不变量:master.m3u8 必须最后上传。播放器一旦拿到 master 就会立刻
去取各档 playlist 与分片,若 master 先到而分片还没传完,播放器会拿到
404 并放弃。原实现的做法是先把 master 移出目录树、put_dir 传完其余
文件、再单独 put_file 传 master、最后移回 —— 搬迁时必须完整保留。
"""

from __future__ import annotations

from pathlib import Path


class HlsPublisher:
    """把本地 HLS 目录发布到对象存储。"""

    async def publish(self, resource_id: str, version_id: str, hls_dir: Path) -> None:
        ...  # 从 TranscodeService._publish_hls 原样搬入

    async def clear(self, resource_id: str, version_id: str) -> None:
        ...  # 从 TranscodeService._clear_published_hls 原样搬入

    def write_master_playlist(self, *args, **kwargs):
        ...  # 从 TranscodeService._write_master_playlist 原样搬入（签名照抄）
```

**实现要求：** `...` 必须替换为 `transcode_service.py:999-1112` 的真实函数体。执行时先 Read 该区间。`write_master_playlist` 的参数列表必须与原方法**逐字一致**，不得简化为 `*args/**kwargs`。**master.m3u8 最后上传的顺序逻辑必须原样保留。**

- [ ] **Step 3: 在 TranscodeService 中改为委托**

`TranscodeService.__init__` 增加：

```python
        from app.services.media.transcode.hls_publisher import HlsPublisher

        self._hls = HlsPublisher()
```

删除被搬走的三个方法，把三处调用点改为 `self._hls.publish(...)` / `self._hls.clear(...)` / `self._hls.write_master_playlist(...)`。

- [ ] **Step 4: 确认没有残留调用**

```bash
cd backend && grep -nE "self\._(publish_hls|clear_published_hls|write_master_playlist)" \
  app/services/media/transcode/transcode_service.py
```

Expected: 无输出。

- [ ] **Step 5: 确认 transcode_service.py 已明显瘦身**

```bash
cd backend && wc -l app/services/media/transcode/transcode_service.py \
  app/services/media/transcode/transcode_probe.py \
  app/services/media/transcode/hls_publisher.py
```

Expected: `transcode_service.py` 显著低于 1112 行，三个文件行数之和与原文件相当（允许 import 样板带来的小幅增加）。

- [ ] **Step 6: 跑全量后端测试**

```bash
cd backend && uv run pytest -q 2>&1 | tail -15
```

Expected: 无 FAILED。这是纯重构 PR，**任何一条新失败都说明搬迁改变了行为，必须回头修而不是改测试**。

- [ ] **Step 7: 提交并开 PR**

```bash
git add backend/app/services/media/transcode/hls_publisher.py \
        backend/app/services/media/transcode/transcode_service.py
git commit -m "refactor(transcode): 拆出 HlsPublisher,transcode_service 瘦身

master.m3u8 最后上传的顺序不变量原样保留。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"

git push -u origin refactor/storage-consolidation
gh pr create --base master --title "refactor(storage): 函数整合 + 拆分 transcode_service（零行为变化）" \
  --body "纯重构 PR,零行为变化。CLAUDE.md 要求 24h 内合入。

- AudioSourceResolver：合并四处重复的音频路径解析（两条链原本行为不一致,取并集）
- DerivedArtifactPaths：派生产物落位收拢,spec #2 只改这一处
- MediaKeyBuilder：键构造集中,原函数保留为薄转发（调用方零影响）
- 拆 transcode_service.py 1112 行 → 编排 / TranscodeProbe / HlsPublisher

全量 pytest 通过,无测试被修改。

https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

# PR3 — `feature/nous-core-stream-io`（Rust IO 接入）

> 必须基于**已合入 PR2** 的 master：`git checkout master && git pull --rebase && git checkout -b feature/nous-core-stream-io`

### Task 8: 前置闸门 —— 实测 httpx 真实基线

spec 中的 243 MB/s 是用 `urllib` 手写脚本测的，**不是应用实际走的 `httpx.aiter_bytes`**。若 httpx 本身就能跑到 400+ MB/s，本 PR 的全部 Rust 工作失去依据，应就此终止。

**Files:**
- Create: `backend/scripts/probe_httpx_baseline.py`

**Interfaces:**
- Consumes: `ObjectStore.get_stream`
- Produces: 一个 go/no-go 判断，无代码产物

- [ ] **Step 1: 写基线探针**

`backend/scripts/probe_httpx_baseline.py`：

```python
"""实测 ObjectStore.get_stream（httpx）的真实吞吐 —— PR3 的前置闸门。

若结果 ≥400 MB/s,说明 Python 不是瓶颈,Rust 方案失去依据,应终止 PR3。
若结果 ≤300 MB/s,继续。

方法学（见 scripts/bench-storage.py 的教训）:字节路径里除被测对象外
不得有额外开销;取最大的对象;多次取中位数。
"""

import asyncio
import statistics
import sys
import time

sys.path.insert(0, ".")


async def main() -> int:
    from app.services.library.media_storage import ObjectStore

    store = ObjectStore("library")

    # 取一个足够大的对象:小文件测的是 IOPS 不是带宽
    from app.db.supabase_client import get_async_supabase_admin

    client = await get_async_supabase_admin()
    rows = (
        await client.table("resources")
        .select("file_path,size_bytes")
        .like("file_path", "sb://library/%")
        .order("size_bytes", desc=True)
        .limit(1)
        .execute()
    )
    if not rows.data:
        print("❌ 没有 sb://library 行,无法测量")
        return 1

    file_path = rows.data[0]["file_path"]
    key = file_path.removeprefix("sb://library/")
    size = rows.data[0]["size_bytes"]
    print(f"样本: {key[:60]}  ({size / 1048576:.0f} MB)")

    rates = []
    for i in range(3):
        total = 0
        start = time.perf_counter()
        async for chunk in store.get_stream(key):
            total += len(chunk)
        elapsed = time.perf_counter() - start
        rate = total / elapsed / 1048576
        rates.append(rate)
        print(f"  第 {i + 1} 次: {rate:.0f} MB/s")

    median = statistics.median(rates)
    print(f"\n中位数: {median:.0f} MB/s")
    if median >= 400:
        print("🛑 httpx 已达 400+ MB/s —— Python 不是瓶颈,终止 PR3")
        return 2
    print("✅ 低于 400 MB/s —— Rust 方案前提成立,继续")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

- [ ] **Step 2: 运行闸门**

```bash
cd backend && uv run python scripts/probe_httpx_baseline.py
```

Expected: 退出码 0 且打印 "✅ 低于 400 MB/s"。

**若退出码为 2**：停止执行本 PR，把结果反馈给设计者 —— spec 的「风险」一节已预案：证伪则 PR3 终止，仅保留 PR1/PR2。

- [ ] **Step 3: 提交探针脚本**

```bash
git add backend/scripts/probe_httpx_baseline.py
git commit -m "test(storage): httpx 基线探针 —— PR3 的前置闸门

243 MB/s 原是 urllib 手写脚本测的,不是应用实际路径。本脚本测
ObjectStore.get_stream 的真实吞吐,≥400 MB/s 则 Rust 方案失去依据。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 9: nous_core 实现 fetch_to_file

**Files:**
- Create: `nous-core/src/http_io.rs`
- Modify: `nous-core/src/errors.rs`
- Modify: `nous-core/src/lib.rs`
- Modify: `nous-core/Cargo.toml`

**Interfaces:**
- Consumes: 无
- Produces: `nous_core.fetch_to_file(url: str, headers: list[tuple[str, str]], dst: str) -> int`（返回写入字节数）

- [ ] **Step 1: 加依赖**

`nous-core/Cargo.toml` 的 `[dependencies]` 增加。**必须 `default-features = false` 并显式选 `rustls-tls`** —— slim 镜像没有 OpenSSL，用默认 features 会在链接期失败：

```toml
reqwest = { version = "0.12", default-features = false, features = ["rustls-tls", "stream"] }
tokio = { version = "1", features = ["rt-multi-thread", "fs", "io-util", "macros"] }
futures-util = "0.3"
```

- [ ] **Step 2: 新增 IO 错误变体**

`nous-core/src/errors.rs` 的 `MediaError` 枚举增加：

```rust
    #[error("http request failed: {0}")]
    Http(String),

    #[error("http status {0} for {1}")]
    HttpStatus(u16, String),

    #[error("io error: {0}")]
    Io(String),
```

- [ ] **Step 3: 实现 http_io.rs**

`nous-core/src/http_io.rs`：

```rust
//! 流式 HTTP 拉取 —— 把字节搬运从 Python 解释器里挪出来。
//!
//! Python 侧只负责算出签名 URL 与 headers（鉴权决策），字节完全不经过
//! 解释器。实测 Python chunk 循环把 470 MB/s 压到 243 MB/s。

use std::path::Path;

use futures_util::StreamExt;
use tokio::io::AsyncWriteExt;

use crate::errors::MediaError;

/// 拉取 `url` 并流式写入 `dst`，返回写入字节数。
///
/// 失败时**必须**删除半成品文件：调用方 `materialize()` 会把 dst 交给
/// ffmpeg，一个被截断的文件不会报错，只会产出静默错误的结果 —— 那是
/// 最难排查的故障形态。
pub async fn fetch_to_file(
    url: &str,
    headers: Vec<(String, String)>,
    dst: &str,
) -> Result<u64, MediaError> {
    match fetch_inner(url, headers, dst).await {
        Ok(n) => Ok(n),
        Err(e) => {
            let _ = tokio::fs::remove_file(Path::new(dst)).await;
            Err(e)
        }
    }
}

async fn fetch_inner(
    url: &str,
    headers: Vec<(String, String)>,
    dst: &str,
) -> Result<u64, MediaError> {
    let client = reqwest::Client::new();
    let mut req = client.get(url);
    for (k, v) in headers {
        req = req.header(k, v);
    }

    let resp = req.send().await.map_err(|e| MediaError::Http(e.to_string()))?;
    let status = resp.status();
    if !status.is_success() {
        return Err(MediaError::HttpStatus(status.as_u16(), url.to_string()));
    }

    let mut file = tokio::fs::File::create(dst)
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?;

    let mut written: u64 = 0;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| MediaError::Http(e.to_string()))?;
        file.write_all(&chunk)
            .await
            .map_err(|e| MediaError::Io(e.to_string()))?;
        written += chunk.len() as u64;
    }
    file.flush().await.map_err(|e| MediaError::Io(e.to_string()))?;
    Ok(written)
}
```

- [ ] **Step 4: 在 lib.rs 导出，并释放 GIL**

`nous-core/src/lib.rs` 增加。**`py.allow_threads` 不可省略** —— 持有 GIL 的同步调用会卡死整个事件循环，并发下会比 Python 更慢：

```rust
mod http_io;

#[pyfunction]
fn fetch_to_file(
    py: Python<'_>,
    url: &str,
    headers: Vec<(String, String)>,
    dst: &str,
) -> PyResult<u64> {
    py.allow_threads(|| {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        rt.block_on(http_io::fetch_to_file(url, headers, dst))
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    })
}
```

并在 `#[pymodule] fn nous_core` 中注册：

```rust
    m.add_function(wrap_pyfunction!(fetch_to_file, m)?)?;
```

顶部需 `use pyo3::exceptions::PyRuntimeError;`。

- [ ] **Step 5: 编译**

```bash
cd nous-core && cargo build --release
```

Expected: 编译通过。若报 OpenSSL 相关链接错误，回到 Step 1 检查 `default-features = false`。

- [ ] **Step 6: 构建 wheel 并本地安装**

```bash
cd nous-core && maturin build --release
cd ../backend && uv pip install ../nous-core/target/wheels/nous_core*.whl --force-reinstall
```

- [ ] **Step 7: 写 Rust 侧行为测试**

`backend/tests/test_nous_core_stream_io.py`：

```python
"""nous_core.fetch_to_file 的行为契约。

不测吞吐（那属于验收,见 Task 11）,只测正确性与失败清理。
"""

import http.server
import os
import threading

import pytest

nous_core = pytest.importorskip("nous_core")


@pytest.fixture
def server(tmp_path):
    """本地 HTTP 服务:/ok 返回 1MB,/boom 返回 500。"""
    payload = os.urandom(1024 * 1024)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/ok":
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            else:
                self.send_response(500)
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", payload
    srv.shutdown()


def test_fetch_writes_exact_bytes(server, tmp_path):
    base, payload = server
    dst = tmp_path / "out.bin"
    n = nous_core.fetch_to_file(f"{base}/ok", [], str(dst))
    assert n == len(payload)
    assert dst.read_bytes() == payload


def test_fetch_sends_headers(server, tmp_path):
    base, _ = server
    dst = tmp_path / "out.bin"
    n = nous_core.fetch_to_file(
        f"{base}/ok", [("Authorization", "Bearer t")], str(dst)
    )
    assert n > 0


def test_fetch_raises_on_error_status(server, tmp_path):
    base, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/boom", [], str(dst))


def test_fetch_removes_partial_file_on_failure(server, tmp_path):
    """失败必须清理 —— 截断文件交给 ffmpeg 会静默产出错误结果。"""
    base, _ = server
    dst = tmp_path / "out.bin"
    with pytest.raises(RuntimeError):
        nous_core.fetch_to_file(f"{base}/boom", [], str(dst))
    assert not dst.exists()


def test_fetch_releases_gil(server, tmp_path):
    """8 并发必须真正并行。若忘了 allow_threads,这里会串行化。"""
    import time
    from concurrent.futures import ThreadPoolExecutor

    base, payload = server

    def one(i: int) -> int:
        return nous_core.fetch_to_file(f"{base}/ok", [], str(tmp_path / f"o{i}.bin"))

    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(8)))
    elapsed = time.perf_counter() - start

    assert all(r == len(payload) for r in results)
    # 串行 8 次本地 1MB 传输也远快于 8s;这里只做粗粒度回归防护
    assert elapsed < 8.0
```

- [ ] **Step 8: 运行测试**

```bash
cd backend && uv run pytest tests/test_nous_core_stream_io.py -v
```

Expected: 5 passed

- [ ] **Step 9: 提交**

```bash
git add nous-core/Cargo.toml nous-core/src/http_io.rs nous-core/src/errors.rs \
        nous-core/src/lib.rs backend/tests/test_nous_core_stream_io.py
git commit -m "feat(nous-core): fetch_to_file —— Rust 流式拉取,字节不进解释器

- reqwest 用 rustls-tls（slim 镜像无 OpenSSL）
- py.allow_threads 释放 GIL,否则并发比 Python 更慢
- 失败路径必须删半成品:截断文件交给 ffmpeg 会静默产出错误结果

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 10: nous_core 实现 put_file

**Files:**
- Modify: `nous-core/src/http_io.rs`
- Modify: `nous-core/src/lib.rs`
- Modify: `backend/tests/test_nous_core_stream_io.py`

**Interfaces:**
- Consumes: Task 9 的 `MediaError` 变体
- Produces: `nous_core.put_file(src: str, url: str, headers: list[tuple[str, str]]) -> None`

- [ ] **Step 1: 在 http_io.rs 增加上传**

```rust
/// 把本地文件 `src` 流式 PUT 到 `url`。
///
/// 用 `Body::wrap_stream` 而非读进内存 —— 生成视频可达数百 MB。
pub async fn put_file(
    src: &str,
    url: &str,
    headers: Vec<(String, String)>,
) -> Result<(), MediaError> {
    let file = tokio::fs::File::open(src)
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?;
    let len = file
        .metadata()
        .await
        .map_err(|e| MediaError::Io(e.to_string()))?
        .len();

    let stream = tokio_util::io::ReaderStream::new(file);
    let client = reqwest::Client::new();
    let mut req = client.put(url).header("Content-Length", len);
    for (k, v) in headers {
        req = req.header(k, v);
    }

    let resp = req
        .body(reqwest::Body::wrap_stream(stream))
        .send()
        .await
        .map_err(|e| MediaError::Http(e.to_string()))?;

    let status = resp.status();
    if !status.is_success() {
        return Err(MediaError::HttpStatus(status.as_u16(), url.to_string()));
    }
    Ok(())
}
```

`Cargo.toml` 增加 `tokio-util = { version = "0.7", features = ["io"] }`。

- [ ] **Step 2: 在 lib.rs 导出**

```rust
#[pyfunction]
fn put_file(
    py: Python<'_>,
    src: &str,
    url: &str,
    headers: Vec<(String, String)>,
) -> PyResult<()> {
    py.allow_threads(|| {
        let rt = tokio::runtime::Runtime::new()
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        rt.block_on(http_io::put_file(src, url, headers))
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))
    })
}
```

并注册 `m.add_function(wrap_pyfunction!(put_file, m)?)?;`

- [ ] **Step 3: 加上传测试**

追加到 `backend/tests/test_nous_core_stream_io.py`：

```python
def test_put_file_uploads_bytes(tmp_path):
    """PUT 收到的字节必须与源文件逐字节一致。"""
    import http.server
    import os
    import threading

    received = {}
    payload = os.urandom(512 * 1024)
    src = tmp_path / "in.bin"
    src.write_bytes(payload)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_PUT(self):
            n = int(self.headers["Content-Length"])
            received["body"] = self.rfile.read(n)
            received["auth"] = self.headers.get("Authorization")
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        nous_core.put_file(
            str(src),
            f"http://127.0.0.1:{srv.server_port}/k",
            [("Authorization", "Bearer t")],
        )
    finally:
        srv.shutdown()

    assert received["body"] == payload
    assert received["auth"] == "Bearer t"
```

- [ ] **Step 4: 重建 wheel 并跑测试**

```bash
cd nous-core && maturin build --release
cd ../backend && uv pip install ../nous-core/target/wheels/nous_core*.whl --force-reinstall
uv run pytest tests/test_nous_core_stream_io.py -v
```

Expected: 6 passed

- [ ] **Step 5: 提交**

```bash
git add nous-core/Cargo.toml nous-core/src/http_io.rs nous-core/src/lib.rs \
        backend/tests/test_nous_core_stream_io.py
git commit -m "feat(nous-core): put_file —— Rust 流式上传

用 wrap_stream 而非读进内存,生成视频可达数百 MB。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

### Task 11: 接线到 materialize，加开关与验收

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/app/services/library/media_storage.py`（`materialize` 的 `sb://` 分支、`ObjectStore.put_file`）
- Create: `backend/tests/test_rust_stream_io_wiring.py`

**Interfaces:**
- Consumes: `nous_core.fetch_to_file` / `nous_core.put_file`（Task 9、10）
- Produces: 无（终端任务）

- [ ] **Step 1: 加开关**

`backend/app/core/config.py` 的 `Settings` 增加：

```python
    FEATURE_RUST_STREAM_IO: bool = Field(
        default=False,
        description=(
            "把 materialize / put_file 的字节搬运交给 nous_core（Rust）。"
            "关掉即回退纯 Python 路径。这是实现替换而非业务能力,故用 env "
            "flag 而不进 Module Control Center。"
        ),
    )
```

- [ ] **Step 2: 写接线测试**

`backend/tests/test_rust_stream_io_wiring.py`：

```python
"""开关接线:开则走 Rust,关则走 Python,两条路径产出必须一致。"""

import hashlib

import pytest

from app.services.library import media_storage


async def test_materialize_uses_python_when_flag_off(monkeypatch, tmp_path):
    """默认关闭时不得触碰 nous_core。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", False)

    called = {"rust": False}

    def _boom(*a, **k):
        called["rust"] = True
        raise AssertionError("Rust path must not be used when flag is off")

    monkeypatch.setattr(media_storage, "_rust_fetch_to_file", _boom, raising=False)

    chunks = [b"a" * 1024, b"b" * 1024]

    class _Store:
        async def get_stream(self, key, chunk_size=None):
            for c in chunks:
                yield c

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    async with media_storage.materialize("sb://library/k") as p:
        assert p.read_bytes() == b"".join(chunks)
    assert called["rust"] is False


async def test_materialize_deletes_temp_on_exit(monkeypatch, tmp_path):
    """两条路径都必须在退出时删临时文件。"""
    from app.core import config

    monkeypatch.setattr(config.settings, "FEATURE_RUST_STREAM_IO", False)

    class _Store:
        async def get_stream(self, key, chunk_size=None):
            yield b"x" * 16

    monkeypatch.setattr(media_storage, "ObjectStore", lambda bucket: _Store())

    async with media_storage.materialize("sb://library/k") as p:
        held = p
        assert held.exists()
    assert not held.exists()
```

- [ ] **Step 3: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/test_rust_stream_io_wiring.py -v
```

Expected: FAIL（`materialize` 尚未有开关分支）

- [ ] **Step 4: 改 materialize 的 sb:// 分支**

`backend/app/services/library/media_storage.py`，把 `sb://` 分支中 `async for chunk … await out.write(chunk)` 的循环替换为开关分支：

```python
    store = ObjectStore(loc.bucket)
    fd, tmp = tempfile.mkstemp(suffix=Path(loc.key).suffix)
    os.close(fd)
    try:
        from app.core.config import settings as _s

        if _s.FEATURE_RUST_STREAM_IO:
            # 字节路径交给 Rust:Python 只算 URL + headers（鉴权决策）。
            # 失败时 nous_core 自己删半成品,不依赖这里的 finally。
            import nous_core

            proxy = await store._proxy()
            url, headers = store._object_target(proxy, loc.key)
            await asyncio.to_thread(
                nous_core.fetch_to_file, url, list(headers.items()), tmp
            )
        else:
            import aiofiles

            async with aiofiles.open(tmp, "wb") as out:
                async for chunk in store.get_stream(loc.key):
                    await out.write(chunk)
        yield Path(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
```

顶部确保 `import asyncio` 存在。

**注意** `_object_target` 是既有私有方法（`media_storage.py:282`），返回 `(url, headers)`；此处复用它保证 Rust 与 Python 两条路径打的是**同一个 URL**。

- [ ] **Step 5: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/test_rust_stream_io_wiring.py -v
```

Expected: 2 passed

- [ ] **Step 6: 把 ObjectStore.put_file 也接上 Rust**

`backend/app/services/library/media_storage.py` 的 `ObjectStore.put_file`（约 `:256`），在方法体开头加开关分支，命中则走 Rust、直接返回；否则落到原有 SDK 上传：

```python
    async def put_file(
        self, key: str, file_path: str, mime: str, *, upsert: bool = True
    ) -> None:
        from app.core.config import settings as _s

        if _s.FEATURE_RUST_STREAM_IO:
            # 与 materialize 同构:Python 只算 URL + headers,字节交给 Rust。
            import asyncio

            import nous_core

            proxy = await self._proxy()
            url, headers = self._object_target(proxy, key)
            headers = dict(headers)
            headers["content-type"] = mime or "application/octet-stream"
            if upsert:
                headers["x-upsert"] = "true"
            await asyncio.to_thread(
                nous_core.put_file, file_path, url, list(headers.items())
            )
            return

        # ↓ 原有实现保持不动
```

- [ ] **Step 7: 验证上传两条路径产出一致**

```bash
cd backend && uv run python - <<'PY'
import asyncio, hashlib, os, tempfile
from app.core.config import settings
from app.services.library.media_storage import ObjectStore

async def main():
    store = ObjectStore("library")
    payload = os.urandom(3 * 1024 * 1024)
    fd, src = tempfile.mkstemp(suffix=".bin"); os.close(fd)
    open(src, "wb").write(payload)
    want = hashlib.sha256(payload).hexdigest()

    for flag, key in ((False, "probe/py.bin"), (True, "probe/rs.bin")):
        settings.FEATURE_RUST_STREAM_IO = flag
        await store.put_file(key, src, "application/octet-stream")
        got = hashlib.sha256(await store.get_bytes(key)).hexdigest()
        print(f"{'rust' if flag else 'python':7} {'一致' if got == want else '❌ 不一致'}")
        await store.remove(key)
    os.unlink(src)

asyncio.run(main())
PY
```

Expected: 两行都打印「一致」。不一致则停止排查上传路径，属正确性缺陷。

- [ ] **Step 8: 字节等价性验证（读取，对真实对象）**

```bash
cd backend && uv run python - <<'PY'
import asyncio, hashlib
from app.core.config import settings
from app.services.library.media_storage import materialize

KEY = "sb://library/<从 DB 取一个真实 sb:// 路径填这里>"

async def one(flag):
    settings.FEATURE_RUST_STREAM_IO = flag
    async with materialize(KEY) as p:
        return hashlib.sha256(p.read_bytes()).hexdigest()

async def main():
    py = await one(False)
    rs = await one(True)
    print("python:", py)
    print("rust  :", rs)
    print("一致" if py == rs else "❌ 不一致")

asyncio.run(main())
PY
```

Expected: 打印「一致」。**不一致则立刻停止**，说明 Rust 路径产出与 Python 不同，属于正确性缺陷而非性能问题。

- [ ] **Step 9: 性能验收**

```bash
cd backend && FEATURE_RUST_STREAM_IO=false uv run python scripts/probe_httpx_baseline.py
cd backend && FEATURE_RUST_STREAM_IO=true  uv run python scripts/probe_httpx_baseline.py
```

Expected: 开启后中位吞吐显著高于关闭时（目标接近 Task 8 里 curl 直测的水平）。若开启后**更慢**，检查 `py.allow_threads` 是否遗漏。

- [ ] **Step 10: 跑全量后端测试**

```bash
cd backend && uv run pytest -q 2>&1 | tail -15
```

Expected: 无 FAILED。开关默认关闭，全部既有行为不变。

- [ ] **Step 11: 提交并开 PR**

```bash
git add backend/app/core/config.py backend/app/services/library/media_storage.py \
        backend/tests/test_rust_stream_io_wiring.py
git commit -m "feat(storage): materialize 接入 nous_core 流式 IO（开关默认关）

FEATURE_RUST_STREAM_IO=false 时行为完全不变;开启后字节路径不经过
Python 解释器。两条路径复用同一个 _object_target,保证打同一个 URL。

Claude-Session: https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"

git push -u origin feature/nous-core-stream-io
gh pr create --base master --title "feat(storage): nous_core 流式 IO 接入（开关默认关）" \
  --body "把 materialize 的字节搬运从 Python 移到 Rust。

前置闸门已通过:实测 ObjectStore.get_stream（httpx）低于 400 MB/s,
确认 Python 是瓶颈（分层探针:curl 直连 476、curl 经 Supabase Storage
470、Python 243 —— Node 层仅损 1%）。

- nous_core.fetch_to_file / put_file（reqwest + rustls-tls）
- py.allow_threads 释放 GIL,含 8 并发回归测试
- 失败路径删半成品:截断文件交给 ffmpeg 会静默产出错误结果
- FEATURE_RUST_STREAM_IO 默认 false,关掉即回退

验证:字节等价性 sha256 一致;全量 pytest 通过。

https://claude.ai/code/session_01NpCU2EEaHKzpDTXeuXAhvA"
```

---

## 完成标准

- [ ] PR1 已合入：`git grep -i mediahub -- . ':!docs' ':!deploy/nas' ':!docker' ':!supabase'` 只剩明确排除项
- [ ] PR2 已合入：`transcode_service.py` 显著低于 1112 行；全量 pytest 通过且**无测试文件被修改**
- [ ] PR3 已合入：`FEATURE_RUST_STREAM_IO=true` 时字节等价、吞吐提升；默认关闭
- [ ] 三个 PR 的顺序未被打乱（PR3 基于已合入 PR2 的 master）

## 后续

本计划完成后进入 spec #2（写入改走 S3，含派生产物）。`DerivedArtifactPaths` 与 `AudioSourceResolver` 是为它铺的路 —— 届时改动应集中在这两个类内部。
