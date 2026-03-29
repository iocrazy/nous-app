"""Shared test fixtures for the MediaHub backend test suite."""

import pytest


@pytest.fixture
def sample_project_data():
    """Sample project creation data for tests."""
    return {
        "name": "Test Project",
        "description": "A test project",
        "project_type": "internal",
    }


@pytest.fixture
def sample_script_data():
    """Sample script project creation data."""
    return {
        "name": "Test Script",
        "description": "A test script",
    }
