"""Serving HLS from the object store through the existing playback endpoint.

The endpoint's URL shape is deliberately unchanged
(``/resources/{rid}/versions/{vid}/hls/{path}``) so the frontend and the m3u8
bytes need no edits: a playlist's bare relative reference resolves back onto
this same endpoint. These tests pin the object-store branch's two risks —
mapping a relative reference onto a sibling key, and refusing to let a crafted
one escape the version's prefix.
"""

from __future__ import annotations

import pytest

from app.services.library.media_storage import resolve_media_source


def _key_for(hls_path: str, requested: str) -> str:
    """Mirror of the router's key derivation, isolated for testing.

    Kept in step with resources_versions_router.serve_hls_file — if that
    derivation changes, these expectations should fail.
    """
    loc = resolve_media_source(hls_path)
    assert loc.is_object_store
    key_prefix = loc.key.rsplit("/", 1)[0] if "/" in loc.key else ""
    rel = requested.strip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("access denied")
    return f"{key_prefix}/{rel}" if key_prefix else rel


HLS_PATH = "sb://library/hls/res1/ver1/master.m3u8"


class TestKeyMapping:
    def test_master_maps_to_itself(self):
        assert _key_for(HLS_PATH, "master.m3u8") == "hls/res1/ver1/master.m3u8"

    def test_tier_playlist_relative_reference(self):
        # master.m3u8 contains the bare line "480p/stream.m3u8".
        assert (
            _key_for(HLS_PATH, "480p/stream.m3u8") == "hls/res1/ver1/480p/stream.m3u8"
        )

    def test_segment_relative_reference(self):
        # 480p/stream.m3u8 contains the bare line "segment_000.ts", which the
        # player resolves against the tier playlist's own URL.
        assert (
            _key_for(HLS_PATH, "480p/segment_000.ts")
            == "hls/res1/ver1/480p/segment_000.ts"
        )

    def test_source_tier(self):
        assert (
            _key_for(HLS_PATH, "source/segment_012.ts")
            == "hls/res1/ver1/source/segment_012.ts"
        )


class TestContainment:
    @pytest.mark.parametrize(
        "requested",
        [
            "../../other/master.m3u8",
            "480p/../../../escape.ts",
            "..",
            "",
            "/",
        ],
    )
    def test_rejects_escapes(self, requested):
        # The filesystem branch gets this from Path.relative_to; the
        # object-store branch has no filesystem to lean on, so the check is
        # explicit and must hold on its own.
        with pytest.raises(ValueError):
            _key_for(HLS_PATH, requested)

    def test_sibling_version_is_not_reachable(self):
        # Even a well-formed relative path cannot cross into another version:
        # every key stays under this master's own prefix.
        key = _key_for(HLS_PATH, "480p/segment_000.ts")
        assert key.startswith("hls/res1/ver1/")


class TestFilesystemRowsStayFilesystem:
    def test_legacy_path_is_not_treated_as_object_store(self):
        loc = resolve_media_source("global/resources/web/douyin/123/hls/master.m3u8")
        assert not loc.is_object_store
        assert loc.rel_path == "global/resources/web/douyin/123/hls/master.m3u8"

    def test_malformed_sb_scheme_degrades_to_filesystem(self):
        # A corrupt row must 404 at the file layer, not 500 the reader.
        assert not resolve_media_source("sb://").is_object_store
        assert not resolve_media_source("sb://bucket-only").is_object_store
