"""Backfill embeddings for resources that have none.

Two buckets, handled differently because they cost differently:

* ``has_analysis`` — the VLM already ran (visual_description etc. exist) but
  the vector never landed (embedder was unconfigured at the time). Re-embed
  from the stored fields: one embedding call, no VLM, fractions of a cent.
* ``no_analysis`` — nothing ran. Dispatch the full analyze_l1 workflow
  (VLM + embed), each as its own Task Center row.

The service picks candidates cheapest-first and reports what remains so a
caller can stage the spend (20 first, then the rest).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from app.services.library import embedding_backfill as bf


# ---------------------------------------------------------------------------
# candidate partitioning
# ---------------------------------------------------------------------------
def test_candidate_dataclass_is_frozen() -> None:
    c = bf.BackfillCandidate(
        resource_id=1,
        media_id=2,
        platform_id="p",
        title="t",
        description="",
        cover_url="http://c",
        has_analysis=True,
    )
    with pytest.raises(Exception):
        c.resource_id = 9  # type: ignore[misc]


def test_partition_puts_cheap_reembeds_first_and_caps_at_limit() -> None:
    rows = [
        bf.BackfillCandidate(1, 11, "a", "", "", "u", has_analysis=False),
        bf.BackfillCandidate(2, 12, "b", "", "", "u", has_analysis=True),
        bf.BackfillCandidate(3, 13, "c", "", "", "u", has_analysis=False),
        bf.BackfillCandidate(4, 14, "d", "", "", "u", has_analysis=True),
    ]
    reembed, dispatch = bf.partition(rows, limit=3)
    assert [c.resource_id for c in reembed] == [2, 4]
    assert [c.resource_id for c in dispatch] == [1]


def test_partition_skips_candidates_without_a_cover_for_dispatch() -> None:
    """analyze_l1 needs a cover image; a no-analysis row without one cannot
    be dispatched and must be reported, not silently dropped."""
    rows = [
        bf.BackfillCandidate(1, 11, "a", "", "", None, has_analysis=False),
        bf.BackfillCandidate(2, 12, "b", "", "", "u", has_analysis=False),
    ]
    reembed, dispatch = bf.partition(rows, limit=10)
    assert reembed == []
    assert [c.resource_id for c in dispatch] == [2]
    assert [c.resource_id for c in bf.undispatchable(rows)] == [1]


# ---------------------------------------------------------------------------
# reembed_existing
# ---------------------------------------------------------------------------
class _Repo:
    def __init__(self, analysis: Dict[str, Any] | None) -> None:
        self.analysis = analysis
        self.updated: List[tuple] = []

    async def get_analysis(self, rid: int, analysis_level=None):
        return self.analysis

    async def update_embedding(self, rid: int, vec, text, analysis_level=None):
        self.updated.append((rid, vec, text))
        return {"resource_id": rid}


class _Tags:
    async def get_resource_tags(self, rid: int):
        return [{"tags": {"name": "hanfu"}}, {"tags": None}]


class _Embedder:
    def __init__(self, outcome) -> None:
        self.outcome = outcome
        self.texts: List[str] = []

    def build_embedding_text(self, **kw):
        return " | ".join(f"{k}={v}" for k, v in kw.items() if v)

    async def try_embed(self, text: str):
        self.texts.append(text)
        return self.outcome


@pytest.mark.asyncio
async def test_reembed_rebuilds_text_from_stored_analysis_and_writes_vector() -> None:
    cand = bf.BackfillCandidate(7, 70, "p7", "Title", "Desc", "u", has_analysis=True)
    repo = _Repo(
        {
            "visual_description": "a woman in hanfu",
            "detected_objects": ["jade hair ornament"],
            "detected_scenes": ["indoor"],
            "detected_text": "",
        }
    )
    emb = _Embedder(([0.1, 0.2], None))
    ok, reason = await bf.reembed_existing(cand, emb, repo, _Tags())
    assert ok and reason is None
    assert repo.updated and repo.updated[0][0] == 7
    assert repo.updated[0][1] == [0.1, 0.2]
    text = emb.texts[0]
    assert "hanfu" in text and "jade hair ornament" in text and "Title" in text


@pytest.mark.asyncio
async def test_reembed_reports_the_embedder_reason() -> None:
    cand = bf.BackfillCandidate(7, 70, "p7", "T", "", "u", has_analysis=True)
    repo = _Repo({"visual_description": "x"})
    ok, reason = await bf.reembed_existing(
        cand, _Embedder((None, "unconfigured")), repo, _Tags()
    )
    assert not ok and reason == "unconfigured"
    assert repo.updated == []


@pytest.mark.asyncio
async def test_reembed_reports_a_missing_analysis_row_instead_of_raising() -> None:
    cand = bf.BackfillCandidate(7, 70, "p7", "T", "", "u", has_analysis=True)
    ok, reason = await bf.reembed_existing(
        cand, _Embedder(([0.1], None)), _Repo(None), _Tags()
    )
    assert not ok and reason == "analysis_row_missing"


def test_partition_when_the_limit_is_smaller_than_the_reembed_count() -> None:
    """The cap is shared by BOTH buckets: 20 means 20 embedding calls OR 20
    VLM dispatches, never 20 of each. With the cheap bucket alone over the
    limit there is no room left and nothing may be dispatched."""
    rows = [
        bf.BackfillCandidate(1, 11, "a", "", "", "u", has_analysis=True),
        bf.BackfillCandidate(2, 12, "b", "", "", "u", has_analysis=True),
        bf.BackfillCandidate(3, 13, "c", "", "", "u", has_analysis=False),
    ]
    reembed, dispatch = bf.partition(rows, limit=1)
    assert [c.resource_id for c in reembed] == [1]
    assert dispatch == []


# ---------------------------------------------------------------------------
# reembed_existing edge shapes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reembed_falls_back_to_a_reason_when_the_embedder_gives_none() -> None:
    """``(None, None)`` from the embedder would otherwise be reported as a
    skip with no reason — the exact silence this whole change removes."""
    cand = bf.BackfillCandidate(7, 70, "p7", "T", "", "u", has_analysis=True)
    repo = _Repo({"visual_description": "x"})
    ok, reason = await bf.reembed_existing(cand, _Embedder((None, None)), repo, _Tags())
    assert not ok
    assert reason == "provider_error: empty result"
    assert repo.updated == []


@pytest.mark.asyncio
async def test_reembed_tolerates_non_list_detected_fields() -> None:
    """``detected_objects`` / ``detected_scenes`` come out of a jsonb column
    and can be NULL or a bare string; they must not reach the text builder
    as something other than a list of strings."""
    cand = bf.BackfillCandidate(7, 70, "p7", "T", "", "u", has_analysis=True)
    repo = _Repo(
        {
            "visual_description": "x",
            "detected_objects": None,
            "detected_scenes": "not-a-list",
            "detected_text": None,
        }
    )
    emb = _Embedder(([0.1], None))
    ok, reason = await bf.reembed_existing(cand, emb, repo, _Tags())
    assert ok and reason is None
    assert "not-a-list" not in emb.texts[0]


# ---------------------------------------------------------------------------
# small pure helpers
# ---------------------------------------------------------------------------
def test_names_keeps_only_rows_with_a_usable_tag_name() -> None:
    rows = [
        {"tags": {"name": "hanfu"}},
        {"tags": None},  # LEFT JOIN miss
        {"tags": {}},  # tag row with no name column
        {"tags": {"name": ""}},  # empty name is not a tag
        {"tags": "oops"},  # not a dict
        "not-a-dict",  # not even a row
        {"tags": {"name": 42}},  # coerced to str
    ]
    assert bf._names(rows) == ["hanfu", "42"]  # type: ignore[arg-type]
    assert bf._names([]) == []
    assert bf._names(None) == []  # type: ignore[arg-type]


def test_as_list_coerces_members_and_refuses_non_lists() -> None:
    assert bf._as_list(["a", "", None, 3]) == ["a", "3"]
    assert bf._as_list([]) == []
    assert bf._as_list(None) == []
    assert bf._as_list("scene") == []  # a bare string is NOT a one-item list
    assert bf._as_list({"a": 1}) == []


def test_first_cover_takes_the_first_usable_url_only() -> None:
    assert bf._first_cover(["http://a", "http://b"]) == "http://a"
    assert bf._first_cover([None, "http://b"]) is None  # first is the cover
    assert bf._first_cover([]) is None
    assert bf._first_cover(None) is None
    assert bf._first_cover("http://a") is None  # a bare string is not a list
    assert bf._first_cover(123) is None  # not a list at all
    assert bf._first_cover([0]) is None  # falsy first element


def test_candidate_statement_is_owner_scoped_and_null_vector_only() -> None:
    """The listing must never leak another user's rows and must only pick
    rows whose vector is actually missing (not merely un-analysed)."""
    sql = str(bf._missing_embedding_stmt("u-1"))
    assert "resources.creator_id = " in sql
    assert "resources.source_type = " in sql
    assert "resources.is_trashed IS false" in sql
    assert "resource_analysis.content_embedding IS NULL" in sql
    assert "JOIN public.parsed_media" in sql
    assert "LEFT OUTER JOIN public.resource_analysis" in sql
    # Composite PK (resource_id, analysis_level): without the level pin a
    # resource with an embedded L1 row plus a NULL-vector row at another
    # level would re-qualify (and re-bill) on every call.
    assert "resource_analysis.analysis_level = " in sql
    # No-analysis rows without a cover cannot be dispatched, so they are
    # excluded in SQL rather than fetched and then reported forever.
    assert "cover_urls ->> " in sql and "IS NOT NULL" in sql


@pytest.mark.asyncio
async def test_list_candidates_refuses_a_falsy_owner() -> None:
    """``creator_id = NULL`` would select exactly the orphan rows nobody
    should be billed for."""
    assert await bf.list_candidates("", 10) == ([], 0)


@pytest.mark.asyncio
async def test_reembed_does_not_claim_success_when_the_write_touched_no_row() -> None:
    """update_embedding returns None when the analysis row vanished between
    the read and the write: nothing landed, so it must not count as done."""

    class _Gone(_Repo):
        async def update_embedding(self, rid: int, vec, text, analysis_level=None):
            return None

    cand = bf.BackfillCandidate(7, 70, "p7", "T", "", "u", has_analysis=True)
    ok, reason = await bf.reembed_existing(
        cand, _Embedder(([0.1], None)), _Gone({"visual_description": "x"}), _Tags()
    )
    assert not ok and reason == "analysis_row_missing"
