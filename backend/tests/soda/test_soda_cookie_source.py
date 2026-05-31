# backend/tests/soda/test_soda_cookie_source.py
import asyncio

from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie


def test_get_soda_cookie_reads_env(monkeypatch):
    monkeypatch.setenv("SODA_COOKIE", "sessionid=abc")
    assert asyncio.run(get_soda_cookie(user_id="u1")) == "sessionid=abc"


def test_get_soda_cookie_empty_when_unset(monkeypatch):
    monkeypatch.delenv("SODA_COOKIE", raising=False)
    assert asyncio.run(get_soda_cookie(user_id="u1")) == ""
