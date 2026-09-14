/**
 * The four kinds a run can register a deliverable under.
 *
 * ⚠️ MIRROR of `backend/app/services/deliverables/kinds.py::ALL_KINDS` —
 * **including the order**. `backend/tests/services/deliverables/
 * test_kinds_frontend_mirror.py` reads THIS file and fails if the two lists
 * disagree, so adding a kind is a two-file edit; there is no way to ship half
 * of it.
 *
 * Adding a kind is EXPECTED to fail in three places until it is finished: the
 * backend mirror test, `tsc` on the four `Record<DeliverableKind, …>` tables,
 * and `deliverableKinds.test.ts`'s inline list of the four words. All three
 * red at once is the guard working, not three separate problems.
 *
 * Why it exists at all: four separate tables key labels and search words by
 * these words (`outputMentionRows`, `OutputMentionList`, `OutputChipBody`,
 * `Todolist/blocks/OutputsBlock`). Typed `Record<string, …>` they each accept
 * a missing kind silently, and the symptom is the mildest kind there is — a
 * picker row reading `script_beat` where its neighbours read "Shot". Keyed by
 * `DeliverableKind` the compiler names all four sites at once.
 *
 * Lives under `components/chat/` beside `attachmentLimits.ts`, the file's
 * nearest relative: both are "a number/word list the server decides and the
 * composer has to say out loud". It is imported outside chat (the issue rail,
 * `types.ts`) — the placement follows the mirror it belongs to, not the widest
 * consumer.
 */

export const DELIVERABLE_KINDS = [
  'generated_media',
  'script_shot',
  'script_scene',
  'script_chapter',
] as const;

/**
 * The union the label tables are keyed by.
 *
 * DERIVED from the array, never hand-written a second time: a literal union
 * next to the literal array is two places to update and one of them is always
 * the one the mirror test does not read.
 */
export type DeliverableKind = (typeof DELIVERABLE_KINDS)[number];
