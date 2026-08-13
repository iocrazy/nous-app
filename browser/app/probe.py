"""Typed-input reconnaissance: what does the page *fetch* while someone types?

Why a second recon module
=========================
`inspect.py` (T0) answers questions about a page as it sits still. A whole
class of platform behaviour is invisible to it, because it only exists as a
consequence of keystrokes: the Douyin description box pops a `#` topic
dropdown as you type, each row carrying an official topic entity and its
cumulative play count. Where that list comes from — an XHR, a stream, or data
the bundle already had — decides whether we can offer the same experience from
our own composer, and no amount of *static* counting can tell us.

So this module does exactly one thing T0 cannot: it puts a probe string into
one editable node and records the network the page produced as a result. It
then reports **shapes**: which hosts were hit, which query parameters exist
(by name), which of those look like a signature, and a bounded, key-redacted
excerpt of each response body.

What it deliberately cannot do
==============================
The boundary is the same one T0 drew, moved by exactly one step — from "reads
only" to "reads, and may put text into a caller-named editable node". Nothing
else moved:

* **No control is ever activated.** There is no path here that presses a
  button, confirms a dialog, or hands anything to a platform.
  `tests/test_probe_units.py` reads this file and fails if a word from that
  vocabulary appears anywhere in it, comments included — the same mechanism
  T0 uses, with `focus` / `press_sequentially` added to the allowed set and
  nothing subtracted from the forbidden one.
* **The URL allow-list is `inspect.url_refusal`, imported, not restated.**
  An endpoint holding a live account's cookies that opens an arbitrary URL is
  a credentialed SSRF; a second copy of that check is a second thing to
  forget.
* **Handing files to a file input is `inspect.seed_file_input`, imported.**
  The Douyin description box only exists after an upload, so seeding is
  required to reach it — but on the recon path the Playwright call that
  performs it stays spelled out in exactly one place, which is what makes
  T0's "appears once" guard mean anything. (The posting flow has its own,
  as it must; the point is that recon does not grow a second one.)

What leaves this module
=======================
Two different things, and the difference matters:

* The **report** is redacted: query values whose parameter name looks
  credential-shaped are masked to their length, response bodies are walked and
  values under credential-shaped keys replaced, request headers are reported
  **by name only** (never a value — that is where `cookie` lives), and
  everything is truncated.
* `replay_targets` carries **raw URLs**, and is the one field here that does
  not. It exists because the decisive question — "can this be called without a
  browser?" — can only be answered by calling it, and the caller that does so
  is the backend, which already holds this account's plaintext session. It is
  internal transport, in the same class as `updated_storage_state`: the
  backend consumes it and it never reaches an HTTP response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from .assets import AssetError, stage_assets
from .browser_runtime import (
    ProxyConfigError,
    apply_stealth,
    build_context_kwargs,
    build_launch_kwargs,
)
from .config import get_settings
from .dom import visible_marker_texts
from .inspect import (
    KIND_TO_STATUS,
    InspectSpec,
    seed_file_input,
    session_refusal,
)
from .redaction import scrub, scrub_page_text
from .schemas import (
    CapturedCall,
    CapturedParam,
    ObservedNode,
    ObservedSelector,
    ProbeRequest,
    ProbeResponse,
    ReplayTarget,
    SessionStatus,
)
from .validation import ProbeKind, classify_playwright_error, storage_state_is_empty

logger = logging.getLogger("nous_browser.probe")

SEED_ROLE_PREFIX = "seed"

# How many distinct hosts appear in the traffic summary. The summary is a
# histogram, not a log: it says "the page talked to these places this often",
# which is enough to notice a suggestion service living on its own domain.
MAX_SUMMARY_HOSTS = 40

# Ceiling on nodes reported from inside the edited box, and on how many matches
# of one observation selector get their text read.
MAX_OBSERVED_NODES = 40
MAX_OBSERVED_MATCHES = 12

# Parameter names whose *value* is never reported. Substring match on the
# lowered name, and deliberately generous: over-masking costs a diagnostic,
# under-masking copies a credential into a terminal buffer. None of the
# keyword-carrying names on any platform we have seen (`keyword`, `query`,
# `search_word`, `word`) collide with these.
SENSITIVE_PARAM_HINTS = (
    "token",
    "sign",
    "bogus",
    "secret",
    "auth",
    "ticket",
    "session",
    "cookie",
    "verifyfp",
    "s_v_web_id",
    "sec_",
    "passport",
    "webid",
    "_rticket",
    "device_id",
    "iid",
)

# The finding, as opposed to the redaction: these are the names that mean "this
# request is signed, and reproducing it outside a browser needs the signer".
# Reported by name; their values are masked by the rule above.
SIGNATURE_PARAM_HINTS = (
    "bogus",
    "signature",
    "mstoken",
    "verifyfp",
    "s_v_web_id",
    "_rticket",
)

# Keys under which a JSON response body's value is replaced before the excerpt
# is taken. Same reasoning as `SENSITIVE_PARAM_HINTS`; numbers survive, which
# matters because a play count is exactly what we came to read.
SENSITIVE_BODY_KEY_HINTS = (
    "token",
    "cookie",
    "session",
    "secret",
    "signature",
    "auth",
    "password",
    "ticket",
    "credential",
    "sec_uid",
    "sec_user_id",
)

# Names a platform is likely to hang the typed word on. Used only to *label*
# which parameter carried our probe text; the real detection is value matching,
# so a platform that invents a new name is still handled.
KEYWORD_PARAM_HINTS = ("keyword", "query", "word", "search", "q", "text", "prefix")


# --- pure redaction helpers -------------------------------------------------


def _hits(name: str, hints: Sequence[str]) -> bool:
    lowered = (name or "").lower()
    return any(hint in lowered for hint in hints)


def redact_param(name: str, value: str) -> CapturedParam:
    """One query parameter, safe to report. Pure.

    A masked value still carries its length, because "this request has a
    172-character signature parameter" is a finding and "this request has one"
    is a weaker one.
    """
    raw = value or ""
    if _hits(name, SENSITIVE_PARAM_HINTS):
        return CapturedParam(name=name, value="***", redacted=True, length=len(raw))
    return CapturedParam(
        name=name,
        value=scrub_page_text(raw, max_len=200),
        redacted=False,
        length=len(raw),
    )


def signature_params(names: Iterable[str]) -> list[str]:
    """Which of these parameter names mean "signed request". Pure."""
    return [name for name in names if _hits(name, SIGNATURE_PARAM_HINTS)]


def redact_json(value: Any, *, depth: int = 0) -> Any:
    """Replace values under credential-shaped keys, keep everything else. Pure.

    Depth-bounded so a self-referential structure cannot spin, and applied
    before serialisation so the excerpt that gets truncated is already safe —
    truncating first and redacting after would leave a half-copied secret at
    the tail.
    """
    if depth > 12:
        return "…"
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _hits(name, SENSITIVE_BODY_KEY_HINTS):
                out[name] = "***"
            else:
                out[name] = redact_json(item, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact_json(item, depth=depth + 1) for item in value[:50]]
    return value


def body_excerpt(raw: str, *, limit: int) -> tuple[str, list[str]]:
    """`(excerpt, top_level_keys)` for one response body. Pure.

    JSON gets key-redaction and keeps its numbers, because the numbers are the
    point: a play count is data about a public topic, not about the account.
    Anything we cannot parse falls back to the page-text scrubber, which masks
    long digit runs — for an unstructured body we cannot tell a play count from
    a phone number, and the safe reading of an unknown blob is the strict one.
    """
    if not raw:
        return "", []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return scrub_page_text(raw, max_len=limit), []
    safe = redact_json(parsed)
    keys = sorted(str(k) for k in parsed) if isinstance(parsed, Mapping) else []
    try:
        rendered = json.dumps(safe, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        rendered = str(safe)
    if len(rendered) > limit:
        rendered = rendered[: max(limit - 1, 0)] + "…"
    return rendered, keys


def find_keyword_param(query: Sequence[tuple[str, str]], probe_text: str) -> str | None:
    """Which parameter carried the text we typed. Pure.

    Value-first, name-second. A platform that calls it `search_word` this
    quarter and `q` the next is still handled, and a name-only guess would
    happily point at a parameter that has nothing to do with the query.
    """
    needle = (probe_text or "").strip().lstrip("#").strip()
    if not needle:
        return None
    for name, value in query:
        if needle and needle in (value or ""):
            return name
    for name, _ in query:
        if _hits(name, KEYWORD_PARAM_HINTS):
            return name
    return None


def url_matches(url: str, needles: Sequence[str]) -> bool:
    """Should this response get the detailed treatment. Pure.

    An empty filter list means "report everything that was recorded", which is
    the right default for a first look at an unknown page: you cannot filter
    for a path you have never seen.
    """
    if not needles:
        return True
    lowered = (url or "").lower()
    return any(needle.lower() in lowered for needle in needles if needle)


# --- read-only page scripts -------------------------------------------------
#
# Constant, and they only read. The node script never returns an input's
# `value`; it reads `textContent` of a box we ourselves just put text into,
# which is the one case where the content is known not to be the owner's.

_NODE_STATE_JS = """
(el, limit) => {
  const kids = [];
  const all = el.querySelectorAll('*');
  for (const node of all) {
    if (kids.length >= limit) break;
    const names = [];
    for (const a of node.attributes) names.push(a.name);
    kids.push({
      tag: node.tagName.toLowerCase(),
      class_name: (node.getAttribute('class') || '').slice(0, 160),
      attr_names: names.slice(0, 24),
      text: ((node.textContent) || '').slice(0, 120)
    });
  }
  return {
    text: ((el.innerText || el.textContent) || '').slice(0, 2000),
    child_total: all.length,
    children: kids
  };
}
"""

_SELECTOR_TEXT_JS = """
([selector, limit]) => {
  const nodes = document.querySelectorAll(selector);
  const out = [];
  for (const n of nodes) {
    if (out.length >= limit) break;
    const rect = n.getBoundingClientRect();
    out.push({
      visible: !!(rect.width || rect.height),
      class_name: (n.getAttribute('class') || '').slice(0, 160),
      text: ((n.innerText || n.textContent) || '').slice(0, 1200)
    });
  }
  return {total: nodes.length, items: out};
}
"""

_USER_AGENT_JS = "() => navigator.userAgent"


# --- capture ----------------------------------------------------------------


class _Recorder:
    """Collects responses off the context, in memory, bounded.

    Two bounds with different jobs. `hosts` counts **everything** from the
    moment the context exists, because "the page never talked to anyone new"
    is itself an answer. Detailed records are only kept while `recording` is
    on — which is switched on immediately before the keystrokes — so the
    quota is spent on traffic our typing caused rather than on page load.
    """

    def __init__(self, *, filters: Sequence[str], limit: int) -> None:
        self._filters = list(filters)
        self._limit = limit
        self.hosts: dict[str, int] = {}
        self.records: list[dict[str, Any]] = []
        self.recording = False
        self.seen_while_recording = 0
        self._pending: set[asyncio.Task] = set()

    def attach(self, context: Any) -> None:
        context.on("response", self._schedule)

    def _schedule(self, response: Any) -> None:
        task = asyncio.ensure_future(self._absorb(response))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def settle(self) -> None:
        """Let in-flight body reads finish before the context goes away."""
        if not self._pending:
            return
        await asyncio.gather(*list(self._pending), return_exceptions=True)

    async def _absorb(self, response: Any) -> None:
        try:
            request = response.request
            url = str(request.url or "")
            host = (urlsplit(url).hostname or "").lower()
            if host:
                if host in self.hosts or len(self.hosts) < MAX_SUMMARY_HOSTS:
                    self.hosts[host] = self.hosts.get(host, 0) + 1
            if not self.recording:
                return
            self.seen_while_recording += 1
            if len(self.records) >= self._limit or not url_matches(url, self._filters):
                return
            try:
                headers = await request.all_headers()
            except Exception:  # noqa: BLE001 - a header read is not the finding
                headers = {}
            try:
                body = await response.text()
            except Exception:  # noqa: BLE001 - streamed / binary / already gone
                body = ""
            self.records.append(
                {
                    "method": str(request.method or ""),
                    "url": url,
                    "status": int(response.status),
                    "resource_type": str(getattr(request, "resource_type", "") or ""),
                    "header_names": sorted(str(k).lower() for k in headers),
                    "post_data": _post_data_keys(request),
                    "content_type": str(headers.get("content-type") or ""),
                    "response_content_type": _content_type(response),
                    "body": body,
                }
            )
        except Exception:  # noqa: BLE001 - recon must never break the run
            return


def _content_type(response: Any) -> str:
    try:
        return str(response.headers.get("content-type") or "")
    except Exception:  # noqa: BLE001
        return ""


def _post_data_keys(request: Any) -> list[str]:
    """Top-level keys of a request body, never its values.

    A body is as likely to hold a token as a query string is, and we do not
    need its contents to answer "what does this call take" — the key names are
    the shape, and the shape is the question.
    """
    try:
        raw = request.post_data
    except Exception:  # noqa: BLE001
        return []
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, Mapping):
            return sorted(str(k) for k in parsed)[:40]
    except (ValueError, TypeError):
        pass
    try:
        return sorted({name for name, _ in parse_qsl(raw, keep_blank_values=True)})[:40]
    except Exception:  # noqa: BLE001
        return []


def build_capture(record: Mapping[str, Any], *, probe_text: str, limit: int) -> tuple[
    CapturedCall, ReplayTarget | None
]:
    """One recorded response → what we report, and what we may replay. Pure."""
    url = str(record.get("url") or "")
    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    names = [name for name, _ in query]
    keyword = find_keyword_param(query, probe_text)
    excerpt, top_keys = body_excerpt(str(record.get("body") or ""), limit=limit)
    call = CapturedCall(
        method=str(record.get("method") or ""),
        host=(parts.hostname or "").lower(),
        path=parts.path or "/",
        query_param_names=names,
        query_params=[redact_param(name, value) for name, value in query],
        signature_params=signature_params(names),
        keyword_param=keyword,
        request_header_names=list(record.get("header_names") or []),
        post_data_param_names=list(record.get("post_data") or []),
        status=int(record.get("status") or 0),
        response_content_type=str(record.get("response_content_type") or ""),
        resource_type=str(record.get("resource_type") or ""),
        body_chars=len(str(record.get("body") or "")),
        body_excerpt=excerpt,
        body_json_top_keys=top_keys,
    )
    # Only a call that carried our probe text is worth replaying: it is the one
    # whose answer changes when the word changes, which is the whole experiment.
    if keyword is None:
        return call, None
    target = ReplayTarget(
        method=call.method or "GET",
        url=url,
        referer=str(record.get("referer") or ""),
        keyword_param=keyword,
        keyword_value=dict(query).get(keyword, ""),
        header_names=call.request_header_names,
    )
    return call, target


# --- driver -----------------------------------------------------------------


def _failure(kind: ProbeKind, message: str, detail: dict[str, Any]) -> ProbeResponse:
    body = dict(detail)
    body.setdefault("reason", kind.value)
    return ProbeResponse(
        success=False,
        status=KIND_TO_STATUS[kind],
        message=message,
        detail=body,
    )


async def _reach_target(page: Any, request: ProbeRequest) -> tuple[Any, str]:
    """The first of the caller's selectors that becomes visible.

    A list rather than one selector because the Douyin editor ships in two
    parallel gray releases whose description boxes carry different attributes
    — a single selector would make a recon run a coin toss.
    """
    settings = get_settings()
    total = max(len(request.target_selectors), 1)
    slice_ms = max(int(request.target_wait_ms / total), 1_000)
    last: Exception | None = None
    for selector in request.target_selectors:
        try:
            locator = page.locator(selector).nth(request.target_index)
            await locator.wait_for(state="visible", timeout=slice_ms)
            return locator, selector
        except Exception as exc:  # noqa: BLE001 - try the next candidate
            last = exc
            continue
    raise RuntimeError(
        "none of the target selectors became visible: "
        + ", ".join(request.target_selectors)
        + (f" (last error: {type(last).__name__})" if last else "")
    ) from last


async def _read_node(locator: Any) -> tuple[str, list[ObservedNode], int]:
    try:
        state = await locator.evaluate(_NODE_STATE_JS, MAX_OBSERVED_NODES)
    except Exception:  # noqa: BLE001
        return "", [], 0
    if not isinstance(state, Mapping):
        return "", [], 0
    nodes = [
        ObservedNode(
            tag=str(item.get("tag") or ""),
            class_name=scrub_page_text(str(item.get("class_name") or ""), max_len=160),
            attr_names=[str(n) for n in (item.get("attr_names") or [])][:24],
            text=scrub_page_text(str(item.get("text") or ""), max_len=120),
        )
        for item in (state.get("children") or [])
        if isinstance(item, Mapping)
    ]
    return (
        scrub_page_text(str(state.get("text") or ""), max_len=2_000),
        nodes,
        int(state.get("child_total") or 0),
    )


async def _read_selectors(page: Any, selectors: Sequence[str]) -> list[ObservedSelector]:
    out: list[ObservedSelector] = []
    for selector in selectors:
        try:
            state = await page.evaluate(
                _SELECTOR_TEXT_JS, [selector, MAX_OBSERVED_MATCHES]
            )
        except Exception:  # noqa: BLE001 - a bad selector is a caller mistake
            out.append(ObservedSelector(selector=selector, error="could not evaluate"))
            continue
        if not isinstance(state, Mapping):
            out.append(ObservedSelector(selector=selector, error="unreadable result"))
            continue
        items = [item for item in (state.get("items") or []) if isinstance(item, Mapping)]
        out.append(
            ObservedSelector(
                selector=selector,
                total=int(state.get("total") or 0),
                visible=sum(1 for item in items if item.get("visible")),
                texts=[
                    scrub_page_text(str(item.get("text") or ""), max_len=1_200)
                    for item in items
                    if item.get("visible")
                ],
                class_names=[
                    scrub_page_text(str(item.get("class_name") or ""), max_len=160)
                    for item in items
                ],
            )
        )
    return out


async def _run_once(spec: InspectSpec, request: ProbeRequest) -> ProbeResponse:
    # patchright, not playwright: drop-in fork covering the CDP-layer leaks.
    # All import sites must agree — `test_patchright_everywhere` enforces it.
    from patchright.async_api import async_playwright

    settings = get_settings()
    proxy_configured = bool(
        request.environment is not None and request.environment.proxy_url
    )

    try:
        launch_kwargs = build_launch_kwargs(request.environment)
    except ProxyConfigError as exc:
        return _failure(
            ProbeKind.PROXY_FAILED,
            scrub(str(exc)),
            {"stage": "proxy_config", "platform": spec.platform},
        )

    context_kwargs = build_context_kwargs(request.environment, request.storage_state)
    started = time.monotonic()
    recorder = _Recorder(
        filters=request.capture_url_contains, limit=request.max_captures
    )

    staged_items = [
        (f"{SEED_ROLE_PREFIX}:{index}", item)
        for index, item in enumerate(request.seed_files)
    ]

    try:
        async with stage_assets(staged_items) as staged:
            paths = [staged[role].path for role, _ in staged_items]
            filenames = [staged[role].filename for role, _ in staged_items]
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**launch_kwargs)
                try:
                    context = await browser.new_context(**context_kwargs)
                    await apply_stealth(context)
                    recorder.attach(context)
                    page = await context.new_page()
                    await page.goto(
                        request.url,
                        wait_until="domcontentloaded",
                        timeout=settings.nav_timeout_ms,
                    )
                    await page.wait_for_timeout(request.settle_ms)

                    visible_login = await visible_marker_texts(
                        page, spec.login_text_markers
                    )
                    lost = session_refusal(page.url, spec.allowed_hosts, visible_login)
                    if lost is not None:
                        return _failure(
                            ProbeKind.INVALID,
                            lost,
                            {
                                "stage": "session",
                                "platform": spec.platform,
                                "url_after": scrub(page.url),
                                "login_markers": list(visible_login),
                            },
                        )

                    if paths:
                        await seed_file_input(
                            page,
                            selector=request.seed_selector,
                            index=request.seed_input_index,
                            wait_ms=request.seed_wait_ms,
                            paths=paths,
                        )

                    locator, used_selector = await _reach_target(page, request)
                    before_text, _, _ = await _read_node(locator)

                    # Everything from here is "caused by the keystrokes", which
                    # is the only traffic worth a detailed record.
                    recorder.recording = True
                    referer = str(page.url)
                    await locator.focus(timeout=settings.inspect_form_timeout_ms)
                    await locator.press_sequentially(
                        request.probe_text,
                        delay=request.keystroke_delay_ms,
                        timeout=settings.inspect_form_timeout_ms,
                    )
                    await page.wait_for_timeout(request.capture_settle_ms)

                    after_text, nodes, child_total = await _read_node(locator)
                    observed = await _read_selectors(page, request.observe_selectors)
                    await recorder.settle()

                    try:
                        user_agent = str(await page.evaluate(_USER_AGENT_JS))
                    except Exception:  # noqa: BLE001
                        user_agent = ""

                    captures: list[CapturedCall] = []
                    targets: list[ReplayTarget] = []
                    for record in recorder.records:
                        call, target = build_capture(
                            {**record, "referer": referer},
                            probe_text=request.probe_text,
                            limit=request.capture_body_chars,
                        )
                        captures.append(call)
                        if target is not None and len(targets) < request.max_replay_targets:
                            targets.append(target)

                    # Harvest the renewed session before the context dies — the
                    # platform rotates cookies on any authenticated load, so
                    # discarding them would make recon a net DRAIN on how long
                    # the account stays bound (same argument as T0).
                    try:
                        fresh = await context.storage_state()
                    except Exception:  # noqa: BLE001
                        fresh = None

                    # The falsifiable part. A run that captured nothing is only
                    # evidence about the platform if the keystrokes actually
                    # landed; without this flag "no requests" and "no typing"
                    # look identical, and we would report the wrong one.
                    landed = _text_landed(before_text, after_text, request.probe_text)

                    return ProbeResponse(
                        success=True,
                        status=SessionStatus.SESSION_VALID,
                        message="typed probe complete",
                        detail={
                            "platform": spec.platform,
                            "stage": "observe",
                            "elapsed_s": round(time.monotonic() - started, 1),
                            "seeded": len(paths),
                            "responses_while_typing": recorder.seen_while_recording,
                        },
                        url_after=scrub(page.url),
                        page_title=scrub(await _title(page), max_len=200),
                        target_selector_used=used_selector,
                        typed_text_landed=landed,
                        target_text_before=before_text,
                        target_text_after=after_text,
                        target_child_total=child_total,
                        target_nodes=nodes,
                        observed_selectors=observed,
                        host_totals=dict(recorder.hosts),
                        captures=captures,
                        captures_dropped=max(
                            recorder.seen_while_recording - len(recorder.records), 0
                        ),
                        replay_targets=targets,
                        replay_user_agent=user_agent,
                        seeded_files=filenames,
                        updated_storage_state=fresh or None,
                    )
                finally:
                    # Explicit teardown on every path: a context left behind
                    # holds live cookies.
                    await recorder.settle()
                    await browser.close()
    except AssetError as exc:
        return _failure(
            ProbeKind.ERROR,
            exc.message,
            {**exc.detail, "stage": "assets", "platform": spec.platform},
        )
    except Exception as exc:  # noqa: BLE001 - every failure becomes a typed status
        raw = f"{type(exc).__name__}: {exc}"
        return _failure(
            classify_playwright_error(raw),
            scrub(raw),
            {
                "stage": "typed_probe",
                "platform": spec.platform,
                "proxy_configured": proxy_configured,
            },
        )


def _text_landed(before: str, after: str, probe_text: str) -> bool:
    """Did our keystrokes actually reach the box. Pure.

    Presence of the probe string is the strong signal; a text that merely grew
    is the weak one, and it is here because some editors normalise what they
    receive (a `#` can become a styled node whose `innerText` differs from what
    was typed). Both beat assuming success, which is how a run that never typed
    anything gets reported as "the platform makes no request".
    """
    needle = (probe_text or "").strip()
    if needle and needle in (after or ""):
        return True
    return len(after or "") > len(before or "")


async def _title(page: Any) -> str:
    try:
        return str(await page.title())
    except Exception:  # noqa: BLE001
        return ""


async def run_probe(spec: InspectSpec, request: ProbeRequest) -> ProbeResponse:
    """Type one probe string into one allow-listed page and report the traffic.

    One attempt, no retries — same reasoning as T0: a human is waiting on the
    answer, re-running is a keystroke, and every attempt is another automated
    visit to a creator console.

    Total by construction: every failure below becomes a typed status, so a
    caller never parses a traceback to learn whether the account is dead or our
    container is.
    """
    if storage_state_is_empty(request.storage_state):
        return ProbeResponse(
            success=False,
            status=SessionStatus.SESSION_INVALID,
            message="storage_state contains no cookies or origins",
            detail={
                "platform": spec.platform,
                "stage": "fail_fast",
                "reason": "empty_storage_state",
            },
        )

    result = await _run_once(spec, request)
    logger.info(
        "[probe] platform=%s status=%s captures=%d landed=%s",
        spec.platform,
        result.status.value,
        len(result.captures),
        result.typed_text_landed,
    )
    return result


__all__ = [
    "KEYWORD_PARAM_HINTS",
    "SENSITIVE_BODY_KEY_HINTS",
    "SENSITIVE_PARAM_HINTS",
    "SIGNATURE_PARAM_HINTS",
    "body_excerpt",
    "build_capture",
    "find_keyword_param",
    "redact_json",
    "redact_param",
    "run_probe",
    "signature_params",
    "url_matches",
]
