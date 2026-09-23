"""The single "current embedding space" module and the writer-seam guard.

Today every vector column is ``vector(2048)`` and the embedder is picked by
admin config. Switching that config to a model with another width used to
fail at the ORM bind / SQL CAST, and every caller read the error as "no
vector, skip" — a silent write-off. Switching to a model with the SAME width
was worse: new vectors landed next to old ones and ``<=>`` compared two
unrelated spaces without a word. These tests pin the two protections:

* a wrong-width vector is refused at ``EmbeddingService`` with a typed error;
* every stored vector carries the model id that produced it, and readers
  only compare vectors of the same space (NULL = legacy, same space).
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pgvector.sqlalchemy import Vector
from sqlalchemy import Text

from app.core.embedding_space import (
    EMBEDDING_DIM,
    EmbeddingDimensionMismatch,
    ensure_embedding_dim,
    spaces_compatible_sql,
)
from app.services.ai.providers.embedding_config import EmbeddingConfig
from app.services.ai.providers.embedding_service import (
    EmbeddingService,
    classify_embed_reason,
)

_MODELS_DIR = Path(__file__).resolve().parent.parent / "app" / "models"


def test_the_current_width_is_2048() -> None:
    assert EMBEDDING_DIM == 2048


def test_guard_raises_a_typed_error_naming_both_widths_and_the_model() -> None:
    with pytest.raises(EmbeddingDimensionMismatch) as ei:
        ensure_embedding_dim([0.0] * 2560, model="qwen3-embedding-4b")
    err = ei.value
    assert (err.expected, err.got, err.model) == (2048, 2560, "qwen3-embedding-4b")
    assert "2048" in str(err) and "2560" in str(err) and "qwen3" in str(err)


def test_guard_accepts_the_current_width() -> None:
    ensure_embedding_dim([0.0] * EMBEDDING_DIM, model="m")


# ---------------------------------------------------------------------------
# ORM: widths come from the constant, and every vector has its space column
# ---------------------------------------------------------------------------
def _vector_columns():
    from app.models.media import ResourceAnalysis
    from app.models.topics import Hotspots, TopicGroups, UserTopicInterests

    return [
        (Hotspots.__table__, "embedding"),
        (TopicGroups.__table__, "embedding"),
        (UserTopicInterests.__table__, "embedding"),
        (ResourceAnalysis.__table__, "content_embedding"),
    ]


def test_every_orm_vector_uses_the_shared_width() -> None:
    for table, col in _vector_columns():
        vtype = table.c[col].type
        assert isinstance(vtype, Vector)
        assert vtype.dim == EMBEDDING_DIM, f"{table.name}.{col}"


def test_no_model_file_spells_a_vector_width_literal() -> None:
    """``Vector(2048)`` written out again would let the ORM and the guard
    drift apart on the next model switch."""
    offenders = []
    for path in _MODELS_DIR.rglob("*.py"):
        for m in re.finditer(r"Vector\(\s*\d+\s*\)", path.read_text()):
            offenders.append(f"{path.name}: {m.group(0)}")
    assert offenders == []


def test_every_vector_column_has_a_nullable_text_space_column() -> None:
    for table, _col in _vector_columns():
        col = table.c.get("embedding_model")
        assert col is not None, table.name
        assert isinstance(col.type, Text) and col.nullable, table.name


# ---------------------------------------------------------------------------
# the SQL rule readers share
# ---------------------------------------------------------------------------
def test_spaces_compatible_sql_treats_null_as_the_same_space() -> None:
    sql = spaces_compatible_sql("a.embedding_model", "b.embedding_model")
    assert sql == (
        "(a.embedding_model IS NULL OR b.embedding_model IS NULL "
        "OR a.embedding_model = b.embedding_model)"
    )


# ---------------------------------------------------------------------------
# EmbeddingService: the writer seam
# ---------------------------------------------------------------------------
def _openai_patches(cfg: EmbeddingConfig, vec: list[float]):
    fake_resp = MagicMock()
    fake_resp.data = [MagicMock(embedding=vec)]
    fake_client = MagicMock()
    fake_client.embeddings.create = AsyncMock(return_value=fake_resp)
    return (
        patch(
            "app.services.ai.providers.embedding_service.resolve_embedding_config",
            AsyncMock(return_value=cfg),
        ),
        patch(
            "app.services.ai.providers.embedding_service.AsyncOpenAI",
            return_value=fake_client,
        ),
    )


_CFG = EmbeddingConfig(
    base_url="http://x/v1", api_key="k", model="qwen3-embedding-4b", dimensions=0
)


@pytest.mark.asyncio
async def test_try_embed_reports_a_wrong_width_as_dimension_mismatch() -> None:
    a, b = _openai_patches(_CFG, [0.1] * 2560)
    with a, b:
        vec, reason = await EmbeddingService().try_embed("hello")
    assert vec is None
    assert reason is not None and reason.startswith("dimension_mismatch")
    assert "2560" in reason and "qwen3-embedding-4b" in reason
    # A code of its own — NOT folded into provider_error.
    assert classify_embed_reason(reason) == "dimension_mismatch"


@pytest.mark.asyncio
async def test_try_embed_logs_the_mismatch_at_error() -> None:
    a, b = _openai_patches(_CFG, [0.1] * 2560)
    with (
        a,
        b,
        patch("app.services.ai.providers.embedding_service.logger") as log,
    ):
        await EmbeddingService().try_embed("hello")
    msgs = " ".join(str(c.args[0]) for c in log.error.call_args_list)
    assert "2560" in msgs and "2048" in msgs and "qwen3-embedding-4b" in msgs


@pytest.mark.asyncio
async def test_generate_embedding_raises_instead_of_returning_none() -> None:
    """The best-effort wrapper drops reasons; it must not drop THIS one."""
    a, b = _openai_patches(_CFG, [0.1] * 2560)
    with a, b, pytest.raises(EmbeddingDimensionMismatch):
        await EmbeddingService().generate_embedding("hello")


@pytest.mark.asyncio
async def test_the_service_names_the_space_that_produced_the_vector() -> None:
    a, b = _openai_patches(_CFG, [0.1] * EMBEDDING_DIM)
    with a, b:
        svc = EmbeddingService()
        vec, reason = await svc.try_embed("hello")
    assert reason is None and len(vec) == EMBEDDING_DIM
    assert svc.model == "qwen3-embedding-4b"
