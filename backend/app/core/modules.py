"""Module permission constants and utilities."""

from typing import Dict, List

# All controllable modules with display names
MODULE_DEFINITIONS: List[Dict[str, str]] = [
    {"key": "parser", "name": "Parser", "description": "URL parsing, video downloading"},
    {"key": "resources", "name": "Resources", "description": "Resource library CRUD, folders, tags"},
    {"key": "library", "name": "Library", "description": "Media library browsing"},
    {"key": "projects", "name": "Projects", "description": "Project management, file review"},
    {"key": "ai_analysis", "name": "AI Analysis", "description": "Transcription, summary, visual analysis"},
    {"key": "dashboard", "name": "Dashboard", "description": "Statistics and analytics"},
    {"key": "cleanup", "name": "Cleanup", "description": "Storage cleanup suggestions"},
]

ALL_MODULE_KEYS: List[str] = [m["key"] for m in MODULE_DEFINITIONS]

# Default: all modules enabled
DEFAULT_ENABLED_MODULES: List[str] = list(ALL_MODULE_KEYS)

# Map module keys to API route prefixes (for future backend enforcement)
MODULE_ROUTE_MAP: Dict[str, List[str]] = {
    "parser": ["/api/v1/media"],
    "resources": ["/api/v1/resources"],
    "library": ["/api/v1/libraries"],
    "projects": ["/api/v1/projects"],
    "ai_analysis": ["/api/v1/ai", "/api/v1/analysis"],
    "dashboard": ["/api/v1/system/stats"],
    "cleanup": ["/api/v1/cleanup"],
}


def validate_module_keys(keys: List[str]) -> List[str]:
    """Validate that all keys are valid module keys. Returns list of invalid keys."""
    return [k for k in keys if k not in ALL_MODULE_KEYS]
