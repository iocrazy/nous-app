"""How long one publish may take. **The single source both services derive from.**

Dependency-free on purpose, exactly like `capabilities.py` and for the same
reason: `nous-backend` loads this file *by path* from its own venv (different
Python, disjoint dependencies) so that a contract test can hold its transport
timeout against the number this service actually enforces. One
`from .config import ...` here and that guard silently stops guarding.

--- why a module at all -----------------------------------------------------

The two services each had a hard-coded magic number, and they were **inverted**:
`nous-backend` gave up on the HTTP read after 900s while this service kept
driving the browser until 1320s. Nothing reported that. The visible symptom is
the worst kind — a publish that is still running is recorded as failed, and
whether the post went out is decided by a race the user never sees.

Fixing the two numbers would have left the same shape: the next person to raise
one side re-creates the inversion. So the ceiling is *computed*, from here, by
both sides, and `backend/tests/test_publish_budget_matches_browser.py` fails if
the ordering is ever violated again.

--- the arithmetic ----------------------------------------------------------

    browser hard ceiling = work budget + SMS window + slack
    backend read timeout = browser hard ceiling + margin

**The SMS window is human time and is deliberately a separate term.** A publish
that stops to ask for a verification code is not "a slow publish": the machine
is idle and a person is reading a text message. Folding that wait into the work
budget would silently shorten every step *after* the challenge, so supplying a
code correctly could still lose the post to a deadline that had been quietly
spent waiting for the human. The window is granted on top, and
`Deadline.extend` is what hands it back to the publish that paid it.

The margin exists so the *browser* is always the side that decides a publish is
over. When the backend times out first it gets a transport failure — "we do not
know whether it published" — and the refreshed `storage_state` is forfeited.
When the browser times out first it returns a typed answer with the cookies
attached. Same failure, one is diagnosable.
"""

from __future__ import annotations

import os

# Env var names. Both services read *these*, so a deployment sets one value and
# the derived numbers stay consistent on both sides.
ENV_WORK_BUDGET = "BROWSER_PUBLISH_TOTAL_TIMEOUT_S"
ENV_SMS_WAIT = "BROWSER_PUBLISH_SMS_WAIT_S"

# Wall-clock for the machine's own work: launch, upload, form, confirm.
DEFAULT_WORK_BUDGET_S = 1_200
MIN_WORK_BUDGET_S = 60

# How long a publish may stand still waiting for a person to supply a
# verification code. 180s: receiving an SMS and typing it is 30-90s in practice,
# so this is roughly double the realistic case — enough that a slow carrier does
# not lose a post, short enough that an abandoned publish gives its browser slot
# back within minutes. It is a ceiling on *one publish*, shared by every retry
# inside it (see `publish_sms.SmsWindow`), not a per-attempt allowance.
DEFAULT_SMS_WAIT_S = 180
# 0 is legal and means "do not wait": the challenge fails immediately, which is
# the pre-existing behaviour and the way to switch this whole channel off.
MIN_SMS_WAIT_S = 0

# Covers a single Playwright call wedging below the cooperative deadline's
# granularity. Firing it forfeits the storage_state write-back, so it sits well
# above the real budget rather than near it.
HARD_SLACK_S = 120

# Keeps the browser as the side that decides a publish is over (see above).
BACKEND_MARGIN_S = 60


def _int_env(env: "dict[str, str] | None", name: str, fallback: int, floor: int) -> int:
    source = os.environ if env is None else env
    raw = source.get(name)
    if raw is None or str(raw).strip() == "":
        return fallback
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return fallback
    return max(floor, value)


def work_budget_s(env: "dict[str, str] | None" = None) -> int:
    """Wall-clock the machine gets for its own work. Excludes human waits."""
    return _int_env(env, ENV_WORK_BUDGET, DEFAULT_WORK_BUDGET_S, MIN_WORK_BUDGET_S)


def sms_wait_s(env: "dict[str, str] | None" = None) -> int:
    """Wall-clock a publish may spend waiting for a person to supply a code."""
    return _int_env(env, ENV_SMS_WAIT, DEFAULT_SMS_WAIT_S, MIN_SMS_WAIT_S)


def browser_hard_ceiling_s(env: "dict[str, str] | None" = None) -> int:
    """The absolute cap this service enforces on `POST /session/publish`."""
    return work_budget_s(env) + sms_wait_s(env) + HARD_SLACK_S


def backend_read_timeout_s(env: "dict[str, str] | None" = None) -> float:
    """What `nous-backend` must use as its HTTP read timeout for a publish.

    Strictly greater than `browser_hard_ceiling_s` by construction — that
    ordering is the whole point of the module and is pinned by a test rather
    than by this sentence.
    """
    return float(browser_hard_ceiling_s(env) + BACKEND_MARGIN_S)
