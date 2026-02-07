# Multi-Source Video Download + Whisper + LLM Analysis Design

> Date: 2026-02-07
> Status: Approved
> Branch: feature/add-whisper-support

## Overview

Extend MediaHub to support multi-source video downloading (via yt-dlp), automatic speech-to-text transcription (via Whisper), and AI-powered video analysis/summarization (via LLM). Introduce Rust components for high-performance media processing and HLS streaming.

## Design Decisions

| Decision | Conclusion |
|----------|-----------|
| yt-dlp positioning | Fallback for non-Douyin sources; Douyin keeps existing browser automation |
| Whisper | Switchable between OpenAI API (default) and local model |
| LLM analysis | Layered: basic text summary (auto), visual analysis (manual trigger) |
| AI providers | Multi-provider adapter: OpenAI, DeepSeek, Doubao, Ollama, LM Studio |
| Trigger mechanism | Configurable automation with toggle switches (like video_bool/music_bool) |
| Frontend display | Detail page with independent Tab panel; MediaCard with AI status icons |
| Input entry | Unified input, auto-detect source platform |
| Database | Rename `douyin_videos` → `videos`, UUID primary key, generalized field names |
| API layer | Keep FastAPI; Rust only for low-level I/O-intensive operations |
| Rust | Included in this release via PyO3; FFmpeg wrapper, audio extraction, HLS segmentation |
| Video streaming | HLS (fMP4 segments) replaces faststart optimization |

---

## Phase 1: Database Refactoring + Rust Core Module

### 1.1 Database Migration

#### Table Rename: `douyin_videos` → `videos`

**Field Renames (generalized):**

| Old Field | New Field | Reason |
|-----------|-----------|--------|
| `aweme_id` | `platform_id` | "aweme" is Douyin-specific |
| `aweme_type` | `media_type` | Generic media type |
| `video_digg_count` | `like_count` | "digg" is Douyin's internal term |
| `video_collect_count` | `favorite_count` | Generalized |
| `video_comment_count` | `comment_count` | Remove `video_` prefix |
| `video_share_count` | `share_count` | Remove `video_` prefix |
| `video_original_url` | `original_url` | Remove `video_` prefix |
| `video_duration` | `duration` | Remove `video_` prefix |
| `video_resolution` | `resolution` | Remove `video_` prefix |
| `video_datasize` | `datasize` | Remove `video_` prefix |
| `video_datasize_bytes` | `datasize_bytes` | Remove `video_` prefix |
| `video_hashtag_name` | `hashtags` | Remove `video_` prefix |
| `video_created_time` | `published_at` | More generic |
| `video_title` | `title` | Remove `video_` prefix |
| `video_desc` | `description` | Remove `video_` prefix |

**Primary Key Change:**

```sql
-- Old
id BIGSERIAL PRIMARY KEY

-- New
id UUID PRIMARY KEY DEFAULT gen_random_uuid()
```

All foreign key references (video_collections, video_tags, video_access_logs, etc.) updated from BIGINT to UUID.

**New Fields:**

```sql
source_platform VARCHAR(50) NOT NULL DEFAULT 'douyin',  -- 'douyin', 'youtube', 'bilibili', 'twitter', 'other'
source_url TEXT,                                         -- Original user input URL
external_id VARCHAR(255),                                -- Platform's original ID (preserves aweme_id value)

-- Transcription & analysis status
transcript_status VARCHAR(20) DEFAULT 'pending',         -- pending/processing/completed/failed/skipped
summary_status VARCHAR(20) DEFAULT 'pending',
visual_analysis_status VARCHAR(20) DEFAULT 'pending',

-- Automation toggles
transcript_bool BOOLEAN DEFAULT true,
summary_bool BOOLEAN DEFAULT true,

-- HLS streaming
hls_path TEXT,                                           -- HLS playlist path
media_format VARCHAR(10) DEFAULT 'mp4',                  -- 'mp4' (legacy) / 'hls' (new)

-- Unique constraint
UNIQUE(platform_id, source_platform)                     -- Composite unique (cross-platform safe)
```

**`media_type` Value Mapping:**

```
-- Douyin (backward compatible)
'video'      -- was aweme_type '0', standard video
'carousel'   -- was aweme_type '2', image collection
'image_text' -- was aweme_type '68', image-text
'special'    -- was aweme_type '4', '61'

-- Generic (yt-dlp sources)
'video'      -- YouTube/Bilibili videos
'short'      -- YouTube Shorts, Twitter short videos
'live_clip'  -- Live stream clips
```

#### New Table: `video_transcripts`

```sql
CREATE TABLE video_transcripts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
    language VARCHAR(10),                -- 'zh', 'en', 'ja', etc.
    full_text TEXT,                      -- Complete transcript
    segments JSONB,                      -- Timestamped segments
    -- segments format: [{"start": 0.0, "end": 2.5, "text": "..."}]
    whisper_model VARCHAR(50),           -- 'openai-api', 'large-v3', etc.
    duration_seconds FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_video_transcripts_video_id ON video_transcripts(video_id);
```

#### New Table: `video_summaries`

```sql
CREATE TABLE video_summaries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID REFERENCES videos(id) ON DELETE CASCADE,
    summary_type VARCHAR(20),            -- 'basic' (auto) / 'visual' (manual)
    summary_text TEXT,                   -- Summary body
    key_points JSONB,                    -- ["point 1", "point 2", ...]
    topics JSONB,                        -- ["tech", "tutorial", ...]
    llm_model VARCHAR(50),              -- 'gpt-4o-mini', 'gpt-4o', etc.
    llm_provider VARCHAR(50),           -- 'openai', 'ollama', etc.
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_video_summaries_video_id ON video_summaries(video_id);
```

#### Code Reference Updates

All backend and frontend references to `douyin_videos` → `videos`:
- Backend: Repository, Service, API router, Celery tasks, Pydantic schemas
- Frontend: dataService.ts, types.ts, components
- API path: `/douyin/` → `/videos/` (with legacy redirect)

### 1.2 Rust Core Module: `mediahub-core`

#### Project Structure

```
mediahub-core/
├── Cargo.toml
├── src/
│   ├── lib.rs              # PyO3 module entry
│   ├── ffmpeg.rs           # FFmpeg wrapper (adapted from douyin-creator-toolkit)
│   │   ├── extract_audio()       # Extract audio → 16kHz mono WAV
│   │   ├── get_video_metadata()  # Duration, resolution, codec, fps
│   │   └── generate_thumbnail()  # Thumbnail extraction
│   ├── hls.rs              # HLS segmentation (replaces faststart)
│   │   ├── segment_video()       # MP4 → HLS fMP4 segments
│   │   ├── is_segmented()        # Check if already segmented
│   │   └── cleanup_segments()    # Clean up segment files
│   └── errors.rs           # Error types (thiserror)
```

#### Reference: douyin-creator-toolkit

Adapted from `/Volumes/program/project-code/github-repos/douyin-creator-toolkit/src-tauri/src/`:
- `utils/ffmpeg.rs` (~819 lines) — FFmpeg command wrapping, metadata parsing, audio extraction
- `core/video_processor.rs` (~622 lines) — High-level video API, batch processing, progress callbacks
- Error handling patterns with `thiserror`

#### Key Functions

**FFmpeg Wrapper** (adapted from toolkit's `ffmpeg.rs`):
```rust
// Audio extraction for Whisper input
pub fn extract_audio(input: &Path, output: &Path, sample_rate: u32) -> Result<AudioResult>
// sample_rate: 16000 for Whisper
// Output: 16-bit PCM, mono, WAV

// Video metadata
pub fn get_video_metadata(input: &Path) -> Result<VideoMetadata>
// Returns: duration_ms, width, height, codec, bitrate, fps

// Thumbnail
pub fn generate_thumbnail(input: &Path, output: &Path, time_seconds: f32) -> Result<()>
```

**HLS Segmentation** (new, replaces faststart):
```rust
// MP4 → HLS fMP4 segments
pub fn segment_video(
    input: &Path,
    output_dir: &Path,
    segment_duration: u32,    // seconds, default 6
    delete_original: bool,
) -> Result<HlsResult>
// Internally calls: ffmpeg -c copy -hls_time 6 -hls_segment_type fmp4 -f hls
// Returns: { playlist, segment_count, duration_ms }
```

#### Python Binding (PyO3)

```python
import mediahub_core

# HLS segmentation
result = mediahub_core.segment_video(
    input_path="/downloads/video.mp4",
    output_dir="/downloads/video_hls/",
    segment_duration=6,
    delete_original=True,
)

# Audio extraction for Whisper
result = mediahub_core.extract_audio(
    input_path="/downloads/video.mp4",
    output_path="/tmp/audio.wav",
    sample_rate=16000,
)

# Video metadata
meta = mediahub_core.get_video_metadata("/downloads/video.mp4")
```

#### Docker Multi-stage Build

```dockerfile
# Stage 1: Rust build
FROM rust:1.77-slim AS rust-builder
RUN pip install maturin
WORKDIR /rust
COPY mediahub-core/ .
RUN maturin build --release

# Stage 2: Final image
FROM python:3.12-slim AS final
COPY --from=rust-builder /rust/target/wheels/*.whl /tmp/
RUN pip install /tmp/mediahub_core*.whl
# ... rest unchanged
```

### 1.3 HLS Streaming Infrastructure

#### Storage Structure

```
/downloads/
├── {platform_id}_hls/          # Per-video HLS directory
│   ├── playlist.m3u8           # HLS playlist
│   ├── init.mp4                # Initialization segment
│   ├── segment_000.m4s         # fMP4 segments
│   ├── segment_001.m4s
│   └── ...
├── covers/                     # Cover images (unchanged)
└── music/                      # Music files (unchanged)
```

#### Processing Pipeline

```
Video downloaded
    ↓
Rust: segment_video()
    ↓ ffmpeg -c copy (no re-encoding, fast)

Input:  /downloads/video.mp4
Output: /downloads/{platform_id}_hls/
    ↓
Delete original MP4 (only keep HLS segments)
    ↓
DB: hls_path = "{platform_id}_hls/playlist.m3u8", media_format = 'hls'
```

#### Nginx Configuration

```nginx
location /stream/ {
    alias /app/downloads/;
    types {
        application/vnd.apple.mpegurl m3u8;
        video/mp4 m4s mp4;
    }
    add_header Cache-Control "public, max-age=31536000";
    add_header Access-Control-Allow-Origin *;
}
```

#### Frontend Playback (hls.js)

```typescript
import Hls from 'hls.js';

if (video.media_format === 'hls') {
    const hls = new Hls();
    hls.loadSource(`${STREAM_URL}/${video.hls_path}`);
    hls.attachMedia(videoElement);
} else {
    // Legacy MP4 fallback
    videoElement.src = getVideoUrl(video);
}
```

---

## Phase 2: yt-dlp + Whisper + LLM Integration

### 2.1 URL Router (Unified Entry)

```python
# services/url_router.py
class URLRouter:
    """Detect URL source, dispatch to handler"""

    def route(url: str) -> tuple[str, str]:
        if "douyin.com" or "iesdouyin.com" in url:
            return ("douyin", "douyin_handler")
        else:
            return ("generic", "ytdlp_handler")
```

User submits URL → URLRouter detects → Douyin: existing flow, Others: yt-dlp. Frontend is unaware.

### 2.2 yt-dlp Download Service

```python
# services/ytdlp_service.py
class YtdlpService:
    """Universal video download via yt-dlp"""

    async def fetch_metadata(url: str) -> dict:
        # yt-dlp --dump-json: title, author, duration, etc.

    async def download_video(url: str, output_path: str) -> str:
        # yt-dlp download, returns file path
```

yt-dlp supports 1000+ websites, no per-platform adapters needed.

### 2.3 Whisper Transcription Service

```python
# services/whisper_service.py
class WhisperService:
    """Speech-to-text, switchable OpenAI API / local model"""

    async def transcribe(audio_path: str) -> TranscriptResult:
        provider = settings.WHISPER_PROVIDER
        if provider == 'openai_api':
            return await self._transcribe_openai(audio_path)
        else:
            return await self._transcribe_local(audio_path)

    async def _transcribe_openai(audio_path: str) -> TranscriptResult:
        # openai.audio.transcriptions.create()
        # Returns timestamped segments

    async def _transcribe_local(audio_path: str) -> TranscriptResult:
        # faster-whisper local inference
```

### 2.4 AI Provider Adapter

```python
# services/ai_provider.py
class AIProvider(ABC):
    """Unified interface for all providers"""
    async def chat(messages, model, **kwargs) -> str: ...
    async def transcribe(audio_path, **kwargs) -> TranscriptResult: ...

# Cloud providers
class OpenAIProvider(AIProvider): ...     # GPT-4o, GPT-4o-mini, Whisper
class DeepSeekProvider(AIProvider): ...   # DeepSeek-V3, R1
class DoubaoProvider(AIProvider): ...     # Doubao (ByteDance)

# Local providers (OpenAI-compatible protocol)
class LMStudioProvider(AIProvider): ...   # http://localhost:1234/v1
class OllamaProvider(AIProvider): ...     # http://localhost:11434/v1

# Factory
class AIProviderFactory:
    providers = {
        "openai": OpenAIProvider,
        "deepseek": DeepSeekProvider,
        "doubao": DoubaoProvider,
        "lmstudio": LMStudioProvider,
        "ollama": OllamaProvider,
    }
```

LM Studio, Ollama, vLLM all support OpenAI-compatible API, so local providers are essentially OpenAI clients with a different `base_url`.

### 2.5 LLM Analysis Service

```python
# services/llm_analysis_service.py
class LLMAnalysisService:
    """Video content analysis"""

    # Basic layer (auto-execute)
    async def generate_summary(transcript: str) -> SummaryResult:
        # LLM: summary + key points + topic classification

    # Advanced layer (manual trigger)
    async def visual_analysis(video_path: str) -> AnalysisResult:
        # Multimodal LLM: keyframe extraction + visual analysis
```

### 2.6 Celery Task Chain

```
download_media_task              # Existing
    ↓ on success
segment_video_task               # New: HLS segmentation (Rust)
    ↓
extract_audio_task               # New: FFmpeg audio extraction (Rust)
    ↓ (if transcript_bool = true)
transcribe_audio_task            # New: Whisper transcription
    ↓ (if summary_bool = true)
generate_summary_task            # New: LLM text summary (basic layer)

# Independent (manual trigger)
visual_analysis_task             # New: Multimodal visual analysis (advanced layer)
```

New Celery queues: `transcription`, `analysis`

### 2.7 Configuration

```yaml
# config.yml
ai:
  providers:
    openai:
      api_key_env: "OPENAI_API_KEY"
    deepseek:
      base_url: "https://api.deepseek.com/v1"
      api_key_env: "DEEPSEEK_API_KEY"
    doubao:
      base_url: "https://ark.cn-beijing.volces.com/api/v3"
      api_key_env: "DOUBAO_API_KEY"
    lmstudio:
      base_url: "http://localhost:1234/v1"
    ollama:
      base_url: "http://localhost:11434/v1"

  # Task assignment (which provider + model per task)
  whisper:
    provider: "openai"
    model: "whisper-1"
  summary:
    provider: "openai"
    model: "gpt-4o-mini"
  visual_analysis:
    provider: "openai"
    model: "gpt-4o"
```

Settings stored in `user_settings` table, overrides config.yml defaults.

---

## Phase 3: Frontend

### 3.1 Settings Page - AI Tab

```
Settings > AI Intelligence

● AI Enabled                          [Disable AI]

Auto-transcribe new videos               [ON]
Auto-summarize after transcription        [ON]
Preferred language                    [Auto ▼]

──────────────────────────────────────────────

● OpenAI                        推荐    [ON]
  Cloud-hosted GPT & Whisper models
  API Key: [sk-••••••••••••••••]
  Whisper Model:  [whisper-1        ▼]
  Summary Model:  [gpt-4o-mini      ▼]
  Analysis Model: [gpt-4o           ▼]

● DeepSeek                             [OFF]
  Cost-effective cloud AI
  API Key: [                        ]
  Model:   [deepseek-v3             ▼]

● Ollama                               [OFF]
  Local models, free & private
  Server URL: [http://localhost:11434]
  Model:      [qwen2.5:7b           ▼]
  [Test Connection]

● LM Studio                           [OFF]
  Local models via LM Studio
  Server URL: [http://localhost:1234]
  Model:      [                      ▼]
  [Test Connection]

──────────────────────────────────────────────
Task Assignment

Transcription:   [OpenAI Whisper     ▼]
Summarization:   [OpenAI GPT-4o-mini ▼]
Visual Analysis: [OpenAI GPT-4o      ▼]

                 [Save Settings]
```

### 3.2 MediaCard AI Status Icons

```
┌─────────────────────────┐
│  [Video Cover]           │
│                    ⏱ 2:30│
├─────────────────────────┤
│  Video title...          │
│  @author · 2024-01-15   │
│  ❤️ 1.2k  💬 89  🔄 56   │
│                         │
│  📝 ✨ 🔍               │  ← AI icon row
│                         │
│  "One-line AI summary..."│  ← Summary preview
└─────────────────────────┘

Icon states:
  Gray   = Not executed
  Spin   = Processing
  Color  = Completed
  Red    = Failed

Icons: 📝 Transcript  ✨ Summary  🔍 Visual Analysis
```

### 3.3 Detail Page Tab Panel

```
Video Detail
┌─────────────────────────────────────────┐
│ [Overview] [Transcript] [Analysis]      │
├─────────────────────────────────────────┤
│                                         │
│ Transcript Tab:                         │
│ ┌─────────────────────────────────────┐ │
│ │ 00:00  Hello everyone, today...     │ │
│ │ 00:05  This topic is very...        │ │
│ │ 00:12  First point is...            │ │
│ │ ...                                 │ │
│ │        [Copy] [Export SRT]          │ │
│ └─────────────────────────────────────┘ │
│                                         │
│ Analysis Tab:                           │
│ ┌─────────────────────────────────────┐ │
│ │ 📋 Summary                          │ │
│ │ This video discusses the latest...  │ │
│ │                                     │ │
│ │ 🔑 Key Points                       │ │
│ │ • Point one...                      │ │
│ │ • Point two...                      │ │
│ │                                     │ │
│ │ 🏷️ Topics                           │ │
│ │ [AI] [Technology] [Tutorial]        │ │
│ │                                     │ │
│ │ 👁️ Visual Analysis                  │ │
│ │ [Trigger Visual Analysis]  ← manual │ │
│ └─────────────────────────────────────┘ │
└─────────────────────────────────────────┘
```

### 3.4 HLS Video Player

```typescript
import Hls from 'hls.js';

// Auto-detect format and play
if (video.media_format === 'hls') {
    if (Hls.isSupported()) {
        const hls = new Hls();
        hls.loadSource(`${STREAM_URL}/${video.hls_path}`);
        hls.attachMedia(videoElement);
    } else if (videoElement.canPlayType('application/vnd.apple.mpegurl')) {
        // Safari native HLS
        videoElement.src = `${STREAM_URL}/${video.hls_path}`;
    }
} else {
    videoElement.src = getVideoUrl(video);
}
```

---

## Docker Deployment

### Updated docker-compose.yml

```yaml
services:
  mediahub:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ${DOWNLOAD_HOST_PATH}:/app/downloads
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  celery-worker:
    build: .
    command: >
      celery -A app.celery_app worker
      -Q downloads,parsing,transcription,analysis,scheduled,celery
      --concurrency=4

  celery-beat:
    build: .
    command: celery -A app.celery_app beat

  nginx:
    image: nginx:alpine
    ports:
      - "8081:80"
    volumes:
      - ${DOWNLOAD_HOST_PATH}:/app/downloads:ro
      - ./nginx-hls.conf:/etc/nginx/conf.d/default.conf
```

### Updated Dockerfile

```dockerfile
# Stage 1: Rust build
FROM rust:1.77-slim AS rust-builder
RUN pip install maturin
WORKDIR /rust
COPY mediahub-core/ .
RUN maturin build --release

# Stage 2: Python app
FROM python:3.12-slim
RUN apt-get update && apt-get install -y ffmpeg
COPY --from=rust-builder /rust/target/wheels/*.whl /tmp/
RUN pip install /tmp/mediahub_core*.whl
RUN pip install yt-dlp faster-whisper
# ... rest unchanged
```

---

## New Backend Dependencies

```toml
# pyproject.toml additions
yt-dlp = ">=2024.1.0"
faster-whisper = ">=1.0.0"          # Optional: local Whisper
mediahub-core = {path = "../mediahub-core"}  # Rust PyO3 module
hls.js = "^1.5.0"                   # Frontend: HLS playback
```

---

## Implementation Order

### Phase 1: Database Refactoring + Rust Core
1. Create `mediahub-core` Rust crate with PyO3
2. Implement `extract_audio()`, `get_video_metadata()`, `segment_video()`
3. Write database migration: rename table, fields, UUID primary key
4. Update all backend code references (Repository → Service → API)
5. Update all frontend code references (dataService → types → components)
6. Replace existing Python subprocess FFmpeg calls with Rust module
7. Add HLS segmentation to download pipeline
8. Update nginx config for HLS serving
9. Add hls.js to frontend, update video player component
10. Verify all existing functionality works (no regression)

### Phase 2: yt-dlp + Whisper + LLM
1. Implement URLRouter (auto-detect source platform)
2. Implement YtdlpService (metadata + download)
3. Implement WhisperService (OpenAI API + local model switch)
4. Implement AIProvider adapter (OpenAI, DeepSeek, Doubao, Ollama, LM Studio)
5. Implement LLMAnalysisService (basic summary + visual analysis)
6. Create Celery task chain (extract → transcribe → summarize)
7. Create video_transcripts and video_summaries tables
8. Add API endpoints for transcription and analysis
9. Build Settings AI configuration page (frontend)

### Phase 3: Frontend Polish
1. Add AI status icons to MediaCard
2. Build video detail page with Tab panel (Overview / Transcript / Analysis)
3. Implement transcript display with timestamps
4. Add transcript export (SRT / TXT)
5. Implement visual analysis trigger button
6. Add summary preview to MediaCard
