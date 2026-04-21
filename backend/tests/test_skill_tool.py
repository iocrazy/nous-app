import pytest
from app.services.skill_tool_service import SkillToolService


class FakeSkillRepo:
    async def get_by_slug(self, slug):
        if slug == "script-outline":
            return {"id": 123, "slug": "script-outline", "body_md": "OUTLINE BODY", "description": "Outline"}
        return None

    async def get_file(self, skill_id, path):
        if skill_id == 123 and path == "references/examples.md":
            return {"path": path, "content": "EXAMPLES", "file_type": "markdown"}
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
    result = await svc.execute({"skill": "script-outline", "file": "references/examples.md"})
    assert result["prompt"] == "EXAMPLES"
    assert result["file"] == "references/examples.md"
    assert result["file_type"] == "markdown"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_file_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "script-outline", "file": "references/doesnotexist.md"})
    assert "error" in result
    assert "doesnotexist.md" in result["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_whitespace_only_skill_returns_error():
    svc = SkillToolService(FakeSkillRepo())
    result = await svc.execute({"skill": "   "})
    assert "error" in result and "required" in result["error"].lower()
