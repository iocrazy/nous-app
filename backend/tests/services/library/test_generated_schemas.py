"""Contract tests for the ``/generated`` request/response schemas.

The request models are the only place a malformed body is rejected: everything
downstream ``int()``s the ids and trusts the action. A validator that quietly
accepts "neither asset_id nor new_asset" hands the service a no-op it cannot
tell from a real save — the silent-no-op class this codebase refuses.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas.generated import (
    BatchRequest,
    CleanupRequest,
    NewAssetSpec,
    SaveAsAssetRequest,
    derive_title,
)


class TestSaveAsAssetRequest:
    def test_accepts_asset_id_only(self):
        req = SaveAsAssetRequest(asset_id="123")
        assert req.asset_id == "123"
        assert req.new_asset is None
        assert req.slot == "unsorted"
        assert req.loadout_id is None

    def test_accepts_new_asset_only(self):
        req = SaveAsAssetRequest(
            new_asset=NewAssetSpec(asset_type="character", name="Ada")
        )
        assert req.new_asset is not None
        assert req.new_asset.asset_type == "character"

    def test_rejects_neither(self):
        with pytest.raises(ValidationError, match="exactly one"):
            SaveAsAssetRequest()

    def test_rejects_both(self):
        with pytest.raises(ValidationError, match="exactly one"):
            SaveAsAssetRequest(
                asset_id="123", new_asset=NewAssetSpec(asset_type="prop", name="Lamp")
            )

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError) as exc:
            SaveAsAssetRequest(asset_id="123", slots="hero")
        assert "slots" in str(exc.value)

    def test_rejects_blank_slot(self):
        with pytest.raises(ValidationError):
            SaveAsAssetRequest(asset_id="123", slot="")

    def test_rejects_non_numeric_asset_id(self):
        with pytest.raises(ValidationError):
            SaveAsAssetRequest(asset_id="abc")

    def test_rejects_asset_id_past_bigint(self):
        with pytest.raises(ValidationError):
            SaveAsAssetRequest(asset_id=str(2**63))

    def test_new_asset_rejects_blank_name(self):
        with pytest.raises(ValidationError):
            NewAssetSpec(asset_type="character", name="")

    def test_new_asset_rejects_unknown_type(self):
        with pytest.raises(ValidationError):
            NewAssetSpec(asset_type="vehicle", name="Truck")

    def test_new_asset_rejects_overlong_name(self):
        NewAssetSpec(asset_type="prop", name="n" * 200)
        with pytest.raises(ValidationError):
            NewAssetSpec(asset_type="prop", name="n" * 201)

    def test_new_asset_rejects_unknown_field(self):
        with pytest.raises(ValidationError) as exc:
            NewAssetSpec(asset_type="prop", name="Lamp", description="x")
        assert "description" in str(exc.value)


class TestBatchRequest:
    def test_accepts_save(self):
        req = BatchRequest(ids=["1", "2"], action="save")
        assert req.action == "save"
        assert req.save_as_asset is None

    def test_accepts_save_as_asset_with_payload(self):
        req = BatchRequest(
            ids=["1"],
            action="save_as_asset",
            save_as_asset=SaveAsAssetRequest(asset_id="9"),
        )
        assert req.save_as_asset is not None

    def test_rejects_save_as_asset_without_payload(self):
        with pytest.raises(ValidationError, match="save_as_asset"):
            BatchRequest(ids=["1"], action="save_as_asset")

    def test_rejects_payload_on_save(self):
        with pytest.raises(ValidationError, match="save_as_asset"):
            BatchRequest(
                ids=["1"],
                action="save",
                save_as_asset=SaveAsAssetRequest(asset_id="9"),
            )

    def test_rejects_payload_on_delete(self):
        with pytest.raises(ValidationError, match="save_as_asset"):
            BatchRequest(
                ids=["1"],
                action="delete",
                save_as_asset=SaveAsAssetRequest(asset_id="9"),
            )

    def test_rejects_unknown_action(self):
        with pytest.raises(ValidationError):
            BatchRequest(ids=["1"], action="promote")

    def test_rejects_empty_ids(self):
        with pytest.raises(ValidationError):
            BatchRequest(ids=[], action="save")

    def test_rejects_more_than_200_ids(self):
        with pytest.raises(ValidationError):
            BatchRequest(ids=[str(i) for i in range(201)], action="save")

    def test_accepts_exactly_200_ids(self):
        req = BatchRequest(ids=[str(i) for i in range(200)], action="delete")
        assert len(req.ids) == 200

    def test_rejects_non_numeric_id(self):
        with pytest.raises(ValidationError):
            BatchRequest(ids=["1", "not-an-id"], action="save")

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError) as exc:
            BatchRequest(ids=["1"], action="save", scope_id="7")
        assert "scope_id" in str(exc.value)


class TestCleanupRequest:
    def test_defaults_are_dry_run_and_30_days(self):
        req = CleanupRequest()
        assert req.dry_run is True
        assert req.older_than_days == 30

    def test_rejects_zero_days(self):
        with pytest.raises(ValidationError):
            CleanupRequest(older_than_days=0)

    def test_rejects_beyond_ten_years(self):
        with pytest.raises(ValidationError):
            CleanupRequest(older_than_days=3651)

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError) as exc:
            CleanupRequest(olderThanDays=7)
        assert "olderThanDays" in str(exc.value)


class TestDeriveTitle:
    def test_cuts_at_first_period(self):
        assert (
            derive_title(
                "A wide shot. Then a close-up.", "image", "m", "p", "canvas_run"
            )
            == "A wide shot"
        )

    def test_cuts_at_ideographic_period(self):
        assert derive_title(
            "一个远景镜头。然后特写。", "image", "m", "p", "canvas_run"
        ) == ("一个远景镜头")

    def test_cuts_at_newline(self):
        assert (
            derive_title(
                "A wide shot\nThen a close-up", "image", "m", "p", "canvas_run"
            )
            == "A wide shot"
        )

    def test_cuts_at_whichever_delimiter_comes_first(self):
        assert derive_title("ab\ncd. ef", "image", None, None, "canvas_run") == "ab"
        assert derive_title("ab. cd\nef", "image", None, None, "canvas_run") == "ab"

    def test_caps_at_80_chars_with_ellipsis(self):
        long_prompt = "x" * 200
        title = derive_title(long_prompt, "image", "m", "p", "canvas_run")
        assert len(title) == 80
        assert title.endswith("…")
        assert title[:-1] == "x" * 79

    def test_exactly_80_chars_is_not_truncated(self):
        title = derive_title("y" * 80, "image", "m", "p", "canvas_run")
        assert title == "y" * 80
        assert not title.endswith("…")

    def test_sentence_cut_then_length_cap_both_apply(self):
        # First sentence is itself over the cap.
        title = derive_title("z" * 200 + ". tail", "image", None, None, "canvas_run")
        assert len(title) == 80
        assert title.endswith("…")

    def test_blank_prompt_falls_back_to_model(self):
        assert derive_title("", "image", "seedream-4", "volc", "canvas_run") == (
            "image · seedream-4"
        )

    def test_none_prompt_falls_back_to_model(self):
        assert derive_title(None, "image", "seedream-4", "volc", "canvas_run") == (
            "image · seedream-4"
        )

    def test_falls_back_to_provider_when_model_missing(self):
        assert derive_title(None, "video", None, "volc", "shot_video") == "video · volc"

    def test_falls_back_to_origin_kind_when_model_and_provider_missing(self):
        assert derive_title(None, "audio", None, None, "agent_run") == (
            "audio · agent_run"
        )

    def test_whitespace_only_prompt_falls_back(self):
        assert derive_title("   \n  ", "image", None, None, "chat_upload") == (
            "image · chat_upload"
        )

    def test_prompt_that_is_only_a_delimiter_falls_back(self):
        # ".foo" has an empty first sentence — a title of "" would render as a
        # blank card, so the fallback has to win here too.
        assert derive_title(". foo", "image", "seedream-4", None, "canvas_run") == (
            "image · seedream-4"
        )

    def test_strips_surrounding_whitespace(self):
        assert derive_title("  hello world  . tail", "image", None, None, "x") == (
            "hello world"
        )
