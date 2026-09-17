"""search_docs 的读写口（3c §2.1）。三条：upsert 按 (entity_kind, entity_id)
幂等（投影可重建是设计的一部分）；body 超 8 KB 截断而不是拒绝；授权谓词与
team 过滤是**两个**谓词——合成一个会让「按别人的 team 过滤」变成一次越权读取
（``rpc_user_media_text_search`` 那类洞的形状）。"""

from contextlib import asynccontextmanager

import pytest

from app.repositories import search_docs_repository as mod
from app.repositories.search_docs_repository import SearchDocsRepository
from app.services.search.types import BODY_MAX_BYTES, SearchDoc

pytestmark = pytest.mark.unit
ME = "11111111-1111-1111-1111-111111111111"


def _sql(stmt) -> str:
    from sqlalchemy.dialects import postgresql

    return str(stmt.compile(dialect=postgresql.dialect()))


def _stmt(**over):
    kw = {
        "q": "rain",
        "kinds": {"run"},
        "team_ids": [],
        "user_id": ME,
        "project_id": None,
        "issue_id": None,
        "limit": 10,
    }
    kw.update(over)
    return SearchDocsRepository()._search_stmt(**kw)


def test_an_upsert_targets_the_entity_key_and_bumps_updated_at():
    sql = _sql(
        SearchDocsRepository()._upsert_stmt(
            SearchDoc(entity_kind="run", entity_id="913", title="MH-96 · Alpha")
        )
    )
    assert "ON CONFLICT (entity_kind, entity_id) DO UPDATE" in sql
    assert "updated_at" in sql


def test_a_long_multibyte_body_is_clipped_on_a_codepoint_boundary():
    # 一个汉字 3 字节：按字节硬切会切出半个字符，asyncpg 绑定时当场报错。
    # 本仓产出正文含中文（剧本行），所以这不是理论情形。
    doc = SearchDoc(
        entity_kind="output",
        entity_id="script_shot:9:4",
        title="t",
        body="中" * BODY_MAX_BYTES,
    )
    body = SearchDocsRepository()._values(doc)["body"]
    assert len(body.encode()) <= BODY_MAX_BYTES and body.encode().decode() == body


def test_the_visibility_predicate_is_always_there_and_team_filter_is_extra():
    wide, narrow = _sql(_stmt()), _sql(_stmt(team_ids=[7]))
    assert "team_members" in wide and "owner_user_id" in wide
    assert "team_members" in narrow
    # team 过滤是**追加**的一个 IN，不是替换授权谓词。
    assert narrow.count("team_id IN") > wide.count("team_id IN")


def test_the_pattern_is_escaped_and_declares_its_escape_char():
    # CLAUDE.md「ILIKE 模式的转义责任要跟着模式走」：Python 侧转义 + SQL 侧
    # ESCAPE 缺一不可——Postgres 的 LIKE 没有默认转义符。
    sql = _sql(_stmt(q="100%", kinds={"run", "output"}))
    assert "ESCAPE" in sql and "ILIKE" in sql.upper()


def test_the_order_is_similarity_then_recency_and_scopes_are_additive():
    sql = _sql(_stmt(kinds={"output"}))
    assert "greatest(similarity" in sql.lower() and "updated_at DESC" in sql
    assert sql.lower().index("order by") < sql.lower().index("limit")
    # 两个坐标都出现在 SELECT 列表里（投影表整行出口），所以「有没有过滤」只能
    # 数出现次数：不传就只有那一次，传了才多一个 WHERE 谓词。
    both = _sql(_stmt(project_id=3, issue_id=96))
    assert both.count("project_id") > sql.count("project_id")
    assert both.count("issue_id") > sql.count("issue_id")


# ── 投影绝不加入调用方的事务 ───────────────────────────────────────────


class _Session:
    def __init__(self, name: str) -> None:
        self.name, self.statements = name, []

    async def execute(self, statement):
        self.statements.append(statement)

    @asynccontextmanager
    async def begin(self):
        yield self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _wire(monkeypatch, *, inside: bool):
    """环境事务里的那个 session 与「另开一个」的那个，各一份、可分辨。"""
    ambient, fresh = _Session("ambient"), _Session("fresh")

    @asynccontextmanager
    async def _write_scope():
        yield ambient

    monkeypatch.setattr(mod, "in_unit_of_work", lambda: inside)
    monkeypatch.setattr(mod, "write_scope", _write_scope)
    monkeypatch.setattr(mod, "get_sessionmaker", lambda: (lambda: fresh))
    return ambient, fresh


def _doc():
    return SearchDoc(entity_kind="run", entity_id="913", title="MH-96 · Alpha")


async def test_a_projection_inside_someone_elses_transaction_opens_its_own_session(
    monkeypatch,
):
    """回退在 ``unit_of_work()`` 里登记产出，而一次失败的 INSERT 会把那个
    session 当场弄废：best-effort 吞得掉异常，吞不掉一个已经 abort 的事务
    （同 ``registry._stamp_seq`` 的 PendingRollbackError 注释）。于是「投影
    写失败」会把一次**内容已经改好**的回退判成失败并回滚它 —— 正是投影的写方
    最不该做的事。所以这条路必须另开 session。"""
    ambient, fresh = _wire(monkeypatch, inside=True)
    await SearchDocsRepository().upsert(_doc())
    assert len(fresh.statements) == 1
    assert ambient.statements == [], "投影碰了调用方的事务"


async def test_an_ordinary_projection_just_uses_the_write_scope(monkeypatch):
    """没有环境事务时不该多开一个 session —— 另开一个的唯一理由是隔离，
    没有要隔离的东西就是白白多一条连接。"""
    ambient, fresh = _wire(monkeypatch, inside=False)
    await SearchDocsRepository().upsert(_doc())
    assert len(ambient.statements) == 1 and fresh.statements == []
