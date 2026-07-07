from app.repositories.social_accounts_repository import (
    _decrypt_token_cols,
    _encrypt_token_cols,
    _public_row,
)


def test_tokens_encrypted_roundtrip():
    row = {
        "access_token": "act-plain",
        "refresh_token": "rft-plain",
        "username": "HEYGO",
    }
    enc = _encrypt_token_cols(dict(row))
    assert enc["access_token"] != "act-plain" and enc["access_token"].startswith(
        "gAAAA"
    )
    assert enc["refresh_token"] != "rft-plain"
    assert enc["username"] == "HEYGO"  # 非 token 列不动
    dec = _decrypt_token_cols(dict(enc))
    assert dec["access_token"] == "act-plain"
    assert dec["refresh_token"] == "rft-plain"


def test_none_token_passthrough():
    enc = _encrypt_token_cols({"access_token": None, "refresh_token": None})
    assert enc["access_token"] is None and enc["refresh_token"] is None


def test_public_row_strips_tokens_and_stringifies_id():
    pub = _public_row(
        {
            "id": 727145299382534145,
            "username": "x",
            "access_token": "gAAAA..",
            "refresh_token": "gAAAA..",
        }
    )
    assert "access_token" not in pub and "refresh_token" not in pub
    assert (
        pub["id"] == "727145299382534145"
    )  # Snowflake BIGINT 走 str（仓库约定，JS 2^53 精度）
    assert pub["username"] == "x"
