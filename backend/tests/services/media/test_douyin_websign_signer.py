"""Argus webSign step of the HTTP ABogus tier.

Context (2026-09-15/16). `a_bogus` alone stopped being enough. Douyin's detail
endpoint now runs two Argus gates:

1. `uifid` must arrive as a REQUEST HEADER (shipped in #2309). Measured: with
   the header the error moved from `Uifid Not Found` to `Signature Not Found`.
2. The URL must carry `timestamp`, `uifid` and `x-secsdk-web-signature`,
   produced by douyin's own `webSignUrl` VM over the already-`a_bogus`-ed
   URL. Order matters: signing before `a_bogus` is appended signs the
   wrong string.

These pin the wiring and the failure shapes. Whether douyin still accepts the
result is only provable against the real service — see
`tests/test_douyin_live_acceptance.py` (opt-in).
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.services.media.parsers.douyin_parse import abogus_parser as ab
from app.services.media.parsers.douyin_parse.abogus_parser import ABogusDouyinParser
from app.services.media.parsers.douyin_parse.failures import (
    DouyinFailure,
    DouyinParseError,
)

SIGN_RUN = "app.services.media.parsers.douyin_parse.abogus_parser.subprocess.run"

# Values that must never appear in a log line or exception text.
SESSION_VALUE = "SUPERSECRETSESSIONVALUE"
UIFID_VALUE = "UIFIDSECRET0123456789"
COOKIE = f"UIFID={UIFID_VALUE}; sessionid={SESSION_VALUE}; ttwid=w1"
UA = "Mozilla/5.0 (test)"
BASE_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=1&a_bogus=AB"
SIGNED_URL = (
    f"{BASE_URL}&timestamp=173&uifid={UIFID_VALUE}"
    "&x-secsdk-web-signature=abcdef0123456789abcdef0123456789"
)


def _completed(stdout: str = "", stderr: str = "", rc: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ------------------------------------------------------------ missing UIFID


def test_missing_uifid_is_a_typed_auth_failure():
    """No UIFID means the request cannot pass Argus. Sending it anyway earns a
    403 that reads as "douyin is throttling you, wait"; the true next move is
    "refresh the saved cookie", which is what AUTH_REQUIRED tells the user."""
    with pytest.raises(DouyinParseError) as excinfo:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, "ttwid=w1")

    assert excinfo.value.kind is DouyinFailure.AUTH_REQUIRED
    assert "UIFID" in str(excinfo.value)


def test_missing_uifid_does_not_spawn_node():
    with patch(SIGN_RUN) as run:
        with pytest.raises(DouyinParseError):
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, "")
    run.assert_not_called()


def test_missing_uifid_error_names_the_field_not_the_value():
    with pytest.raises(DouyinParseError) as excinfo:
        ABogusDouyinParser._sign_with_websign(
            BASE_URL, UA, f"sessionid={SESSION_VALUE}"
        )

    assert SESSION_VALUE not in str(excinfo.value)


@pytest.mark.asyncio
async def test_parse_surfaces_the_typed_failure_instead_of_none():
    """`parse` flattens generic exceptions to None; a typed one must pass
    through so the chain can tell the user why."""
    with (
        patch.object(
            ABogusDouyinParser, "_resolve_aweme_id", new=AsyncMock(return_value="1")
        ),
        patch.object(
            ABogusDouyinParser,
            "_resolve_cookie",
            new=AsyncMock(return_value=("ttwid=w1", {})),
        ),
        patch.object(ABogusDouyinParser, "_sign_with_python", return_value="AB"),
        patch(SIGN_RUN) as run,
    ):
        with pytest.raises(DouyinParseError) as excinfo:
            await ABogusDouyinParser.parse("1", user_agent=UA)

    assert excinfo.value.kind is DouyinFailure.AUTH_REQUIRED
    run.assert_not_called()


# ------------------------------------------------------- subprocess wiring


def test_cookie_ua_and_uifid_reach_the_node_subprocess():
    """All three must agree across a_bogus, webSignUrl and the final GET.
    A UA that differs between signing and sending is a flagged session."""
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL)) as run:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    env = run.call_args.kwargs["env"]
    assert env["DOUYIN_COOKIE"] == COOKIE
    assert env["DOUYIN_UIFID"] == UIFID_VALUE
    assert env["DOUYIN_UA"] == UA


def test_node_runs_under_permission_sandbox():
    """`--permission` + a read allowlist confines douyin's bundled VM bytecode.
    Dropping it to tolerate an older Node is not an acceptable fix."""
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL)) as run:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    argv = run.call_args.args[0]
    assert "--permission" in argv
    assert f"--allow-fs-read={ab._WEBSIGN_ENV_JS.parent}" in argv
    assert argv[-1] == BASE_URL


def test_signed_url_is_returned_verbatim():
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL + "\n")):
        assert ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE) == SIGNED_URL


def test_signed_url_must_carry_the_signature():
    """The VM answering without `x-secsdk-web-signature` means the runtime
    bundle drifted. Passing that URL on would come back as a generic Argus
    rejection and hide the real cause."""
    unsigned = f"{BASE_URL}&timestamp=173&uifid={UIFID_VALUE}"
    with patch(SIGN_RUN, return_value=_completed(stdout=unsigned)):
        with pytest.raises(RuntimeError, match="x-secsdk-web-signature") as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    # The returned URL embeds uifid; it must not be echoed into the error.
    assert UIFID_VALUE not in str(excinfo.value)


@pytest.mark.asyncio
async def test_websign_signs_the_url_that_already_carries_a_bogus():
    """Order is part of the contract: webSignUrl signs the full query string,
    so it must see `a_bogus` — signing first and appending after produces a
    signature over a different string."""
    seen: dict[str, str] = {}

    def fake_websign(url: str, ua: str, cookie: str) -> str:
        seen["url"] = url
        return url + "&x-secsdk-web-signature=sig"

    with (
        patch.object(ABogusDouyinParser, "_sign_with_python", return_value="AB"),
        patch.object(
            ABogusDouyinParser, "_sign_with_websign", side_effect=fake_websign
        ),
    ):
        out = await ABogusDouyinParser._sign(
            "https://www.douyin.com/x/?aweme_id=1", UA, "python", "", COOKIE
        )

    assert parse_qs(urlsplit(seen["url"]).query)["a_bogus"] == ["AB"]
    assert out.endswith("&x-secsdk-web-signature=sig")


# ------------------------------------------------------ failure classification


def test_node_nonzero_exit_is_a_signing_failure():
    with patch(
        SIGN_RUN, return_value=_completed(rc=3, stderr="webSignUrl unavailable")
    ):
        with pytest.raises(RuntimeError, match="webSign sign failed"):
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)


def test_node_timeout_propagates():
    """A hung Node must surface as a timeout, not as a silent empty URL."""
    with patch(SIGN_RUN, side_effect=subprocess.TimeoutExpired("node", 10)):
        with pytest.raises(subprocess.TimeoutExpired):
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)


def test_node_failure_message_does_not_leak_the_cookie():
    """The signer holds the cookie in its env, so its stderr may echo it. That
    text becomes the exception, the log line and the task row a user opens."""
    leaky_stderr = f"env was DOUYIN_COOKIE={COOKIE}"
    with patch(SIGN_RUN, return_value=_completed(rc=1, stderr=leaky_stderr)):
        with pytest.raises(RuntimeError) as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    text = str(excinfo.value)
    assert SESSION_VALUE not in text, "cookie leaked into exception"
    assert UIFID_VALUE not in text, "UIFID leaked into exception"


def test_unsupported_node_permission_flag_names_the_real_cause():
    """Node 20/21 reject `--permission` (they spell it
    `--experimental-permission`). A generic "signing failed" sends the
    operator to look at douyin; the message has to say "upgrade Node"."""
    with patch(
        SIGN_RUN, return_value=_completed(rc=9, stderr="node: bad option: --permission")
    ):
        with pytest.raises(RuntimeError) as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    text = str(excinfo.value)
    assert "Node >= 22" in text
    assert "--experimental-permission" in text


# ------------------------------------------------------------- node runtime


def test_the_runtime_files_ship_next_to_the_parser():
    """Both JS files are loaded by path at runtime; a packaging slip shows up
    only as a failed parse in production."""
    assert ab._WEBSIGN_ENV_JS.is_file()
    assert ab._WEBSIGN_ENV_JS.with_name("websign_runtime.js").is_file()


def test_dockerfile_installs_a_node_that_supports_permission():
    """Debian trixie's `nodejs` package is 20.19.2, which cannot run the signer.
    If the image is simplified back to the distro package, fail in CI rather
    than on every douyin HTTP parse in production."""
    dockerfile = Path(__file__).resolve().parents[4] / "Dockerfile"
    text = dockerfile.read_text(encoding="utf-8")

    apt_block = text.split("apt-get install -y", 1)[1].split(
        "--no-install-recommends", 1
    )[0]
    assert "nodejs" not in apt_block.split(), "distro nodejs is 20.x — no --permission"
    assert "ARG NODE_VERSION=22." in text
    # The build must prove the flag works rather than assume it.
    assert "node --permission" in text
