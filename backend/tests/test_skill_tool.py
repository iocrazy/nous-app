import pytest

from app.services.ai.skills.skill_tool_service import SkillToolService


class FakeSkillRepo:
    async def get_by_slug(self, slug):
        if slug == "script-outline":
            return {
                "id": 123,
                "slug": "script-outline",
                "body_md": "OUTLINE BODY",
                "description": "Outline",
            }
        return None

    async def get_file(self, skill_id, path):
        if skill_id != 123:
            return None
        if path == "references/examples.md":
            return {"path": path, "content": "EXAMPLES", "file_type": "markdown"}
        if path == "assets/brief.pdf":
            return {
                "path": path,
                "content": None,
                "file_type": "binary-ref",
                "binary_url": "https://cdn.example.com/brief.pdf",
            }
        return None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "missing"})
    assert "error" in result
    assert "missing" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": ""})
    assert "error" in result and "required" in result["error"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_skill_key_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({})
    assert "error" in result and "required" in result["error"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_default_returns_body_md():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline"})
    assert result["prompt"] == "OUTLINE BODY"
    assert result["skill"] == "script-outline"
    assert result["description"] == "Outline"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_file_returns_file_content():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute(
        {"skill": "script-outline", "file": "references/examples.md"}
    )
    assert result["prompt"] == "EXAMPLES"
    assert result["file"] == "references/examples.md"
    assert result["file_type"] == "markdown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_file_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute(
        {"skill": "script-outline", "file": "references/doesnotexist.md"}
    )
    assert "error" in result
    assert "doesnotexist.md" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_whitespace_only_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "   "})
    assert "error" in result and "required" in result["error"].lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_binary_ref_returns_url_and_note():
    """binary-ref files must hand back the URL + an explicit note so the
    agent doesn't silently swallow an empty prompt."""
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline", "file": "assets/brief.pdf"})
    assert "error" not in result
    assert result["binary_url"] == "https://cdn.example.com/brief.pdf"
    assert result["file_type"] == "binary-ref"
    # Both the legacy `prompt` channel and the explicit `note` field
    # carry the diagnostic so agents using either contract see it.
    assert "binary reference" in result["prompt"].lower()
    assert "binary reference" in result["note"].lower()


# ─── audit #14: oversized skill content is capped ────────────────────


class _BigSkillRepo:
    async def get_by_slug(self, slug):
        if slug == "big":
            return {"id": 1, "slug": "big", "body_md": "x" * 70000, "description": "B"}
        if slug == "small":
            return {"id": 2, "slug": "small", "body_md": "tiny", "description": "S"}
        return None

    async def get_file(self, skill_id, path):
        if skill_id == 1 and path == "huge.md":
            return {"path": path, "content": "y" * 70000, "file_type": "markdown"}
        return None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversized_body_md_truncated_with_notice():
    from app.services.ai.skills.skill_tool_service import MAX_SKILL_CONTENT_CHARS

    svc = SkillToolService(_BigSkillRepo())
    out = await svc.execute({"skill": "big"})
    assert len(out["prompt"]) < 70000
    assert "truncated" in out["prompt"]
    assert out["prompt"][:MAX_SKILL_CONTENT_CHARS] == "x" * MAX_SKILL_CONTENT_CHARS
    assert out["prompt"][MAX_SKILL_CONTENT_CHARS] == "\n"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_oversized_file_content_truncated_with_notice():
    svc = SkillToolService(_BigSkillRepo())
    out = await svc.execute({"skill": "big", "file": "huge.md"})
    assert "truncated" in out["prompt"]
    assert len(out["prompt"]) < 70000


@pytest.mark.unit
@pytest.mark.asyncio
async def test_small_skill_content_unchanged():
    svc = SkillToolService(_BigSkillRepo())
    out = await svc.execute({"skill": "small"})
    assert out["prompt"] == "tiny"
    assert "truncated" not in out["prompt"]
