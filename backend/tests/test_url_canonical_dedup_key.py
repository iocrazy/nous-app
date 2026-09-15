"""canonical_url — the dedup key that survives share-link tracking params.

Regression anchor (production, 2026-09-15): the same bilibili video submitted
twice produced two different ``original_url`` strings because ``spm_id_from``
differed per surface. The L2 "already in your library" probe compared those
strings for equality, read the second submit as "never downloaded", ran a full
parse and dispatched a Download task — which the in-workflow cache check then
discarded as a cache hit. The user saw a download they did not ask for.
"""

from __future__ import annotations

import pytest

from app.utils.url_canonical import canonical_url

pytestmark = pytest.mark.unit

# The two real URLs from the report, seven days apart.
SUBMIT_0908 = (
    "https://www.bilibili.com/video/BV1nUtj6QEuS/"
    "?spm_id_from=333.1007.tianma.1-2-2.click"
    "&vd_source=79705ab0af488edbc043d7490c0306dc"
)
SUBMIT_0915 = (
    "https://www.bilibili.com/video/BV1nUtj6QEuS/"
    "?spm_id_from=333.1391.0.0"
    "&vd_source=79705ab0af488edbc043d7490c0306dc"
)


def test_the_reported_pair_collapses_to_one_key():
    assert canonical_url(SUBMIT_0908) == canonical_url(SUBMIT_0915)
    assert canonical_url(SUBMIT_0908) == "https://bilibili.com/video/BV1nUtj6QEuS"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bilibili.com/video/BV1x?spm_id_from=1",
        "https://www.bilibili.com/video/BV1x?vd_source=deadbeef",
        "https://www.bilibili.com/video/BV1x?utm_source=x&utm_campaign=y",
        "https://www.bilibili.com/video/BV1x/",
        "https://bilibili.com/video/BV1x#comments",
        "HTTPS://WWW.BiliBili.com/video/BV1x",
    ],
)
def test_noise_forms_share_one_key(url):
    assert canonical_url(url) == "https://bilibili.com/video/BV1x"


def test_identity_bearing_params_are_kept():
    """A denylist must never merge two different items."""
    assert canonical_url("https://www.youtube.com/watch?v=abc&si=xyz") != canonical_url(
        "https://www.youtube.com/watch?v=def&si=xyz"
    )
    # bilibili multi-part video: ?p= selects the part.
    assert canonical_url(
        "https://www.bilibili.com/video/BV1x?p=2&spm_id_from=1"
    ) != canonical_url("https://www.bilibili.com/video/BV1x?p=3&spm_id_from=1")
    # `mid` is an author id on space URLs — explicitly NOT in the denylist.
    assert canonical_url("https://space.bilibili.com/x?mid=1") != canonical_url(
        "https://space.bilibili.com/x?mid=2"
    )


def test_param_order_does_not_matter():
    assert canonical_url("https://x.example/a?b=1&c=2") == canonical_url(
        "https://x.example/a?c=2&b=1"
    )


def test_idempotent():
    once = canonical_url(SUBMIT_0915)
    assert canonical_url(once) == once


@pytest.mark.parametrize("junk", ["", "   ", "not a url", "mailto:a@b.c"])
def test_unparseable_input_degrades_instead_of_raising(junk):
    """A canonical form is an optimisation. Failing to produce one must mean
    "no dedup hit", never a 500 at the API edge."""
    assert canonical_url(junk) == junk.strip()


def test_stamp_helper_is_immutable_and_only_fires_on_url_writes():
    from app.repositories.media_repository import _stamp_canonical_url

    data = {"original_url": SUBMIT_0915, "title": "Krea2"}
    out = _stamp_canonical_url(data)
    assert out["canonical_url"] == "https://bilibili.com/video/BV1nUtj6QEuS"
    assert "canonical_url" not in data  # caller's dict untouched

    # A write that doesn't touch original_url leaves the stored key alone.
    unrelated = {"video_download_status": "completed"}
    assert _stamp_canonical_url(unrelated) == unrelated


# ── the predicate both readers share ─────────────────────────────────────


def _compiled(url: str) -> str:
    from app.utils.url_canonical import parsed_media_url_predicate

    return str(
        parsed_media_url_predicate(url).compile(compile_kwargs={"literal_binds": True})
    )


def test_predicate_matches_both_the_verbatim_url_and_the_key():
    sql = _compiled(SUBMIT_0915)
    assert "original_url" in sql
    assert "canonical_url" in sql
    assert "https://bilibili.com/video/BV1nUtj6QEuS" in sql


def test_predicate_drops_the_key_side_when_there_is_no_key():
    """An empty canonical key would match every unusable-URL row — a
    wildcard where a dedup probe is meant to be."""
    sql = _compiled("")
    assert "canonical_url" not in sql
    assert "original_url" in sql
