# Supabase Async Migration Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert all Supabase sync calls to async for true parallel execution and better FastAPI performance.

**Architecture:** Replace sync `Client` with `AsyncClient` throughout repositories and services. Use `await` for all database operations. Keep sync client for Celery tasks.

**Tech Stack:** supabase-py AsyncClient, FastAPI async dependencies

---

## Overview

| Layer | Files | Status |
|-------|-------|--------|
| DB Client | `supabase_client.py` | ✅ Already has async |
| Dependencies | `deps.py` | ❌ Needs async |
| Repositories | 6 files | ❌ Needs async |
| Services | 4 files | ❌ Needs async |
| API Routers | 2 files (inline) | ❌ Needs async |
| Celery Tasks | 1 file | ⚠️ Keep sync |

---

## Task 1: Update DB Module Exports

**Files:**
- Modify: `backend/app/db/__init__.py`

**Step 1: Add async exports**

```python
# app/db/__init__.py

"""
数据库模块

提供 Supabase 客户端的初始化和管理。
"""

from app.db.supabase_client import (
    get_supabase,
    get_supabase_admin,
    get_async_supabase,
    get_async_supabase_admin,
)

__all__ = [
    "get_supabase",
    "get_supabase_admin",
    "get_async_supabase",
    "get_async_supabase_admin",
]
```

**Step 2: Verify import works**

Run: `cd backend && uv run python -c "from app.db import get_async_supabase_admin; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/app/db/__init__.py
git commit -m "feat: export async Supabase client functions"
```

---

## Task 2: Update Dependencies Module

**Files:**
- Modify: `backend/app/core/deps.py`

**Step 1: Add async client imports and functions**

Replace the imports and sync functions at the top:

```python
# app/core/deps.py

"""
依赖注入模块

提供 Supabase 客户端依赖注入和认证功能。
支持同步和异步客户端。
"""

from dataclasses import dataclass
from typing import Annotated, Optional, List
from fastapi import Depends, Header, HTTPException, Request, status
from loguru import logger
from supabase import Client
from supabase._async.client import AsyncClient

from app.db.supabase_client import (
    get_supabase as _get_supabase,
    get_supabase_admin as _get_supabase_admin,
    get_async_supabase as _get_async_supabase,
    get_async_supabase_admin as _get_async_supabase_admin,
)
from app.core.api_key_scopes import get_required_scopes, check_scope_permission


# 同步客户端 (for backward compatibility and Celery)
def get_supabase() -> Client:
    """获取 Supabase 客户端 (同步)"""
    return _get_supabase()


def get_supabase_admin() -> Client:
    """获取 Supabase Admin 客户端 (同步)"""
    return _get_supabase_admin()


# 异步客户端 (推荐用于 FastAPI)
async def get_async_supabase() -> AsyncClient:
    """获取异步 Supabase 客户端"""
    return await _get_async_supabase()


async def get_async_supabase_admin() -> AsyncClient:
    """获取异步 Supabase Admin 客户端"""
    return await _get_async_supabase_admin()


# 依赖注入类型
SupabaseDep = Annotated[Client, Depends(get_supabase)]
SupabaseAdminDep = Annotated[Client, Depends(get_supabase_admin)]
AsyncSupabaseDep = Annotated[AsyncClient, Depends(get_async_supabase)]
AsyncSupabaseAdminDep = Annotated[AsyncClient, Depends(get_async_supabase_admin)]
```

**Step 2: Update `_validate_bearer_token` to use async client**

```python
async def _validate_bearer_token(authorization: str) -> AuthContext:
    """验证 Bearer Token (JWT)"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证格式，需要 Bearer token",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = authorization[7:]
    client = await _get_async_supabase()  # 使用异步客户端

    try:
        user_response = await client.auth.get_user(token)

        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的认证令牌"
            )

        return AuthContext(
            user_id=str(user_response.user.id),
            auth_type="jwt",
            scopes=None
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"JWT 验证失败: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"认证失败: {str(e)}"
        )
```

**Step 3: Update `get_current_user` to use async**

```python
async def get_current_user(authorization: str = Header(...)):
    """从 Authorization header 获取当前用户"""
    try:
        token = authorization.replace("Bearer ", "")
        client = await _get_async_supabase()

        user_response = await client.auth.get_user(token)

        if not user_response or not user_response.user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="无效的认证令牌"
            )

        return user_response.user

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"认证失败: {str(e)}"
        )
```

**Step 4: Verify syntax**

Run: `cd backend && uv run python -c "from app.core.deps import get_async_supabase_admin, AsyncSupabaseAdminDep; print('OK')"`
Expected: `OK`

**Step 5: Commit**

```bash
git add backend/app/core/deps.py
git commit -m "feat: add async Supabase dependencies"
```

---

## Task 3: Convert ApiKeyRepository to Async

**Files:**
- Modify: `backend/app/repositories/api_key_repository.py`

**Step 1: Update imports and __init__**

```python
# app/repositories/api_key_repository.py

"""API Key 仓储 - 管理 API 密钥的存储和验证"""

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class ApiKeyRepository:
    """API Key 数据仓储 (异步)"""

    TABLE_NAME = "api_keys"

    def __init__(self):
        self._client = None  # 延迟初始化

    async def _get_client(self):
        """获取异步客户端"""
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client
```

**Step 2: Convert all methods to async**

For each method, change:
- `self.client` → `await self._get_client()`
- `.execute()` → `await ...execute()`

Example for `validate_key`:

```python
async def validate_key(self, api_key: str) -> Optional[Dict[str, Any]]:
    """验证 API Key 并返回关联信息"""
    try:
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()
        client = await self._get_client()

        result = await client.table(self.TABLE_NAME).select(
            "id, user_id, name, scopes, expires_at, is_active"
        ).eq("key_hash", key_hash).eq("is_active", True).execute()

        if not result.data:
            return None

        key_data = result.data[0]

        # 检查是否过期
        if key_data.get("expires_at"):
            expires_at = datetime.fromisoformat(key_data["expires_at"].replace("Z", "+00:00"))
            if expires_at < datetime.now(expires_at.tzinfo):
                return None

        return {
            "key_id": key_data["id"],
            "user_id": key_data["user_id"],
            "name": key_data["name"],
            "scopes": key_data.get("scopes", []),
        }

    except Exception as e:
        logger.error(f"验证 API Key 失败: {e}")
        return None
```

**Step 3: Verify syntax**

Run: `cd backend && uv run python -c "from app.repositories.api_key_repository import ApiKeyRepository; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add backend/app/repositories/api_key_repository.py
git commit -m "refactor: convert ApiKeyRepository to async"
```

---

## Task 4: Convert SupabaseDouyinRepository to Async

**Files:**
- Modify: `backend/app/repositories/supabase_douyin_repository.py`

**Step 1: Update imports and __init__**

```python
from app.db.supabase_client import get_async_supabase_admin


class SupabaseDouyinRepository:
    """Supabase 抖音数据仓储 (异步)"""

    TABLE_NAME = "douyin_videos"

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def _get_table(self):
        client = await self._get_client()
        return client.table(self.TABLE_NAME)
```

**Step 2: Convert all methods**

Pattern for each method:
```python
async def method_name(self, ...):
    table = await self._get_table()
    result = await table.select(...).execute()
    return result.data
```

**Step 3: Commit**

```bash
git add backend/app/repositories/supabase_douyin_repository.py
git commit -m "refactor: convert SupabaseDouyinRepository to async"
```

---

## Task 5: Convert Remaining Repositories

**Files:**
- Modify: `backend/app/repositories/collections_repository.py`
- Modify: `backend/app/repositories/analysis_repository.py`
- Modify: `backend/app/repositories/tags_repository.py`
- Modify: `backend/app/repositories/user_settings_repository.py`

**Pattern:** Same as Task 4 - update imports, add `_get_client()`, convert methods.

**Step 1-4:** Apply the async pattern to each repository

**Step 5: Commit all**

```bash
git add backend/app/repositories/
git commit -m "refactor: convert all repositories to async"
```

---

## Task 6: Convert CleanupService to Async

**Files:**
- Modify: `backend/app/services/cleanup_service.py`

**Step 1: Update imports**

```python
from app.db.supabase_client import get_async_supabase_admin
```

**Step 2: Update __init__ and add _get_client**

```python
class CleanupService:
    """Service for generating and managing cleanup suggestions (异步)."""

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client
```

**Step 3: Convert all methods to use await**

Example:
```python
async def get_cleanup_data(self, user_id: str, limit: int = 50, include_duplicates: bool = True) -> dict:
    client = await self._get_client()
    result = await client.rpc("get_cleanup_data", {"p_user_id": user_id, "p_limit": limit}).execute()
    # ... rest of the method
```

**Step 4: Commit**

```bash
git add backend/app/services/cleanup_service.py
git commit -m "refactor: convert CleanupService to async"
```

---

## Task 7: Convert Remaining Services

**Files:**
- Modify: `backend/app/services/collections_service.py`
- Modify: `backend/app/services/search_service.py`
- Modify: `backend/app/services/supabase_auth_service.py`

**Pattern:** Same as Task 6.

**Step 1-3:** Apply async pattern to each service

**Step 4: Commit**

```bash
git add backend/app/services/
git commit -m "refactor: convert all services to async"
```

---

## Task 8: Update API Routers with Inline Supabase Calls

**Files:**
- Modify: `backend/app/api/cleanup_router.py`
- Modify: `backend/app/api/analysis_router.py`

**Step 1: Find and replace inline imports**

Search for:
```python
from app.db.supabase_client import get_supabase_admin
supabase = get_supabase_admin()
```

Replace with:
```python
from app.db.supabase_client import get_async_supabase_admin
supabase = await get_async_supabase_admin()
```

**Step 2: Update all .execute() to await**

```python
# Before
result = supabase.table("douyin_videos").delete().eq("id", video_id).execute()

# After
result = await supabase.table("douyin_videos").delete().eq("id", video_id).execute()
```

**Step 3: Commit**

```bash
git add backend/app/api/
git commit -m "refactor: convert router inline Supabase calls to async"
```

---

## Task 9: Keep Celery Tasks Sync (Document Decision)

**Files:**
- Review: `backend/app/tasks/analysis_tasks.py`

**Decision:** Celery tasks run in separate worker processes and don't benefit from async. Keep them using sync client.

**No code changes needed.** Just ensure the sync client remains available.

**Step 1: Add comment to clarify**

```python
# analysis_tasks.py
# Note: Using sync Supabase client for Celery tasks
# Celery workers run in separate processes, async provides no benefit here
from app.db.supabase_client import get_supabase_admin  # Sync client for Celery
```

**Step 2: Commit**

```bash
git add backend/app/tasks/analysis_tasks.py
git commit -m "docs: clarify sync client usage in Celery tasks"
```

---

## Task 10: Integration Test

**Step 1: Start the backend server**

```bash
cd backend && uv run uvicorn app.main:app --reload
```

**Step 2: Test the cleanup endpoint**

```bash
curl -X GET "http://localhost:8080/api/v1/cleanup/data" \
  -H "Authorization: Bearer <your_token>"
```

Expected: JSON response with suggestions and stats

**Step 3: Run performance comparison**

```bash
cd backend && uv run python /tmp/test_async.py
```

Expected: Async should be faster for parallel operations

**Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete Supabase async migration"
```

---

## Summary

| Task | Description | Files |
|------|-------------|-------|
| 1 | Export async functions | `db/__init__.py` |
| 2 | Update dependencies | `core/deps.py` |
| 3 | ApiKeyRepository | `repositories/api_key_repository.py` |
| 4 | DouyinRepository | `repositories/supabase_douyin_repository.py` |
| 5 | Other repositories | 4 files |
| 6 | CleanupService | `services/cleanup_service.py` |
| 7 | Other services | 3 files |
| 8 | API routers | 2 files |
| 9 | Celery tasks | Document only |
| 10 | Integration test | - |

**Total estimated changes:** ~15 files
