"""Shared PostgreSQL-backed enums for all ORM models."""

from __future__ import annotations

import enum


class AiTaskStatus(str, enum.Enum):
    NONE = "none"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ApiKeyStatus(str, enum.Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class DownloadStatus(str, enum.Enum):
    PENDING = "pending"
    DOWNLOADING = "downloading"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    USER = "user"
    TEST = "test"
