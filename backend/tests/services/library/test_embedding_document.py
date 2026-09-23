"""Semantic-layer document composition (PR 2 / mig 499).

The document is what ``resource_embeddings`` layer ``semantic`` embeds: title +
description + tags + summary + a transcript excerpt + the VLM analysis, each
only when present. It no longer depends on the VLM having run, which is why the
backfill embeds in place instead of dispatching analyze_l1.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.services.library import embedding_document as doc
from app.services.library.embedding_document import (
    DOC_VERSION,
    TRANSCRIPT_EXCERPT_CHARS,
    compose_semantic_document,
)


def test_compose_uses_everything_available_and_hash_changes_with_content():
    text, h = compose_semantic_document(
        title="T",
        description="D",
        tags=["a"],
        summary_text="S",
        transcript_text="x" * 5000,
        analysis={"visual_description": "V"},
    )
    assert text.startswith(
        "Title: T\nDescription: D\nTags: a\nSummary: S\nTranscript: "
    )
    assert len(text) <= 16000 and "Visual: V" in text
    assert (
        h
        != compose_semantic_document(
            title="T2",
            description="D",
            tags=["a"],
            summary_text="S",
            transcript_text="",
            analysis=None,
        )[1]
    )


def test_compose_without_analysis_or_summary_still_embeds_title():
    text, _ = compose_semantic_document(
        title="T",
        description="",
        tags=[],
        summary_text=None,
        transcript_text=None,
        analysis=None,
    )
    assert text == "Title: T"


def test_compose_empty_everything_is_empty_string():
    assert (
        compose_semantic_document(
            title="",
            description="",
            tags=[],
            summary_text=None,
            transcript_text=None,
            analysis=None,
        )[0]
        == ""
    )


def test_transcript_is_cut_to_the_excerpt_length():
    text, _ = compose_semantic_document(
        title="",
        description="",
        tags=[],
        summary_text=None,
        transcript_text="y" * (TRANSCRIPT_EXCERPT_CHARS + 500),
        analysis=None,
    )
    assert text == "Transcript: " + "y" * TRANSCRIPT_EXCERPT_CHARS


def test_analysis_lists_render_and_junk_shapes_are_ignored():
    text, _ = compose_semantic_document(
        title="T",
        description="",
        tags=[],
        summary_text=None,
        transcript_text=None,
        analysis={
            "visual_description": "V",
            "detected_objects": ["cat", "", None, "sofa"],
            "detected_scenes": "not-a-list",
            "detected_text": "SALE",
        },
    )
    assert text == "Title: T\nVisual: V\nObjects: cat, sofa\nText in video: SALE"


def test_hash_is_versioned_and_stable():
    import hashlib

    kwargs: dict[str, Any] = dict(
        title="T",
        description="",
        tags=[],
        summary_text=None,
        transcript_text=None,
        analysis=None,
    )
    text, h = compose_semantic_document(**kwargs)
    assert h == compose_semantic_document(**kwargs)[1]
    assert h == hashlib.sha1(f"{DOC_VERSION}\n{text}".encode()).hexdigest()


def test_blank_fields_are_skipped():
    text, _ = compose_semantic_document(
        title="  ",
        description="D",
        tags=["", "a"],
        summary_text="   ",
        transcript_text="  ",
        analysis={"visual_description": ""},
    )
    assert text == "Description: D\nTags: a"


# ---------------------------------------------------------------------------
# load_semantic_inputs
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, row):
        self._row = row

    def first(self):
        return self._row


class _Session:
    def __init__(self, row):
        self.row = row
        self.stmts: list = []

    async def execute(self, stmt):
        self.stmts.append(stmt)
        return _Result(self.row)


class _Analysis:
    def __init__(self, row):
        self.row = row
        self.calls: list = []

    async def get_analysis(self, rid, analysis_level=None):
        self.calls.append((rid, analysis_level))
        return self.row


class _Tags:
    async def get_resource_tags(self, rid):
        return [{"tags": {"name": "hanfu"}}, {"tags": None}, {"tags": {"name": ""}}]


class _AI:
    def __init__(self, summary, transcript):
        self.summary, self.transcript = summary, transcript
        self.keys: list = []

    async def get_summary(self, rid):
        self.keys.append(("summary", rid))
        return self.summary

    async def get_transcript(self, rid):
        self.keys.append(("transcript", rid))
        return self.transcript


def _patch_session(monkeypatch, row):
    session = _Session(row)

    @asynccontextmanager
    async def _scope():
        yield session

    monkeypatch.setattr(doc, "read_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_load_semantic_inputs_reads_every_source(monkeypatch):
    session = _patch_session(monkeypatch, (7, "Title", "Desc"))
    analysis = _Analysis({"visual_description": "V"})
    ai = _AI({"summary_text": "S"}, {"full_text": "hello"})
    out = await doc.load_semantic_inputs(
        7, analysis_repo=analysis, tags_repo=_Tags(), ai_repo=ai
    )
    assert out == {
        "title": "Title",
        "description": "Desc",
        "tags": ["hanfu"],
        "summary_text": "S",
        "transcript_text": "hello",
        "analysis": {"visual_description": "V"},
    }
    assert analysis.calls == [(7, "L1")]
    assert ("summary", "7") in ai.keys and ("transcript", "7") in ai.keys
    sql = str(session.stmts[0])
    assert "JOIN public.parsed_media" in sql and "resources.id = " in sql


@pytest.mark.asyncio
async def test_load_semantic_inputs_tolerates_missing_rows(monkeypatch):
    _patch_session(monkeypatch, None)
    out = await doc.load_semantic_inputs(
        8, analysis_repo=_Analysis(None), tags_repo=_Tags(), ai_repo=_AI(None, None)
    )
    assert out["title"] == "" and out["description"] == ""
    assert out["summary_text"] is None and out["transcript_text"] is None
    assert out["analysis"] is None
