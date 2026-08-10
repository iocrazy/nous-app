/**
 * What the publish pipeline can actually do, per platform.
 *
 * This file exists because the same capability used to be claimed in three
 * places that disagreed: the page offered an "Images" tab, the backend profile
 * listed `images` in `content_types`, and the browser service — the only layer
 * that really posts anything — refused it. The user filled in the whole form,
 * submitted, waited in the queue, and got `unsupported_content_type` at the
 * last step.
 *
 * The chain of truth runs upward, and each layer must only ever claim what the
 * one below it implements:
 *
 *   browser/app/publish.py            SUPPORTED_CONTENT_TYPES = ("video",)
 *     -> backend session_adapter.py   SESSION_PLATFORM_PROFILES[...].content_types
 *       -> this file                  what the page lets the user pick
 *
 * So this set is EMPTY today. That is a statement about the browser service,
 * not about the product: image posts are wanted (P2-1 step 2) and every images
 * code path on this page is kept alive for them. When `douyin_publish.py`
 * learns to upload an ordered gallery, add the platform here in the same
 * change that adds it to the backend profile — never ahead of it.
 */
export const IMAGE_POST_PLATFORMS: ReadonlySet<string> = new Set<string>();

/** Can a post made of images be published to this platform today? */
export const supportsImagePosts = (platform: string): boolean =>
  IMAGE_POST_PLATFORMS.has(platform);
