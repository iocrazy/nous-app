"""Smoke tests — verify basic imports and app creation work."""

import pytest


def test_app_imports():
    """Verify the FastAPI app can be imported without errors."""
    from app.main import app

    assert app is not None
    assert app.title is not None


def test_api_router_imports():
    """Verify all API routers can be imported."""
    from app.api import api_router

    assert len(api_router.routes) > 0


def test_script_service_imports():
    """Verify script service imports cleanly."""
    from app.services.storyboard.script.script_service import ScriptService

    svc = ScriptService()
    assert svc.project_repo is not None
    assert svc.chapter_repo is not None


def test_script_ai_service_imports():
    """Verify script AI service imports cleanly."""
    from app.services.storyboard.script.script_ai_service import ScriptAIService

    svc = ScriptAIService()
    assert svc.model is not None


def test_display_code_service_imports():
    """Verify display code service imports cleanly."""
    from app.services.library.display_code_service import generate_display_code

    assert callable(generate_display_code)


def test_schema_validation():
    """Verify Pydantic schemas validate correctly."""
    from app.schemas.script import ScriptChapterCreate, ScriptProjectCreate

    project = ScriptProjectCreate(name="Test", project_id=123)
    assert project.name == "Test"
    assert project.project_id == 123

    chapter = ScriptChapterCreate(title="Ch1", position_x=100, position_y=200)
    assert chapter.title == "Ch1"
    assert chapter.position_x == 100


def test_schema_validation_rejects_invalid():
    """Verify schemas reject invalid input."""
    from pydantic import ValidationError

    from app.schemas.script import ScriptProjectCreate

    with pytest.raises(ValidationError):
        ScriptProjectCreate(name="", project_id=123)  # name too short
