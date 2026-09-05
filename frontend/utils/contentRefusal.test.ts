import { describe, it, expect } from 'vitest';
import { humanizeTaskError } from './humanizeTaskError';
import { taskErrorCopy } from './taskErrorCopy';

// The 2026-09-04 failure, end to end. The model declined a prompt on content
// grounds and explained why (naming the offending words AND handing back a
// rewrite that works) — and the user was shown "Processing failed — see
// details.", with the details holding one shredded line of pipeline jargon.
//
// Both raw shapes below are REAL strings taken from the production
// task_tracking row, not invented ones.

// What a 0.5.0 daemon + the fixed backend produce, AS STORED — i.e. after the
// mirror trigger has run the pickled exception through
// `public.dbos_error_to_text()`, which prepends the class name. That is the
// string the UI is handed, and it is what this must be tested against; the
// message as *raised* never reaches a component.
//
// Verified 2026-09-04 by feeding a protocol-4 pickle of the raised message to
// the live function on nous-db: it comes back whole, byte for byte. (DBOS
// pickles at protocol 4 — the SQL side only detects gAS/gAU/gAQ/gAV prefixes,
// so this had to be checked rather than assumed.)
const REFUSAL_MSG =
  'RuntimeError: [content_refused] The image model declined this prompt and answered ' +
  "with an explanation instead of an image. Open this task's details to read its own " +
  'wording and the rewrite it suggests.';

// What is in production TODAY, and what a 0.4.0 daemon still produces: the
// daemon's multi-line JSON shredded by the same function down to its longest
// line. Reproduced exactly by the same live-function check.
const LEGACY_MSG =
  'RuntimeError: "message": "The response did not include an image_generation_call result."';

const MODEL_WORDS =
  '抱歉，我不能帮助生成带有明显性化服饰与姿势的写实人物图像。\n\n' +
  '你也可以直接用这条更安全的提示词：成年年轻女性，黑色时尚连体服与长靴，半蹲姿，写实摄影风格，85mm镜头';

describe('content refusal copy', () => {
  it('names the refusal for the message the fixed backend raises', () => {
    const r = humanizeTaskError(REFUSAL_MSG);
    expect(r.message).toMatch(/declined/i);
    expect(r.message).not.toMatch(/see details/i);
  });

  // Without this row the fix reaches nobody who has not updated their daemon,
  // and every failure already in the history stays unreadable.
  it('names the refusal for the shredded string still in production today', () => {
    const r = humanizeTaskError(LEGACY_MSG);
    expect(r.message).toMatch(/declined/i);
  });

  it('tells the user the prompt is what to change, not the platform', () => {
    const r = humanizeTaskError(REFUSAL_MSG);
    expect(r.hint).toMatch(/prompt|wording|rewrite/i);
  });

  // A refusal is deterministic: the same words are declined the same way. The
  // generic "try again shortly" advice would be actively wrong here.
  it('never advises a plain retry', () => {
    for (const raw of [REFUSAL_MSG, LEGACY_MSG]) {
      expect(humanizeTaskError(raw).hint ?? '').not.toMatch(/try again|retry/i);
    }
  });

  it("surfaces the model's own words from task metadata", () => {
    const copy = taskErrorCopy(
      { failure: { code: 'content_refused', detail: MODEL_WORDS } },
      REFUSAL_MSG,
    );
    expect(copy.message).toMatch(/declined/i);
    expect(copy.detail).toBe(MODEL_WORDS);
  });

  // A 0.4.0 daemon sends no detail. Absent must read as absent: an empty
  // string would render a blank explanation panel, which says "the model had
  // nothing to say" rather than "this daemon cannot tell you".
  it('reports no detail when the daemon did not send one', () => {
    const copy = taskErrorCopy({ failure: { code: 'content_refused', detail: '' } }, REFUSAL_MSG);
    expect(copy.detail).toBeUndefined();
    expect(taskErrorCopy({}, LEGACY_MSG).detail).toBeUndefined();
  });

  // The metadata comes off the wire; shape is not a promise.
  it('ignores a failure block that is not the expected shape', () => {
    for (const failure of [null, 'nope', 42, { detail: 7 }, []]) {
      expect(taskErrorCopy({ failure }, REFUSAL_MSG).detail).toBeUndefined();
    }
  });
});

// 2026-09-05: OpenAI dropped `gpt-5.4` for ChatGPT-account Codex. The stored
// string once the daemon forwards the skill's HTTP body (see imageJobFailure).
const MODEL_UNSUPPORTED =
  "RuntimeError: job_failed: gpt-image-2-skill: http_error: HTTP 400: The 'gpt-5.4' model " +
  'is not supported when using Codex with a ChatGPT account.';

describe('codex orchestrator model rejected', () => {
  it('names the cause and points at the model setting, not at retrying', () => {
    const r = humanizeTaskError(MODEL_UNSUPPORTED);
    expect(r.message).toMatch(/model.*(not supported|not accepted|rejected)/i);
    expect(r.hint ?? '').toMatch(/Settings|model/i);
    expect(r.hint ?? '').not.toMatch(/try again/i);
    expect(r.message).not.toMatch(/see details/i);
  });
});
