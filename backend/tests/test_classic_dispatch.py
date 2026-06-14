"""Tests for the ClassicMode node-type → provider_slug dispatch map
(Phase 5a C2)."""

from __future__ import annotations

import pytest

from app.services.canvas.classic_dispatch import (
    ClassicDispatchError,
    resolve_provider_slug,
)


class TestComfyMapping:
    def test_comfy_resolves_nous_workflow_from_data(self):
        slug = resolve_provider_slug("comfy", {"data": {"workflow_slug": "render-xl"}})
        assert slug == "nous/render-xl"

    def test_comfy_reads_flat_data_payload(self):
        # Caller may pass the data dict directly (data merged at top level).
        slug = resolve_provider_slug("comfy", {"workflow_slug": "upscale"})
        assert slug == "nous/upscale"

    def test_comfy_accepts_camel_case_key(self):
        slug = resolve_provider_slug("comfy", {"workflowSlug": "inpaint"})
        assert slug == "nous/inpaint"

    def test_distinct_workflows_route_distinctly(self):
        a = resolve_provider_slug("comfy", {"workflow_slug": "a"})
        b = resolve_provider_slug("comfy", {"workflow_slug": "b"})
        assert a == "nous/a"
        assert b == "nous/b"

    def test_comfy_missing_workflow_slug_raises(self):
        with pytest.raises(ClassicDispatchError) as exc:
            resolve_provider_slug("comfy", {"data": {}})
        assert "workflow_slug" in str(exc.value)

    def test_comfy_blank_workflow_slug_raises(self):
        with pytest.raises(ClassicDispatchError):
            resolve_provider_slug("comfy", {"workflow_slug": "   "})


class TestLlmMapping:
    def test_llm_uses_provider_slug_from_data(self):
        slug = resolve_provider_slug(
            "llm", {"data": {"provider_slug": "anthropic/claude-sonnet-4-6"}}
        )
        assert slug == "anthropic/claude-sonnet-4-6"

    def test_llm_falls_back_to_model_key(self):
        slug = resolve_provider_slug("llm", {"model": "qwen-plus"})
        assert slug == "qwen-plus"

    def test_llm_without_override_returns_none_for_default_model(self):
        # None is meaningful: run_prompt falls back to DEFAULT_MODEL. It is
        # NOT the "no mapping" signal (that path raises).
        assert resolve_provider_slug("llm", {"data": {}}) is None
        assert resolve_provider_slug("llm", None) is None


class TestNoMapping:
    def test_unknown_node_type_raises(self):
        with pytest.raises(ClassicDispatchError) as exc:
            resolve_provider_slug("frobnicate", {})
        assert "frobnicate" in str(exc.value)

    @pytest.mark.parametrize("literal_type", ["image", "prompt", "output"])
    def test_literal_and_sink_types_have_no_provider_mapping(self, literal_type):
        # These hold data / collect results — they never dispatch. Raising
        # (not None) keeps us from silently routing to a wrong provider.
        with pytest.raises(ClassicDispatchError):
            resolve_provider_slug(literal_type, {})

    def test_missing_type_raises(self):
        with pytest.raises(ClassicDispatchError):
            resolve_provider_slug(None, {})
