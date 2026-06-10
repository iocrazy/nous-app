"""Built-in tasklets — 10 ready-to-use cheap-model micro-calls.

Each tasklet here is a :class:`Tasklet` instance (not a class) wired with
its own system prompt + optional output schema. Callers do:

    from app.services.ai.tasklets.builtins import title_generator
    result = await title_generator.run(first_user_message, settings=settings)
    if result.ok:
        title = result.value

10 tasklets, in plan §5 Phase 0.5 order:
1. title_generator           — generate a chat session title
2. intent_classifier         — classify user message intent
3. tag_inferrer              — infer resource tags from content
4. route_selector            — pick which agent should handle a request
5. sentiment_check           — frustration / satisfaction / neutral
6. prompt_rewriter           — improve user's free-form image prompt
7. brief_summary             — short summary of given text (replaces L4)
8. key_phrase_extractor      — pull key phrases from text
9. style_preference_extractor — extract style cues (color/aesthetic/medium)
10. character_entity_extractor — character names + traits from text

All tasklets default to ``qwen-turbo`` for the 16x cost saving over the
full model. Bump model on a specific tasklet by passing a kwarg::

    custom = replace(title_generator, model="qwen-plus")
"""

from __future__ import annotations

from app.services.ai.tasklets.base import Tasklet

# ============================================================================
# 1. title_generator
# ============================================================================

title_generator = Tasklet(
    slug="title_generator",
    system_prompt=(
        "You generate a short title for a chat session based on the user's "
        "first message. Title must be:\n"
        "- 3-8 words\n"
        "- No quotes, no period at the end\n"
        "- Title Case\n"
        "- Captures the gist of what the user is trying to do\n"
        "Respond with just the title, nothing else."
    ),
    max_tokens=32,
    cache_fingerprint_suffix="v1",
)


# ============================================================================
# 2. intent_classifier
# ============================================================================

intent_classifier = Tasklet(
    slug="intent_classifier",
    system_prompt=(
        "You classify a user's message into exactly one intent category. "
        "Categories:\n"
        "- chat: general conversation, no specific deliverable\n"
        "- question: user asking for info or explanation\n"
        "- bug_report: user reporting something broken\n"
        "- feature_request: user asking for new capability\n"
        "- storyboard: user wants to create / edit a storyboard\n"
        "- image_task: user wants to generate / edit an image\n"
        "- video_task: user wants to generate / edit a video\n"
        "- script_task: user wants to write / edit a script\n"
        "- other: doesn't fit any of the above\n\n"
        'Return STRICT JSON: {"intent": "<category>", "confidence": <0..1>}'
    ),
    output_schema={
        "type": "object",
        "required": ["intent"],
        "properties": {
            "intent": {
                "type": "string",
                "enum": [
                    "chat",
                    "question",
                    "bug_report",
                    "feature_request",
                    "storyboard",
                    "image_task",
                    "video_task",
                    "script_task",
                    "other",
                ],
            },
            "confidence": {"type": "number"},
        },
    },
    max_tokens=64,
)


# ============================================================================
# 3. tag_inferrer
# ============================================================================

tag_inferrer = Tasklet(
    slug="tag_inferrer",
    system_prompt=(
        "You infer 3-7 short tags for a resource based on its content "
        "snippet. Tags must be:\n"
        "- Lowercase, single word or short hyphenated phrase\n"
        "- No spaces, no punctuation\n"
        "- Cover topic + format + style if applicable\n\n"
        'Return STRICT JSON: {"tags": ["...", "..."]}'
    ),
    output_schema={
        "type": "object",
        "required": ["tags"],
        "properties": {
            "tags": {"type": "array"},
        },
    },
    max_tokens=128,
)


# ============================================================================
# 4. route_selector
# ============================================================================

route_selector = Tasklet(
    slug="route_selector",
    system_prompt=(
        "You pick the best agent for a user request. Choose from:\n"
        "- script_ai: writing or editing scripts / outlines / dialogue\n"
        "- storyboard_ai: creating or editing storyboards / shot lists\n"
        "- summarize: condensing long text\n"
        "- analyze: visual or content analysis tasks\n"
        "- chat: general conversation, no specialist needed\n\n"
        'Return STRICT JSON: {"agent_slug": "<slug>", "reasoning": "<1 sentence why>"}'
    ),
    output_schema={
        "type": "object",
        "required": ["agent_slug"],
        "properties": {
            "agent_slug": {
                "type": "string",
                "enum": ["script_ai", "storyboard_ai", "summarize", "analyze", "chat"],
            },
            "reasoning": {"type": "string"},
        },
    },
    max_tokens=128,
)


# ============================================================================
# 5. sentiment_check
# ============================================================================

sentiment_check = Tasklet(
    slug="sentiment_check",
    system_prompt=(
        "You detect the emotional tone of a user message. Categories:\n"
        "- frustrated: user is annoyed, repeating, escalating\n"
        "- dissatisfied: user is unhappy with prior output\n"
        "- neutral: no strong signal either way\n"
        "- satisfied: user expressed positive feedback\n"
        "- excited: user is enthusiastic / eager\n\n"
        'Return STRICT JSON: {"sentiment": "<category>", "trigger_phrase": "<the words that flagged it, or empty>"}'
    ),
    output_schema={
        "type": "object",
        "required": ["sentiment"],
        "properties": {
            "sentiment": {
                "type": "string",
                "enum": [
                    "frustrated",
                    "dissatisfied",
                    "neutral",
                    "satisfied",
                    "excited",
                ],
            },
            "trigger_phrase": {"type": "string"},
        },
    },
    max_tokens=64,
)


# ============================================================================
# 6. prompt_rewriter
# ============================================================================

prompt_rewriter = Tasklet(
    slug="prompt_rewriter",
    system_prompt=(
        "You rewrite a user's free-form image-generation request into a "
        "concrete SDXL/FLUX-style prompt. Rules:\n"
        "- Keep the user's intent\n"
        "- Add concrete visual descriptors (composition, lighting, mood)\n"
        "- Use comma-separated phrases (no narrative sentences)\n"
        "- Don't exceed 60 words\n"
        '- Don\'t include any uncertain claims like "maybe" or "if you want"\n\n'
        "Respond with just the rewritten prompt, nothing else."
    ),
    max_tokens=200,
)


# ============================================================================
# 7. brief_summary — replacement-grade summariser
# ============================================================================

brief_summary = Tasklet(
    slug="brief_summary",
    system_prompt=(
        "You summarise the given text. Capture:\n"
        "- Decisions made\n"
        "- Rejected options (and why)\n"
        "- Facts established\n"
        "- User style preferences expressed\n"
        "- Current task state\n"
        "Be terse. No preamble. 200 words max."
    ),
    max_tokens=512,
)


# ============================================================================
# 8. key_phrase_extractor
# ============================================================================

key_phrase_extractor = Tasklet(
    slug="key_phrase_extractor",
    system_prompt=(
        "You extract 3-10 key noun-phrases from the given text. Each phrase:\n"
        "- 1-4 words\n"
        "- A concrete concept or named entity\n"
        '- No generic glue words ("the system", "a thing")\n\n'
        'Return STRICT JSON: {"phrases": ["...", "..."]}'
    ),
    output_schema={
        "type": "object",
        "required": ["phrases"],
        "properties": {"phrases": {"type": "array"}},
    },
    max_tokens=128,
)


# ============================================================================
# 9. style_preference_extractor
# ============================================================================

style_preference_extractor = Tasklet(
    slug="style_preference_extractor",
    system_prompt=(
        "You extract durable visual / aesthetic preferences the user expressed. "
        "Categories you might find:\n"
        '- color (e.g. "warm tones", "high contrast")\n'
        '- medium (e.g. "oil painting", "3d render", "line art")\n'
        '- mood (e.g. "melancholic", "playful", "dramatic")\n'
        '- composition (e.g. "close-up", "wide shot", "asymmetric")\n'
        "- avoid (things the user explicitly does NOT want)\n\n"
        "Return STRICT JSON:\n"
        '{"preferences": [{"category": "<one of above>", "value": "<short phrase>", "strength": "strong|moderate|weak"}]}\n'
        "Empty array if nothing durable found."
    ),
    output_schema={
        "type": "object",
        "required": ["preferences"],
        "properties": {"preferences": {"type": "array"}},
    },
    max_tokens=384,
)


# ============================================================================
# 10. character_entity_extractor
# ============================================================================

character_entity_extractor = Tasklet(
    slug="character_entity_extractor",
    system_prompt=(
        "You extract character entities from a script or story passage. "
        "For each character, capture:\n"
        "- name (canonical)\n"
        "- aliases (any other names used in the passage)\n"
        "- traits (1-5 short adjectives or roles)\n"
        "- first_mentioned (the sentence or fragment where they first appear)\n\n"
        "Return STRICT JSON:\n"
        '{"characters": [{"name": "...", "aliases": [...], "traits": [...], "first_mentioned": "..."}]}\n'
        "Empty array if no proper character is found."
    ),
    output_schema={
        "type": "object",
        "required": ["characters"],
        "properties": {"characters": {"type": "array"}},
    },
    max_tokens=512,
)


# ============================================================================
# Registry — useful for telemetry / cost-by-tasklet rollups (plan §0.5
# dashboard "By Tasklet" view).
# ============================================================================

BUILTIN_TASKLETS: tuple[Tasklet, ...] = (
    title_generator,
    intent_classifier,
    tag_inferrer,
    route_selector,
    sentiment_check,
    prompt_rewriter,
    brief_summary,
    key_phrase_extractor,
    style_preference_extractor,
    character_entity_extractor,
)


__all__ = [
    "BUILTIN_TASKLETS",
    "brief_summary",
    "character_entity_extractor",
    "intent_classifier",
    "key_phrase_extractor",
    "prompt_rewriter",
    "route_selector",
    "sentiment_check",
    "style_preference_extractor",
    "tag_inferrer",
    "title_generator",
]
