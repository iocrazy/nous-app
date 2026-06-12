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

    # Teams related (read-only — used by the browser extension to pick an
    # upload scope; teams listing only returns the caller's own teams)
    TEAMS_READ = "teams:read"

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
    # Media fetch
    ("POST", "/media/fetch"): [
        ApiKeyScope.VIDEOS_FETCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("POST", "/media/fetch/batch"): [
        ApiKeyScope.VIDEOS_FETCH_BATCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Media read
    ("GET", "/media"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    ("GET", "/media/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Media write
    ("DELETE", "/media/{platform_id}"): [
        ApiKeyScope.VIDEOS_VIDEOS_WRITE.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Search
    ("POST", "/media/search"): [
        ApiKeyScope.VIDEOS_SEARCH.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Statistics
    ("GET", "/media/statistics"): [
        ApiKeyScope.VIDEOS_STATISTICS.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Pending downloads list
    ("GET", "/media/pending"): [
        ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        ApiKeyScope.VIDEOS_ALL.value,
    ],
    # Retry download
    ("POST", "/media/retry/{platform_id}"): [
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
    # Temp token (create temp token for web page access)
    ("POST", "/auth/temp-token"): [
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
    # Extension scan-import flow: upload + pickers + optional batch auto-tag.
    # The upload handler itself re-checks scope membership (verify_scope_access)
    # and /resources/ai/batch re-checks per-resource access, so the scope here
    # only gates *which keys* may reach the endpoint at all.
    ("POST", "/resources/upload"): [
        ApiKeyScope.RESOURCES_WRITE.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    ("GET", "/resources/folders/list"): [
        ApiKeyScope.RESOURCES_READ.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    ("POST", "/resources/ai/batch"): [
        ApiKeyScope.RESOURCES_WRITE.value,
        ApiKeyScope.RESOURCES_ALL.value,
    ],
    # Teams (scope picker — list is already filtered to the caller's teams)
    ("GET", "/teams"): [
        ApiKeyScope.TEAMS_READ.value,
    ],
}


# Available scopes list (for frontend selection)
AVAILABLE_SCOPES = [
    {
        "scope": ApiKeyScope.VIDEOS_FETCH.value,
        "name": "Fetch Media",
        "description": "Fetch single media info via URL",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_FETCH_BATCH.value,
        "name": "Batch Fetch",
        "description": "Fetch multiple media info in batch",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_VIDEOS_READ.value,
        "name": "Read Media",
        "description": "View media list and details",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_VIDEOS_WRITE.value,
        "name": "Manage Media",
        "description": "Delete media records",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_SEARCH.value,
        "name": "Search Media",
        "description": "Search media",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_STATISTICS.value,
        "name": "View Statistics",
        "description": "View statistics data",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_RETRY.value,
        "name": "Retry Download",
        "description": "Retry failed media downloads",
        "category": "Media",
    },
    {
        "scope": ApiKeyScope.VIDEOS_ALL.value,
        "name": "Full Access",
        "description": "All media-related permissions",
        "category": "Media",
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
    # Teams
    {
        "scope": ApiKeyScope.TEAMS_READ.value,
        "name": "Read Teams",
        "description": "List your teams (scope picker for uploads)",
        "category": "Teams",
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
