// components/Distribution/CoverStudio/coverReferences.ts
//
// The reference pool's data model, kept out of the component so the rules can
// be tested without rendering anything.
//
// ★ Every entry carries a `genId` — a generated_media row id — and a `url`
// derived from it. That is not a convenience: the image generation bridge
// accepts ONLY `/api/v1/generated-media/{id}/(cover|stream|file)` URLs as
// references and silently drops anything else, so a pool entry that is not
// backed by a generated_media row would sail through the whole flow and simply
// not be in the picture. Frames and library picks are therefore imported
// before they can enter the pool, never after.

/** How this picture got into the pool. The three say different things to the
 *  model and to the user, so they are not collapsed into one flag. */
export type CoverReferenceKind = 'person' | 'frame' | 'template';

export interface CoverReference {
  kind: CoverReferenceKind;
  /** generated_media row id, as a STRING (snowflake > 2^53). */
  genId: string;
  /** Ready for both <img src> and params.source_urls — the same URL. */
  url: string;
  /** Frames: where in the video this came from. Templates: the saved name. */
  label?: string;
  /** Templates only — so usage can be counted and the tile can be deselected. */
  templateId?: string;
  /** Frames only — the coordinate the frame was taken at. */
  timestampSeconds?: number;
}

/**
 * The shared cap.
 *
 * Nine because the server enforces nine, in two independent places
 * (`canvas_generation.py` slices `source_urls[:9]`, and the codex adapter
 * slices its `--ref-image` list again). Frames and templates share it because
 * the model sees one flat list — presenting them as two budgets would let a
 * user fill both and silently lose the overflow.
 */
export const MAX_REFERENCES = 9;

/** `mm:ss.s`, the form the design captions frames with. */
export function formatTimestamp(seconds: number): string {
  const safe = Number.isFinite(seconds) && seconds > 0 ? seconds : 0;
  const m = Math.floor(safe / 60);
  const s = safe - m * 60;
  return `${m}:${s.toFixed(1).padStart(4, '0')}`;
}

export function isFull(refs: CoverReference[]): boolean {
  return refs.length >= MAX_REFERENCES;
}

/**
 * Add a reference, refusing duplicates and overflow.
 *
 * Returns the SAME array when nothing changed so callers can compare by
 * identity, and a reason when it refused — a silent no-op on an "Add" click is
 * the failure mode this whole codebase keeps re-learning.
 */
export type AddResult =
  | { ok: true; refs: CoverReference[] }
  | { ok: false; reason: 'full' | 'duplicate'; refs: CoverReference[] };

export function addReference(
  refs: CoverReference[],
  next: CoverReference,
): AddResult {
  // Same picture, however it got here: two entries pointing at one
  // generated_media row would burn two of the nine slots on one image.
  if (refs.some((r) => r.genId === next.genId)) {
    return { ok: false, reason: 'duplicate', refs };
  }
  // The person slot is singular by definition — replacing rather than
  // appending is what "the character slot" means, and it must not consume a
  // second slot when the user grabs a better frame of themselves.
  if (next.kind === 'person') {
    const without = refs.filter((r) => r.kind !== 'person');
    return { ok: true, refs: [next, ...without] };
  }
  if (isFull(refs)) {
    return { ok: false, reason: 'full', refs };
  }
  return { ok: true, refs: [...refs, next] };
}

export function removeReference(
  refs: CoverReference[],
  genId: string,
): CoverReference[] {
  return refs.filter((r) => r.genId !== genId);
}

/**
 * The person reference, if one is set.
 *
 * The style treats a missing one as a BLOCKER, not a warning: half of it is
 * "keep the same person", and generating without that picture produces a
 * different face every draft — which reads as the style being broken rather
 * than as the user having skipped a step.
 */
export function personReference(
  refs: CoverReference[],
): CoverReference | undefined {
  return refs.find((r) => r.kind === 'person');
}

/** What actually goes into `params.source_urls`, in a stable order. */
export function sourceUrls(refs: CoverReference[]): string[] {
  // Person first when present. The prompt talks about "the supplied character
  // image"; putting it first makes that reference unambiguous rather than
  // depending on which slot the user filled in what order.
  const person = refs.filter((r) => r.kind === 'person');
  const rest = refs.filter((r) => r.kind !== 'person');
  return [...person, ...rest].slice(0, MAX_REFERENCES).map((r) => r.url);
}

/** Template ids in the pool — what `markCoverTemplatesUsed` gets. */
export function templateIds(refs: CoverReference[]): string[] {
  return refs
    .filter((r) => r.kind === 'template' && r.templateId)
    .map((r) => r.templateId as string);
}
