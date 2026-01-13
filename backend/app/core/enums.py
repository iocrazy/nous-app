"""
应用程序枚举定义模块

包含应用程序中使用的各种枚举类型定义，集中管理以确保一致性。
"""

from enum import Enum


class DownloadStatus(str, Enum):
    """下载状态枚举"""
    PENDING = "pending"      # 待下载
    DOWNLOADING = "downloading"  # 下载中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"        # 失败
    SKIPPED = "skipped"      # 跳过

class RoleType(str, Enum):
    ADMIN = "admin"
    USER = "user"
    TEST = "test"  # 添加新的枚举值
    # EDITOR = "editor"
    # VIEWER = "viewer"
