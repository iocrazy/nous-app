"""``/cover-templates`` —— 端点层测试（migration 435）。

端点层只负责五件事，所以只测这五件：

- **scope 闸门**：模板引用的图必须在调用者自己的 scope 里。外键**不查 scope**，
  所以少了这道检查就能靠猜 snowflake 给别人的图挂模板，而且缺行时会以裸 500
  的形态出现（IntegrityError），不是 404。
- **只收图**：视频不能当封面样图。
- **幂等**：同一张图第二次添加返回**同一行**，不是 409，也不是第二张卡片。
- **wire 形状**：所有 snowflake id 是 JSON **string**；``image_url`` 必须正好是
  ``/api/v1/generated-media/{id}/cover`` —— 那是出图链路 ``params.source_urls``
  唯一认得的形状（``generated_media_service.GENERATED_MEDIA_URL_RE``），写错
  不会报错，只会让参考图被静默丢弃。
- **类型化 409**：删一张被模板引用的图要回 409 且点名模板，不能漏成 500。

删模板之后同一张图必须重新可删 —— 这条是硬删除存在的理由，用真库验证在
``tests/db/`` 那侧（本文件用替身，只钉端点行为）。
"""

from __future__ import annotations

import sys

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.deps import AuthContext, get_auth
from app.main import app

# 必须走 sys.modules —— app/api/__init__.py 把同名属性重绑成了 APIRouter 实例。
ct = sys.modules["app.api.cover_templates_router"]
gm = sys.modules["app.api.generated_media_router"]

FAKE_USER_ID = "00000000-0000-0000-0000-000000000042"
SCOPE_ID = 42
GEN_ID = 341582104263581  # > 2^53：JS 里必须是字符串才不丢精度
TPL_ID = 341588599799820


async def _fake_auth() -> AuthContext:
    return AuthContext(user_id=FAKE_USER_ID, auth_type="jwt")


@pytest.fixture(autouse=True)
def _override_auth():
    app.dependency_overrides[get_auth] = _fake_auth
    yield
    app.dependency_overrides.pop(get_auth, None)


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
def _fake_scope(monkeypatch):
    async def _scope(auth):
        return SCOPE_ID

    monkeypatch.setattr(ct, "_scope", _scope)
    monkeypatch.setattr(gm, "_scope", _scope)


def _row(**over) -> dict:
    """仓储层返回的形状：id 已经 str 化（``_normalize``），时间是 datetime。"""
    import datetime

    base = {
        "id": str(TPL_ID),
        "scope_id": str(SCOPE_ID),
        "creator_id": FAKE_USER_ID,
        "name": "Bold headline",
        "generated_media_id": str(GEN_ID),
        "source_kind": "upload",
        "source_resource_id": None,
        "usage_count": 12,
        "last_used_at": None,
        "created_at": datetime.datetime(2026, 8, 22, tzinfo=datetime.UTC),
        "updated_at": datetime.datetime(2026, 8, 22, tzinfo=datetime.UTC),
    }
    base.update(over)
    return base


class _FakeGenRepo:
    """generated_media 仓储替身。``get`` 带 scope 参数，返回 None 表示不在本 scope。"""

    def __init__(self, row):
        self._row = row
        self.deleted = []

    async def get(self, gen_id, scope_id):
        return self._row

    async def delete(self, gen_id, scope_id):
        self.deleted.append((gen_id, scope_id))
        return True


class _FakeTplRepo:
    def __init__(self, *, existing=None, blocking=None):
        self._existing = existing
        self._blocking = blocking or []
        self.created = []
        self.deleted = []
        self.bumped = []

    async def find_by_media(self, scope_id, generated_media_id):
        return self._existing

    async def create(self, **kw):
        self.created.append(kw)
        return _row(
            name=kw["name"],
            source_kind=kw["source_kind"],
            source_resource_id=(
                str(kw["source_resource_id"]) if kw["source_resource_id"] else None
            ),
        )

    async def list_for_scope(self, scope_id):
        return [_row(usage_count=12), _row(id="9", name="Split compare", usage_count=4)]

    async def delete(self, template_id, scope_id):
        self.deleted.append((template_id, scope_id))
        return True

    async def bump_usage(self, template_ids, scope_id):
        self.bumped.append((template_ids, scope_id))

    async def names_blocking_media(self, generated_media_id):
        return self._blocking


def _wire(monkeypatch, *, gen_row=None, tpl_repo=None, gen_repo=None):
    tpl = tpl_repo or _FakeTplRepo()
    gen = gen_repo or _FakeGenRepo(gen_row)
    monkeypatch.setattr(ct, "CoverTemplatesRepository", lambda: tpl)
    monkeypatch.setattr(ct, "GeneratedMediaRepository", lambda: gen)
    monkeypatch.setattr(gm, "GeneratedMediaRepository", lambda: gen)
    import app.repositories.cover_templates_repository as ctr

    monkeypatch.setattr(ctr, "CoverTemplatesRepository", lambda: tpl)
    return tpl, gen


IMAGE_ROW = {"id": GEN_ID, "media_kind": "image", "mime": "image/png"}
VIDEO_ROW = {"id": GEN_ID, "media_kind": "video", "mime": "video/mp4"}


# ── create ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_outside_callers_scope_is_404_and_creates_nothing(
    monkeypatch, client
):
    """外键不查 scope，所以这道检查是唯一阻止「猜 id 给别人的图挂模板」的东西。
    缺了它，不存在的行还会以 IntegrityError → 500 的形态出现。"""
    tpl, _ = _wire(monkeypatch, gen_row=None)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "x"},
    )

    assert resp.status_code == 404
    assert tpl.created == []


@pytest.mark.asyncio
async def test_video_generation_cannot_become_a_cover_template(monkeypatch, client):
    tpl, _ = _wire(monkeypatch, gen_row=VIDEO_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "x"},
    )

    assert resp.status_code == 400
    assert tpl.created == []


@pytest.mark.asyncio
async def test_adding_the_same_image_twice_returns_the_existing_row(
    monkeypatch, client
):
    """幂等，不是 409。两张只有名字不同的相同卡片不是用户要的状态；而对着一个
    看上去没做错任何事的按钮报错，比一个把他要的卡片显示出来的 no-op 更糟。"""
    existing = _row(name="Bold headline")
    tpl, _ = _wire(
        monkeypatch, gen_row=IMAGE_ROW, tpl_repo=_FakeTplRepo(existing=existing)
    )

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "A different name"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["id"] == str(TPL_ID)
    assert tpl.created == [], "第二次不该再插一行"


@pytest.mark.asyncio
async def test_image_url_is_exactly_the_shape_the_generation_bridge_accepts(
    monkeypatch, client
):
    """``canvas_generation.py`` 把 source_urls 每一项喂给
    ``generated_media_local_path()``，它只认
    ``/generated-media/(\\d+)/(cover|stream|file)$``；不匹配就返回 None 并被
    **静默丢弃**。所以这里断言字面量，而不是"包含 id"之类的松断言。"""
    _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "Bold headline"},
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["image_url"] == f"/api/v1/generated-media/{GEN_ID}/cover"


@pytest.mark.asyncio
async def test_every_snowflake_id_crosses_the_wire_as_a_string(monkeypatch, client):
    """> 2^53。JSON number 会在浏览器里丢低位，模板就指向另一行了。"""
    _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "Bold headline"},
    )

    data = resp.json()["data"]
    assert isinstance(data["id"], str)
    assert isinstance(data["generated_media_id"], str)


@pytest.mark.asyncio
async def test_library_pick_records_the_originating_resource_id(monkeypatch, client):
    tpl, _ = _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={
            "generated_media_id": str(GEN_ID),
            "name": "From library",
            "source_kind": "library",
            "source_resource_id": "777",
        },
    )

    assert resp.status_code == 200
    assert tpl.created[0]["source_resource_id"] == 777
    assert tpl.created[0]["source_kind"] == "library"


@pytest.mark.asyncio
async def test_a_blank_name_is_rejected_and_creates_nothing(monkeypatch, client):
    """``min_length=1`` 查的是**原串**，四个空格能过校验、strip 之后变空 ——
    模板网格里就多一张没有名字的卡片，用户既选不出也说不清它是什么。"""
    tpl, _ = _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "    "},
    )

    assert resp.status_code == 422
    assert tpl.created == []


@pytest.mark.asyncio
async def test_a_name_is_stored_trimmed(monkeypatch, client):
    tpl, _ = _wire(monkeypatch, gen_row=IMAGE_ROW)

    await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": str(GEN_ID), "name": "  Bold headline  "},
    )

    assert tpl.created[0]["name"] == "Bold headline"


@pytest.mark.asyncio
async def test_use_refuses_more_ids_than_can_reach_the_model(monkeypatch, client):
    """参考池服务端上限是 9（canvas_generation.py 与 codex_cli.py 各钉一次）。
    比这更多的 id 说明调用方搞错了，不该被翻译成一条十万元素的 IN 子句。"""
    tpl, _ = _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates/use",
        json={"template_ids": [str(i) for i in range(10)]},
    )

    assert resp.status_code == 422
    assert tpl.bumped == []


@pytest.mark.asyncio
async def test_a_non_numeric_id_is_400_not_500(monkeypatch, client):
    _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates",
        json={"generated_media_id": "abc", "name": "x"},
    )

    assert resp.status_code == 400


# ── list / use ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_items_with_derived_urls(monkeypatch, client):
    _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.get("/api/v1/cover-templates")

    items = resp.json()["data"]["items"]
    assert len(items) == 2
    assert all(i["image_url"].endswith("/cover") for i in items)
    assert items[0]["usage_count"] >= items[1]["usage_count"]


@pytest.mark.asyncio
async def test_use_bumps_the_named_templates(monkeypatch, client):
    tpl, _ = _wire(monkeypatch, gen_row=IMAGE_ROW)

    resp = await client.post(
        "/api/v1/cover-templates/use", json={"template_ids": ["1", "2"]}
    )

    assert resp.status_code == 200
    assert resp.json()["data"]["counted"] == 2
    assert tpl.bumped == [([1, 2], SCOPE_ID)]


# ── the RESTRICT foreign key, translated ────────────────────────────────────


@pytest.mark.asyncio
async def test_deleting_a_cited_image_is_a_typed_409_naming_the_template(
    monkeypatch, client
):
    """裸 500 会告诉用户"服务坏了"，而真相是他自己的模板正握着这张图 ——
    既不准确也不可操作。"""
    _wire(
        monkeypatch,
        gen_row=IMAGE_ROW,
        tpl_repo=_FakeTplRepo(blocking=["Bold headline"]),
    )

    resp = await client.delete(f"/api/v1/generated-media/{GEN_ID}")

    assert resp.status_code == 409, "绝不能是 500"
    body = resp.json()
    # 共用的 AppError handler 渲染成 {error, code, request_id, details}。
    # ``error`` 必须是真句子而不是 "Request failed" —— 用户读到的就是它。
    assert body["code"] == "used_by_cover_templates"
    assert "cover template" in body["error"]
    assert "Bold headline" in body["details"]["template_names"]


@pytest.mark.asyncio
async def test_once_no_template_cites_it_the_image_deletes_normally(
    monkeypatch, client
):
    """这正是硬删除存在的理由：软删除会让归档行继续握着 RESTRICT 外键，
    于是这张图永远删不掉，而挡住它的东西在任何界面上都看不见。"""
    _, gen = _wire(monkeypatch, gen_row=IMAGE_ROW, tpl_repo=_FakeTplRepo(blocking=[]))

    resp = await client.delete(f"/api/v1/generated-media/{GEN_ID}")

    assert resp.status_code == 200
    assert resp.json()["data"]["deleted"] is True
    assert gen.deleted == [(GEN_ID, SCOPE_ID)]
