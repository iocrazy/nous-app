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
        assert patch == {"gen_prompt": "cat"}


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
