# Smart Organization Phase 4: AI Visual Analysis

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement tiered visual analysis (L1/L2/L3) for video content using multimodal AI.

**Architecture:**
- L1 (cover analysis) runs automatically after download
- L2/L3 triggered via API on demand
- Results stored in video_analysis table
- Embeddings generated for semantic search

**Tech Stack:**
- OpenAI GPT-4o for visual analysis
- OpenAI text-embedding-3-small for embeddings
- FFmpeg for keyframe extraction (L2)
- Celery for async processing

---

## Task 4.1: Create OpenAI Service Configuration

**Files:**
- Modify: `backend/app/core/config.py`
- Modify: `backend/.env.example`

**Step 1: Add OpenAI configuration to config.py**

Add to the config loading section:

```python
# OpenAI Configuration
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
OPENAI_EMBEDDING_MODEL: str = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
```

**Step 2: Update .env.example**

Add:
```
# OpenAI Configuration
OPENAI_API_KEY=sk-your-api-key
OPENAI_MODEL=gpt-4o
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

**Step 3: Commit**

```bash
git add backend/app/core/config.py backend/.env.example
git commit -m "feat(config): add OpenAI configuration"
```

---

## Task 4.2: Create Analysis Repository

**Files:**
- Create: `backend/app/repositories/analysis_repository.py`

**Step 1: Create repository file**

```python
"""Repository for Video Analysis data access."""
from typing import Optional, List
from datetime import datetime

from app.db.supabase_client import get_supabase_admin
from loguru import logger


class AnalysisRepository:
    """Repository for video analysis CRUD operations."""

    def __init__(self):
        self.supabase = get_supabase_admin()

    async def get_analysis(self, video_id: int) -> Optional[dict]:
        """Get analysis for a video."""
        result = self.supabase.table("video_analysis").select("*").eq("video_id", video_id).maybe_single().execute()
        return result.data

    async def create_analysis(self, video_id: int, **kwargs) -> dict:
        """Create analysis record for a video."""
        data = {
            "video_id": video_id,
            "analysis_level": kwargs.get("analysis_level", "none"),
            "visual_description": kwargs.get("visual_description"),
            "detected_objects": kwargs.get("detected_objects", []),
            "detected_scenes": kwargs.get("detected_scenes", []),
            "detected_people": kwargs.get("detected_people", []),
            "detected_text": kwargs.get("detected_text"),
            "full_text_for_embedding": kwargs.get("full_text_for_embedding"),
            "analysis_model": kwargs.get("analysis_model"),
            "analysis_cost": kwargs.get("analysis_cost", 0),
            "analyzed_at": datetime.utcnow().isoformat(),
        }

        # Filter out None values
        data = {k: v for k, v in data.items() if v is not None}

        result = self.supabase.table("video_analysis").insert(data).execute()
        logger.info(f"Created analysis for video {video_id}")
        return result.data[0]

    async def update_analysis(self, video_id: int, **kwargs) -> Optional[dict]:
        """Update analysis record."""
        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await self.get_analysis(video_id)

        update_data["analyzed_at"] = datetime.utcnow().isoformat()

        result = self.supabase.table("video_analysis").update(update_data).eq("video_id", video_id).execute()
        return result.data[0] if result.data else None

    async def upsert_analysis(self, video_id: int, **kwargs) -> dict:
        """Create or update analysis record."""
        existing = await self.get_analysis(video_id)

        if existing:
            return await self.update_analysis(video_id, **kwargs)
        else:
            return await self.create_analysis(video_id, **kwargs)

    async def update_embedding(self, video_id: int, embedding: List[float], full_text: str) -> dict:
        """Update the embedding vector for a video."""
        # Convert list to PostgreSQL vector format
        embedding_str = f"[{','.join(map(str, embedding))}]"

        result = self.supabase.table("video_analysis").update({
            "content_embedding": embedding_str,
            "full_text_for_embedding": full_text,
        }).eq("video_id", video_id).execute()

        logger.info(f"Updated embedding for video {video_id}")
        return result.data[0] if result.data else None

    async def get_videos_without_analysis(self, limit: int = 100) -> List[dict]:
        """Get videos that don't have analysis yet."""
        # Get video IDs that have analysis
        analyzed = self.supabase.table("video_analysis").select("video_id").execute()
        analyzed_ids = [r["video_id"] for r in analyzed.data]

        # Get videos not in that list
        query = self.supabase.table("douyin_videos").select("id, title, desc, cover_url").limit(limit)

        if analyzed_ids:
            query = query.not_.in_("id", analyzed_ids)

        result = query.execute()
        return result.data

    async def get_videos_by_analysis_level(self, level: str, limit: int = 100) -> List[dict]:
        """Get videos with a specific analysis level."""
        result = self.supabase.table("video_analysis").select(
            "*, douyin_videos(id, title, desc, cover_url)"
        ).eq("analysis_level", level).limit(limit).execute()

        return result.data

    async def search_by_embedding(self, embedding: List[float], limit: int = 10, threshold: float = 0.7) -> List[dict]:
        """Search for similar videos using vector similarity."""
        # Use Supabase's vector similarity search via RPC
        embedding_str = f"[{','.join(map(str, embedding))}]"

        result = self.supabase.rpc(
            "match_videos_by_embedding",
            {
                "query_embedding": embedding_str,
                "match_threshold": threshold,
                "match_count": limit
            }
        ).execute()

        return result.data
```

**Step 2: Commit**

```bash
git add backend/app/repositories/analysis_repository.py
git commit -m "feat(api): add analysis repository for video analysis data"
```

---

## Task 4.3: Create Visual Analysis Service

**Files:**
- Create: `backend/app/services/visual_analysis_service.py`

**Step 1: Create service file**

```python
"""Visual analysis service using OpenAI GPT-4o."""
import os
import base64
import httpx
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

from openai import AsyncOpenAI
from loguru import logger


@dataclass
class VisualAnalysisResult:
    """Result of visual analysis."""
    category: str
    visual_description: str
    detected_objects: List[str] = field(default_factory=list)
    detected_scenes: List[str] = field(default_factory=list)
    detected_people: List[Dict[str, str]] = field(default_factory=list)
    detected_text: str = ""
    mood: str = ""
    cost: float = 0.0


# Prompts for different analysis levels
L1_PROMPT = """Analyze this video cover image and provide:

1. Main content category (choose ONE): Food, Tutorial, Comedy, Dance, Music, Beauty, Fashion, Gaming, Pets, Travel, Tech, Sports, Vlog, Other
2. Brief visual description (1-2 sentences)
3. Key objects visible (list up to 5)
4. Scene type (indoor/outdoor, specific location if identifiable)
5. People description if any (gender, clothing, action)
6. Any visible text (OCR)
7. Overall mood/style

Respond in JSON format:
{
  "category": "",
  "visual_description": "",
  "detected_objects": [],
  "detected_scenes": [],
  "detected_people": [{"gender": "", "clothing": "", "action": ""}],
  "detected_text": "",
  "mood": ""
}"""

L2_PROMPT = """Analyze these video keyframes (cover + 3 frames at 25%, 50%, 75% of video).

Provide a comprehensive analysis:
1. Main content category
2. Detailed visual description covering all frames
3. All objects visible across frames
4. Scene transitions or changes
5. People and their actions throughout
6. Any text visible (subtitles, captions, on-screen text)
7. Overall narrative/content summary

Respond in JSON format:
{
  "category": "",
  "visual_description": "",
  "detected_objects": [],
  "detected_scenes": [],
  "detected_people": [{"gender": "", "clothing": "", "action": ""}],
  "detected_text": "",
  "mood": "",
  "content_summary": ""
}"""


class VisualAnalysisService:
    """Service for analyzing video content using AI."""

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, visual analysis will be disabled")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=api_key)

        self.model = os.getenv("OPENAI_MODEL", "gpt-4o")

    async def _encode_image_from_url(self, url: str) -> Optional[str]:
        """Download and encode image to base64."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, timeout=30.0)
                if response.status_code == 200:
                    return base64.b64encode(response.content).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to download image from {url}: {e}")
        return None

    async def _encode_image_from_file(self, file_path: str) -> Optional[str]:
        """Read and encode local image to base64."""
        try:
            with open(file_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.error(f"Failed to read image from {file_path}: {e}")
        return None

    async def analyze_l1(self, cover_url: str) -> Optional[VisualAnalysisResult]:
        """
        L1 Analysis: Cover image only.
        Cost: ~$0.001 per image
        """
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping L1 analysis")
            return None

        image_data = await self._encode_image_from_url(cover_url)
        if not image_data:
            logger.error(f"Failed to encode cover image: {cover_url}")
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": L1_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{image_data}",
                                    "detail": "low"  # Low detail for cost efficiency
                                }
                            }
                        ]
                    }
                ],
                max_tokens=500,
                response_format={"type": "json_object"}
            )

            # Parse response
            import json
            content = response.choices[0].message.content
            data = json.loads(content)

            # Calculate cost (approximate)
            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            cost = (input_tokens * 0.0025 + output_tokens * 0.01) / 1000  # GPT-4o pricing

            return VisualAnalysisResult(
                category=data.get("category", "Other"),
                visual_description=data.get("visual_description", ""),
                detected_objects=data.get("detected_objects", []),
                detected_scenes=data.get("detected_scenes", []),
                detected_people=data.get("detected_people", []),
                detected_text=data.get("detected_text", ""),
                mood=data.get("mood", ""),
                cost=cost
            )

        except Exception as e:
            logger.error(f"L1 analysis failed: {e}")
            return None

    async def analyze_l2(self, cover_url: str, keyframe_paths: List[str]) -> Optional[VisualAnalysisResult]:
        """
        L2 Analysis: Cover + keyframes.
        Cost: ~$0.005 per video
        """
        if not self.client:
            return None

        # Encode all images
        images = []

        cover_data = await self._encode_image_from_url(cover_url)
        if cover_data:
            images.append(f"data:image/jpeg;base64,{cover_data}")

        for path in keyframe_paths:
            frame_data = await self._encode_image_from_file(path)
            if frame_data:
                images.append(f"data:image/jpeg;base64,{frame_data}")

        if not images:
            logger.error("No images available for L2 analysis")
            return None

        try:
            content = [{"type": "text", "text": L2_PROMPT}]
            for img in images:
                content.append({
                    "type": "image_url",
                    "image_url": {"url": img, "detail": "low"}
                })

            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=800,
                response_format={"type": "json_object"}
            )

            import json
            data = json.loads(response.choices[0].message.content)

            input_tokens = response.usage.prompt_tokens
            output_tokens = response.usage.completion_tokens
            cost = (input_tokens * 0.0025 + output_tokens * 0.01) / 1000

            return VisualAnalysisResult(
                category=data.get("category", "Other"),
                visual_description=data.get("visual_description", ""),
                detected_objects=data.get("detected_objects", []),
                detected_scenes=data.get("detected_scenes", []),
                detected_people=data.get("detected_people", []),
                detected_text=data.get("detected_text", ""),
                mood=data.get("mood", ""),
                cost=cost
            )

        except Exception as e:
            logger.error(f"L2 analysis failed: {e}")
            return None
```

**Step 2: Commit**

```bash
git add backend/app/services/visual_analysis_service.py
git commit -m "feat(service): add visual analysis service with OpenAI GPT-4o"
```

---

## Task 4.4: Create Embedding Service

**Files:**
- Create: `backend/app/services/embedding_service.py`

**Step 1: Create service file**

```python
"""Embedding generation service using OpenAI."""
import os
from typing import Optional, List

from openai import AsyncOpenAI
from loguru import logger


class EmbeddingService:
    """Service for generating text embeddings."""

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("OPENAI_API_KEY not set, embedding generation will be disabled")
            self.client = None
        else:
            self.client = AsyncOpenAI(api_key=api_key)

        self.model = os.getenv("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")

    async def generate_embedding(self, text: str) -> Optional[List[float]]:
        """
        Generate embedding vector for text.

        Returns 1536-dimensional vector for text-embedding-3-small.
        Cost: ~$0.00002 per 1000 tokens
        """
        if not self.client:
            logger.warning("OpenAI client not initialized, skipping embedding generation")
            return None

        if not text or not text.strip():
            logger.warning("Empty text provided for embedding")
            return None

        try:
            # Truncate if too long (max 8191 tokens for text-embedding-3-small)
            # Approximate: 1 token ≈ 4 characters for English, 2 for Chinese
            max_chars = 16000
            if len(text) > max_chars:
                text = text[:max_chars]
                logger.info(f"Truncated text to {max_chars} characters for embedding")

            response = await self.client.embeddings.create(
                model=self.model,
                input=text,
                encoding_format="float"
            )

            embedding = response.data[0].embedding
            logger.debug(f"Generated embedding with {len(embedding)} dimensions")
            return embedding

        except Exception as e:
            logger.error(f"Embedding generation failed: {e}")
            return None

    def build_embedding_text(
        self,
        title: str,
        description: str = "",
        author: str = "",
        tags: List[str] = None,
        visual_description: str = "",
        detected_objects: List[str] = None,
        detected_scenes: List[str] = None,
        detected_text: str = ""
    ) -> str:
        """
        Build the text content for embedding generation.
        Combines all available metadata into a single text.
        """
        parts = []

        if title:
            parts.append(f"Title: {title}")

        if description:
            parts.append(f"Description: {description}")

        if author:
            parts.append(f"Author: {author}")

        if tags:
            parts.append(f"Tags: {', '.join(tags)}")

        if visual_description:
            parts.append(f"Visual: {visual_description}")

        if detected_objects:
            parts.append(f"Objects: {', '.join(detected_objects)}")

        if detected_scenes:
            parts.append(f"Scenes: {', '.join(detected_scenes)}")

        if detected_text:
            parts.append(f"Text in video: {detected_text}")

        return "\n".join(parts)
```

**Step 2: Commit**

```bash
git add backend/app/services/embedding_service.py
git commit -m "feat(service): add embedding generation service"
```

---

## Task 4.5: Create Analysis Celery Task

**Files:**
- Create: `backend/app/tasks/analysis_tasks.py`

**Step 1: Create task file**

```python
"""Celery tasks for video analysis."""
import asyncio
from typing import Optional

from celery import shared_task
from loguru import logger

from app.repositories.analysis_repository import AnalysisRepository
from app.repositories.tags_repository import TagsRepository
from app.services.visual_analysis_service import VisualAnalysisService
from app.services.embedding_service import EmbeddingService


def run_async(coro):
    """Helper to run async code in Celery task."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def analyze_video_l1_task(self, video_id: int, cover_url: str, title: str = "", description: str = ""):
    """
    L1 Analysis: Analyze video cover image.

    This task:
    1. Analyzes the cover image using GPT-4o
    2. Stores results in video_analysis table
    3. Updates tags based on detected category
    4. Generates embedding for semantic search
    """
    logger.info(f"Starting L1 analysis for video {video_id}")

    async def _analyze():
        analysis_service = VisualAnalysisService()
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        # Run visual analysis
        result = await analysis_service.analyze_l1(cover_url)

        if not result:
            logger.warning(f"L1 analysis returned no result for video {video_id}")
            return None

        # Store analysis results
        await analysis_repo.upsert_analysis(
            video_id=video_id,
            analysis_level="L1",
            visual_description=result.visual_description,
            detected_objects=result.detected_objects,
            detected_scenes=result.detected_scenes,
            detected_people=result.detected_people,
            detected_text=result.detected_text,
            analysis_model="gpt-4o",
            analysis_cost=result.cost
        )

        # Add tag based on detected category
        if result.category and result.category != "Other":
            tag = await tags_repo.get_tag_by_name(result.category)
            if tag:
                await tags_repo.add_tag_to_video(
                    video_id=video_id,
                    tag_id=tag["id"],
                    confidence=0.8,  # AI-based confidence
                    source="ai"
                )
                logger.info(f"Added AI tag '{result.category}' to video {video_id}")

        # Generate embedding
        # Get existing tags for this video
        video_tags = await tags_repo.get_video_tags(video_id)
        tag_names = [t["tags"]["name"] for t in video_tags if t.get("tags")]

        embedding_text = embedding_service.build_embedding_text(
            title=title,
            description=description,
            tags=tag_names,
            visual_description=result.visual_description,
            detected_objects=result.detected_objects,
            detected_scenes=result.detected_scenes,
            detected_text=result.detected_text
        )

        embedding = await embedding_service.generate_embedding(embedding_text)

        if embedding:
            await analysis_repo.update_embedding(video_id, embedding, embedding_text)
            logger.info(f"Generated embedding for video {video_id}")

        logger.info(f"Completed L1 analysis for video {video_id}, cost: ${result.cost:.4f}")
        return {
            "video_id": video_id,
            "category": result.category,
            "cost": result.cost
        }

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L1 analysis failed for video {video_id}: {e}")
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=2, default_retry_delay=120)
def analyze_video_l2_task(self, video_id: int, cover_url: str, video_path: str, title: str = "", description: str = ""):
    """
    L2 Analysis: Analyze cover + keyframes.

    This task:
    1. Extracts keyframes from video using FFmpeg
    2. Analyzes cover + keyframes using GPT-4o
    3. Updates analysis record
    4. Regenerates embedding with richer data
    """
    import subprocess
    import tempfile
    import os

    logger.info(f"Starting L2 analysis for video {video_id}")

    async def _analyze():
        analysis_service = VisualAnalysisService()
        embedding_service = EmbeddingService()
        analysis_repo = AnalysisRepository()
        tags_repo = TagsRepository()

        # Extract keyframes using FFmpeg
        keyframe_paths = []

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                # Get video duration
                probe_cmd = [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    video_path
                ]
                duration = float(subprocess.check_output(probe_cmd).decode().strip())

                # Extract frames at 25%, 50%, 75%
                for i, pct in enumerate([0.25, 0.50, 0.75]):
                    timestamp = duration * pct
                    output_path = os.path.join(tmpdir, f"frame_{i}.jpg")

                    extract_cmd = [
                        "ffmpeg", "-y", "-ss", str(timestamp),
                        "-i", video_path,
                        "-vframes", "1",
                        "-q:v", "2",
                        output_path
                    ]
                    subprocess.run(extract_cmd, capture_output=True, check=True)

                    if os.path.exists(output_path):
                        keyframe_paths.append(output_path)

                logger.info(f"Extracted {len(keyframe_paths)} keyframes for video {video_id}")

                # Run L2 analysis
                result = await analysis_service.analyze_l2(cover_url, keyframe_paths)

                if not result:
                    logger.warning(f"L2 analysis returned no result for video {video_id}")
                    return None

                # Update analysis results
                await analysis_repo.upsert_analysis(
                    video_id=video_id,
                    analysis_level="L2",
                    visual_description=result.visual_description,
                    detected_objects=result.detected_objects,
                    detected_scenes=result.detected_scenes,
                    detected_people=result.detected_people,
                    detected_text=result.detected_text,
                    analysis_model="gpt-4o",
                    analysis_cost=result.cost
                )

                # Update tag if category changed
                if result.category and result.category != "Other":
                    tag = await tags_repo.get_tag_by_name(result.category)
                    if tag:
                        await tags_repo.add_tag_to_video(
                            video_id=video_id,
                            tag_id=tag["id"],
                            confidence=0.9,  # Higher confidence for L2
                            source="ai"
                        )

                # Regenerate embedding
                video_tags = await tags_repo.get_video_tags(video_id)
                tag_names = [t["tags"]["name"] for t in video_tags if t.get("tags")]

                embedding_text = embedding_service.build_embedding_text(
                    title=title,
                    description=description,
                    tags=tag_names,
                    visual_description=result.visual_description,
                    detected_objects=result.detected_objects,
                    detected_scenes=result.detected_scenes,
                    detected_text=result.detected_text
                )

                embedding = await embedding_service.generate_embedding(embedding_text)

                if embedding:
                    await analysis_repo.update_embedding(video_id, embedding, embedding_text)

                logger.info(f"Completed L2 analysis for video {video_id}, cost: ${result.cost:.4f}")
                return {
                    "video_id": video_id,
                    "category": result.category,
                    "cost": result.cost
                }

        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg failed for video {video_id}: {e}")
            return None

    try:
        return run_async(_analyze())
    except Exception as e:
        logger.error(f"L2 analysis failed for video {video_id}: {e}")
        raise self.retry(exc=e)


@shared_task
def batch_analyze_l1_task(video_ids: list, batch_size: int = 10):
    """
    Batch L1 analysis for multiple videos.
    Dispatches individual L1 tasks.
    """
    from app.db.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()

    for video_id in video_ids[:batch_size]:
        # Get video info
        result = supabase.table("douyin_videos").select(
            "id, title, desc, cover_url"
        ).eq("id", video_id).maybe_single().execute()

        if result.data:
            video = result.data
            analyze_video_l1_task.delay(
                video_id=video["id"],
                cover_url=video.get("cover_url", ""),
                title=video.get("title", ""),
                description=video.get("desc", "")
            )
            logger.info(f"Dispatched L1 analysis for video {video_id}")
```

**Step 2: Commit**

```bash
git add backend/app/tasks/analysis_tasks.py
git commit -m "feat(tasks): add Celery tasks for video analysis"
```

---

## Task 4.6: Create Analysis API Router

**Files:**
- Create: `backend/app/api/analysis_router.py`

**Step 1: Create router file**

```python
"""API routes for Video Analysis."""
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status, BackgroundTasks
from pydantic import BaseModel
from loguru import logger

from app.core.dependencies import AuthDep
from app.repositories.analysis_repository import AnalysisRepository
from app.tasks.analysis_tasks import analyze_video_l1_task, analyze_video_l2_task, batch_analyze_l1_task


router = APIRouter(prefix="/analysis", tags=["Analysis"])


# Response schemas
class AnalysisResponse(BaseModel):
    video_id: int
    analysis_level: str
    visual_description: Optional[str]
    detected_objects: List[str]
    detected_scenes: List[str]
    detected_people: List[dict]
    detected_text: Optional[str]
    analysis_model: Optional[str]
    analysis_cost: float
    analyzed_at: Optional[str]


class AnalysisStatsResponse(BaseModel):
    total_videos: int
    analyzed_videos: int
    by_level: dict
    total_cost: float


class AnalyzeRequest(BaseModel):
    level: str = "L1"  # L1, L2, or L3


class BatchAnalyzeRequest(BaseModel):
    video_ids: List[int]
    level: str = "L1"


@router.get("/{video_id}", response_model=AnalysisResponse)
async def get_video_analysis(
    video_id: int,
    auth: AuthDep,
):
    """Get analysis results for a video."""
    repo = AnalysisRepository()
    analysis = await repo.get_analysis(video_id)

    if not analysis:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Analysis not found for this video"
        )

    return analysis


@router.post("/{video_id}/analyze", status_code=status.HTTP_202_ACCEPTED)
async def trigger_analysis(
    video_id: int,
    request: AnalyzeRequest,
    auth: AuthDep,
):
    """
    Trigger analysis for a video.

    - L1: Cover image analysis (fast, ~$0.001)
    - L2: Cover + keyframes analysis (requires downloaded video, ~$0.005)
    - L3: Full video analysis (manual, ~$0.05)
    """
    from app.db.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()

    # Get video info
    result = supabase.table("douyin_videos").select(
        "id, title, desc, cover_url, download_path"
    ).eq("id", video_id).maybe_single().execute()

    if not result.data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Video not found"
        )

    video = result.data

    if request.level == "L1":
        if not video.get("cover_url"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video has no cover URL"
            )

        task = analyze_video_l1_task.delay(
            video_id=video_id,
            cover_url=video["cover_url"],
            title=video.get("title", ""),
            description=video.get("desc", "")
        )

        return {
            "message": "L1 analysis started",
            "task_id": task.id,
            "video_id": video_id
        }

    elif request.level == "L2":
        if not video.get("download_path"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Video file not downloaded yet"
            )

        # Get full path
        from app.core.utils import Utils
        base_path = Utils.get_download_base_path()
        video_path = f"{base_path}/{video['download_path']}"

        task = analyze_video_l2_task.delay(
            video_id=video_id,
            cover_url=video.get("cover_url", ""),
            video_path=video_path,
            title=video.get("title", ""),
            description=video.get("desc", "")
        )

        return {
            "message": "L2 analysis started",
            "task_id": task.id,
            "video_id": video_id
        }

    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid analysis level: {request.level}. Use L1 or L2."
        )


@router.post("/batch", status_code=status.HTTP_202_ACCEPTED)
async def trigger_batch_analysis(
    request: BatchAnalyzeRequest,
    auth: AuthDep,
):
    """Trigger batch analysis for multiple videos."""
    if len(request.video_ids) > 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Maximum 100 videos per batch"
        )

    task = batch_analyze_l1_task.delay(
        video_ids=request.video_ids,
        batch_size=len(request.video_ids)
    )

    return {
        "message": f"Batch {request.level} analysis started for {len(request.video_ids)} videos",
        "task_id": task.id
    }


@router.get("/stats", response_model=AnalysisStatsResponse)
async def get_analysis_stats(auth: AuthDep):
    """Get analysis statistics."""
    from app.db.supabase_client import get_supabase_admin

    supabase = get_supabase_admin()

    # Total videos
    total_result = supabase.table("douyin_videos").select("id", count="exact").execute()
    total_videos = total_result.count or 0

    # Analyzed videos
    analysis_result = supabase.table("video_analysis").select(
        "analysis_level, analysis_cost"
    ).execute()

    analyzed_videos = len(analysis_result.data)

    # Group by level
    by_level = {"none": 0, "L1": 0, "L2": 0, "L3": 0}
    total_cost = 0.0

    for item in analysis_result.data:
        level = item.get("analysis_level", "none")
        by_level[level] = by_level.get(level, 0) + 1
        total_cost += item.get("analysis_cost", 0) or 0

    by_level["none"] = total_videos - analyzed_videos

    return AnalysisStatsResponse(
        total_videos=total_videos,
        analyzed_videos=analyzed_videos,
        by_level=by_level,
        total_cost=round(total_cost, 4)
    )


@router.get("/queue")
async def get_analysis_queue(
    limit: int = Query(50, le=100),
    auth: AuthDep,
):
    """Get videos pending analysis."""
    repo = AnalysisRepository()
    videos = await repo.get_videos_without_analysis(limit=limit)

    return {
        "pending_count": len(videos),
        "videos": videos
    }
```

**Step 2: Register router in main.py**

Add to imports:
```python
from app.api.analysis_router import router as analysis_router
```

Add to router registration:
```python
app.include_router(analysis_router, prefix="/api/v1")
```

**Step 3: Commit**

```bash
git add backend/app/api/analysis_router.py backend/app/main.py
git commit -m "feat(api): add analysis router with L1/L2 endpoints"
```

---

## Task 4.7: Create Vector Search Function in Database

**Files:**
- Create: `supabase/migrations/018_create_vector_search_function.sql`

**Step 1: Create migration file**

```sql
-- Function for vector similarity search
CREATE OR REPLACE FUNCTION match_videos_by_embedding(
    query_embedding vector(1536),
    match_threshold float DEFAULT 0.7,
    match_count int DEFAULT 10
)
RETURNS TABLE (
    video_id bigint,
    title text,
    description text,
    cover_url text,
    similarity float
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        va.video_id,
        dv.title,
        dv.desc as description,
        dv.cover_url,
        1 - (va.content_embedding <=> query_embedding) as similarity
    FROM video_analysis va
    JOIN douyin_videos dv ON dv.id = va.video_id
    WHERE va.content_embedding IS NOT NULL
    AND 1 - (va.content_embedding <=> query_embedding) > match_threshold
    ORDER BY va.content_embedding <=> query_embedding
    LIMIT match_count;
END;
$$;
```

**Step 2: Apply migration**

```bash
mcp__supabase__apply_migration with name: "create_vector_search_function"
```

**Step 3: Commit**

```bash
git add supabase/migrations/018_create_vector_search_function.sql
git commit -m "feat(db): add vector search function for semantic search"
```

---

## Task 4.8: Integrate L1 Analysis into Download Flow

**Files:**
- Modify: `backend/app/tasks/parse_tasks.py`

**Step 1: Add L1 analysis trigger after download completes**

Find the section where download task is triggered and add:

```python
# After download task is dispatched, also trigger L1 analysis
from app.tasks.analysis_tasks import analyze_video_l1_task

# Trigger L1 analysis (non-blocking)
try:
    if cover_url:
        analyze_video_l1_task.delay(
            video_id=video_db_id,
            cover_url=cover_url,
            title=video_title or "",
            description=video.get("desc", "")
        )
        logger.info(f"Triggered L1 analysis for video {aweme_id}")
except Exception as e:
    logger.warning(f"Failed to trigger L1 analysis for {aweme_id}: {e}")
```

**Step 2: Commit**

```bash
git add backend/app/tasks/parse_tasks.py
git commit -m "feat(tasks): trigger L1 analysis automatically after download"
```

---

## Checkpoint: Phase 4 Complete

At this point you should have:

- [x] OpenAI configuration
- [x] Analysis repository
- [x] Visual analysis service (L1/L2)
- [x] Embedding service
- [x] Analysis Celery tasks
- [x] Analysis API router
- [x] Vector search function
- [x] L1 auto-trigger on download

**Verification:**

```bash
# Check API endpoint
curl -X POST http://localhost:8080/api/v1/analysis/123/analyze \
  -H "Content-Type: application/json" \
  -d '{"level": "L1"}'

# Check stats
curl http://localhost:8080/api/v1/analysis/stats
```
