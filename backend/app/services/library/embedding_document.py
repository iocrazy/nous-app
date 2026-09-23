"""The semantic-layer document: what ``resource_embeddings`` layer
``semantic`` embeds for one resource (migration 494).

Title + description + tags + summary + a transcript excerpt + the L1 visual
analysis — each line only when there is something to put on it. Before PR 2
the document was built from the VLM analysis fields, so a resource without an
analysis row had no document at all and the backfill had to dispatch the VLM
first. Now the VLM is one optional source among six: a resource with only a
title still gets a vector, and the backfill embeds everything in place.

The document side is always the RAW text. The asymmetric-retrieval instruction
(``search_service.QUERY_INSTRUCTION``) goes on queries only.

``source_hash`` covers :data:`DOC_VERSION` + the text, so changing the layout
here (bump the version) or any input re-qualifies a row, and an unchanged row
is skipped without paying for an embedding call.
"""

from __future__ import annotations

import hashlib
from typing import Any, Protocol

from sqlalchemy import select

from app.db.session import read_scope
from app.models import ParsedMedia, Resources

DOC_VERSION = "semantic_v2"
TRANSCRIPT_EXCERPT_CHARS = 2000
# Same cap the embedder applies (``embedding_service._MAX_CHARS``). Cut here so
# the stored ``source_text`` / hash describe exactly what was embedded.
DOC_MAX_CHARS = 16000
# The only analysis level analyze_l1 writes.
ANALYSIS_LEVEL = "L1"


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _as_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if v and str(v).strip()]


def _analysis_lines(analysis: dict | None) -> list[str]:
    if not isinstance(analysis, dict):
        return []
    lines: list[str] = []
    visual = _clean(analysis.get("visual_description"))
    if visual:
        lines.append(f"Visual: {visual}")
    objects = _as_list(analysis.get("detected_objects"))
    if objects:
        lines.append(f"Objects: {', '.join(objects)}")
    scenes = _as_list(analysis.get("detected_scenes"))
    if scenes:
        lines.append(f"Scenes: {', '.join(scenes)}")
    detected_text = _clean(analysis.get("detected_text"))
    if detected_text:
        lines.append(f"Text in video: {detected_text}")
    return lines


def compose_semantic_document(
    *,
    title: str | None,
    description: str | None,
    tags: list[str] | None,
    summary_text: str | None,
    transcript_text: str | None,
    analysis: dict | None,
) -> tuple[str, str]:
    """``(text, source_hash)``. Empty text means there is nothing to embed."""
    lines: list[str] = []
    if _clean(title):
        lines.append(f"Title: {_clean(title)}")
    if _clean(description):
        lines.append(f"Description: {_clean(description)}")
    tag_names = _as_list(tags)
    if tag_names:
        lines.append(f"Tags: {', '.join(tag_names)}")
    if _clean(summary_text):
        lines.append(f"Summary: {_clean(summary_text)}")
    transcript = _clean(transcript_text)
    if transcript:
        lines.append(f"Transcript: {transcript[:TRANSCRIPT_EXCERPT_CHARS]}")
    lines.extend(_analysis_lines(analysis))
    text = "\n".join(lines)[:DOC_MAX_CHARS]
    source_hash = hashlib.sha1(f"{DOC_VERSION}\n{text}".encode()).hexdigest()
    return text, source_hash


# ---------------------------------------------------------------------------
# loading the inputs
# ---------------------------------------------------------------------------
class _AnalysisRepo(Protocol):
    async def get_analysis(
        self, resource_id: int, analysis_level: str | None = None
    ) -> dict | None: ...


class _TagsRepo(Protocol):
    async def get_resource_tags(self, resource_id: Any) -> list[dict]: ...


class _AIRepo(Protocol):
    async def get_summary(self, resource_id: str) -> dict | None: ...

    async def get_transcript(self, resource_id: str) -> dict | None: ...


def _tag_names(tag_rows: list[dict] | None) -> list[str]:
    out: list[str] = []
    for row in tag_rows or []:
        tag = row.get("tags") if isinstance(row, dict) else None
        name = tag.get("name") if isinstance(tag, dict) else None
        if name:
            out.append(str(name))
    return out


def semantic_media_stmt(resource_id: int):
    """Title + description of the resource's parsed_media. Selected FROM
    ``resources`` (a scoped model, INNER JOIN): under a user scope the tenant
    filter attaches; DBOS steps wrap it in ``system_request_scope``."""
    return (
        select(Resources.id, ParsedMedia.title, ParsedMedia.description)
        .join(ParsedMedia, ParsedMedia.id == Resources.media_id)
        .where(Resources.id == resource_id)
        .limit(1)
    )


async def load_semantic_inputs(
    resource_id: int,
    *,
    analysis_repo: _AnalysisRepo | None = None,
    tags_repo: _TagsRepo | None = None,
    ai_repo: _AIRepo | None = None,
) -> dict[str, Any]:
    """Everything :func:`compose_semantic_document` takes, read for one
    resource. Missing sources come back empty / None, never raise."""
    if analysis_repo is None:
        from app.repositories.analysis_repository import get_analysis_repository

        analysis_repo = get_analysis_repository()
    if tags_repo is None:
        from app.repositories.tags_repository import get_tags_repository

        tags_repo = get_tags_repository()
    if ai_repo is None:
        from app.repositories.ai_repository import get_ai_repository

        ai_repo = get_ai_repository()

    async with read_scope() as session:
        row = (await session.execute(semantic_media_stmt(resource_id))).first()
    title, description = (row[1] or "", row[2] or "") if row is not None else ("", "")

    summary = await ai_repo.get_summary(str(resource_id))
    transcript = await ai_repo.get_transcript(str(resource_id))
    return {
        "title": title,
        "description": description,
        "tags": _tag_names(await tags_repo.get_resource_tags(resource_id)),
        "summary_text": (summary or {}).get("summary_text"),
        "transcript_text": (transcript or {}).get("full_text"),
        "analysis": await analysis_repo.get_analysis(
            resource_id, analysis_level=ANALYSIS_LEVEL
        ),
    }
