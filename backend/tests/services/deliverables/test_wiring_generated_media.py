"""接线一：``register_generated_media`` 插行成功后恰好登记一次。

叠在唯一的写入点上，所以 11 个调用点一个都不用改——它们的区别只剩
``origin.run_id`` 有没有值。上传路径（``_insert_uploaded_row``）不是 agent
产出，一次都不登记。
"""

from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy.dialects import postgresql


class _FakeResult:
    def __init__(self, row: dict):
        self._row = row

    def mappings(self):
        return self

    def first(self):
        return self._row


def _fake_write_scope(row_id: int):
    @asynccontextmanager
    async def _scope():
        class _Session:
            async def execute(self, stmt):
                params = dict(stmt.compile(dialect=postgresql.dialect()).params)
                return _FakeResult({"id": row_id, **params})

        yield _Session()

    return _scope


@pytest.fixture
def register_spy(monkeypatch):
    import app.services.library.generated_media_service as gm

    calls: list[dict] = []

    async def _spy(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(gm, "register_deliverable_best_effort", _spy)
    return calls


@pytest.fixture
def insert_stub(monkeypatch, tmp_path):
    import app.services.library.generated_media_service as gm

    async def _fake_download(dest_path, source_url, **_k):
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"img")
        return 3

    monkeypatch.setattr(gm, "_download_to", _fake_download)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(gm.settings, "FEATURE_CHAT_MEDIA_OBJECT_STORE", False)
    monkeypatch.setattr(gm, "write_scope", _fake_write_scope(4242))
    return gm


async def test_agent_generation_registers_one_deliverable(register_spy, insert_stub):
    """agent 生图：插行成功后恰好登记一次，标题取 origin 的 prompt 首行。"""
    gm = insert_stub
    await gm.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://x/y.png",
        mime="image/png",
        origin=gm.GenerationOrigin(
            kind="agent_run",
            run_id="777",
            agent_id="a",
            model="gpt-image-2.5",
            cost_cents=0.12,
            prompt="A cafe at dusk\nsecond line",
            turn=1,
            step=3,
        ),
    )
    assert register_spy == [
        dict(
            run_id="777",
            kind="generated_media",
            ref_id="4242",
            title="A cafe at dusk",
            model="gpt-image-2.5",
            cost_cents=0.12,
            turn=1,
            step=3,
            # 这个调用点没有活 recorder（DBOS 侧回来的出图，父 run 多半已
            # 收工），显式的 None 让登记口照旧走 ``for_run``。带 recorder 的
            # 那条路见 ``test_live_recorder_wiring``。
            recorder=None,
        )
    ]


async def test_canvas_generation_registers_nothing(register_spy, insert_stub):
    """画布路径 origin 无 run_id —— 登记口收到 ``run_id=None`` 就是零登记。

    注意断的是「传下去的 run_id 是空」，不是「没调用」：唯一入口必须无条件
    流经，判空的责任在登记口一处，不在 11 个调用点各自记得。"""
    gm = insert_stub
    await gm.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://x/y.png",
        mime="image/png",
        origin=gm.GenerationOrigin(kind="canvas_run", canvas_id=7, node_id="n1"),
    )
    assert [c["run_id"] for c in register_spy] == [None]


async def test_an_upload_never_registers(register_spy, insert_stub, tmp_path):
    """上传不是 agent 产出。登记挪进 ``_insert_uploaded_row`` 这条会转红。"""
    gm = insert_stub
    await gm._insert_uploaded_row(
        user_id="u",
        scope_id=1,
        kind="image",
        mime="image/png",
        file_path="teams/1/uploads/x.png",
        file_size_bytes=3,
        origin=gm.GenerationOrigin(kind="chat_upload", conversation_id=5),
    )
    assert register_spy == []


@pytest.fixture
def price_stub(monkeypatch):
    import app.services.library.generated_media_service as gm

    asked: list[tuple] = []

    async def _price(model, provider):
        asked.append((model, provider))
        return 12.0

    monkeypatch.setattr(gm, "media_price_cents", _price)
    return asked


async def test_media_cost_is_filled_from_the_catalog_price(
    register_spy, insert_stub, price_stub
):
    """登记行与 generated_media 行拿到同一个数（3b spec §5 验收⑤）。"""
    gm = insert_stub
    out = await gm.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://x/y.png",
        mime="image/png",
        origin=gm.GenerationOrigin(
            kind="agent_run",
            run_id="777",
            model="doubao-seedream-4-0",
            provider="ark",
            prompt="A cafe at dusk",
        ),
    )
    assert price_stub == [("doubao-seedream-4-0", "ark")]
    assert register_spy[0]["cost_cents"] == 12.0
    assert out["cost_cents"] == 12.0


async def test_an_explicit_cost_is_never_overwritten(
    register_spy, insert_stub, price_stub
):
    """调用方已经知道花费时不查表——目录价是兜底，不是覆盖。"""
    gm = insert_stub
    await gm.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://x/y.png",
        mime="image/png",
        origin=gm.GenerationOrigin(
            kind="agent_run", run_id="777", model="m", provider="p", cost_cents=0.5
        ),
    )
    assert price_stub == [] and register_spy[0]["cost_cents"] == 0.5


async def test_half_an_attribution_means_no_lookup(
    register_spy, insert_stub, price_stub
):
    """归因缺一半就查不出价（Task 0 的理由）——不瞎猜，留 None。"""
    gm = insert_stub
    await gm.register_generated_media(
        user_id="u",
        scope_id=1,
        source_url="http://x/y.png",
        mime="image/png",
        origin=gm.GenerationOrigin(kind="agent_run", run_id="777", model="m"),
    )
    assert price_stub == [] and register_spy[0]["cost_cents"] is None
