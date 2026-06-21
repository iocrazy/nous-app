import pytest


@pytest.mark.asyncio
async def test_register_inserts_row_with_provenance(tmp_path, monkeypatch):
    import app.services.library.generated_media_service as gm

    async def _fake_download(dest_path, source_url, **k):
        from pathlib import Path

        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"img")
        return 3

    captured = {}

    async def _fake_execute_returning_one(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return {"id": 999, **params}

    monkeypatch.setattr(gm, "_download_to", _fake_download)
    monkeypatch.setattr(gm.settings, "DOWNLOAD_PATH", str(tmp_path))
    monkeypatch.setattr(
        gm.db_engine, "execute_returning_one", _fake_execute_returning_one
    )

    origin = gm.GenerationOrigin(
        kind="canvas_run",
        run_id="r1",
        canvas_id=7,
        node_id="n1",
        prompt="a cat",
        model="m",
        provider="p",
        params={"size": "1024"},
    )
    row = await gm.register_generated_media(
        user_id="u-uuid",
        scope_id=42,
        source_url="http://x/y.png",
        mime="image/png",
        origin=origin,
    )
    assert row["id"] == 999
    p = captured["params"]
    assert p["scope_id"] == 42 and p["creator_id"] == "u-uuid"
    assert p["media_kind"] == "image" and p["origin_kind"] == "canvas_run"
    assert p["canvas_id"] == 7 and p["prompt"] == "a cat"
    assert "generations/" in p["file_path"]
