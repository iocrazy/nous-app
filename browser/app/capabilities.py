"""What each platform's publisher can actually produce. The bottom of the
capability chain, and therefore its single source of truth.

Two properties of this file are load-bearing; both are enforced by
``backend/tests/test_capability_matches_browser.py``.

**1. It has no imports.** Not "few" - none. The backend guard loads this file
by path with ``importlib.util.spec_from_file_location``, from a venv that has
none of this service's dependencies installed (no playwright, no pydantic, not
even ``app`` on ``sys.path``). One ``from .schemas import ...`` and that guard
stops being able to read the truth it is guarding. It also must stay inside the
syntax both interpreters accept: browser runs Python 3.12 (pinned by the
upstream playwright image), the backend venv that loads it runs 3.13.

**2. Content types are declared per platform, not globally.** This table is what
``publish.py::supported_content_types_for`` answers from, and it supersedes that
module's ``SUPPORTED_CONTENT_TYPES`` as the capability declaration. (That tuple
still exists, reduced to the answer for callers that name no platform at all -
see its comment.) A global tuple as *the* declaration means the day Douyin learns
image posts, *every* platform with a registered publisher starts passing the
``validate_intent`` gate for galleries -
including ones that have not written a single line of gallery code. The gate
would then be answering "does anybody support this?" while the caller is asking
"does THIS account's platform support this?".

Adding a content type here is a claim that the platform's publisher can drive
it end to end. The backend profile
(``backend/app/services/distribution/session_adapter.py``) may only declare a
subset of what this file declares; the reverse - browser ahead of backend - is
allowed, because "implemented but not yet offered" fails safe while "offered but
not implemented" is the failure this whole mechanism exists to stop (a user
filled in a whole form, queued, and got ``unsupported_content_type`` at the very
last step).
"""

# platform -> the content types its publisher can actually drive.
#
# Douyin is ``("video",)`` because ``douyin_publish.py`` uploads exactly one
# file (``job.assets[VIDEO_ROLE]``). Image posts are wanted and the neutral
# layer is being built for them, but this line is a capability statement, not a
# roadmap: it flips to ``("video", "images")`` in the same PR that makes the
# gallery upload real (spec T7), and the backend guard makes "the same PR"
# mechanical rather than a comment someone has to obey.
PLATFORM_CONTENT_TYPES = {
    "douyin": ("video",),
}


def content_types_for(platform):
    """The content types ``platform`` can publish. Unknown platform -> empty.

    Empty rather than a permissive default on purpose: a platform nobody
    declared has, by definition, no publisher that was written for it, so the
    only honest answer is "nothing". ``validate_intent`` turns that into a typed
    refusal at the front of the pipeline.
    """
    return PLATFORM_CONTENT_TYPES.get(platform, ())


def supports_content_type(platform, content_type):
    """Can ``platform`` publish ``content_type`` today?"""
    return content_type in content_types_for(platform)
