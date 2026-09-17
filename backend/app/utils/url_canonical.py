"""Canonical form of a share URL, for dedup keys only.

Why this exists
---------------
The "you already have this in your library" probe (L2, in
``api/media_fetch_helpers.handle_media_fetch_dispatch``) compared
``parsed_media.original_url`` for **exact string equality**. Share links carry
analytics parameters that change every time the same video is opened from a
different surface, so the same content produced different keys:

* 2026-09-08  ``…/BV1nUtj6QEuS/?spm_id_from=333.1007.tianma.1-2-2.click&vd_source=797…``
* 2026-09-15  ``…/BV1nUtj6QEuS/?spm_id_from=333.1391.0.0&vd_source=797…``

L2 read that as "never downloaded", ran a full parse, and dispatched a
download task that the in-workflow cache check then short-circuited — the user
saw a Download card for a video they had owned for a week. The same equality
test also gates the points pre-charge, so a repeat submit read as a first
parse there too.

Scope of this module
--------------------
The canonical form is a **dedup key**, never the URL we fetch. Downloads keep
using ``original_url`` verbatim (tokens in the query can be load-bearing for
the fetch even when they are meaningless for identity).

Design
------
A *denylist* of tracking parameters, not a keep-list. Identity for the
platforms we support lives either in the path (bilibili ``/video/BV…``, douyin
``/video/712…``, xiaohongshu ``/explore/…``) or in a small set of well-known
query keys (YouTube's ``v`` and ``list``). A denylist can only ever make two
URLs for the *same* item compare equal; it cannot merge two different items
unless a genuinely identity-bearing key is added to it by mistake — so keep
identity keys out of ``_TRACKING_PARAMS`` and prefer leaving an unknown
parameter in place.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Analytics / referral / session parameters. None of these identify content.
# Grouped by where we first saw them; the set is applied to every host.
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        # generic web analytics
        "gclid",
        "fbclid",
        "msclkid",
        "ref",
        "ref_src",
        "refer",
        "referrer",
        "referer",
        # bilibili
        "spm_id_from",
        "spmid",
        "vd_source",
        "from_spmid",
        "from_source",
        "from",
        "seid",
        "buvid",
        "bbid",
        "ts",
        "timestamp",
        "unique_k",
        "is_story_h5",
        # NB: "mid" is deliberately absent — on bilibili space URLs it is the
        # author id, i.e. identity-bearing. Tracking-looking names are not
        # enough; a key only goes in this set when it cannot select content.
        "plat_id",
        "up_id",
        "msource",
        "refer_from",
        "broadcast_type",
        "launch_id",
        "session_id",
        "visit_id",
        "trackid",
        "hotrank",
        "share_source",
        "share_medium",
        "share_plat",
        "share_session_id",
        "share_tag",
        "share_times",
        "share_from",
        # douyin / tiktok
        "previous_page",
        "enter_from",
        "enter_method",
        "region",
        "u_code",
        "did",
        "iid",
        "with_sec_did",
        "video_share_track_ver",
        "titletype",
        "share_sign",
        "share_version",
        "from_aid",
        "from_ssr",
        "extra_params",
        "_r",
        "is_copy_url",
        "is_from_webapp",
        "sender_device",
        # xiaohongshu
        "xhsshare",
        "appuid",
        "apptime",
        "share_id",
        "xsec_source",
        "xsec_token",
        # youtube
        "si",
        "feature",
        "pp",
        "t",
        "start_radio",
        "ab_channel",
        # twitter / x
        "s",
    }
)

# Whole families dropped by prefix.
_TRACKING_PREFIXES: tuple[str, ...] = ("utm_",)


def _is_tracking(key: str) -> bool:
    lowered = key.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PREFIXES)


def canonical_url(url: str) -> str:
    """Return the dedup key for ``url``.

    Lowercases scheme and host, drops a leading ``www.``, drops the fragment,
    removes tracking parameters, sorts what remains, and strips a trailing
    slash from the path. Input that cannot be parsed comes back unchanged —
    a canonical form is an optimisation, and refusing to produce one must
    degrade to "no dedup hit", never to an exception at the API edge.

    >>> canonical_url("https://www.bilibili.com/video/BV1x/?spm_id_from=1&p=2")
    'https://bilibili.com/video/BV1x?p=2'
    >>> canonical_url("https://www.youtube.com/watch?v=abc&si=xyz")
    'https://youtube.com/watch?v=abc'
    """
    if not url or not url.strip():
        return ""
    try:
        parts = urlsplit(url.strip())
        if not parts.scheme or not parts.netloc:
            return url.strip()

        host = (parts.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        netloc = host
        if parts.port and parts.port not in (80, 443):
            netloc = f"{host}:{parts.port}"

        path = parts.path
        if len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")

        kept = [
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _is_tracking(k)
        ]
        query = urlencode(sorted(kept), doseq=False)

        return urlunsplit((parts.scheme.lower(), netloc, path, query, ""))
    except Exception:
        return url.strip()


def parsed_media_url_predicate(url: str):
    """SQLAlchemy predicate: "this ``parsed_media`` row is about ``url``".

    Every reader goes through here so the two-sided match stays one
    implementation:

    * ``original_url == url`` — the legacy exact match. It is the ONLY thing
      that works for rows written before migration 471's backfill, where
      ``canonical_url`` is still NULL, so a NULL degrades to the old behaviour
      rather than to a miss.
    * ``canonical_url == canonical_url(url)`` — the tracking-parameter-proof
      key, added only when there IS one. Comparing against an empty key would
      match every row whose URL was unparseable, i.e. a wildcard.
    """
    from sqlalchemy import or_

    from app.models import ParsedMedia

    key = canonical_url(url)
    exact = ParsedMedia.original_url == url
    if not key:
        return exact
    return or_(exact, ParsedMedia.canonical_url == key)


__all__ = ["canonical_url", "parsed_media_url_predicate"]
