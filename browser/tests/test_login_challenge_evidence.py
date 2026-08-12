"""When the identity check stalls, say what was on the screen.

The 2026-08-11 fix taught the flow to *click* the 身份验证 chooser and to fail
in a typed way when the screen would not move. It shipped, and the very next
real bind failed inside it (2026-08-12, ~27s from scan to failure):

    reason  = identity_challenge_stalled
    message = "selected the SMS verification option, but the platform is still
               showing the identity check"

which is everything we knew. The user saw no code screen and got no text
message, and there were (at least) four explanations that produce that exact
record:

  1. the click never reached a handler (leaf text node, handler on the card);
  2. the click worked and the platform moved to a screen our captions still
     match, so the judge kept calling it "the chooser";
  3. the platform escalated to something unattended browsers cannot do;
  4. the platform simply took longer than the 15s the flow allowed.

Nothing in `detail` could tell them apart. These tests pin the fix for *that*:
a stalled challenge now carries the page it stalled on — the URL, the visible
text, every input and button, and the shape of the node we clicked — so the
next failure is read rather than guessed at.

The page here is **real HTML**, parsed by `tests/dom_fixture`, laid out like
the screenshot: the caption is a leaf `<span>` inside a `role="button"` card,
and a code field is already on screen before anything has been sent. The
document does not change when clicked, which is precisely the observed failure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from app.login import BUTTON_ANCESTOR_XPATH, LoginDriver
from app.login_sessions import (
    IDENTITY_CHALLENGE_STALLED,
    MAX_IDENTITY_CHALLENGE_POLLS,
    PAGE_EVIDENCE_KEY,
    LoginSession,
)
from app.platforms.douyin import LOGIN_SPEC
from app.schemas import SessionStatus
from tests.dom_fixture import FakePage

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parent / "fixtures" / "douyin_identity_challenge.html"
CHALLENGE_URL = "https://creator.douyin.com/"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The post-click waits are real seconds in production. Not here."""
    monkeypatch.setenv("BROWSER_LOGIN_SMS_SETTLE_S", "0")
    monkeypatch.setenv("BROWSER_LOGIN_CHALLENGE_PROGRESS_POLL_S", "0")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def page() -> FakePage:
    return FakePage(FIXTURE.read_text(encoding="utf-8"), url=CHALLENGE_URL)


def session_over(page: FakePage) -> LoginSession:
    driver = LoginDriver(LOGIN_SPEC, None, None, None, page)
    return LoginSession("sess-1", LOGIN_SPEC, driver, "data:image/png;base64,QR0")


async def stall(page: FakePage):
    """Poll a page that never moves until the flow gives up."""
    session = session_over(page)
    snapshot = None
    for _ in range(MAX_IDENTITY_CHALLENGE_POLLS):
        snapshot = await session.poll_status()
    assert snapshot is not None
    return snapshot


# --- the diagnostics ---------------------------------------------------------


async def test_a_stall_records_the_page_it_stalled_on(page):
    """The headline: `detail` answers "what was the platform showing".

    Without this the failure is a reason and a sentence, and the next round of
    this bug is another round of guessing.
    """
    snapshot = await stall(page)

    assert snapshot.status is SessionStatus.FAILED
    assert snapshot.detail["reason"] == IDENTITY_CHALLENGE_STALLED
    evidence = snapshot.detail[PAGE_EVIDENCE_KEY]

    assert evidence["url"] == CHALLENGE_URL
    # The screen's own words. This is what separates "still the chooser" from
    # "a different screen our captions happen to match" — hypotheses 1 and 2.
    assert "身份验证" in evidence["visible_text"]
    assert "接收短信验证码" in evidence["visible_text"]


async def test_the_controls_on_the_page_are_listed(page):
    """Every input and button, visible or not.

    "There was a code field but no send-code button" and "there was neither"
    are different findings with different fixes, and they were indistinguishable
    from the old failure record.
    """
    evidence = (await stall(page)).detail[PAGE_EVIDENCE_KEY]

    placeholders = {item.get("placeholder") for item in evidence["inputs"]["items"]}
    assert "请输入验证码" in placeholders
    assert evidence["inputs"]["total"] == 2

    captions = {item.get("text", "") for item in evidence["buttons"]["items"]}
    assert "确认" in captions
    # The cards are `role="button"` divs, not `<button>`s — so a diagnostic that
    # only looked for real buttons would report this screen as having one
    # control on it. Both groups are enumerated for that reason.
    assert any("接收短信验证码" in text for text in captions)


async def test_the_clicked_caption_is_described(page):
    """Did our caption resolve at all, and to how many nodes?

    `matches: 0` would mean the caption is stale and the click never had a
    target; `matches: 1, visible: True` (this fixture) rules that out and points
    at everything downstream of the click instead.
    """
    evidence = (await stall(page)).detail[PAGE_EVIDENCE_KEY]

    target = next(
        t for t in evidence["click_targets"] if t["caption"] == "接收短信验证码"
    )
    assert target["matches"] == 1
    assert target["visible"] is True
    # This shim cannot run JavaScript, so the DOM-shape half of the probe is
    # absent — and says so. "We never looked" must never read as "nothing was
    # covering it"; the shape itself is exercised in
    # `test_douyin_identity_challenge.py`, where the page can answer.
    assert target["shape_error"] == "evaluate_unavailable"


async def test_the_bookkeeping_says_what_we_did_to_the_page(page):
    """The other half of a readable failure: not the screen, our actions.

    Lining the two up is the whole diagnostic — "we clicked this, nothing
    moved, and here is the screen that did not move".
    """
    detail = (await stall(page)).detail

    assert detail["identity_option"] == "接收短信验证码"
    assert detail["progress_signals"] == []
    assert detail["escalated_click"] == BUTTON_ANCESTOR_XPATH
    assert detail["options_seen"] == ["接收短信验证码", "发送短信验证"]
    assert detail["polls"] == MAX_IDENTITY_CHALLENGE_POLLS


# --- the clicking ------------------------------------------------------------


async def test_the_caption_is_clicked_and_then_its_button_card_is(page):
    """Two attempts, and the second one is the card, not a wrapper.

    `get_by_text` resolves to the leaf `<span>`; the handler on a page like this
    may sit on the card. The escalation therefore re-clicks the nearest ancestor
    the page itself declares a button — never the bare parent, because a click
    lands at the element's centre and a wrapper spanning both cards would put
    that centre on 发送短信验证, the option an unattended login can never finish.
    """
    await stall(page)

    clicked = [(node.tag, node.attrs.get("role")) for node in page.clicks]
    assert clicked == [("span", None), ("div", "button")]

    # And the card that was clicked is the one holding our caption — not the
    # other one, and not something containing both.
    card = page.clicks[1]
    assert "接收短信验证码" in card.inner_text_value()
    assert "发送短信验证" not in card.inner_text_value()


async def test_the_escalation_happens_once_however_long_the_page_lingers(page):
    """Each click on that card is a request for a real text message."""
    await stall(page)

    assert len(page.clicks) == 2
