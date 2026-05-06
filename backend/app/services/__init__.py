# app/services/__init__.py
"""
业务逻辑层模块。

After the 2026-05-06 reorg, files live under sub-packages:
  ai/        — agent runner, chat, prompts, skills, providers, llm, transcribe, summarize, visual, billing
  media/     — parsers, downloader, transcode, render
  library/   — resources / projects / collections / etc
  storyboard/, billing/, infra/, workforce/

The re-exports below preserve historical `from app.services import X`
import shape so callers don't have to chase paths. New code should
import from the sub-package directly.
"""

from app.services.media.parsers.douyin_parse import (
    ABogusDouyinParser,
    DouyinFormatter,
    DrissionPageParser,
    IesDouyinParser,
)
from app.services.media.downloader.downloader import DownloaderService
from app.services.ai.providers.embedding_service import EmbeddingService
from app.services.media.parsers.media_service import MediaService
from app.services.infra.supabase_auth_service import (
    SupabaseAdminAuthService,
    SupabaseAuthService,
)
from app.services.ai.visual.visual_analysis_service import (
    VisualAnalysisResult,
    VisualAnalysisService,
)

__all__ = [
    "ABogusDouyinParser",
    "DouyinFormatter",
    "DrissionPageParser",
    "IesDouyinParser",
    "DownloaderService",
    "MediaService",
    "SupabaseAuthService",
    "SupabaseAdminAuthService",
    "VisualAnalysisService",
    "VisualAnalysisResult",
    "EmbeddingService",
]
