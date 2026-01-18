# Smart Organization System - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an intelligent content organization system with auto-tagging, semantic search, smart collections, and cleanup suggestions.

**Architecture:** Virtual organization layer on top of existing physical storage. Tags and collections are database records (no file movement). AI-powered classification with tiered visual analysis. Vector embeddings for semantic search.

**Tech Stack:**
- Backend: FastAPI, Supabase (PostgreSQL + pgvector), Celery
- AI: OpenAI GPT-4o (classification/analysis), text-embedding-3-small (embeddings)
- Frontend: React 19, TypeScript, TailwindCSS

**PRD Reference:** `docs/plans/2026-01-19-smart-organization-design.md`

---

## Phase 1: Database Foundation

### Task 1.1: Enable pgvector Extension

**Files:**
- Create: `supabase/migrations/011_enable_pgvector.sql`

**Step 1: Create migration file**

```sql
-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;
```

**Step 2: Apply migration via Supabase MCP**

Run: `mcp__supabase__apply_migration` with:
- project_id: (get from list_projects)
- name: "enable_pgvector"
- query: (the SQL above)

**Step 3: Verify extension enabled**

Run: `mcp__supabase__execute_sql` with:
```sql
SELECT * FROM pg_extension WHERE extname = 'vector';
```
Expected: One row returned

**Step 4: Commit**

```bash
git add supabase/migrations/011_enable_pgvector.sql
git commit -m "feat(db): enable pgvector extension for semantic search"
```

---

### Task 1.2: Create Tags Table

**Files:**
- Create: `supabase/migrations/012_create_tags_table.sql`

**Step 1: Create migration file**

```sql
-- Tags table for system and user-defined tags
CREATE TABLE tags (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(50) NOT NULL,
    type VARCHAR(20) NOT NULL CHECK (type IN ('system', 'user', 'time')),
    color VARCHAR(20) DEFAULT '#6366f1',
    icon VARCHAR(50),
    user_id UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

    CONSTRAINT unique_tag_per_scope UNIQUE(name, type, user_id)
);

-- Indexes
CREATE INDEX idx_tags_type ON tags(type);
CREATE INDEX idx_tags_user ON tags(user_id) WHERE user_id IS NOT NULL;

-- RLS Policies
ALTER TABLE tags ENABLE ROW LEVEL SECURITY;

-- System tags visible to all authenticated users
CREATE POLICY "System tags visible to all" ON tags
    FOR SELECT USING (type = 'system' OR type = 'time');

-- User tags visible only to owner
CREATE POLICY "User tags visible to owner" ON tags
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Users can create own tags" ON tags
    FOR INSERT WITH CHECK (user_id = auth.uid() AND type = 'user');

CREATE POLICY "Users can update own tags" ON tags
    FOR UPDATE USING (user_id = auth.uid() AND type = 'user');

CREATE POLICY "Users can delete own tags" ON tags
    FOR DELETE USING (user_id = auth.uid() AND type = 'user');

-- Insert predefined system tags
INSERT INTO tags (name, type, color, icon) VALUES
    ('Food', 'system', '#ef4444', '🍕'),
    ('Tutorial', 'system', '#3b82f6', '📚'),
    ('Comedy', 'system', '#eab308', '😂'),
    ('Dance', 'system', '#ec4899', '💃'),
    ('Music', 'system', '#8b5cf6', '🎵'),
    ('Beauty', 'system', '#f472b6', '💄'),
    ('Fashion', 'system', '#06b6d4', '👗'),
    ('Gaming', 'system', '#22c55e', '🎮'),
    ('Pets', 'system', '#f97316', '🐱'),
    ('Travel', 'system', '#14b8a6', '✈️'),
    ('Tech', 'system', '#6366f1', '💻'),
    ('Sports', 'system', '#84cc16', '⚽'),
    ('Vlog', 'system', '#a855f7', '📹'),
    ('Other', 'system', '#64748b', '📦');
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "create_tags_table"

**Step 3: Verify table created**

Run: `mcp__supabase__list_tables` with schemas: ["public"]
Expected: `tags` table in list

**Step 4: Verify system tags inserted**

Run: `mcp__supabase__execute_sql`:
```sql
SELECT name, type, color FROM tags WHERE type = 'system' ORDER BY name;
```
Expected: 14 system tags returned

**Step 5: Commit**

```bash
git add supabase/migrations/012_create_tags_table.sql
git commit -m "feat(db): create tags table with system tags"
```

---

### Task 1.3: Create Video-Tags Association Table

**Files:**
- Create: `supabase/migrations/013_create_video_tags_table.sql`

**Step 1: Create migration file**

```sql
-- Video-Tag many-to-many association
CREATE TABLE video_tags (
    video_id UUID NOT NULL REFERENCES douyin_videos(id) ON DELETE CASCADE,
    tag_id UUID NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    confidence FLOAT CHECK (confidence >= 0 AND confidence <= 1),
    source VARCHAR(20) CHECK (source IN ('auto', 'manual', 'ai')) DEFAULT 'manual',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),

    PRIMARY KEY (video_id, tag_id)
);

-- Indexes for efficient queries
CREATE INDEX idx_video_tags_video ON video_tags(video_id);
CREATE INDEX idx_video_tags_tag ON video_tags(tag_id);
CREATE INDEX idx_video_tags_source ON video_tags(source);

-- RLS Policies
ALTER TABLE video_tags ENABLE ROW LEVEL SECURITY;

-- Users can view tags on videos they own
CREATE POLICY "View tags on owned videos" ON video_tags
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Users can add tags to videos they own
CREATE POLICY "Add tags to owned videos" ON video_tags
    FOR INSERT WITH CHECK (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Users can remove tags from videos they own
CREATE POLICY "Remove tags from owned videos" ON video_tags
    FOR DELETE USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_tags.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "create_video_tags_table"

**Step 3: Verify table created**

Run: `mcp__supabase__execute_sql`:
```sql
SELECT column_name, data_type FROM information_schema.columns
WHERE table_name = 'video_tags' ORDER BY ordinal_position;
```
Expected: video_id, tag_id, confidence, source, created_at columns

**Step 4: Commit**

```bash
git add supabase/migrations/013_create_video_tags_table.sql
git commit -m "feat(db): create video_tags association table"
```

---

### Task 1.4: Create Video Analysis Table

**Files:**
- Create: `supabase/migrations/014_create_video_analysis_table.sql`

**Step 1: Create migration file**

```sql
-- Video Analysis results with embeddings
CREATE TABLE video_analysis (
    video_id UUID PRIMARY KEY REFERENCES douyin_videos(id) ON DELETE CASCADE,

    -- Analysis level tracking
    analysis_level VARCHAR(10) DEFAULT 'none'
        CHECK (analysis_level IN ('none', 'L1', 'L2', 'L3')),

    -- Visual analysis results
    visual_description TEXT,
    detected_objects JSONB DEFAULT '[]'::jsonb,
    detected_scenes JSONB DEFAULT '[]'::jsonb,
    detected_people JSONB DEFAULT '[]'::jsonb,
    detected_text TEXT,

    -- Full text for embedding generation
    full_text_for_embedding TEXT,

    -- Vector embedding (1536 dimensions for OpenAI)
    content_embedding vector(1536),

    -- Metadata
    analysis_model VARCHAR(50),
    analysis_cost DECIMAL(10, 6) DEFAULT 0,
    analyzed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Vector similarity search index (IVFFlat for better performance)
CREATE INDEX idx_video_analysis_embedding
    ON video_analysis USING ivfflat (content_embedding vector_cosine_ops)
    WITH (lists = 100);

-- Index for filtering by analysis level
CREATE INDEX idx_video_analysis_level ON video_analysis(analysis_level);

-- RLS Policies
ALTER TABLE video_analysis ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View analysis of owned videos" ON video_analysis
    FOR SELECT USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_analysis.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

CREATE POLICY "Manage analysis of owned videos" ON video_analysis
    FOR ALL USING (
        EXISTS (
            SELECT 1 FROM douyin_videos
            WHERE douyin_videos.id = video_analysis.video_id
            AND douyin_videos.user_id = auth.uid()
        )
    );

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_video_analysis_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER video_analysis_updated
    BEFORE UPDATE ON video_analysis
    FOR EACH ROW
    EXECUTE FUNCTION update_video_analysis_timestamp();
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "create_video_analysis_table"

**Step 3: Verify vector index created**

Run: `mcp__supabase__execute_sql`:
```sql
SELECT indexname, indexdef FROM pg_indexes
WHERE tablename = 'video_analysis' AND indexname LIKE '%embedding%';
```
Expected: idx_video_analysis_embedding index exists

**Step 4: Commit**

```bash
git add supabase/migrations/014_create_video_analysis_table.sql
git commit -m "feat(db): create video_analysis table with vector embedding"
```

---

### Task 1.5: Create Smart Collections Table

**Files:**
- Create: `supabase/migrations/015_create_smart_collections_table.sql`

**Step 1: Create migration file**

```sql
-- Smart Collections with rule-based filtering
CREATE TABLE smart_collections (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    icon VARCHAR(50) DEFAULT '📁',
    description TEXT,

    -- Rule definition (JSON)
    rules JSONB NOT NULL DEFAULT '{
        "match": "all",
        "conditions": []
    }'::jsonb,

    -- Cache for performance
    cached_video_ids UUID[] DEFAULT '{}',
    cached_count INT DEFAULT 0,
    cached_at TIMESTAMP WITH TIME ZONE,

    -- Settings
    is_preset BOOLEAN DEFAULT false,
    sort_by VARCHAR(50) DEFAULT 'created_at',
    sort_order VARCHAR(10) DEFAULT 'desc' CHECK (sort_order IN ('asc', 'desc')),

    created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes
CREATE INDEX idx_smart_collections_user ON smart_collections(user_id);
CREATE INDEX idx_smart_collections_preset ON smart_collections(is_preset);

-- RLS Policies
ALTER TABLE smart_collections ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View own collections" ON smart_collections
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Create own collections" ON smart_collections
    FOR INSERT WITH CHECK (user_id = auth.uid());

CREATE POLICY "Update own collections" ON smart_collections
    FOR UPDATE USING (user_id = auth.uid());

CREATE POLICY "Delete own collections" ON smart_collections
    FOR DELETE USING (user_id = auth.uid() AND is_preset = false);

-- Trigger for updated_at
CREATE TRIGGER smart_collections_updated
    BEFORE UPDATE ON smart_collections
    FOR EACH ROW
    EXECUTE FUNCTION update_video_analysis_timestamp();
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "create_smart_collections_table"

**Step 3: Verify table created**

Run: `mcp__supabase__list_tables` with schemas: ["public"]
Expected: `smart_collections` table in list

**Step 4: Commit**

```bash
git add supabase/migrations/015_create_smart_collections_table.sql
git commit -m "feat(db): create smart_collections table"
```

---

### Task 1.6: Create Video Access Logs Table

**Files:**
- Create: `supabase/migrations/016_create_video_access_logs_table.sql`

**Step 1: Create migration file**

```sql
-- Video Access Logs for tracking user behavior
CREATE TABLE video_access_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    video_id UUID NOT NULL REFERENCES douyin_videos(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    action VARCHAR(20) NOT NULL
        CHECK (action IN ('view', 'play', 'share', 'download_again', 'export')),
    metadata JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Indexes for efficient queries
CREATE INDEX idx_access_logs_video ON video_access_logs(video_id);
CREATE INDEX idx_access_logs_user ON video_access_logs(user_id);
CREATE INDEX idx_access_logs_created ON video_access_logs(created_at DESC);
CREATE INDEX idx_access_logs_action ON video_access_logs(action);

-- RLS Policies
ALTER TABLE video_access_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "View own access logs" ON video_access_logs
    FOR SELECT USING (user_id = auth.uid());

CREATE POLICY "Create own access logs" ON video_access_logs
    FOR INSERT WITH CHECK (user_id = auth.uid());
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "create_video_access_logs_table"

**Step 3: Commit**

```bash
git add supabase/migrations/016_create_video_access_logs_table.sql
git commit -m "feat(db): create video_access_logs table for behavior tracking"
```

---

### Task 1.7: Alter douyin_videos Table for Cleanup Features

**Files:**
- Create: `supabase/migrations/017_add_cleanup_fields_to_videos.sql`

**Step 1: Create migration file**

```sql
-- Add fields to douyin_videos for cleanup suggestions
ALTER TABLE douyin_videos
    ADD COLUMN IF NOT EXISTS view_count INT DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_viewed_at TIMESTAMP WITH TIME ZONE,
    ADD COLUMN IF NOT EXISTS storage_size BIGINT,
    ADD COLUMN IF NOT EXISTS keep_forever BOOLEAN DEFAULT false;

-- Index for cleanup queries
CREATE INDEX IF NOT EXISTS idx_videos_view_count ON douyin_videos(view_count);
CREATE INDEX IF NOT EXISTS idx_videos_last_viewed ON douyin_videos(last_viewed_at);
CREATE INDEX IF NOT EXISTS idx_videos_storage_size ON douyin_videos(storage_size);

-- Function to update view stats from access logs (called periodically)
CREATE OR REPLACE FUNCTION update_video_view_stats()
RETURNS void AS $$
BEGIN
    UPDATE douyin_videos v
    SET
        view_count = stats.cnt,
        last_viewed_at = stats.last_view
    FROM (
        SELECT
            video_id,
            COUNT(*) as cnt,
            MAX(created_at) as last_view
        FROM video_access_logs
        WHERE action IN ('view', 'play')
        GROUP BY video_id
    ) stats
    WHERE v.id = stats.video_id;
END;
$$ LANGUAGE plpgsql;
```

**Step 2: Apply migration**

Run: `mcp__supabase__apply_migration` with name "add_cleanup_fields_to_videos"

**Step 3: Verify columns added**

Run: `mcp__supabase__execute_sql`:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'douyin_videos'
AND column_name IN ('view_count', 'last_viewed_at', 'storage_size', 'keep_forever');
```
Expected: 4 rows returned

**Step 4: Commit**

```bash
git add supabase/migrations/017_add_cleanup_fields_to_videos.sql
git commit -m "feat(db): add cleanup tracking fields to douyin_videos"
```

---

## Phase 2: Tags Backend API

### Task 2.1: Create Tags Pydantic Schemas

**Files:**
- Create: `backend/app/schemas/tags.py`

**Step 1: Create schema file**

```python
"""Pydantic schemas for Tags API."""
from datetime import datetime
from typing import Optional, List, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TagBase(BaseModel):
    """Base tag schema."""
    name: str = Field(..., min_length=1, max_length=50, description="Tag name")
    color: Optional[str] = Field("#6366f1", description="Hex color code")
    icon: Optional[str] = Field(None, description="Emoji or icon identifier")


class TagCreate(TagBase):
    """Schema for creating a user tag."""
    pass


class TagUpdate(BaseModel):
    """Schema for updating a tag."""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    color: Optional[str] = None
    icon: Optional[str] = None


class TagResponse(TagBase):
    """Schema for tag response."""
    id: UUID
    type: Literal["system", "user", "time"]
    user_id: Optional[UUID] = None
    created_at: datetime

    class Config:
        from_attributes = True


class TagListResponse(BaseModel):
    """Schema for list of tags response."""
    tags: List[TagResponse]
    total: int


class VideoTagCreate(BaseModel):
    """Schema for adding tag to video."""
    tag_id: UUID
    confidence: Optional[float] = Field(None, ge=0, le=1)
    source: Literal["auto", "manual", "ai"] = "manual"


class VideoTagResponse(BaseModel):
    """Schema for video tag association."""
    tag: TagResponse
    confidence: Optional[float]
    source: str
    created_at: datetime


class VideoTagsResponse(BaseModel):
    """Schema for video's tags response."""
    video_id: UUID
    tags: List[VideoTagResponse]
```

**Step 2: Commit**

```bash
git add backend/app/schemas/tags.py
git commit -m "feat(api): add Pydantic schemas for tags"
```

---

### Task 2.2: Create Tags Repository

**Files:**
- Create: `backend/app/repositories/tags_repository.py`

**Step 1: Create repository file**

```python
"""Repository for Tags data access."""
from typing import Optional, List
from uuid import UUID

from app.db.supabase_client import get_supabase_client
from loguru import logger


class TagsRepository:
    """Repository for tags CRUD operations."""

    @staticmethod
    async def get_all_tags(user_id: Optional[str] = None) -> List[dict]:
        """Get all tags (system + user's own tags)."""
        supabase = get_supabase_client()

        # Get system tags
        system_result = supabase.table("tags").select("*").eq("type", "system").execute()

        # Get time tags
        time_result = supabase.table("tags").select("*").eq("type", "time").execute()

        tags = system_result.data + time_result.data

        # Get user tags if user_id provided
        if user_id:
            user_result = supabase.table("tags").select("*").eq("user_id", user_id).execute()
            tags.extend(user_result.data)

        return tags

    @staticmethod
    async def get_tag_by_id(tag_id: str) -> Optional[dict]:
        """Get a single tag by ID."""
        supabase = get_supabase_client()
        result = supabase.table("tags").select("*").eq("id", tag_id).single().execute()
        return result.data if result.data else None

    @staticmethod
    async def get_tag_by_name(name: str, user_id: Optional[str] = None) -> Optional[dict]:
        """Get a tag by name (checks system tags first, then user tags)."""
        supabase = get_supabase_client()

        # Check system tags
        result = supabase.table("tags").select("*").eq("name", name).eq("type", "system").execute()
        if result.data:
            return result.data[0]

        # Check user tags
        if user_id:
            result = supabase.table("tags").select("*").eq("name", name).eq("user_id", user_id).execute()
            if result.data:
                return result.data[0]

        return None

    @staticmethod
    async def create_tag(name: str, user_id: str, color: str = "#6366f1", icon: Optional[str] = None) -> dict:
        """Create a new user tag."""
        supabase = get_supabase_client()

        data = {
            "name": name,
            "type": "user",
            "user_id": user_id,
            "color": color,
            "icon": icon
        }

        result = supabase.table("tags").insert(data).execute()
        logger.info(f"Created tag: {name} for user: {user_id}")
        return result.data[0]

    @staticmethod
    async def update_tag(tag_id: str, user_id: str, **kwargs) -> Optional[dict]:
        """Update a user tag."""
        supabase = get_supabase_client()

        # Filter out None values
        update_data = {k: v for k, v in kwargs.items() if v is not None}

        if not update_data:
            return await TagsRepository.get_tag_by_id(tag_id)

        result = supabase.table("tags").update(update_data).eq("id", tag_id).eq("user_id", user_id).execute()
        return result.data[0] if result.data else None

    @staticmethod
    async def delete_tag(tag_id: str, user_id: str) -> bool:
        """Delete a user tag."""
        supabase = get_supabase_client()

        result = supabase.table("tags").delete().eq("id", tag_id).eq("user_id", user_id).eq("type", "user").execute()
        return len(result.data) > 0

    @staticmethod
    async def add_tag_to_video(video_id: str, tag_id: str, confidence: Optional[float] = None, source: str = "manual") -> dict:
        """Add a tag to a video."""
        supabase = get_supabase_client()

        data = {
            "video_id": video_id,
            "tag_id": tag_id,
            "source": source
        }
        if confidence is not None:
            data["confidence"] = confidence

        result = supabase.table("video_tags").upsert(data).execute()
        logger.info(f"Added tag {tag_id} to video {video_id}")
        return result.data[0]

    @staticmethod
    async def remove_tag_from_video(video_id: str, tag_id: str) -> bool:
        """Remove a tag from a video."""
        supabase = get_supabase_client()

        result = supabase.table("video_tags").delete().eq("video_id", video_id).eq("tag_id", tag_id).execute()
        return len(result.data) > 0

    @staticmethod
    async def get_video_tags(video_id: str) -> List[dict]:
        """Get all tags for a video."""
        supabase = get_supabase_client()

        result = supabase.table("video_tags").select(
            "*, tags(*)"
        ).eq("video_id", video_id).execute()

        return result.data

    @staticmethod
    async def get_videos_by_tag(tag_id: str, user_id: str, limit: int = 50, offset: int = 0) -> List[dict]:
        """Get all videos with a specific tag."""
        supabase = get_supabase_client()

        result = supabase.table("video_tags").select(
            "video_id, douyin_videos(*)"
        ).eq("tag_id", tag_id).range(offset, offset + limit - 1).execute()

        return [r["douyin_videos"] for r in result.data if r.get("douyin_videos")]
```

**Step 2: Commit**

```bash
git add backend/app/repositories/tags_repository.py
git commit -m "feat(api): add tags repository for data access"
```

---

### Task 2.3: Create Tags Router

**Files:**
- Create: `backend/app/api/tags_router.py`

**Step 1: Create router file**

```python
"""API routes for Tags management."""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from app.core.dependencies import get_current_user
from app.repositories.tags_repository import TagsRepository
from app.schemas.tags import (
    TagCreate,
    TagUpdate,
    TagResponse,
    TagListResponse,
    VideoTagCreate,
    VideoTagsResponse,
    VideoTagResponse,
)

router = APIRouter(prefix="/tags", tags=["Tags"])


@router.get("", response_model=TagListResponse)
async def list_tags(
    type_filter: Optional[str] = Query(None, description="Filter by tag type: system, user, time"),
    current_user: dict = Depends(get_current_user),
):
    """
    List all available tags.

    Returns system tags and user's own tags.
    """
    user_id = current_user.get("id")
    tags = await TagsRepository.get_all_tags(user_id)

    if type_filter:
        tags = [t for t in tags if t["type"] == type_filter]

    return TagListResponse(tags=tags, total=len(tags))


@router.post("", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
async def create_tag(
    tag: TagCreate,
    current_user: dict = Depends(get_current_user),
):
    """
    Create a new user tag.

    System tags cannot be created via API.
    """
    user_id = current_user.get("id")

    # Check if tag with same name exists
    existing = await TagsRepository.get_tag_by_name(tag.name, user_id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tag '{tag.name}' already exists"
        )

    created_tag = await TagsRepository.create_tag(
        name=tag.name,
        user_id=user_id,
        color=tag.color,
        icon=tag.icon
    )

    return created_tag


@router.get("/{tag_id}", response_model=TagResponse)
async def get_tag(
    tag_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Get a specific tag by ID."""
    tag = await TagsRepository.get_tag_by_id(str(tag_id))

    if not tag:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    # Check access: system tags are public, user tags must belong to user
    if tag["type"] == "user" and tag["user_id"] != current_user.get("id"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    return tag


@router.put("/{tag_id}", response_model=TagResponse)
async def update_tag(
    tag_id: UUID,
    tag_update: TagUpdate,
    current_user: dict = Depends(get_current_user),
):
    """
    Update a user tag.

    Only user tags can be updated. System tags are immutable.
    """
    user_id = current_user.get("id")

    # Check tag exists and is user's own
    existing = await TagsRepository.get_tag_by_id(str(tag_id))
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    if existing["type"] != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot update system tags"
        )

    if existing["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )

    updated = await TagsRepository.update_tag(
        tag_id=str(tag_id),
        user_id=user_id,
        name=tag_update.name,
        color=tag_update.color,
        icon=tag_update.icon
    )

    return updated


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """
    Delete a user tag.

    Only user tags can be deleted. System tags cannot be deleted.
    """
    user_id = current_user.get("id")

    existing = await TagsRepository.get_tag_by_id(str(tag_id))
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag not found"
        )

    if existing["type"] != "user":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot delete system tags"
        )

    deleted = await TagsRepository.delete_tag(str(tag_id), user_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied"
        )


# Video-Tag association endpoints

@router.get("/videos/{video_id}/tags", response_model=VideoTagsResponse)
async def get_video_tags(
    video_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Get all tags associated with a video."""
    tags_data = await TagsRepository.get_video_tags(str(video_id))

    tags = []
    for item in tags_data:
        if item.get("tags"):
            tags.append(VideoTagResponse(
                tag=item["tags"],
                confidence=item.get("confidence"),
                source=item.get("source", "manual"),
                created_at=item.get("created_at")
            ))

    return VideoTagsResponse(video_id=video_id, tags=tags)


@router.post("/videos/{video_id}/tags", status_code=status.HTTP_201_CREATED)
async def add_tag_to_video(
    video_id: UUID,
    video_tag: VideoTagCreate,
    current_user: dict = Depends(get_current_user),
):
    """Add a tag to a video."""
    result = await TagsRepository.add_tag_to_video(
        video_id=str(video_id),
        tag_id=str(video_tag.tag_id),
        confidence=video_tag.confidence,
        source=video_tag.source
    )

    return {"message": "Tag added successfully", "data": result}


@router.delete("/videos/{video_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_tag_from_video(
    video_id: UUID,
    tag_id: UUID,
    current_user: dict = Depends(get_current_user),
):
    """Remove a tag from a video."""
    removed = await TagsRepository.remove_tag_from_video(str(video_id), str(tag_id))

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag association not found"
        )
```

**Step 2: Commit**

```bash
git add backend/app/api/tags_router.py
git commit -m "feat(api): add tags router with CRUD endpoints"
```

---

### Task 2.4: Register Tags Router in Main App

**Files:**
- Modify: `backend/app/main.py`

**Step 1: Add import and router registration**

Find the router imports section and add:

```python
from app.api.tags_router import router as tags_router
```

Find the router registration section (where other routers are included) and add:

```python
app.include_router(tags_router, prefix="/api/v1")
```

**Step 2: Verify router registered**

Run backend and check:
```bash
curl http://localhost:8080/api/v1/tags
```
Expected: Returns tags list (or 401 if auth required)

**Step 3: Commit**

```bash
git add backend/app/main.py
git commit -m "feat(api): register tags router in main app"
```

---

## Phase 3: Auto-Classification Service

### Task 3.1: Create Classification Service

**Files:**
- Create: `backend/app/services/classification_service.py`

**Step 1: Create service file**

```python
"""Content classification service using keyword matching and AI."""
import re
from typing import Optional, List, Tuple
from dataclasses import dataclass

from loguru import logger

from app.repositories.tags_repository import TagsRepository


@dataclass
class ClassificationResult:
    """Result of content classification."""
    primary_tag: str
    confidence: float
    secondary_tag: Optional[str] = None
    source: str = "auto"  # "auto" for keyword, "ai" for AI


# Keyword mapping for auto-classification
KEYWORD_MAPPING = {
    "Food": {
        "keywords_cn": ["美食", "做饭", "菜谱", "厨房", "吃", "烹饪", "料理", "食材", "炒菜", "烘焙"],
        "keywords_en": ["food", "cook", "recipe", "kitchen", "eat", "dish", "meal"],
    },
    "Tutorial": {
        "keywords_cn": ["教程", "教学", "学习", "怎么", "如何", "教你", "学会", "技巧", "方法"],
        "keywords_en": ["tutorial", "learn", "how to", "guide", "tips", "lesson"],
    },
    "Comedy": {
        "keywords_cn": ["搞笑", "笑死", "哈哈", "段子", "沙雕", "整蛊", "恶搞", "幽默"],
        "keywords_en": ["funny", "comedy", "lol", "joke", "humor", "laugh"],
    },
    "Dance": {
        "keywords_cn": ["舞蹈", "跳舞", "热舞", "编舞", "舞步", "街舞", "广场舞"],
        "keywords_en": ["dance", "dancing", "choreography", "dancer"],
    },
    "Music": {
        "keywords_cn": ["音乐", "唱歌", "翻唱", "原创", "歌曲", "演唱", "歌词"],
        "keywords_en": ["music", "sing", "cover", "song", "vocal", "melody"],
    },
    "Beauty": {
        "keywords_cn": ["美妆", "化妆", "护肤", "变美", "妆容", "口红", "眼影"],
        "keywords_en": ["makeup", "beauty", "skincare", "cosmetic"],
    },
    "Fashion": {
        "keywords_cn": ["穿搭", "时尚", "衣服", "搭配", "潮流", "服装", "ootd"],
        "keywords_en": ["fashion", "outfit", "style", "clothes", "wear"],
    },
    "Gaming": {
        "keywords_cn": ["游戏", "电竞", "主播", "直播", "玩家", "手游", "端游"],
        "keywords_en": ["game", "gaming", "esports", "player", "streamer"],
    },
    "Pets": {
        "keywords_cn": ["宠物", "猫", "狗", "萌宠", "猫咪", "狗狗", "铲屎官"],
        "keywords_en": ["pet", "cat", "dog", "cute", "puppy", "kitten"],
    },
    "Travel": {
        "keywords_cn": ["旅行", "旅游", "风景", "打卡", "景点", "出行", "游玩"],
        "keywords_en": ["travel", "trip", "scenery", "tour", "journey"],
    },
    "Tech": {
        "keywords_cn": ["科技", "数码", "测评", "开箱", "手机", "电脑", "评测"],
        "keywords_en": ["tech", "digital", "review", "unbox", "gadget"],
    },
    "Sports": {
        "keywords_cn": ["运动", "健身", "篮球", "足球", "跑步", "锻炼", "健康"],
        "keywords_en": ["sports", "fitness", "gym", "workout", "exercise"],
    },
    "Vlog": {
        "keywords_cn": ["vlog", "日常", "生活", "记录", "一天", "日记"],
        "keywords_en": ["vlog", "daily", "life", "routine", "day in"],
    },
}


class ClassificationService:
    """Service for auto-classifying video content."""

    @staticmethod
    def classify_by_keywords(
        title: str,
        description: Optional[str] = None,
        original_tags: Optional[List[str]] = None
    ) -> ClassificationResult:
        """
        Classify content using keyword matching.

        Returns classification result with confidence score.
        """
        # Combine all text for matching
        text_parts = [title]
        if description:
            text_parts.append(description)
        if original_tags:
            text_parts.extend(original_tags)

        combined_text = " ".join(text_parts).lower()

        # Score each category
        scores: List[Tuple[str, int]] = []

        for category, keywords in KEYWORD_MAPPING.items():
            score = 0

            # Check Chinese keywords
            for kw in keywords["keywords_cn"]:
                if kw in combined_text:
                    score += 2  # Higher weight for Chinese (primary language)

            # Check English keywords
            for kw in keywords["keywords_en"]:
                if kw in combined_text:
                    score += 1

            if score > 0:
                scores.append((category, score))

        if not scores:
            return ClassificationResult(
                primary_tag="Other",
                confidence=0.3,
                source="auto"
            )

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        primary = scores[0]
        secondary = scores[1] if len(scores) > 1 else None

        # Calculate confidence based on score difference
        max_possible_score = 20  # Approximate max score
        confidence = min(primary[1] / max_possible_score, 0.95)

        # Boost confidence if significantly higher than second place
        if secondary and primary[1] > secondary[1] * 2:
            confidence = min(confidence + 0.1, 0.95)

        return ClassificationResult(
            primary_tag=primary[0],
            confidence=round(confidence, 2),
            secondary_tag=secondary[0] if secondary else None,
            source="auto"
        )

    @staticmethod
    async def auto_tag_video(
        video_id: str,
        title: str,
        description: Optional[str] = None,
        original_tags: Optional[List[str]] = None,
        min_confidence: float = 0.3
    ) -> List[dict]:
        """
        Automatically tag a video based on its content.

        Returns list of tags added.
        """
        result = ClassificationService.classify_by_keywords(
            title=title,
            description=description,
            original_tags=original_tags
        )

        added_tags = []

        # Get the system tag
        primary_tag = await TagsRepository.get_tag_by_name(result.primary_tag)

        if primary_tag and result.confidence >= min_confidence:
            await TagsRepository.add_tag_to_video(
                video_id=video_id,
                tag_id=primary_tag["id"],
                confidence=result.confidence,
                source=result.source
            )
            added_tags.append({
                "tag": primary_tag,
                "confidence": result.confidence
            })
            logger.info(f"Auto-tagged video {video_id} as '{result.primary_tag}' (confidence: {result.confidence})")

        # Add secondary tag if confidence is reasonable
        if result.secondary_tag and result.confidence >= 0.5:
            secondary_tag = await TagsRepository.get_tag_by_name(result.secondary_tag)
            if secondary_tag:
                secondary_confidence = result.confidence * 0.7  # Lower confidence for secondary
                await TagsRepository.add_tag_to_video(
                    video_id=video_id,
                    tag_id=secondary_tag["id"],
                    confidence=secondary_confidence,
                    source=result.source
                )
                added_tags.append({
                    "tag": secondary_tag,
                    "confidence": secondary_confidence
                })

        return added_tags
```

**Step 2: Commit**

```bash
git add backend/app/services/classification_service.py
git commit -m "feat(service): add keyword-based content classification"
```

---

### Task 3.2: Integrate Auto-Tagging into Download Flow

**Files:**
- Modify: `backend/app/tasks/parse_tasks.py`

**Step 1: Add import**

At the top of the file, add:

```python
from app.services.classification_service import ClassificationService
```

**Step 2: Add auto-tagging after metadata save**

Find the section where metadata is saved (after `aweme_id` is available) and add:

```python
# Auto-tag the video
try:
    await ClassificationService.auto_tag_video(
        video_id=aweme_id,
        title=video_title or "",
        description=video.get("desc", ""),
        original_tags=video.get("text_extra", [])
    )
except Exception as e:
    logger.warning(f"Auto-tagging failed for {aweme_id}: {e}")
```

**Step 3: Test the integration**

Parse a test video and verify tags are added:
```bash
curl -X POST http://localhost:8080/api/v1/douyin/fetch \
  -H "Content-Type: application/json" \
  -d '{"url": "test_video_url"}'
```

Then check video tags:
```bash
curl http://localhost:8080/api/v1/tags/videos/{video_id}/tags
```

**Step 4: Commit**

```bash
git add backend/app/tasks/parse_tasks.py
git commit -m "feat(tasks): integrate auto-tagging into parse flow"
```

---

## Checkpoint: Phase 1-3 Complete

At this point you should have:

- [x] pgvector extension enabled
- [x] tags table with system tags
- [x] video_tags association table
- [x] video_analysis table with vector column
- [x] smart_collections table
- [x] video_access_logs table
- [x] cleanup fields on douyin_videos
- [x] Tags API (CRUD + video associations)
- [x] Auto-classification service
- [x] Auto-tagging integrated into download

**Verification:**

```bash
# Check all migrations applied
mcp__supabase__list_migrations

# Check tags endpoint works
curl http://localhost:8080/api/v1/tags

# Check system tags exist in DB
mcp__supabase__execute_sql: SELECT COUNT(*) FROM tags WHERE type = 'system';
# Expected: 14
```

---

## Next Phases (Separate Implementation Plans)

The following phases should be implemented after Phase 1-3 is verified:

- **Phase 4**: AI Visual Analysis (L1/L2/L3)
- **Phase 5**: Semantic Search (Embeddings + Text2SQL)
- **Phase 6**: Smart Collections UI & Backend
- **Phase 7**: Cleanup Suggestions System
- **Phase 8**: Frontend Integration

Each phase will have its own detailed implementation plan.
