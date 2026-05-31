import asyncio

from app.services.media.parsers.soda_music.cookie_source import get_soda_cookie


class _FakeRepo:
    def __init__(self, row):
        self._row = row
        self.calls = []

    async def get_by_user_and_platform(self, user_id, platform):
        self.calls.append((user_id, platform))
        return self._row


def test_get_soda_cookie_reads_user_cookies_text():
    repo = _FakeRepo({"cookie_text": "sessionid=abc", "cookie_file": None})
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == "sessionid=abc"
    assert repo.calls == [("u1", "qishui")]


def test_get_soda_cookie_falls_back_to_cookie_file():
    repo = _FakeRepo({"cookie_text": None, "cookie_file": "ck.txt-contents"})
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == "ck.txt-contents"


def test_get_soda_cookie_empty_when_no_row():
    repo = _FakeRepo(None)
    assert asyncio.run(get_soda_cookie("u1", repo=repo)) == ""


def test_get_soda_cookie_empty_when_no_user():
    repo = _FakeRepo({"cookie_text": "x"})
    assert asyncio.run(get_soda_cookie(None, repo=repo)) == ""
