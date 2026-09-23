"""OpenAI Images API (gpt-image-2.5) — the API-key path.

Same binary the retired server-side `codex` subscription protocol drove, with
`--provider openai`. Unlike the subscription path the API honours --size and the quality tiers `xhigh` / `max`, and the
image model is chosen by us (`actual_model` → `--model`). Billing is per token
on the operator's OpenAI account (the catalog row's api_key), which is why the
seeded display names carry "(OpenAI API)".

★ Provenance of the "honours exact sizes and xhigh/max" claim: OpenAI docs read
2026-09-13 (the gpt-image-2.5-flare / -sunburst model pages) plus the
gpt-image-2-skill 0.7.4 `--quality` enum. NOT yet measured against the live API
— that is Task 7 acceptance. Contrast the subscription path (the server-side
`codex` protocol, retired 2026-09-23; the user-device `codex-local` protocol
still rides it), whose counterpart claim IS measured (2026-09-09: that path
rewrites `xhigh` to `medium` and says nothing). Until the real-stack probe runs, treat this paragraph as a
documented expectation rather than an observation.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from loguru import logger

from app.services.ai.provider_protocols.base import (
    ALL_RATIOS,
    IMAGE_25_QUALITY_TIERS,
    ProviderCapabilities,
    ProviderNotConfiguredError,
    ProviderProtocol,
)
from app.services.media.parsers.video_providers.base import (
    BaseImageProvider,
    ImageGenResult,
    TaskStatus,
)

if TYPE_CHECKING:
    from app.services.media.parsers.video_providers.codex_cli import (
        CodexCliProvider,
    )


class _CodexImageAdapter(BaseImageProvider):
    """Adapts CodexCliProvider onto the BaseImageProvider ``generate`` contract.

    ``generate`` returns an ``ImageGenResult`` whose ``image_path`` is the local
    PNG the CLI produced (``image_url`` stays empty — there is no URL). The
    downstream persist step ingests the local file via ``source_path``.

    ``provider_name`` is what the result CALLS itself. It is required rather
    than defaulted: ``_stamp_provider_key`` overwrites ``provider_key``
    downstream, but the result's own ``provider`` string is read on its way
    there, so it must be the protocol key that built the adapter.

    (Moved here from the retired server-side ``codex`` protocol module,
    2026-09-23 — the subscription-session path is gone, this API-key path is
    the adapter's only server-side user.)
    """

    def __init__(self, provider: "CodexCliProvider", provider_name: str) -> None:
        self._provider = provider
        self._provider_name = provider_name

    async def generate(self, prompt: str, model: str, **kwargs) -> ImageGenResult:
        # Preferred channel: LOCAL reference paths the workflow already
        # materialized (multi-ref, IC 图1/图2 semantics). Fallback: a single
        # reference_image_url that happens to be a local file; remote urls
        # degrade to plain generate — loudly, not silently.
        paths = [
            str(p)
            for p in (kwargs.get("reference_image_paths") or [])
            if p and os.path.isfile(str(p))
        ]
        if not paths:
            ref = kwargs.get("reference_image_url") or None
            if ref and os.path.isfile(str(ref)):
                paths = [str(ref)]
            elif ref:
                logger.warning(
                    "[{}] reference image {} is not a local file — "
                    "generating without it",
                    self._provider_name,
                    str(ref)[:200],
                )
        result = await self._provider.generate_image(
            prompt=prompt,
            aspect=kwargs.get("aspect_ratio") or "",
            model_version=model or None,
            quality=kwargs.get("quality") or None,
            ref_image_paths=paths or None,
            resolution=kwargs.get("resolution") or None,
        )
        return ImageGenResult(
            image_url="",
            image_path=result.local_path,
            provider=self._provider_name,
            model=model or "",
            metadata={"mime": result.mime, **(result.raw or {})},
        )

    async def check_status(self, task_id: str) -> TaskStatus:
        raise NotImplementedError(
            "CodexCliProvider generation is synchronous; check_status is n/a"
        )

    def list_models(self) -> list[str]:
        return []


class OpenAIImagesProtocol(ProviderProtocol):
    key = "openai-images"
    label = "OpenAI Images API (GPT Image 2.5)"
    description = (
        "gpt-image-2-skill CLI over the OpenAI Images API with the row's api_key "
        "(pay-as-you-go). actual_model is the image model: gpt-image-2.5-flare "
        "or gpt-image-2.5-sunburst. Honours exact sizes and xhigh/max quality "
        "(per docs 2026-09-13; not yet measured on the live API)."
    )
    model_types = ("image",)
    credential_kind = "api_key"
    aliases = ()
    generation_family = "openai-images"
    supports_http_image_probe = False  # shells out to the CLI
    capabilities = ProviderCapabilities(
        ratios=ALL_RATIOS,
        quality=True,
        quality_tiers=IMAGE_25_QUALITY_TIERS,
        resolution=True,
        max_refs=9,
        negative=False,
        video_modes=frozenset(),
        honours_ratio="native",
    )

    def build_image_provider(self, row: dict[str, Any]) -> Any:
        from app.services.media.parsers.video_providers.codex_cli import (
            CodexCliProvider,
        )

        key = (row.get("api_key") or "").strip()
        actual_model = row.get("actual_model") or ""
        if not key:
            # A typed refusal at build time beats the CLI's `not_logged_in`
            # (which would read as a codex session problem, not a missing key).
            #
            # `ProviderNotConfiguredError`, NOT `ProtocolCapabilityError`: the
            # latter says "this protocol cannot do images", which is both
            # false and unactionable. The row is fine and the fix is one paste
            # by the person who just enabled it, so the refusal names the row
            # and the place — and carries it as `detail`, the field the
            # failure record shows the operator.
            raise ProviderNotConfiguredError(
                self.key,
                actual_model,
                detail=(f"{self.key} row has no api_key; set it in Admin > AI Models"),
            )
        return (
            _CodexImageAdapter(
                CodexCliProvider(provider_kind="openai", api_key=key),
                provider_name=self.key,
            ),
            actual_model,
        )
