# PR 2 · 向量空间 + `resource_embeddings`(halfvec 2048 + HNSW)+ 多模态协议适配器 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 语义层向量从 `resource_analysis.content_embedding vector(2048)`（建不了 HNSW、无空间概念）搬到新表 `resource_embeddings`（`halfvec(2048)` + HNSW + `space_id`），并让 `EmbeddingService` 能按「内容项列表」嵌入（文本 / 图片 / 视频帧），doubao 先行、nous-engine 后接。

**Architecture:** 三层：① 数据层 `embedding_spaces` / `resource_embeddings` 两张表 + 一个 RPC + 两个 repository；② provider 层 `EmbeddingService.try_embed_items()`，内容项归一为两种线上形状（Ark `/embeddings/multimodal` 与 OpenAI 兼容 `/v1/embeddings` 多模态扩展），能力声明**写在代码里**（同 `ProviderCapabilities` 的既有原则，不进目录表）；③ 接线层：`analyze_l1` / 回填 / hybrid & semantic & similar 三条读路径 / 一个给 UI 的 `GET /search/vectors/status`。语义层文档扩为 标题 + 简介 + 标签 + 摘要 + 转录前 2000 字 + VLM 分析（有什么用什么），所以**回填不再需要派 VLM**，全部就地嵌入。

**Tech Stack:** Postgres 17 + pgvector 0.8（`halfvec`、HNSW）；SQLAlchemy 2 async + `pgvector.sqlalchemy.HALFVEC`；httpx；pytest + AsyncMock。

**Spec:** `docs/superpowers/specs/2026-09-16-video-vector-layers-design.md` §4.1 / §4.2 / §7 PR 2 行；协议外形见 `2026-09-16-nous-engine-multimodal-embedding-request.md` §1。

**基底：** 本分支叠在 PR #2396（mig 490，`app/core/embedding_space.py` 的 `EMBEDDING_DIM` / `ensure_embedding_dim` / `EmbeddingDimensionMismatch`）之上。#2396 合并后 rebase 掉那一个提交即可。

## Global Constraints

- 迁移编号 **493**（490 = #2396、491 已合并、492 = #2398）。合并前再核一次 `ls supabase/migrations | tail`。
- 所有向量列一律 `halfvec(2048)`；宽度只从 `app.core.embedding_space.EMBEDDING_DIM` 读。
- 新建的 SQL 函数必须 `REVOKE EXECUTE ... FROM PUBLIC, anon, authenticated`（CLAUDE.md「SECURITY DEFINER」条）。
- 不新增 `text()` 裸 SQL，**结构性例外**：调用 pgvector RPC 的 `SELECT * FROM <fn>(CAST(:q AS halfvec), ...)`（与 `analysis_repository.search_by_embedding` 同一豁免）。bind 写 `CAST(:x AS type)`，绝不写 `:x::type`。
- 迁移与代码部署无顺序保证：代码先行时新表/新函数不存在（SQLSTATE 42P01 / 42883）→ 写入口返回类型化 reason `store_missing`、读入口回退旧 RPC；迁移先行时旧代码继续写旧列，无害。
- 后端 CI 门禁：`black` + `isort` + `flake8 --max-line-length=120`（三件，不是 ruff）。isort 对带行内 noqa 的 import 不幂等，跑两遍。
- 每个新表要有 ORM 模型（schema-drift 两向零容忍），ORM 里声明的 `Index` 名字与列集合必须与迁移一致（`tests/db/test_orm_indexes_integration.py`）。
- `schema-drift.yml` 加真库步骤后，要把 `what "green" means for all <english-number>` 的英文数字**重数**（不是 +1），否则自检步骤红。
- 用户可见错误码只用稳定的小写 snake_case 码，不带 provider 原文。

---

## 文件结构

| 文件 | 责任 |
|---|---|
| `supabase/migrations/493_resource_embeddings_halfvec_spaces.sql` | 两张表、HNSW、RPC `match_resource_embeddings`、`find_duplicate_videos` 改读新表 |
| `backend/app/models/embeddings.py` | `EmbeddingSpaces` / `ResourceEmbeddings` ORM |
| `backend/app/core/embedding_space.py` | +`SEMANTIC_LAYER` / `LAYERS` / `SpaceSpec` |
| `backend/app/repositories/embedding_space_repository.py` | `get_or_create(spec)` / `get(id)` |
| `backend/app/repositories/resource_embeddings_repository.py` | `upsert` / `get` / `search` / `coverage` / `missing_for_user`；`EmbeddingStoreMissing` |
| `backend/app/services/ai/providers/embedding_items.py` | 内容项类型 + 两种线上 payload 的构造/解析（纯函数） |
| `backend/app/services/ai/providers/embedding_capabilities.py` | 代码内能力表 `capabilities_for(cfg)` |
| `backend/app/services/ai/providers/embedding_service.py` | +`try_embed_items` / `space_spec()`；`try_embed` 委托 |
| `backend/app/services/library/embedding_document.py` | 语义层文档拼装 `compose_semantic_document` + `source_hash` |
| `backend/app/services/library/embedding_backfill.py` | 候选改按新表缺行；`reembed_existing` → `embed_candidate` 全部就地 |
| `backend/app/api/ai_router.py` | 回填端点：去掉 VLM 派发分支，响应加 `space` |
| `backend/app/workflows/analyze_l1.py` | 写新表 |
| `backend/app/services/library/search_service.py` | 三条读路径走新表，`vector_leg` 加 `store_missing` |
| `backend/app/api/search_router.py` | `GET /search/vectors/status` |
| `backend/app/schemas/search.py` | `VectorsStatusResponse` |

---

### Task A: 数据层 — 迁移 493 + ORM + 两个 repository

**Files:**
- Create: `supabase/migrations/493_resource_embeddings_halfvec_spaces.sql`
- Create: `backend/app/models/embeddings.py`
- Modify: `backend/app/models/__init__.py`（加 `from app.models.embeddings import EmbeddingSpaces, ResourceEmbeddings  # noqa: F401`）
- Modify: `backend/app/core/embedding_space.py`（追加常量与 `SpaceSpec`）
- Create: `backend/app/repositories/embedding_space_repository.py`
- Create: `backend/app/repositories/resource_embeddings_repository.py`
- Test: `backend/tests/test_resource_embeddings_repository.py`（桩 session，验语句形状与错误分类）
- Test: `backend/tests/db/test_migration_493_resource_embeddings_integration.py`（真库，挂 schema-drift）
- Modify: `.github/workflows/schema-drift.yml`（加一步 + 重数英文数字）

**Interfaces:**
- Produces（Task C 依赖）：
  - `app.core.embedding_space.SEMANTIC_LAYER = "semantic"`, `LAYERS = ("semantic", "transcript")`
  - `app.core.embedding_space.SpaceSpec(actual_model: str, dims: int, protocol: str, modalities: tuple[str, ...], instruction_version: str = "en_keyword_v1")`（frozen dataclass）
  - `EmbeddingSpaceRepository.get_or_create(spec: SpaceSpec) -> dict`  返回 `{id:int, actual_model, protocol, dims, modalities:list[str], instruction_version, created_at:str}`
  - `EmbeddingSpaceRepository.get(space_id: int) -> dict | None`
  - `ResourceEmbeddingsRepository.upsert(*, resource_id:int, layer:str, space_id:int, embedding:list[float], source_hash:str, source_text:str|None) -> None`
  - `ResourceEmbeddingsRepository.get(resource_id:int, layer:str, space_id:int) -> dict | None`（含 `embedding: list[float]`, `source_hash`）
  - `ResourceEmbeddingsRepository.search(*, embedding:list[float], space_id:int, layer:str, user_id:str, limit:int, threshold:float) -> list[dict]`（行键与 `AnalysisRepository.search_by_embedding` 相同：`media_id, platform_id, title, description, cover_urls, author, view_count, created_at, similarity`，另加 `resource_id`）
  - `ResourceEmbeddingsRepository.coverage(*, user_id:str, space_id:int, layer:str) -> tuple[int,int]`（covered, total；total = 用户未回收站的 web 资源数）
  - `ResourceEmbeddingsRepository.missing_for_user(*, user_id:str, space_id:int, layer:str, limit:int) -> tuple[list[BackfillRow], int]`，`BackfillRow(resource_id:int, media_id:int, platform_id:str, title:str, description:str, has_analysis:bool)`；第二个返回值是 total_missing
  - `EmbeddingStoreMissing(RuntimeError)`：表或函数不存在（42P01 / 42883）时由 repository 抛，其余 ProgrammingError 原样再抛
  - `get_embedding_space_repository()` / `get_resource_embeddings_repository()` 单例工厂

- [ ] **Step 1: 写迁移 493**

```sql
-- 493: resource_embeddings (halfvec 2048 + HNSW) + embedding_spaces.
--
-- resource_analysis.content_embedding is vector(2048): pgvector's HNSW caps
-- `vector` at 2000 dims, so that column has never had an index and every
-- semantic query is a sequential scan. halfvec goes to 4000, so the vectors
-- move to a table of their own, keyed by (resource, layer, space):
--   * layer    — which retrieval layer ('semantic' now; 'transcript' later)
--   * space_id — which embedder produced it (embedding_spaces). A vector only
--     means something next to vectors of the same space; switching models is
--     "new space + re-embed", never "overwrite in place".
-- The old column stays for one release (readers fall back to it while the
-- new table fills); mig 490's embedding_model stamp on resource_analysis is
-- superseded for this table by space_id and left in place.
--
-- Deploy-order safe both ways: old code keeps writing the old column; new
-- code treats a missing table/function as a typed store_missing.
-- Idempotent; safe on an empty DB.

BEGIN;

CREATE TABLE IF NOT EXISTS public.embedding_spaces (
  id                  bigint PRIMARY KEY DEFAULT public.generate_snowflake_id(),
  actual_model        text NOT NULL,
  protocol            text NOT NULL,
  dims                integer NOT NULL DEFAULT 2048,
  modalities          jsonb NOT NULL DEFAULT '["text"]'::jsonb,
  instruction_version text NOT NULL DEFAULT 'en_keyword_v1',
  created_at          timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT embedding_spaces_model_dims_key UNIQUE (actual_model, dims),
  CONSTRAINT embedding_spaces_dims_check CHECK (dims > 0 AND dims <= 4000)
);
COMMENT ON TABLE public.embedding_spaces IS
  'One row per (embedder model, width). Vectors are only comparable within a space.';

CREATE TABLE IF NOT EXISTS public.resource_embeddings (
  resource_id  bigint NOT NULL REFERENCES public.resources(id) ON DELETE CASCADE,
  layer        text NOT NULL,
  space_id     bigint NOT NULL REFERENCES public.embedding_spaces(id) ON DELETE CASCADE,
  embedding    halfvec(2048) NOT NULL,
  source_hash  text NOT NULL,
  source_text  text,
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT resource_embeddings_pkey PRIMARY KEY (resource_id, layer, space_id),
  CONSTRAINT resource_embeddings_layer_check CHECK (layer IN ('semantic', 'transcript'))
);
COMMENT ON COLUMN public.resource_embeddings.source_hash IS
  'sha1 of the embedded document (+ composer version); unchanged hash = skip re-embed.';

CREATE INDEX IF NOT EXISTS resource_embeddings_embedding_hnsw
  ON public.resource_embeddings USING hnsw (embedding halfvec_cosine_ops);
CREATE INDEX IF NOT EXISTS resource_embeddings_space_layer_idx
  ON public.resource_embeddings (space_id, layer);

-- Backend-only tables: RLS on, owner-scoped policy mirrors resource_analysis
-- so a browser role that ever reaches them sees only its own rows.
ALTER TABLE public.embedding_spaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.resource_embeddings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Read embeddings of owned resources" ON public.resource_embeddings;
CREATE POLICY "Read embeddings of owned resources" ON public.resource_embeddings
  FOR SELECT USING (EXISTS (
    SELECT 1 FROM public.resources r
    WHERE r.id = resource_embeddings.resource_id AND r.creator_id = auth.uid()));
DROP POLICY IF EXISTS "Read embedding spaces" ON public.embedding_spaces;
CREATE POLICY "Read embedding spaces" ON public.embedding_spaces FOR SELECT USING (true);

-- Nearest resources in ONE space and ONE layer. One row per resource by
-- construction (PK), and resources ↔ parsed_media is 1:1, so no DISTINCT ON:
-- ORDER BY distance LIMIT n is exactly the shape the HNSW index serves.
DROP FUNCTION IF EXISTS public.match_resource_embeddings(halfvec, bigint, text, double precision, integer, uuid);
CREATE FUNCTION public.match_resource_embeddings(
  query_embedding halfvec,
  p_space_id      bigint,
  p_layer         text,
  match_threshold double precision DEFAULT 0.5,
  match_count     integer DEFAULT 10,
  p_user_id       uuid DEFAULT NULL
)
RETURNS TABLE(
  resource_id bigint,
  media_id bigint,
  platform_id text,
  title text,
  description text,
  cover_urls jsonb,
  author text,
  view_count bigint,
  created_at timestamptz,
  similarity double precision
)
LANGUAGE sql STABLE
SET search_path TO 'public', 'pg_catalog'
AS $$
  SELECT
    re.resource_id,
    pm.id                              AS media_id,
    pm.platform_id::text               AS platform_id,
    pm.title,
    pm.description,
    pm.cover_urls,
    pm.author::text                    AS author,
    COALESCE(pm.view_count, 0)::bigint AS view_count,
    pm.created_at,
    (1 - (re.embedding <=> query_embedding))::float AS similarity
  FROM resource_embeddings re
  JOIN resources r     ON r.id = re.resource_id
  JOIN parsed_media pm ON pm.id = r.media_id
  WHERE re.space_id = p_space_id
    AND re.layer = p_layer
    AND r.media_id IS NOT NULL
    AND r.is_trashed = false
    AND r.source_type = 'web'
    AND (p_user_id IS NULL OR r.creator_id = p_user_id)
    AND (1 - (re.embedding <=> query_embedding)) > match_threshold
  ORDER BY re.embedding <=> query_embedding
  LIMIT match_count;
$$;
REVOKE EXECUTE ON FUNCTION public.match_resource_embeddings(halfvec, bigint, text, double precision, integer, uuid)
  FROM PUBLIC, anon, authenticated;

-- find_duplicate_videos: same signature (ACL from 490 preserved by OR REPLACE),
-- pairs now come from resource_embeddings and must share a space.
CREATE OR REPLACE FUNCTION public.find_duplicate_videos(
    p_user_id uuid,
    similarity_threshold double precision DEFAULT 0.85,
    max_results integer DEFAULT 20
)
RETURNS TABLE (
    media_id bigint, title text, cover_urls jsonb, author character varying,
    storage_size bigint, created_at timestamp with time zone,
    last_viewed_at timestamp with time zone, view_count integer,
    similar_to bigint, similarity_score double precision
)
LANGUAGE plpgsql STABLE SECURITY DEFINER
SET search_path = public, pg_catalog
AS $$
BEGIN
    RETURN QUERY
    WITH user_media AS (
        SELECT pm.id, re.embedding, re.space_id
        FROM resources r
        JOIN parsed_media pm ON pm.id = r.media_id
        JOIN resource_embeddings re ON re.resource_id = r.id AND re.layer = 'semantic'
        WHERE r.creator_id = p_user_id
          AND r.is_trashed = false
          AND pm.keep_forever = false
    ),
    similarity_pairs AS (
        SELECT um1.id AS mid1, um2.id AS mid2,
               (1 - (um1.embedding <=> um2.embedding))::float AS sim_score
        FROM user_media um1
        JOIN user_media um2 ON um2.id > um1.id AND um2.space_id = um1.space_id
        WHERE (1 - (um1.embedding <=> um2.embedding)) >= similarity_threshold
    )
    SELECT DISTINCT ON (sp.mid2)
        sp.mid2, pm.title, pm.cover_urls, pm.author, pm.storage_size, pm.created_at,
        pm.last_viewed_at, pm.view_count, sp.mid1, sp.sim_score
    FROM similarity_pairs sp
    JOIN parsed_media pm ON pm.id = sp.mid2
    ORDER BY sp.mid2, sp.sim_score DESC
    LIMIT max_results;
END;
$$;
REVOKE EXECUTE ON FUNCTION public.find_duplicate_videos(uuid, double precision, integer)
  FROM PUBLIC, anon, authenticated;

COMMIT;
```

- [ ] **Step 2: ORM 模型 `backend/app/models/embeddings.py`**

```python
"""embedding_spaces / resource_embeddings (migration 493).

Vector width comes from ``app.core.embedding_space.EMBEDDING_DIM`` (one
source); the column is ``halfvec`` so HNSW can index 2048 dims.
"""
from __future__ import annotations

import datetime
from typing import Any

from pgvector.sqlalchemy import HALFVEC
from sqlalchemy import (BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint,
                        Index, Integer, PrimaryKeyConstraint, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.embedding_space import EMBEDDING_DIM
from app.db.orm_base import Base


class EmbeddingSpaces(Base):
    __tablename__ = "embedding_spaces"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="embedding_spaces_pkey"),
        UniqueConstraint("actual_model", "dims", name="embedding_spaces_model_dims_key"),
        CheckConstraint("dims > 0 AND dims <= 4000", name="embedding_spaces_dims_check"),
        {"schema": "public"},
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True,
                                    server_default=text("generate_snowflake_id()"))
    actual_model: Mapped[str] = mapped_column(Text, nullable=False)
    protocol: Mapped[str] = mapped_column(Text, nullable=False)
    dims: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("2048"))
    modalities: Mapped[Any] = mapped_column(JSONB, nullable=False,
                                            server_default=text("'[\"text\"]'::jsonb"))
    instruction_version: Mapped[str] = mapped_column(Text, nullable=False,
                                                     server_default=text("'en_keyword_v1'::text"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False,
                                                          server_default=text("now()"))


class ResourceEmbeddings(Base):
    __tablename__ = "resource_embeddings"
    __table_args__ = (
        PrimaryKeyConstraint("resource_id", "layer", "space_id", name="resource_embeddings_pkey"),
        ForeignKeyConstraint(["resource_id"], ["public.resources.id"], ondelete="CASCADE",
                             name="resource_embeddings_resource_id_fkey"),
        ForeignKeyConstraint(["space_id"], ["public.embedding_spaces.id"], ondelete="CASCADE",
                             name="resource_embeddings_space_id_fkey"),
        CheckConstraint("layer IN ('semantic', 'transcript')", name="resource_embeddings_layer_check"),
        Index("resource_embeddings_embedding_hnsw", "embedding", postgresql_using="hnsw",
              postgresql_ops={"embedding": "halfvec_cosine_ops"}),
        Index("resource_embeddings_space_layer_idx", "space_id", "layer"),
        {"schema": "public"},
    )
    resource_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    layer: Mapped[str] = mapped_column(Text, primary_key=True)
    space_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(HALFVEC(EMBEDDING_DIM), nullable=False)
    source_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False,
                                                          server_default=text("now()"))
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False,
                                                          server_default=text("now()"))
```

注册进 `backend/app/models/__init__.py`（按字母序放在 `cover_templates` 之后）。`schema-drift` 的 ORM 对账会检查列名/类型/可空/外键——`halfvec` 的类型名对账若报 `HALFVEC(2048)` vs `halfvec`，看 `tests/db/test_schema_drift.py` 里 `Vector` 是怎么豁免/归一的，照同一处加 `halfvec`。

- [ ] **Step 3: `app/core/embedding_space.py` 追加**

```python
SEMANTIC_LAYER = "semantic"
LAYERS: tuple[str, ...] = ("semantic", "transcript")


@dataclass(frozen=True)
class SpaceSpec:
    """Identity of an embedding space, derived from the resolved embedder
    (never typed by hand). ``(actual_model, dims)`` is the unique key."""
    actual_model: str
    dims: int
    protocol: str
    modalities: tuple[str, ...]
    instruction_version: str = "en_keyword_v1"
```

加进 `__all__`。

- [ ] **Step 4: 桩 session 测试（先写、先红）`backend/tests/test_resource_embeddings_repository.py`**

照 `tests/test_analysis_repository_vector_binds.py` 的写法（`patch("app.repositories.resource_embeddings_repository.read_scope")` / `write_scope`，session 用 `AsyncMock`）。至少覆盖：

```python
async def test_search_calls_rpc_with_casts_and_space(session_stub):
    # 断言 text() 语句包含 "match_resource_embeddings(" 与 "CAST(:q AS halfvec)"，
    # 参数含 space_id / layer / user_id / limit / threshold；不含 "::"。

async def test_search_translates_undefined_function_to_store_missing(session_stub):
    # session.execute 抛 ProgrammingError(orig.sqlstate="42883") → raises EmbeddingStoreMissing

async def test_upsert_translates_undefined_table_to_store_missing(session_stub):
    # 42P01 → EmbeddingStoreMissing；其他 ProgrammingError 原样再抛

async def test_upsert_uses_on_conflict_do_update(session_stub):
    # 编译语句字符串含 "ON CONFLICT (resource_id, layer, space_id) DO UPDATE"，
    # SET 集合含 embedding / source_hash / source_text / updated_at

async def test_get_or_create_space_is_idempotent_on_model_dims(session_stub):
    # 第一次 insert on conflict do nothing → 再 select；返回 dict 含 id/actual_model/dims/protocol/modalities

async def test_missing_for_user_refuses_falsy_user_id():
    # user_id="" → ([], 0)，不碰 session（同 embedding_backfill.list_candidates）
```

运行 `uv run pytest tests/test_resource_embeddings_repository.py -v`，预期 ImportError 红。

- [ ] **Step 5: repository 实现**

`embedding_space_repository.py`：

```python
class EmbeddingSpaceRepository:
    async def get_or_create(self, spec: SpaceSpec) -> dict:
        stmt = (pg_insert(EmbeddingSpaces)
                .values(actual_model=spec.actual_model, dims=spec.dims, protocol=spec.protocol,
                        modalities=list(spec.modalities), instruction_version=spec.instruction_version)
                .on_conflict_do_nothing(constraint="embedding_spaces_model_dims_key"))
        try:
            async with write_scope() as session:
                await session.execute(stmt)
                row = (await session.execute(
                    select(EmbeddingSpaces).where(EmbeddingSpaces.actual_model == spec.actual_model,
                                                  EmbeddingSpaces.dims == spec.dims))).scalars().first()
        except ProgrammingError as exc:
            if _is_missing_relation(exc):
                raise EmbeddingStoreMissing("embedding_spaces does not exist (migration 493)") from exc
            raise
        return _space_dict(row)
```

`resource_embeddings_repository.py`：`EmbeddingStoreMissing` 定义在这里，`embedding_space_repository` 从这里 import。`_is_missing_relation(exc)`：`getattr(exc.orig, "sqlstate", None) in ("42P01", "42883")` 或 `"does not exist" in str(exc)`（照 `analysis_repository._is_undefined_function` 的写法）。

`search()` 的 SQL：

```python
"SELECT * FROM match_resource_embeddings("
"CAST(:q AS halfvec), CAST(:s AS bigint), CAST(:l AS text), "
"CAST(:t AS double precision), CAST(:c AS int), CAST(:u AS uuid))"
```
向量串 `"[" + ",".join(map(str, embedding)) + "]"`。输出行处理照 `AnalysisRepository._search_rows_out`（media_id/resource_id/view_count → int，created_at → isoformat）。

`coverage()`：两条 count —— total：`resources` where creator_id=user, source_type='web', is_trashed=false, media_id not null；covered：同条件 join `resource_embeddings` on resource_id where space_id/layer。

`missing_for_user()`：`resources ⋈ parsed_media ⟕ resource_embeddings(space,layer) ⟕ resource_analysis(L1)`，where `resource_embeddings.resource_id IS NULL`，order by `resource_analysis.resource_id IS NULL, resources.id DESC`（有分析的先）；返回 `BackfillRow` 列表 + count。`BackfillRow` 定义在 `app/services/library/embedding_backfill.py`？——不，repository 不能依赖 service：把 `BackfillRow` 放在 repository 模块里，service 从这里 import。

- [ ] **Step 6: 跑桩测试转绿；black/isort/flake8**

- [ ] **Step 7: 真库集成测试 `backend/tests/db/test_migration_493_resource_embeddings_integration.py`**

照 `tests/db/test_playback_positions_integration.py` 的骨架（asyncpg 直连 `INTEGRATION_DATABASE_URL`，未设跳过，`pytestmark = [integration, asyncio]`）。断言：
1. 两表存在，`resource_embeddings.embedding` 的 `format_type` 是 `halfvec(2048)`；
2. `pg_indexes` 里 `resource_embeddings_embedding_hnsw` 的 indexdef 含 `USING hnsw` 与 `halfvec_cosine_ops`；
3. 插一个 space + 一个 resource（需要先插 `resources` 的最小行——照 `test_assets_repository_integration.py` 的 fixture 建 user/team/resource）+ 一条 2048 维向量，调 `match_resource_embeddings` 用同向量能查回 similarity ≈ 1，换 space_id 查不到；
4. `has_function_privilege('anon', 'match_resource_embeddings(halfvec,bigint,text,double precision,integer,uuid)', 'EXECUTE')` 为 false，`authenticated` 同；
5. 全部在事务里 ROLLBACK。

- [ ] **Step 8: `schema-drift.yml` 加一步**

照 `tests/db/test_playback_positions_integration.py` 那一步的写法复制一段，指向新文件；然后 `grep -c` 真库步骤数，把 `what "green" means for all <n>` 的英文数字改成新的总数（重数）。本地跑 actionlint（CLAUDE.md 有下载命令）。

- [ ] **Step 9: Commit** `feat(db): embedding_spaces + resource_embeddings（halfvec 2048 + HNSW）与两个 repository（mig 493）`

---

### Task B: provider 层 — 内容项适配器 + 代码内能力表 + `try_embed_items`

**Files:**
- Create: `backend/app/services/ai/providers/embedding_items.py`
- Create: `backend/app/services/ai/providers/embedding_capabilities.py`
- Modify: `backend/app/services/ai/providers/embedding_service.py`
- Test: `backend/tests/test_embedding_items.py`
- Test: `backend/tests/test_embedding_capabilities.py`
- Modify: `backend/tests/test_embedding_service.py`（加 `try_embed_items` 用例）

**Interfaces:**
- Produces（Task C 依赖）：
  - `embedding_items.TextItem(text: str)` / `ImageUrlItem(url: str)` / `VideoFramesItem(frame_urls: tuple[str, ...])` / `VideoUrlItem(url: str)`，联合类型 `ContentItem`；`modality_of(item) -> "text" | "image" | "video"`
  - `embedding_items.build_ark_payload(model: str, items: Sequence[ContentItem]) -> dict`
  - `embedding_items.parse_ark_response(body: dict) -> list[float] | None`
  - `embedding_items.build_openai_multimodal_payload(model: str, groups: Sequence[Sequence[ContentItem]], dims: int) -> dict`
  - `embedding_items.parse_openai_embeddings_response(body: dict, expected: int) -> list[list[float]]`（顺序按 index；条数不符抛 `ValueError`）
  - `embedding_capabilities.EmbeddingCapabilities(protocol: str, modalities: frozenset[str], native_dims: int, matryoshka_dims: tuple[int, ...], max_video_frames: int, instruction_style: str)`
  - `embedding_capabilities.capabilities_for(cfg: EmbeddingConfig) -> EmbeddingCapabilities`
  - `embedding_capabilities.PROTOCOL_ARK_MULTIMODAL = "ark-multimodal"`, `PROTOCOL_OPENAI_MULTIMODAL = "openai-embeddings-multimodal"`, `PROTOCOL_OPENAI_TEXT = "openai-embeddings"`
  - `EmbeddingService.try_embed_items(items: Sequence[ContentItem]) -> tuple[list[float] | None, str | None]`：一组内容项 → 一条向量；reason 新增 `"modality_unsupported: <modality>"`
  - `EmbeddingService.space_spec() -> SpaceSpec | None`（`await`；unconfigured 返回 None；`dims = EMBEDDING_DIM`）
  - `EMBED_REASON_CODES` 新增 `"modality_unsupported"`

- [ ] **Step 1: 失败测试 `tests/test_embedding_items.py`**

```python
def test_ark_payload_text_and_image():
    p = build_ark_payload("doubao-embedding-vision-251215",
                          [TextItem("hello"), ImageUrlItem("data:image/jpeg;base64,AAA")])
    assert p == {"model": "doubao-embedding-vision-251215",
                 "input": [{"type": "text", "text": "hello"},
                           {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}}]}

def test_ark_payload_video_frames_becomes_video_url_items():
    # Ark 的视频输入是 {"type":"video_url","video_url":{"url":...}}；帧列表按帧展开成多个 image_url
    ...

def test_parse_ark_response_dict_and_list_shapes():
    assert parse_ark_response({"data": {"embedding": [0.1, 0.2]}}) == [0.1, 0.2]
    assert parse_ark_response({"data": [{"embedding": [0.3]}]}) == [0.3]
    assert parse_ark_response({"data": {}}) is None

def test_openai_multimodal_payload_groups_and_dims():
    p = build_openai_multimodal_payload("wemm-embedding-2b",
        [[TextItem("a")], [ImageUrlItem("data:x"), TextItem("b")]], dims=2048)
    assert p["dimensions"] == 2048 and p["encoding_format"] == "float"
    assert p["input"][1] == [{"type": "image_url", "image_url": {"url": "data:x"}},
                             {"type": "text", "text": "b"}]

def test_openai_multimodal_payload_video_frames_shape():
    p = build_openai_multimodal_payload("m", [[VideoFramesItem(("data:1", "data:2"))]], dims=2048)
    assert p["input"][0] == [{"type": "video_frames",
                              "frames": [{"image_url": {"url": "data:1"}}, {"image_url": {"url": "data:2"}}]}]

def test_parse_openai_response_orders_by_index_and_checks_count():
    body = {"data": [{"index": 1, "embedding": [2.0]}, {"index": 0, "embedding": [1.0]}]}
    assert parse_openai_embeddings_response(body, expected=2) == [[1.0], [2.0]]
    with pytest.raises(ValueError):
        parse_openai_embeddings_response(body, expected=3)
```

- [ ] **Step 2: 跑红 → 实现 `embedding_items.py`（纯函数，frozen dataclass）→ 跑绿**

- [ ] **Step 3: 失败测试 `tests/test_embedding_capabilities.py`**

```python
def _cfg(model, base_url="https://ark.cn-beijing.volces.com/api/v3", multimodal=False):
    return EmbeddingConfig(base_url=base_url, api_key="k", model=model, dimensions=0, multimodal=multimodal)

def test_doubao_vision_is_ark_multimodal_text_image_video():
    caps = capabilities_for(_cfg("doubao-embedding-vision-251215", multimodal=True))
    assert caps.protocol == "ark-multimodal"
    assert caps.modalities == frozenset({"text", "image", "video"})
    assert caps.native_dims == 2048 and caps.max_video_frames == 0  # Ark takes video_url, not frames

def test_wemm_is_openai_multimodal_with_frames():
    caps = capabilities_for(_cfg("wemm-embedding-2b", base_url="http://nous-engine:8000/v1"))
    assert caps.protocol == "openai-embeddings-multimodal"
    assert "video" in caps.modalities and caps.max_video_frames == 16
    assert 2048 in caps.matryoshka_dims

def test_unknown_openai_model_is_text_only():
    caps = capabilities_for(_cfg("text-embedding-3-large", base_url="https://api.openai.com/v1"))
    assert caps.protocol == "openai-embeddings" and caps.modalities == frozenset({"text"})
```

- [ ] **Step 4: 跑红 → 实现 `embedding_capabilities.py`（前缀匹配表：`doubao-embedding-vision` / `wemm-embedding` / `qwen3-vl-embedding`；否则文本）→ 跑绿**

- [ ] **Step 5: `EmbeddingService.try_embed_items` 与 `space_spec`**

改造点（保留现有 `try_embed(text)` 语义与全部既有测试）：
- `_embed_checked(text)` 内部改为 `_embed_items_checked([TextItem(text)])`；空文本仍先返回 `empty_text`，截断仍在文本项上做。
- `_embed_items_checked(items)`：`_ensure_client()` → unconfigured → `caps = capabilities_for(self._cfg)`；任一项 `modality_of(item) not in caps.modalities` → `(None, f"modality_unsupported: {modality}")`；按 `caps.protocol` 分派：
  - `ark-multimodal`：`httpx.post(url, json=build_ark_payload(...))`，`parse_ark_response`；url 规则沿用现有 `_embed_multimodal`；
  - `openai-embeddings-multimodal`：`httpx.post(base_url.rstrip('/') + '/embeddings', json=build_openai_multimodal_payload(model, [items], dims=EMBEDDING_DIM))`，`parse_openai_embeddings_response(body, expected=1)[0]`；
  - `openai-embeddings`：现有 `self.client.embeddings.create(...)`（只接受单个 TextItem）。
  - 之后 `ensure_embedding_dim` 照旧。
- `try_embed_items(items)` = `try_embed` 同款外壳（把 `EmbeddingDimensionMismatch` 翻成 reason）。
- `async def space_spec(self) -> SpaceSpec | None`：`_ensure_client()`；`None` if unconfigured；否则 `SpaceSpec(actual_model=self.model, dims=EMBEDDING_DIM, protocol=caps.protocol, modalities=tuple(sorted(caps.modalities)))`。
- 删掉旧的 `_embed_multimodal(text)`（被 items 版取代），但 `EmbeddingConfig.multimodal` 字段保留（`_is_multimodal` 仍用于识别 Ark）。

测试（追加到 `tests/test_embedding_service.py`，用 `patch("httpx.AsyncClient")` 或 `respx`——看文件里既有用法，照抄）：
```python
async def test_try_embed_items_ark_posts_typed_parts_and_returns_vector(...)
async def test_try_embed_items_rejects_unsupported_modality_without_network(...)
    # cfg 为 openai 文本模型，传 ImageUrlItem → (None, "modality_unsupported: image")，httpx 未被调用
async def test_space_spec_none_when_unconfigured(...)
async def test_space_spec_reports_ark_protocol_for_doubao(...)
```

- [ ] **Step 6: 全量跑 `uv run pytest tests/test_embedding_service.py tests/test_embedding_space.py tests/test_embedding_space_wiring.py tests/test_embedding_items.py tests/test_embedding_capabilities.py -q`；black/isort/flake8**

- [ ] **Step 7: Commit** `feat(embedding): 内容项适配器（Ark / OpenAI 多模态）+ 代码内能力声明 + try_embed_items / space_spec`

---

### Task C: 接线 — 文档拼装、analyze_l1、回填、三条读路径、`/search/vectors/status`

**Files:**
- Create: `backend/app/services/library/embedding_document.py`
- Modify: `backend/app/services/library/embedding_backfill.py`
- Modify: `backend/app/api/ai_router.py:1170-1330`（回填端点）
- Modify: `backend/app/workflows/analyze_l1.py:305-350`
- Modify: `backend/app/services/library/search_service.py`（`_vector_hits` / `semantic_search` / `find_similar`）
- Modify: `backend/app/api/search_router.py`（新端点）
- Modify: `backend/app/schemas/search.py`（`VectorsStatusResponse`）
- Modify: `CLAUDE.md`（向量陷阱条补一句）、spec §7 PR 2 状态
- Test: `backend/tests/services/library/test_embedding_document.py`
- Test: `backend/tests/services/library/test_embedding_backfill.py`（改）
- Test: `backend/tests/test_search_vector_leg_store.py`（新：三条读路径的空间/回退）
- Test: `backend/tests/test_vectors_status_endpoint.py`

**Interfaces:**
- Consumes：Task A 的两个 repository 与 `SpaceSpec` / `SEMANTIC_LAYER`；Task B 的 `try_embed` / `space_spec()`。
- Produces：
  - `embedding_document.compose_semantic_document(*, title, description, tags, summary_text, transcript_text, analysis) -> tuple[str, str]`（text, source_hash）。`DOC_VERSION = "semantic_v2"` 参与 hash；`TRANSCRIPT_EXCERPT_CHARS = 2000`。
  - `embedding_document.load_semantic_inputs(resource_id:int) -> dict`（title/description/tags/summary_text/transcript_text/analysis，从 `parsed_media` / tags / `ai_repository.get_summary` / `get_transcript` / `analysis_repo.get_analysis(level='L1')` 读）
  - `embedding_backfill.embed_candidate(row: BackfillRow, *, embedder, space_id:int, repo) -> tuple[bool, str|None]`
  - `GET /api/v1/search/vectors/status` → `VectorsStatusResponse{ space: {id, actual_model, protocol, dims, modalities, instruction_version} | None, status: "ok"|"unconfigured"|"store_missing", layers: [{layer:"semantic", status:"ok"|"not_built", covered:int, total:int}, {layer:"transcript", status:"not_built", covered:0, total:int}] }`
  - `VECTOR_LEG_OUTCOMES` 新增 `"store_missing"`（新表/RPC 不存在且旧路径也不可用）
  - `SearchResult.layer: "text"|"semantic"`、`SearchResponse.legs: {text:int, semantic:int}`、`SearchResponse.reranked: bool`（hybrid 填；UI legs chip 读）

- [ ] **Step 1: 文档拼装（先测后写）**

```python
def test_compose_uses_everything_available_and_hash_changes_with_content():
    text, h = compose_semantic_document(title="T", description="D", tags=["a"], summary_text="S",
                                        transcript_text="x" * 5000, analysis={"visual_description": "V"})
    assert text.startswith("Title: T\nDescription: D\nTags: a\nSummary: S\nTranscript: ")
    assert len(text) <= 16000 and "Visual: V" in text
    assert h != compose_semantic_document(title="T2", description="D", tags=["a"],
                                          summary_text="S", transcript_text="", analysis=None)[1]

def test_compose_without_analysis_or_summary_still_embeds_title():
    text, _ = compose_semantic_document(title="T", description="", tags=[], summary_text=None,
                                        transcript_text=None, analysis=None)
    assert text == "Title: T"

def test_compose_empty_everything_is_empty_string():
    assert compose_semantic_document(title="", description="", tags=[], summary_text=None,
                                     transcript_text=None, analysis=None)[0] == ""
```
实现：沿用 `EmbeddingService.build_embedding_text` 的行格式（不带指令前缀——文档侧永远裸文本）；转录截到 `TRANSCRIPT_EXCERPT_CHARS`；hash = `sha1(DOC_VERSION + "\n" + text)`。`build_embedding_text` 保留（topics 还在用），文档拼装只在这里。

- [ ] **Step 2: 回填改造**

`embedding_backfill.py`：
- 删 `partition` / `undispatchable` / `reembed_existing` / `list_candidates` / `_missing_embedding_stmt`（它们只服务旧列）；
- `embed_candidate(row, *, embedder, space_id, repo, analysis_repo, tags_repo, ai_repo)`：`load_semantic_inputs` → `compose_semantic_document` → 空文本返回 `(False, "empty_text")` → `existing = await repo.get(...)`，hash 相同返回 `(True, None)`（幂等，不花钱）→ `try_embed(text)` → `repo.upsert(...)` → `(True, None)`；`EmbeddingStoreMissing` 翻成 `(False, "store_missing")`。

`ai_router.backfill_embeddings`：
- 409 `embedder_unconfigured` 逻辑不变；再 `spec = await embedder.space_spec()`，`space = await space_repo.get_or_create(spec)`（`EmbeddingStoreMissing` → 503 `{"code":"vector_store_missing"}`）；
- `rows, total_missing = await repo.missing_for_user(user_id=..., space_id=space["id"], layer=SEMANTIC_LAYER, limit=opts.limit)`；不再需要 `_resources_with_active_l1` 与 `_BACKFILL_OVERFETCH_FACTOR`（没有异步在途了）——删掉这两处及其常量；
- dry_run 响应：`{"success":True,"dry_run":True,"space":space,"reembedded":[ids],"dispatched":[],"skipped":[],"in_flight":0,"remaining":total,"total_missing":total}`（`dispatched` / `in_flight` 保留为空以兼容前端读者）；
- 真跑：逐行 `embed_candidate`，`embedder_unconfigured` / `dimension_mismatch` / `store_missing` 三个码是进程级 → 记 `aborted`，剩余行按同码进 skipped 并 break（沿用现有循环）；
- docstring 改写：说明不再派 VLM，为什么（文档不再依赖 VLM 字段）。

`tests/services/library/test_embedding_backfill.py`：删旧 partition 用例，加 `embed_candidate` 的四条：hash 相同不调 embedder；空文本 `empty_text`；`EmbeddingStoreMissing` → `store_missing`；正常路径 upsert 参数正确（layer='semantic'、space_id、source_hash）。

- [ ] **Step 3: `analyze_l1` 写新表**

`analyze_l1.py` 305-335 处：
```python
inputs = await load_semantic_inputs(resource_id)
embedding_text, source_hash = compose_semantic_document(**inputs)
spec = await embedding_service.space_spec()
embedding, embed_error = (None, "unconfigured") if spec is None else await embedding_service.try_embed(embedding_text)
if embedding:
    try:
        space = await get_embedding_space_repository().get_or_create(spec)
        await get_resource_embeddings_repository().upsert(resource_id=resource_id, layer=SEMANTIC_LAYER,
            space_id=space["id"], embedding=embedding, source_hash=source_hash, source_text=embedding_text)
    except EmbeddingStoreMissing as e:
        embedding, embed_error = None, f"store_missing: {e}"
```
`classify_embed_reason` 要认得 `store_missing`（加进 `EMBED_REASON_CODES`）。不再调用 `analysis_repo.update_embedding`。`tests/workflows/test_workflow_failure_raises.py` 里若有对 `update_embedding` 的断言，改成对新 repo 的断言。

- [ ] **Step 4: 三条读路径**

`search_service.py`：
- 构造函数加 `space_repo` / `embeddings_repo`（默认工厂），保留 `analysis_repo`（旧列回退与 `get_analysis`）。
- `_vector_hits`：embed 查询后 `spec = await self.embedding_service.space_spec()`；`space = await self.space_repo.get_or_create(spec)`；`rows = await self.embeddings_repo.search(embedding=vec, space_id=space["id"], layer=SEMANTIC_LAYER, user_id=..., limit=..., threshold=...)`。`EmbeddingStoreMissing` → 回退 `self.analysis_repo.search_by_embedding(...)`（旧列；日志 warning「migration 493 not applied」），旧路径再抛 `EmbeddingSearchUnavailable` → `"store_missing"`。
- `semantic_search` 同样改（它也调 `search_by_embedding`）。
- `find_similar`：先 `embeddings_repo.get(media→resource_id, SEMANTIC_LAYER, space_id)`（注意 `media_id` → `resources.id` 要经 `analysis_repo`/`resources` 查一次；现有代码用 `get_analysis(media_id)` 是按 resource_id 键的——保持现状的键），拿到向量走 `embeddings_repo.search`；没有则回退旧列（现有代码）。
- `VECTOR_LEG_OUTCOMES` + `"store_missing"`；`schemas/search.py` 的注释同步。

`tests/test_search_vector_leg_store.py`：
```python
async def test_hybrid_vector_leg_uses_space_and_new_repo(...)  # search 被调且带 space_id/layer
async def test_hybrid_vector_leg_falls_back_to_legacy_rpc_when_store_missing(...)
async def test_hybrid_vector_leg_reports_store_missing_when_both_paths_gone(...)
async def test_find_similar_prefers_new_table_vector(...)
```

- [ ] **Step 5: `GET /search/vectors/status`**

`search_router.py`：
```python
@router.get("/vectors/status", response_model=VectorsStatusResponse)
async def vectors_status(auth: AuthDep):
    embedder = EmbeddingService()
    spec = await embedder.space_spec()
    if spec is None:
        return VectorsStatusResponse(space=None, status="unconfigured", layers=[...covered 0 / total N...])
    try:
        space = await get_embedding_space_repository().get_or_create(spec)
        covered, total = await get_resource_embeddings_repository().coverage(
            user_id=auth.user_id, space_id=space["id"], layer=SEMANTIC_LAYER)
    except EmbeddingStoreMissing:
        return VectorsStatusResponse(space=None, status="store_missing", layers=[])
    return VectorsStatusResponse(space=space, status="ok", layers=[
        LayerStatus(layer="semantic", status="ok" if covered else "not_built", covered=covered, total=total),
        LayerStatus(layer="transcript", status="not_built", covered=0, total=total)])
```
放在 `/similar/{id}` 之前注册（路径不冲突但保持可读）。测试用 FastAPI `TestClient` + `app.dependency_overrides`，照 `tests/test_visual_analysis_read_endpoint.py` 的做法；三种 status 各一条。

- [ ] **Step 5b: 命中标层与逐腿计数（给 UI PR 的 legs chip）**

`app/services/library/search_service.py` 的 `SearchResult` dataclass 加 `layer: str = "text"`（`"text"` | `"semantic"`）；`_merge_text_and_vector` 里 text 命中 `replace(h, similarity=1.0, layer="text")`，向量命中 `replace(h, layer="semantic")`；`SearchResponse` 加 `legs: dict[str, int] | None = None`（`{"text": n_text, "semantic": n_vector}`，只在 hybrid 填）与 `reranked: bool = False`。`schemas/search.py` 的 pydantic `SearchResult` / `SearchResponse` 同步加这三个字段（可选、默认值同上），router 的映射处把它们带过去。测试：`tests/test_search_vector_leg_store.py` 加一条断言合并后 text 命中 layer=="text"、向量命中 layer=="semantic"、`legs == {"text": 2, "semantic": 1}`。

- [ ] **Step 6: 文档**

- CLAUDE.md 向量陷阱条末尾加：`**2026-09-23 起（mig 493）语义层向量在 resource_embeddings（halfvec(2048) + HNSW，按 space_id 分空间）；resource_analysis.content_embedding 只读回退、下一版删。换模型 = 新空间 + 全量重嵌（回填端点就地嵌，不再派 VLM）。`
- spec §7 PR 2 行状态改「已实现（本 PR）」，§4.2 补一句「语义层不依赖 VLM 字段，有什么嵌什么」。

- [ ] **Step 7: 全量后端测试 + 三件 lint；Commit** `feat(search): 语义层接线到 resource_embeddings；回填就地嵌入；/search/vectors/status`

---

## 自审清单（写完计划后我自己过）

- 覆盖 spec §4.1 三样东西：协议（B）、能力声明（B，代码内）、向量空间（A）✔；§4.2 `resource_embeddings` / 过渡策略 / 文档扩写 ✔；§7 PR 2 行 ✔。
- 类型一致：`SpaceSpec` 在 A 定义、B 产出、C 消费；`EmbeddingStoreMissing` 在 A 定义、C 捕获；`BackfillRow` 在 A 的 repository 模块定义、C 消费。
- 未做（刻意）：admin UI 展示能力；`hotspots` 等三张 topics 表不搬（仍 `vector(2048)` + 490 的 `embedding_model`）；`transcript` 层只留枚举位。
