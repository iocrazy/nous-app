/**
 * Attachment limits the chat UI has to SAY OUT LOUD.
 *
 * ⚠️ MIRROR of `backend/app/services/ai/chat/ai_library_chat_service.py`.
 * `backend/tests/services/ai/chat/test_attachment_limit_frontend_mirror.py`
 * reads THIS file and fails if the numbers disagree — change one, change both.
 *
 * Why a constant instead of the number in the sentence: the failure banner
 * tells the user how many references were used, and a literal `8` baked into
 * two locale strings drifts from the server the day the cap moves. The copy
 * interpolates `{{n}}` from here, so both languages move together and the
 * mirror test makes the backend move with them.
 *
 * Only limits the USER IS TOLD ABOUT belong here. The binary bucket's cap
 * (`chat_attachment_resolver.MAX_ATTACHMENTS_PER_TURN`, also 8) is deliberately
 * absent: it truncates SILENTLY, so no string interpolates it, and mirroring a
 * number nothing renders would be a second place to get wrong for no gain.
 */

/**
 * How many `asset_ref` attachments one turn resolves. Refs past this are not
 * resolved and come back as a typed `attachment_limit_exceeded` failure.
 *
 * ASSET refs only. `resource_ref` is uncapped on purpose — any number of them
 * costs one batched query, while each asset costs about five serial ones.
 */
export const MAX_ASSET_REF_ATTACHMENTS = 8;

/**
 * How many `output_ref` CITATIONS one comment may carry (3a Task 6).
 *
 * ⚠️ Mirrors `output_ref_resolver.MAX_OUTPUT_REF_ATTACHMENTS` — a DIFFERENT
 * constant from the one above that happens to hold the same number today. The
 * two bound different costs (an asset is about five serial queries; a cited
 * object is one `lineage_for`, shared by every version of it), so folding them
 * into one would make each move for the other's reason. Read the pair through
 * `test_attachment_limit_frontend_mirror.py`, which pins both independently.
 *
 * Enforced HERE as well as on the server, and the two do different jobs: the
 * picker refuses the ninth pick so the writer sees the cap while choosing,
 * and the server refuses the whole comment. Unlike the asset cap, exceeding
 * this one is not a partial success — a citation the server cannot resolve
 * means the writer and the server disagree about what was pointed at, so the
 * comment does not go at all.
 */
export const MAX_OUTPUT_REF_ATTACHMENTS = 8;
