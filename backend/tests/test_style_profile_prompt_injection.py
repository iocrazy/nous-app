"""Tests for project style profile → generation prompt injection (Phase 4 M9)."""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.services.storyboard.storyboard_ai_service import StoryboardAIService


class FakeStyleRepo:
    def __init__(self, profile: Optional[dict] = None, error: bool = False):
        self.profile = profile
        self.error = error
        self.calls: list[int] = []

    async def get_for_storyboard_project(self, sb_project_id: int):
        self.calls.append(sb_project_id)
        if self.error:
            raise RuntimeError("db down")
        return self.profile


@pytest.fixture
def service() -> StoryboardAIService:
    return StoryboardAIService()


def _patch_repo(monkeypatch: pytest.MonkeyPatch, repo: FakeStyleRepo) -> None:
    monkeypatch.setattr(
        "app.repositories.project_style_profile_repository."
        "get_project_style_profile_repository",
        lambda: repo,
    )


# ============================================================
# build_project_style_fragment
# ============================================================


@pytest.mark.asyncio
async def test_fragment_joins_style_md_and_traits(
    service: StoryboardAIService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repo(
        monkeypatch,
        FakeStyleRepo(
            {
                "style_md": "Moody film noir.",
                "visual_style": {"palette": "muted", "mood": "tense", "empty": ""},
            }
        ),
    )
    fragment = await service.build_project_style_fragment("55")
    assert fragment == "Moody film noir. — palette: muted, mood: tense"


@pytest.mark.asyncio
async def test_fragment_clips_long_style(
    service: StoryboardAIService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repo(monkeypatch, FakeStyleRepo({"style_md": "x" * 5000}))
    fragment = await service.build_project_style_fragment("55")
    assert len(fragment) == 600


@pytest.mark.asyncio
async def test_fragment_empty_without_profile_or_on_failure(
    service: StoryboardAIService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repo(monkeypatch, FakeStyleRepo(None))
    assert await service.build_project_style_fragment("55") == ""
    _patch_repo(monkeypatch, FakeStyleRepo(error=True))
    assert await service.build_project_style_fragment("55") == ""
    # Non-numeric id (defensive) also degrades to empty, not raise.
    _patch_repo(monkeypatch, FakeStyleRepo({"style_md": "x"}))
    assert await service.build_project_style_fragment("not-a-number") == ""


# ============================================================
# generate_image prompt assembly
# ============================================================


class FakeImageProvider:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def generate(self, prompt: str, model: str, **kwargs: Any):
        from dataclasses import dataclass

        self.prompts.append(prompt)

        @dataclass
        class R:
            url: str = "http://img"
            model: str = "m"

        return R()


@pytest.mark.asyncio
async def test_generate_image_appends_style_suffix(
    service: StoryboardAIService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repo(monkeypatch, FakeStyleRepo({"style_md": "Anime cel-shading."}))
    provider = FakeImageProvider()
    monkeypatch.setattr(
        "app.services.storyboard.storyboard_ai_service."
        "provider_registry.get_image_provider",
        lambda name: provider,
    )
    await service.generate_image(
        project_id="55",
        node_id="n1",
        prompt="a chef plating noodles",
        model="m",
        provider_name="fake",
    )
    assert provider.prompts == ["a chef plating noodles. Style: Anime cel-shading."]


@pytest.mark.asyncio
async def test_generate_image_unchanged_without_profile(
    service: StoryboardAIService, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_repo(monkeypatch, FakeStyleRepo(None))
    provider = FakeImageProvider()
    monkeypatch.setattr(
        "app.services.storyboard.storyboard_ai_service."
        "provider_registry.get_image_provider",
        lambda name: provider,
    )
    await service.generate_image(
        project_id="55",
        node_id="n1",
        prompt="a chef plating noodles",
        model="m",
        provider_name="fake",
    )
    assert provider.prompts == ["a chef plating noodles"]
