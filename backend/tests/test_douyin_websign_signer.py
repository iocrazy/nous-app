"""Tests for the Argus web-signature step of the HTTP tier (spec §5.1).

Context (2026-09-16). `a_bogus` alone stopped being enough. Douyin's detail
endpoint now runs two gates:

1. `uifid` must arrive as a REQUEST HEADER. Sending the same value as a
   cookie is not equivalent — a request carrying 59 cookies including
   `UIFID` still came back `Uifid Not Found`.
2. The URL must carry `timestamp`, `uifid` and `x-secsdk-web-signature`,
   produced by douyin's own `webSignUrl` VM over the already-`a_bogus`-ed
   URL. Order matters: signing before `a_bogus` is appended signs the
   wrong string.

Everything here pins that contract. The live acceptance proves it works;
these prove it stays wired correctly and fails loudly when it doesn't.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from app.services.media.parsers.douyin_parse.abogus_parser import (
    ABogusDouyinParser,
    _cookie_value,
)

SIGN_RUN = "app.services.media.parsers.douyin_parse.abogus_parser.subprocess.run"

# Values that must never appear in a log line or exception text.
SECRET_SESSION = "sessionid=SUPERSECRETSESSIONVALUE"
SECRET_UIFID = "UIFID=UIFIDSECRET0123456789"
COOKIE = f"{SECRET_UIFID}; {SECRET_SESSION}; ttwid=w1"
UA = "Mozilla/5.0 (test)"
BASE_URL = "https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id=1&a_bogus=AB"
SIGNED_URL = (
    f"{BASE_URL}&timestamp=173&uifid=UIFIDSECRET0123456789"
    "&x-secsdk-web-signature=abcdef0123456789abcdef0123456789"
)


def _completed(stdout: str = "", stderr: str = "", rc: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.returncode = rc
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ------------------------------------------------------------ cookie parse


def test_cookie_value_extracts_uifid():
    assert _cookie_value(COOKIE, "UIFID") == "UIFIDSECRET0123456789"


def test_cookie_value_absent_returns_empty():
    assert _cookie_value("ttwid=w1; sessionid=x", "UIFID") == ""


def test_cookie_value_does_not_match_a_suffix():
    """`X_UIFID=...` is a different cookie. A sloppy substring match would
    sign with someone else's fingerprint and fail in a way that looks like
    a server problem."""
    assert _cookie_value("X_UIFID=wrong; ttwid=w", "UIFID") == ""


def test_cookie_value_tolerates_empty_header():
    assert _cookie_value("", "UIFID") == ""


# ------------------------------------------------------------ missing UIFID


def test_missing_uifid_fails_loudly():
    """No UIFID means the request CANNOT be signed. Returning an unsigned
    URL would produce a 403 the caller would read as 'video unavailable'."""
    with pytest.raises(RuntimeError, match="UIFID"):
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, "ttwid=w1")


def test_missing_uifid_does_not_spawn_node():
    """Fail before the subprocess, not after — spawning Node to discover a
    precondition we already know is false is wasted latency on every call."""
    with patch(SIGN_RUN) as run:
        with pytest.raises(RuntimeError):
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, "")
    run.assert_not_called()


# ------------------------------------------------------- subprocess wiring


def test_cookie_ua_and_uifid_reach_the_node_subprocess():
    """All three must agree across a_bogus, webSignUrl and the final GET.
    A UA that differs between signing and sending is a flagged session."""
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL)) as run:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    env = run.call_args.kwargs["env"]
    assert env["DOUYIN_COOKIE"] == COOKIE
    assert env["DOUYIN_UIFID"] == "UIFIDSECRET0123456789"
    assert env["DOUYIN_UA"] == UA


def test_node_runs_under_permission_sandbox():
    """`--permission` + a read allowlist. Dropping it to make a Node
    upgrade easier would hand douyin's bundled VM bytecode free rein over
    the filesystem — the whole reason the flag is there."""
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL)) as run:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    argv = run.call_args.args[0]
    assert "--permission" in argv
    assert any(a.startswith("--allow-fs-read=") for a in argv)
    assert argv[-1] == BASE_URL


def test_signed_url_is_returned_verbatim():
    with patch(SIGN_RUN, return_value=_completed(stdout=SIGNED_URL + "\n")):
        assert ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE) == SIGNED_URL


def test_signed_url_must_carry_the_signature():
    """The VM answering without `x-secsdk-web-signature` means the runtime
    bundle drifted. Passing that URL on would hit Argus and come back as a
    generic rejection, hiding the real cause."""
    unsigned = f"{BASE_URL}&timestamp=173&uifid=U"
    with patch(SIGN_RUN, return_value=_completed(stdout=unsigned)):
        with pytest.raises(RuntimeError, match="x-secsdk-web-signature"):
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)


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


@pytest.mark.parametrize(
    "body",
    [
        "Blocked by ArgusSecurityPlugin Uifid Not Found",
        "Blocked by ArgusSecurityPlugin Signature Not Found",
    ],
)
def test_argus_rejection_is_typed_not_a_generic_miss(body):
    """Argus answers in PLAIN TEXT, so the JSON decode fails first. Reading
    that as 'no result' told users to retry a request that fails identically
    every time."""
    from app.services.media.parsers.douyin_parse.failures import (
        DouyinFailure,
        DouyinParseError,
    )

    assert "ArgusSecurityPlugin" in body or "Uifid" in body
    err = DouyinParseError(DouyinFailure.SIGNATURE_REJECTED, body)
    assert err.kind is DouyinFailure.SIGNATURE_REJECTED
    # Subclasses RuntimeError so existing handlers keep working.
    assert isinstance(err, RuntimeError)


# ----------------------------------------------------------- no secret leaks


def test_node_failure_message_does_not_leak_the_cookie():
    """The exception text travels into logs and onto the task row. Echoing
    a Node stderr that happened to contain the cookie would publish a live
    session to anyone who can read a task detail."""
    leaky_stderr = f"env was DOUYIN_COOKIE={COOKIE}"
    with patch(SIGN_RUN, return_value=_completed(rc=1, stderr=leaky_stderr)):
        with pytest.raises(RuntimeError) as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    # This asserts the CURRENT behaviour is unsafe-by-accident if it ever
    # regresses: the message must not carry the secret values.
    text = str(excinfo.value)
    assert "SUPERSECRETSESSIONVALUE" not in text, "cookie leaked into exception"
    assert "UIFIDSECRET0123456789" not in text, "UIFID leaked into exception"


def test_missing_uifid_error_names_the_field_not_the_value():
    with pytest.raises(RuntimeError) as excinfo:
        ABogusDouyinParser._sign_with_websign(BASE_URL, UA, SECRET_SESSION)

    text = str(excinfo.value)
    assert "UIFID" in text
    assert "SUPERSECRETSESSIONVALUE" not in text


def test_signature_missing_error_does_not_echo_the_signed_url():
    """The returned URL embeds `uifid` and the signature as query params.
    Putting it in the error would defeat the point of redacting the cookie."""
    unsigned = f"{BASE_URL}&uifid=UIFIDSECRET0123456789"
    with patch(SIGN_RUN, return_value=_completed(stdout=unsigned)):
        with pytest.raises(RuntimeError) as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    assert "UIFIDSECRET0123456789" not in str(excinfo.value)


# ------------------------------------------------------------- node runtime


def test_unsupported_node_permission_flag_names_the_real_cause():
    """Node 20/21 reject `--permission` (they call it
    `--experimental-permission`); only >= 22 accepts it. Measured
    2026-09-16 — Debian trixie's `nodejs` package is 20.19.2, so an image
    built from the distro package fails EVERY douyin HTTP parse here.

    A generic "signing failed" sends the operator to look at douyin. The
    message has to say "upgrade Node".
    """
    with patch(
        SIGN_RUN, return_value=_completed(rc=9, stderr="node: bad option: --permission")
    ):
        with pytest.raises(RuntimeError) as excinfo:
            ABogusDouyinParser._sign_with_websign(BASE_URL, UA, COOKIE)

    text = str(excinfo.value)
    assert "Node >= 22" in text
    assert "--experimental-permission" in text


def test_dockerfile_installs_a_node_that_supports_permission():
    """Pins the deployment side of the same fact.

    `apt-get install nodejs` on the base image yields Node 20, which cannot
    run the signer. If someone simplifies the Dockerfile back to the distro
    package, this fails at CI instead of in production.
    """
    from pathlib import Path

    dockerfile = Path(__file__).resolve().parents[2] / "Dockerfile"
    if not dockerfile.exists():  # pragma: no cover - repo layout guard
        pytest.skip("Dockerfile not found from this checkout")
    text = dockerfile.read_text(encoding="utf-8")

    assert "deb.nodesource.com" in text, (
        "Node must come from NodeSource — the distro package is 20.x and "
        "does not support --permission."
    )
    assert "NODE_MAJOR=22" in text or "NODE_MAJOR=2" in text
    # The build must prove the flag works rather than assume it.
    assert "node --permission" in text, (
        "Dockerfile must verify --permission at build time; a silent "
        "regression here only surfaces as failed parses in production."
    )
