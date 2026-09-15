"""mig 470: 主账号名 —— 系统发一个，用户能改，全局唯一。

用户的裁定（2026-09-15）：「生成默认的用户名，用户可以更改，但是全局唯一」。

这些断言问的全是 Postgres 对象，桩 session 回答不了：

* 「全局唯一且大小写不敏感」是一条函数唯一索引（`lower(username)`）。既有的
  `user_profiles_username_key` 是大小写敏感的，`iocrazy` 与 `IOCrazy` 在它下面
  可以并存 —— 对一个用来认人的名字，那是可冒充的。
* 「每个账号都有名字」是 NOT NULL。
* `unique_username()` 是 PL/pgSQL，出了数据库就不存在。
* 「注册的两条路径用同一份去重逻辑」只有让触发器真跑一遍才看得出来。

  INTEGRATION_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/drift \\
    uv run pytest tests/db/test_migration_470_username_integration.py -v

DSN 没设时干净跳过；schema-drift 里经 pytest-no-full-skip.sh 跑，全跳即红。
"""

from __future__ import annotations

import os
import uuid

import asyncpg
import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TEST_DSN = os.environ.get("INTEGRATION_DATABASE_URL", "").strip()

pytest.importorskip("asyncpg")

_skip = pytest.mark.skipif(
    not _TEST_DSN,
    reason="INTEGRATION_DATABASE_URL not set — mig 470 needs a real database.",
)


@pytest.fixture
async def conn():
    """写入一律回滚，并装上注册触发器。

    触发器挂在 `auth.users` 上，而 drift 库从来没有它（baseline 只 dump
    public）—— 与 mig 468/469 的套件同一个做法，理由也一样：在迁移里建它会把
    它挂到其他集成文件的合成 `auth.users` 插入上。
    """
    c = await asyncpg.connect(_TEST_DSN)
    tx = c.transaction()
    await tx.start()
    await c.execute(
        "CREATE TRIGGER on_auth_user_created AFTER INSERT ON auth.users "
        "FOR EACH ROW EXECUTE FUNCTION public.handle_new_user()"
    )
    try:
        yield c
    finally:
        await tx.rollback()
        await c.close()


async def _signup(conn, **cols) -> uuid.UUID:
    uid = uuid.uuid4()
    keys = ["id"] + list(cols)
    placeholders = ", ".join(f"${i + 1}" for i in range(len(keys)))
    await conn.execute(
        f"INSERT INTO auth.users ({', '.join(keys)}) VALUES ({placeholders})",
        uid,
        *cols.values(),
    )
    return uid


async def _username(conn, uid) -> str:
    return await conn.fetchval(
        "SELECT username FROM public.user_profiles WHERE id = $1", uid
    )


# ---------------------------------------------------------------------------
# 全局唯一，且大小写不敏感
# ---------------------------------------------------------------------------


@_skip
async def test_a_name_cannot_be_taken_twice_in_different_case(conn):
    """`iocrazy` 占了，`IOCrazy` 就不能再占。

    既有的 `user_profiles_username_key` 拦不住这个 —— 它是大小写敏感的。本条
    钉的是 mig 470 新加的那条函数唯一索引；没有它，改名接口就是一个冒充别人
    的入口。
    """
    a = await _signup(conn, email="iocrazy@example.com")
    assert await _username(conn, a) == "iocrazy"

    b = await _signup(conn, email="someone@example.com")
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(
            "UPDATE public.user_profiles SET username = 'IOCrazy' WHERE id = $1", b
        )


@_skip
async def test_every_account_has_a_name(conn):
    """「都有名字」是结构性的，不是两条注册路径碰巧都填了。"""
    notnull = await conn.fetchval(
        "SELECT is_nullable FROM information_schema.columns "
        " WHERE table_schema = 'public' AND table_name = 'user_profiles' "
        "   AND column_name = 'username'"
    )
    assert notnull == "NO"


# ---------------------------------------------------------------------------
# unique_username() —— 两条注册路径共用的那一份
# ---------------------------------------------------------------------------


@_skip
async def test_unique_username_hands_back_a_free_name(conn):
    taken = await _signup(conn, email="sam@one.example")
    assert await _username(conn, taken) == "sam"

    newcomer = uuid.uuid4()
    got = await conn.fetchval("SELECT public.unique_username('sam', $1)", newcomer)
    assert got == "sam_" + newcomer.hex[:8]


@_skip
async def test_unique_username_is_case_insensitive(conn):
    """大小写不敏感要在**发名字**这一步就生效，否则发出来的名字会撞上索引，
    而那是一次注册失败。"""
    await _signup(conn, email="Dana@one.example")
    newcomer = uuid.uuid4()

    got = await conn.fetchval("SELECT public.unique_username('dana', $1)", newcomer)
    assert got != "dana"


@_skip
async def test_unique_username_returns_your_own_name_unchanged(conn):
    """自己已经叫这个名字 → 原样返回，不加后缀。

    改名接口重试（网络抖动、用户连点两次）会走到这里；每次都加一截后缀的话，
    一次重试就把 `sam` 变成 `sam_3f2a1b9c`。
    """
    uid = await _signup(conn, email="stable@one.example")

    got = await conn.fetchval("SELECT public.unique_username('stable', $1)", uid)
    assert got == "stable"


@_skip
async def test_an_empty_base_still_produces_a_name(conn):
    """空基名不是「没意见」，是「没有名字」—— 那会拼出一个 "'s Workspace"。"""
    newcomer = uuid.uuid4()

    for base in (None, "", "   "):
        got = await conn.fetchval(
            "SELECT public.unique_username($1, $2)", base, newcomer
        )
        assert got == "user_" + newcomer.hex[:8], f"base={base!r}"


@_skip
async def test_the_signup_trigger_uses_the_shared_function(conn):
    """触发器不再内联自己那份去重逻辑。

    内联那份（mig 469）不知道大小写不敏感索引的存在，会发出一个撞索引的名字 ——
    也就是一次注册失败。这条断言的是两条路径的结果一致。
    """
    first = await _signup(conn, email="dup@one.example")
    second = await _signup(conn, email="DUP@two.example")

    assert await _username(conn, first) == "dup"
    # 大小写不同也算占用，所以第二个人拿到后缀名 —— 而且注册**成功了**。
    assert await _username(conn, second) == "DUP_" + second.hex[:8]


@_skip
async def test_the_shared_function_is_not_reachable_from_the_browser(conn):
    """它只读 user_profiles，但浏览器没有任何理由能直接调它（改名走后端接口）。

    同 CLAUDE.md 2026-09-11 那条：定义在 public 里的函数默认对 anon /
    authenticated 开放，PostgREST 的 /rest/v1/rpc/<name> 直接可达。
    """
    for role in ("anon", "authenticated"):
        assert not await conn.fetchval(
            "SELECT has_function_privilege($1, "
            "'public.unique_username(text, uuid)', 'EXECUTE')",
            role,
        ), f"{role} 仍然调得到 unique_username"


# ---------------------------------------------------------------------------
# 改名之后，账号还是同一个账号
# ---------------------------------------------------------------------------


@_skip
async def test_the_numeric_id_survives_a_rename(conn):
    """`display_id` 才是「你是谁」—— 名字是可变的，它不是。

    这也是账号中心那种界面同时显示两者的原因：名字给人看，ID 用来指认。
    """
    uid = await _signup(conn, email="renamer@one.example")
    before = await conn.fetchval(
        "SELECT display_id FROM public.user_profiles WHERE id = $1", uid
    )

    await conn.execute(
        "UPDATE public.user_profiles SET username = '改个名字' WHERE id = $1", uid
    )

    after = await conn.fetchrow(
        "SELECT username, display_id FROM public.user_profiles WHERE id = $1", uid
    )
    assert after["username"] == "改个名字"
    assert after["display_id"] == before
