# backend/app/core/api_key_scopes.py

"""
API 密钥权限范围定义

定义可用的权限范围和端点映射关系。
"""

from enum import Enum
from typing import Dict, List, Tuple


class ApiKeyScope(str, Enum):
    """API 密钥权限范围枚举"""

    # 抖音视频相关
    DOUYIN_FETCH = "douyin:fetch"
    DOUYIN_FETCH_BATCH = "douyin:fetch:batch"
    DOUYIN_VIDEOS_READ = "douyin:videos:read"
    DOUYIN_VIDEOS_WRITE = "douyin:videos:write"
    DOUYIN_SEARCH = "douyin:search"
    DOUYIN_STATISTICS = "douyin:statistics"
    DOUYIN_RETRY = "douyin:retry"

    # 全部抖音权限（通配符）
    DOUYIN_ALL = "douyin:*"

    # 用户相关
    USER_PROFILE_READ = "user:profile:read"
    USER_PROFILE_WRITE = "user:profile:write"


# 端点与权限范围映射
# 格式: (method, path_pattern): [allowed_scopes]
# 请求只需要匹配其中一个 scope 即可访问
ENDPOINT_SCOPE_MAP: Dict[Tuple[str, str], List[str]] = {
    # 抖音视频获取
    ("POST", "/douyin/fetch"): [
        ApiKeyScope.DOUYIN_FETCH.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],
    ("POST", "/douyin/fetch/batch"): [
        ApiKeyScope.DOUYIN_FETCH_BATCH.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 抖音视频读取
    ("GET", "/douyin/videos"): [
        ApiKeyScope.DOUYIN_VIDEOS_READ.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],
    ("GET", "/douyin/videos/{aweme_id}"): [
        ApiKeyScope.DOUYIN_VIDEOS_READ.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 抖音视频写入
    ("DELETE", "/douyin/videos/{aweme_id}"): [
        ApiKeyScope.DOUYIN_VIDEOS_WRITE.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 搜索
    ("POST", "/douyin/videos/search"): [
        ApiKeyScope.DOUYIN_SEARCH.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 统计
    ("GET", "/douyin/statistics"): [
        ApiKeyScope.DOUYIN_STATISTICS.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 待下载列表
    ("GET", "/douyin/pending"): [
        ApiKeyScope.DOUYIN_VIDEOS_READ.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],

    # 重试下载
    ("POST", "/douyin/retry/{aweme_id}"): [
        ApiKeyScope.DOUYIN_RETRY.value,
        ApiKeyScope.DOUYIN_ALL.value
    ],
}


# Available scopes list (for frontend selection)
AVAILABLE_SCOPES = [
    {
        "scope": ApiKeyScope.DOUYIN_FETCH.value,
        "name": "Fetch Video",
        "description": "Fetch single video info via URL",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_FETCH_BATCH.value,
        "name": "Batch Fetch",
        "description": "Fetch multiple videos info in batch",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_VIDEOS_READ.value,
        "name": "Read Videos",
        "description": "View video list and details",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_VIDEOS_WRITE.value,
        "name": "Manage Videos",
        "description": "Delete video records",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_SEARCH.value,
        "name": "Search Videos",
        "description": "Search videos",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_STATISTICS.value,
        "name": "View Statistics",
        "description": "View statistics data",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_RETRY.value,
        "name": "Retry Download",
        "description": "Retry failed video downloads",
        "category": "Video"
    },
    {
        "scope": ApiKeyScope.DOUYIN_ALL.value,
        "name": "Full Access",
        "description": "All video-related permissions",
        "category": "Video"
    },
]


def get_valid_scopes() -> set:
    """获取所有有效的权限范围集合"""
    return {s["scope"] for s in AVAILABLE_SCOPES}


def validate_scopes(scopes: List[str]) -> Tuple[bool, List[str]]:
    """
    验证权限范围列表

    Args:
        scopes: 待验证的权限范围列表

    Returns:
        (is_valid, invalid_scopes)
    """
    valid_scopes = get_valid_scopes()
    invalid = [s for s in scopes if s not in valid_scopes]
    return len(invalid) == 0, invalid


def match_path_pattern(pattern: str, path: str) -> bool:
    """
    匹配路径模式

    支持 {param} 形式的路径参数

    Args:
        pattern: 路径模式，如 /douyin/videos/{aweme_id}
        path: 实际路径，如 /douyin/videos/123456

    Returns:
        是否匹配
    """
    pattern_parts = pattern.split("/")
    path_parts = path.split("/")

    if len(pattern_parts) != len(path_parts):
        return False

    for p, pp in zip(pattern_parts, path_parts):
        if p.startswith("{") and p.endswith("}"):
            continue  # 路径参数，匹配任意值
        if p != pp:
            return False

    return True


def get_required_scopes(method: str, path: str) -> List[str]:
    """
    获取端点所需的权限范围

    Args:
        method: HTTP 方法
        path: 请求路径

    Returns:
        所需权限范围列表（任一即可）
    """
    for (m, pattern), scopes in ENDPOINT_SCOPE_MAP.items():
        if m == method and match_path_pattern(pattern, path):
            return scopes
    return []


def check_scope_permission(required_scopes: List[str], user_scopes: List[str]) -> bool:
    """
    检查用户是否拥有所需权限

    Args:
        required_scopes: 所需权限列表（任一即可）
        user_scopes: 用户拥有的权限列表

    Returns:
        是否有权限
    """
    if not required_scopes:
        return True  # 无权限要求

    # 检查是否拥有通配符权限
    if "*" in user_scopes:
        return True

    # 检查是否匹配任一所需权限
    for scope in required_scopes:
        if scope in user_scopes:
            return True

        # 检查分类通配符（如 douyin:* 匹配 douyin:fetch）
        scope_category = scope.split(":")[0] if ":" in scope else scope
        if f"{scope_category}:*" in user_scopes:
            return True

    return False
