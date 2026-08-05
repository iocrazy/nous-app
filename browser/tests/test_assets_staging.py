"""Media staging: the byte ceiling, the proxy bypass, and the cleanup.

Downloading runs against a real local HTTP server rather than a mocked
`urlopen`. The three things worth testing here - a response that lies about its
size, a response that declares none at all, and a socket that never finishes -
are properties of the HTTP layer, and a mock that stands in for it would be a
test of the mock.

Every test that asserts "nothing was left behind" points `tempfile` at a fresh
directory and checks that directory is empty afterwards, which is the only
formulation that catches a leak regardless of how the scratch directory is
named (spec 7.6: this container has no storage volume, and an orphaned few
hundred megabytes fills its writable layer silently).
"""

from __future__ import annotations

import tempfile
import threading
import time
import urllib.request
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app import assets
from app.assets import (
    AssetError,
    IMAGE_EXTENSIONS,
    VIDEO_EXTENSIONS,
    sanitize_filename,
    stage_assets,
    validate_extension,
    validate_media_url,
)
from app.config import get_settings
from app.schemas import MediaItem, SessionStatus

pytestmark = pytest.mark.unit


# --- pure validators --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://nous-backend:8080/signed/clip.mp4",
        "http://nous-backend:8080/signed/clip.mp4?X-Amz-Signature=abc",
    ],
)
def test_signed_http_urls_are_accepted(url):
    assert validate_media_url(url) is None


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://host/clip.mp4", "clip.mp4", "", "   ", "https:///clip.mp4"],
)
def test_anything_that_is_not_an_http_url_with_a_host_is_refused(url):
    """`file://` is the one that matters: this string is about to be opened, and
    a bad backend row must not become a read of the container's own filesystem."""
    assert validate_media_url(url) is not None


def test_a_known_video_extension_passes():
    assert validate_extension("clip.MP4", VIDEO_EXTENSIONS, "video") is None


@pytest.mark.parametrize("filename", ["clip.exe", "clip", "clip.", "clip.jpg"])
def test_an_unexpected_video_extension_is_refused(filename):
    """The platform's file input infers the type from the name, so a wrong
    extension does not fail loudly - it uploads and then sits in an editor state
    that never resolves."""
    assert validate_extension(filename, VIDEO_EXTENSIONS, "video") is not None


def test_a_video_extension_is_not_a_valid_cover():
    assert validate_extension("cover.mp4", IMAGE_EXTENSIONS, "cover") is not None


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("clip.mp4", "clip.mp4"),
        ("../../etc/passwd", "passwd"),
        ("nested/dir/clip.mp4", "clip.mp4"),
        ("cl:ip*?.mp4", "cl_ip__.mp4"),
        ("with\nnewline.mp4", "with_newline.mp4"),
        ("  spaced.mp4  ", "spaced.mp4"),
    ],
)
def test_filenames_are_reduced_to_a_single_safe_component(raw, expected):
    assert sanitize_filename(raw, fallback="fallback.bin") == expected


@pytest.mark.parametrize("raw", ["", "   ", ".", "..", "...", "/", "///"])
def test_filenames_with_nothing_usable_fall_back(raw):
    assert sanitize_filename(raw, fallback="video.bin") == "video.bin"


def test_absurdly_long_filenames_are_bounded():
    assert len(sanitize_filename("x" * 500 + ".mp4", fallback="v.bin")) == 180


# --- a real HTTP origin -----------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    """Serves the response shapes the ceiling and timeout logic must survive."""

    protocol_version = "HTTP/1.0"  # no keep-alive: the client reads until EOF

    def log_message(self, *_args):  # keep pytest output clean
        return

    def do_GET(self):  # noqa: N802 - stdlib naming
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        size = int(query.get("n", ["16"])[0])

        if parts.path == "/missing":
            self.send_error(404, "nope")
            return

        if parts.path == "/slow":
            time.sleep(2.0)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if parts.path == "/declared-huge":
            # Declares far more than it will ever send: the guard must fire off
            # the header, before a single byte of body is written to disk.
            self.send_header("Content-Length", str(10**12))
        elif parts.path != "/undeclared":
            # `/undeclared` deliberately omits Content-Length, the way a chunked
            # or streaming origin does.
            self.send_header("Content-Length", str(size))
        self.end_headers()
        try:
            self.wfile.write(b"v" * size)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture(scope="module")
def origin():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


@pytest.fixture
def scratch(monkeypatch, tmp_path):
    """Point staging at an empty directory so leaks are visible as leftovers."""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def item(url: str, filename: str = "clip.mp4") -> MediaItem:
    return MediaItem(kind="video", url=url, filename=filename)


def set_max_bytes(monkeypatch, value: int) -> None:
    monkeypatch.setenv("BROWSER_ASSET_MAX_BYTES", str(value))
    get_settings.cache_clear()


# --- downloading ------------------------------------------------------------


async def test_a_staged_asset_lands_in_the_scratch_directory_with_its_bytes(
    origin, scratch
):
    async with stage_assets([("video", item(f"{origin}/ok?n=1024"))]) as staged:
        asset = staged["video"]
        assert asset.size_bytes == 1024
        assert Path(asset.path).read_bytes() == b"v" * 1024
        assert Path(asset.path).parent.parent == scratch


async def test_both_roles_are_staged_under_the_same_directory(origin, scratch):
    items = [
        ("video", item(f"{origin}/ok?n=32")),
        ("cover", MediaItem(kind="cover", url=f"{origin}/ok?n=16", filename="cover.jpg")),
    ]
    async with stage_assets(items) as staged:
        assert set(staged) == {"video", "cover"}
        assert Path(staged["video"].path).parent == Path(staged["cover"].path).parent


async def test_a_traversal_filename_cannot_escape_the_scratch_directory(origin, scratch):
    """The filename originates from a database row and becomes a real path. A
    write outside the scratch directory would also survive the cleanup, which
    is the part that turns a naming bug into a persistent one."""
    bad = MediaItem(kind="video", url=f"{origin}/ok?n=8", filename="../../escaped.mp4")
    async with stage_assets([("video", bad)]) as staged:
        path = Path(staged["video"].path).resolve()
        assert path.parent.parent == scratch.resolve()
        assert path.name == "escaped.mp4"


async def test_a_declared_size_over_the_ceiling_is_refused_before_the_body(
    origin, scratch, monkeypatch
):
    """First of the two ceiling paths. Honouring `Content-Length` costs one
    round trip; discovering the size by writing it to disk costs the disk."""
    set_max_bytes(monkeypatch, 1024)
    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", item(f"{origin}/declared-huge"))]):
            pass
    assert excinfo.value.detail["reason"] == "asset_too_large"
    assert excinfo.value.status is SessionStatus.FAILED


async def test_an_undeclared_size_over_the_ceiling_is_caught_mid_download(
    origin, scratch, monkeypatch
):
    """Second ceiling path, and the one that actually protects the disk.

    A chunked or streaming origin sends no `Content-Length` at all, so a guard
    that only reads the header is a guard that is absent for exactly the
    responses that can be unbounded. The count of bytes received is what the
    limit is enforced against.
    """
    set_max_bytes(monkeypatch, 512)
    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", item(f"{origin}/undeclared?n=4096"))]):
            pass
    assert excinfo.value.detail["reason"] == "asset_too_large"
    assert "mid-download" in excinfo.value.message


async def test_an_asset_exactly_at_the_ceiling_is_still_allowed(origin, scratch, monkeypatch):
    set_max_bytes(monkeypatch, 256)
    async with stage_assets([("video", item(f"{origin}/ok?n=256"))]) as staged:
        assert staged["video"].size_bytes == 256


async def test_an_http_error_becomes_a_typed_failure_carrying_the_code(origin, scratch):
    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", item(f"{origin}/missing"))]):
            pass
    assert excinfo.value.detail["reason"] == "asset_unavailable"
    assert excinfo.value.detail["http_status"] == 404
    assert excinfo.value.status is SessionStatus.FAILED


async def test_an_unreachable_origin_becomes_a_typed_failure_not_a_traceback(scratch):
    with pytest.raises(AssetError) as excinfo:
        # Port 1 has no listener.
        async with stage_assets([("video", item("http://127.0.0.1:1/clip.mp4"))]):
            pass
    assert excinfo.value.detail["reason"] == "asset_download_failed"
    assert excinfo.value.detail["role"] == "video"


async def test_an_empty_file_is_refused(origin, scratch):
    """An empty video uploads "successfully" and then wedges the editor in a
    state that carries no error message, so the publish times out minutes later
    pointing at the wrong thing."""
    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", item(f"{origin}/ok?n=0"))]):
            pass
    assert excinfo.value.detail["reason"] == "asset_empty"


async def test_a_download_that_never_finishes_is_a_timeout_not_a_generic_failure(
    origin, scratch, monkeypatch
):
    """`timeout` and `failed` route differently at the caller: one is worth
    retrying, the other is worth surfacing. The distinction has to survive the
    thread boundary the download runs on."""
    monkeypatch.setattr(
        assets,
        "get_settings",
        lambda: replace(get_settings(), asset_download_timeout_s=0.2),
    )
    with pytest.raises(AssetError) as excinfo:
        async with stage_assets([("video", item(f"{origin}/slow?n=8"))]):
            pass
    assert excinfo.value.status is SessionStatus.TIMEOUT
    assert excinfo.value.detail["reason"] == "asset_download_timeout"


async def test_the_ambient_proxy_is_not_used_for_our_own_asset_fetch(
    origin, scratch, monkeypatch
):
    """The signed URL is an address on the docker network. Routing it through
    the account's outbound residential proxy would be slow, wrong, and would
    make an asset fetch fail whenever the *account's* proxy is down."""
    dead = "http://127.0.0.1:1"
    monkeypatch.setenv("http_proxy", dead)
    monkeypatch.setenv("HTTP_PROXY", dead)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    # Control: an opener that *does* pick up the environment must fail on this
    # URL. Without it the assertion below would pass on any platform that
    # bypasses proxies for local addresses, proving nothing.
    ambient = urllib.request.build_opener(urllib.request.ProxyHandler())
    try:
        ambient.open(f"{origin}/ok?n=8", timeout=5).read()
    except Exception:
        pass
    else:
        pytest.skip("ambient proxy is bypassed for this host regardless")

    async with stage_assets([("video", item(f"{origin}/ok?n=8"))]) as staged:
        assert staged["video"].size_bytes == 8


# --- cleanup (spec 7.6) -----------------------------------------------------


async def test_the_scratch_directory_is_removed_after_a_successful_publish(
    origin, scratch
):
    """Cleanup on the happy path. Stated separately from the failure paths
    because it is the one an `except` handler would not cover."""
    async with stage_assets([("video", item(f"{origin}/ok?n=64"))]) as staged:
        inside = Path(staged["video"].path)
        assert inside.exists()  # not vacuous: something really was written

    assert inside.exists() is False
    assert list(scratch.iterdir()) == []


async def test_the_scratch_directory_is_removed_when_staging_itself_fails(
    origin, scratch
):
    """The half-written file is the leak that matters: a download aborted at the
    byte ceiling has already put megabytes on disk, and the request that would
    have cleaned them up is the one that just failed."""
    with pytest.raises(AssetError):
        async with stage_assets([("video", item(f"{origin}/missing"))]):
            pass

    assert list(scratch.iterdir()) == []


async def test_a_partial_multi_asset_stage_leaves_nothing_behind(origin, scratch):
    """The first asset succeeded and is on disk when the second one fails. The
    directory - not the individual files - is what gets removed, which is what
    makes this case fall out for free."""
    items = [
        ("video", item(f"{origin}/ok?n=64")),
        ("cover", MediaItem(kind="cover", url=f"{origin}/missing", filename="cover.jpg")),
    ]
    with pytest.raises(AssetError):
        async with stage_assets(items):
            pass

    assert list(scratch.iterdir()) == []


async def test_an_exception_from_the_publish_itself_still_removes_the_assets(
    origin, scratch
):
    """The interesting case: staging worked, the browser work blew up. Cleanup
    lives in `finally` rather than at the end of the happy path precisely so
    this path is covered - and a crashed publish is the one most likely to
    happen repeatedly, which is how a leak becomes a full disk."""
    with pytest.raises(RuntimeError):
        async with stage_assets([("video", item(f"{origin}/ok?n=64"))]):
            raise RuntimeError("chromium vanished mid-publish")

    assert list(scratch.iterdir()) == []


async def test_cancellation_mid_publish_still_removes_the_assets(origin, scratch):
    """A hard timeout at the endpoint cancels the task rather than letting it
    return, so cleanup must survive `CancelledError` too - otherwise the exact
    requests that time out are the ones that leak."""
    import asyncio

    with pytest.raises(asyncio.CancelledError):
        async with stage_assets([("video", item(f"{origin}/ok?n=64"))]):
            raise asyncio.CancelledError()

    assert list(scratch.iterdir()) == []


async def test_a_failure_to_clean_up_is_logged_rather_than_replacing_the_outcome(
    origin, scratch, monkeypatch, caplog
):
    """Cleanup is best effort by design: a filesystem error must not overwrite
    the real result of the publish. Silent is not an option either - an
    invisible leak is how a container's writable layer fills over weeks."""
    def _boom(_path):
        raise OSError("device or resource busy")

    monkeypatch.setattr(assets.shutil, "rmtree", _boom)
    with caplog.at_level("ERROR", logger="nous_browser.assets"):
        async with stage_assets([("video", item(f"{origin}/ok?n=8"))]) as staged:
            assert staged["video"].size_bytes == 8

    assert any("scratch directory" in record.message for record in caplog.records)


def test_the_download_never_holds_a_whole_asset_in_memory():
    """Structural guard for the one property a behavioural test cannot see.

    These files are hundreds of megabytes; a `response.read()` with no argument
    works perfectly in every test and OOMs the container in production. The
    streaming form is the contract.
    """
    source = Path(assets.__file__).read_text(encoding="utf-8")
    assert "response.read(chunk)" in source
    assert "response.read()" not in source
