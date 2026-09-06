"""Unit tests for the resource_gen_params backfill (pure helpers + router wiring)."""

from types import SimpleNamespace

from app.api.admin.backfill_router import _BACKFILLS, workflow_kwargs
from app.workflows.backfill_resource_gen_params import build_patch


class TestBuildPatch:
    def test_fills_every_empty_field(self):
        patch = build_patch(
            {"gen_prompt": "", "gen_prompt_negative": None, "gen_params": None},
            {
                "positive": "cat",
                "negative": "dog",
                "params": {"tool": "a1111", "seed": 1},
            },
        )
        assert patch == {
            "gen_params": {"tool": "a1111", "seed": 1},
            "gen_prompt": "cat",
            "gen_prompt_negative": "dog",
            # mig 455: writing the positive text stamps who wrote it.
            "prompt_origin": "extracted",
        }

    def test_never_clobbers_user_values(self):
        patch = build_patch(
            {
                "gen_prompt": "user typed",
                "gen_prompt_negative": "mine",
                "gen_params": {"x": 1},
            },
            {"positive": "cat", "negative": "dog", "params": {"tool": "a1111"}},
        )
        assert patch == {}

    def test_no_params_means_no_params_key(self):
        patch = build_patch(
            {"gen_prompt": ""}, {"positive": "cat", "negative": None, "params": None}
        )
        assert patch == {"gen_prompt": "cat", "prompt_origin": "extracted"}


class TestRouterWiring:
    def test_registered(self):
        assert "resource_gen_params" in _BACKFILLS

    def test_run_user_id_passed_only_to_workflows_that_accept_it(self):
        body = SimpleNamespace(dry_run=True, limit=10)
        assert workflow_kwargs("resource_gen_params", body, "admin-uuid") == {
            "dry_run": True,
            "limit": 10,
            "run_user_id": "admin-uuid",
        }
        assert workflow_kwargs("issue_scope", body, "admin-uuid") == {
            "dry_run": True,
            "limit": 10,
        }


class TestGenerationRowSource:
    """Second backfill source: resources promoted from generated_media before
    migration 440 (the PNG carries no chunks; the provenance is in the row)."""

    def test_promoted_row_yields_params(self):
        from app.workflows.backfill_resource_gen_params import extracted_from_generation

        out = extracted_from_generation(
            {
                "model": "gpt-5.4",
                "provider": "codex",
                "params": {"ratio": "16:9", "quality": "high"},
            }
        )
        assert out == {
            "positive": None,
            "negative": None,
            "params": {
                "tool": "nous",
                "model": "gpt-5.4",
                "provider": "codex",
                "aspect_ratio": "16:9",
            },
        }

    def test_row_without_provenance_is_none(self):
        from app.workflows.backfill_resource_gen_params import extracted_from_generation

        assert (
            extracted_from_generation({"model": "", "provider": None, "params": {}})
            is None
        )
        assert extracted_from_generation(None) is None


class TestSystemScope:
    """Resources mixes in UserScoped(creator_id). With SCOPE_ENFORCE_RESOURCES
    on (production; never in unit tests) a scoped-mapper touch with no ambient
    scope is fail-closed, so the SYSTEM boundary has to open before the first
    statement — both the scan and the per-row write.
    """

    def _source(self) -> str:
        from pathlib import Path

        import app.workflows.backfill_resource_gen_params as mod

        return Path(mod.__file__).read_text()

    def test_scan_runs_under_system_request_scope(self):
        source = self._source()
        assert "system_request_scope(" in source
        assert source.index("system_request_scope(") < source.index("select(")
        assert source.index("system_request_scope(") < source.index("read_scope()")

    def test_write_runs_under_system_request_scope(self):
        source = self._source()
        assert source.index("system_request_scope(") < source.index("write_scope()")
