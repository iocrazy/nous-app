import asyncio

from app.services.media.parsers.soda_music.cookie_validate import validate_soda_cookie


class _OkApi:
    async def get_me(self):
        return {"my_info": {"id": "12345"}}


class _NoUserApi:
    async def get_me(self):
        return {"my_info": {}}


class _RaiseApi:
    async def get_me(self):
        raise RuntimeError("403 forbidden")


def test_validate_ok():
    assert asyncio.run(validate_soda_cookie("ck", api=_OkApi())) == (True, None)


def test_validate_no_user_id():
    ok, err = asyncio.run(validate_soda_cookie("ck", api=_NoUserApi()))
    assert ok is False and err


def test_validate_request_error():
    ok, err = asyncio.run(validate_soda_cookie("ck", api=_RaiseApi()))
    assert ok is False and "403" in err


def test_validate_empty_cookie():
    ok, err = asyncio.run(validate_soda_cookie("", api=_OkApi()))
    assert ok is False and err
