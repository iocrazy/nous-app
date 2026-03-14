# backend/app/core/api_key_scopes.py

"""
API Key Scope Definitions

Defines available permission scopes and endpoint mapping relationships.
"""

from enum import Enum
from typing import Dict, List, Tuple


class ApiKeyScope(str, Enum):
    """API Key permission scope enum"""

    # Video related
    VIDEOS_FETCH = "videos:fetch"
    VIDEOS_FETCH_BATCH = "videos:fetch:batch"
    VIDEOS_VIDEOS_READ = "videos:videos:read"
    VIDEOS_VIDEOS_WRITE = "videos:videos:write"
    VIDEOS_SEARCH = "videos:search"
    VIDEOS_STATISTICS = "videos:statistics"
    VIDEOS_RETRY = "videos:retry"

    # All video permissions (wildcard)
    VIDEOS_ALL = "videos:*"

    # Tags related
    TAGS_READ = "tags:read"
    TAGS_WRITE = "tags:write"
    TAGS_ALL = "tags:*"

    # Resources related
    RESOURCES_READ = "resources:read"
    RESOURCES_WRITE = "resources:write"
    RESOURCES_ALL = "resources:*"

    # User related
    USER_PROFILE_READ = "user:profile:read"
    USER_PROFILE_WRITE = "user:profile:write"


# Legacy scope aliases for backward compatibility
LEGACY_SCOPE_MAP = {
    "douyin:fetch": "videos:fetch",
    "douyin:fetch:batch": "videos:fetch:batch",
    "douyin:videos:read": "videos:videos:read",
    "douyin:videos:write": "videos:videos:write",
    "douyin:search": "videos:search",
    "douyin:statistics": "videos:statistics",
    "douyin:retry": "videos:retry",
    "douyin:*": "videos:*",
}


# Endpoint to scope mapping
# Format: (method, path_pattern): [allowed_scopes]
# Request only needs to match one scope for access
ENDPOINT_SCOPE_MAP: Dict[Tuple[str, str], List[str]] = {
    # Video fetch
    ("POST", "/videos/fetch"): [
        ApiKeyScope.VIDEOS_FETCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("POST", "/videos/fetch/batch"): [
        ApiKeyScope.VIDEOS_FETCH_BATCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Video read
    ("GET", "/videos/videos"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/videos/videos/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Video write
    ("DELETE", "/videos/videos/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_WRITE.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Search
    ("POST", "/videos/videos/search"): [
        ApiKeyScope.VIDEOS_SEARCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Statistics
    ("GET", "/videos/statistics"): [
        ApiKeyScope.VIDEOS_STATISTICS.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Pending downloads list
    ("GET", "/videos/pending"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Retry download
    ("POST", "/videos/retry/{platform_id}"): [
        ApiKeyScope.VIDEOS_RETRY.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Legacy /douyin/ paths (backward compatibility)
    ("POST", "/douyin/fetch"): [
        ApiKeyScope.VIDEOS_FETCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("POST", "/douyin/fetch/batch"): [
        ApiKeyScope.VIDEOS_FETCH_BATCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/douyin/videos"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/douyin/videos/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("DELETE", "/douyin/videos/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_WRITE.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("POST", "/douyin/videos/search"): [
        ApiKeyScope.VIDEOS_SEARCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/douyin/statistics"): [
        ApiKeyScope.VIDEOS_STATISTICS.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/douyin/pending"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("POST", "/douyin/retry/{platform_id}"): [
        ApiKeyScope.VIDEOS_RETRY.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Tags
    ("GET", "/tags"): [
        ApiKeyScope.TAGS_READ.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("POST", "/tags"): [
        ApiKeyScope.TAGS_WRITE.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("PUT", "/tags/{id}"): [
        ApiKeyScope.TAGS_WRITE.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("DELETE", "/tags/{id}"): [
        ApiKeyScope.TAGS_WRITE.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("GET", "/tags/media/{media_id}/tags"): [
        ApiKeyScope.TAGS_READ.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("GET", "/tags/groups"): [
        ApiKeyScope.TAGS_READ.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    # Resources
    ("GET", "/resources"): [
        ApiKeyScope.RESOURCES_READ.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    ("GET", "/resources/{id}"): [
        ApiKeyScope.RESOURCES_READ.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    ("PATCH", "/resources/{id}"): [
        ApiKeyScope.RESOURCES_WRITE.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    ("GET", "/resources/{id}/tags"): [
        ApiKeyScope.TAGS_READ.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
    ("POST", "/resources/{id}/tags"): [
        ApiKeyScope.TAGS_WRITE.value,
        ApiKeyScope.TAGS_ALL.value,
    ],
}


# Available scopes list (for frontend selection)
AVAILABLE_SCOPES = [
    {
        "scope": ApiKeyScope.VIDEOS_FETCH.value,
        "name": "Fetch Video",
        "description": "Fetch single video info via URL",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_FETCH_BATCH.value,
        "name": "Batch Fetch",
        "description": "Fetch multiple videos info in batch",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        "name": "Read Videos",
        "description": "View video list and details",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_VIDEOS_WRITE.value,
        "name": "Manage Videos",
        "description": "Delete video records",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_SEARCH.value,
        "name": "Search Videos",
        "description": "Search videos",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_STATISTICS.value,
        "name": "View Statistics",
        "description": "View statistics data",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_RETRY.value,
        "name": "Retry Download",
        "description": "Retry failed video downloads",
        "category": "Video",
    },
    {
        "scope": ApiKeyScope.VIDEOS_ALL.value,
        "name": "Full Access",
        "description": "All video-related permissions",
        "category": "Video",
    },
    # Tags
    {
        "scope": ApiKeyScope.TAGS_READ.value,
        "name": "Read Tags",
        "description": "View tags and tag groups",
        "category": "Tags",
    },
    {
        "scope": ApiKeyScope.TAGS_WRITE.value,
        "name": "Manage Tags",
        "description": "Create, update, and delete tags",
        "category": "Tags",
    },
    {
        "scope": ApiKeyScope.TAGS_ALL.value,
        "name": "Full Tag Access",
        "description": "All tag-related permissions",
        "category": "Tags",
    },
    # Resources
    {
        "scope": ApiKeyScope.RESOURCES_READ.value,
        "name": "Read Resources",
        "description": "View resource library and details",
        "category": "Resources",
    },
    {
        "scope": ApiKeyScope.RESOURCES_WRITE.value,
        "name": "Manage Resources",
        "description": "Update and organize resources",
        "category": "Resources",
    },
    {
        "scope": ApiKeyScope.RESOURCES_ALL.value,
        "name": "Full Resource Access",
        "description": "All resource-related permissions",
        "category": "Resources",
    },
]


def get_valid_scopes() -> set:
    """Get all valid permission scope set"""
    valid = {s["scope"] for s in AVAILABLE_SCOPES}
    # Also accept legacy scope values
    valid.update(LEGACY_SCOPE_MAP.keys())
    return valid


def normalize_scope(scope: str) -> str:
    """Normalize a scope value, mapping legacy scopes to new ones."""
    return LEGACY_SCOPE_MAP.get(scope, scope)


def validate_scopes(scopes: List[str]) -> Tuple[bool, List[str]]:
    """
    Validate permission scope list

    Args:
        scopes: List of scopes to validate

    Returns:
        (is_valid, invalid_scopes)
    """
    valid_scopes = get_valid_scopes()
    invalid = [s for s in scopes if s not in valid_scopes]
    return len(invalid) == 0, invalid


def match_path_pattern(pattern: str, path: str) -> bool:
    """
    Match path pattern

    Supports {param} style path parameters

    Args:
        pattern: Path pattern, e.g. /videos/videos/{platform_id}
        path: Actual path, e.g. /videos/videos/123456

    Returns:
        Whether it matches
    """
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")

    if len(pattern_parts) != len(path_parts):
        return False

    for p, pp in zip(pattern_parts, path_parts):
        if p.startswith("{") and p.endswith("}"):
            continue  # Path parameter, matches any value
        if p != pp:
            return False

    return True


def get_required_scopes(method: str, path: str) -> List[str]:
    """
    Get required permission scopes for an endpoint

    Args:
        method: HTTP method
        path: Request path

    Returns:
        Required permission scope list (any one is sufficient)
    """
    for (m, pattern), scopes in ENDPOINT_SCOPE_MAP.items():
        if m == method and match_path_pattern(pattern, path):
            return scopes
    return []


def check_scope_permission(required_scopes: List[str], user_scopes: List[str]) -> bool:
    """
    Check if user has required permissions

    Args:
        required_scopes: Required scope list (any one is sufficient)
        user_scopes: User's scope list

    Returns:
        Whether user has permission
    """
    if not required_scopes:
        return True  # No permission required

    # Normalize user scopes (map legacy to new)
    normalized_user_scopes = [normalize_scope(s) for s in user_scopes]

    # Check for wildcard permission
    if "*" in user_scopes or "*" in normalized_user_scopes:
        return True

    # Check if any required scope matches
    for scope in required_scopes:
        if scope in normalized_user_scopes:
            return True

        # Check category wildcard (e.g. videos:* matches videos:fetch)
        scope_category = scope.split(":")[0] if ":" in scope else scope
        if f"{scope_category}:*" in normalized_user_scopes:
            return True

    return False
