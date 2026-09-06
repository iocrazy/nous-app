// Turn a raw task failure string (task_tracking.error_msg, agent run
// error_message, distribution publish errors) into user-facing copy.
//
// WHY THIS EXISTS
// task_tracking.error_msg is synced one-way from the DBOS engine (route C
// discipline, see CLAUDE.md) — the backend must NOT rewrite that column, so
// humanization can only happen in the presentation layer. Users were shown
// raw exception chains such as:
//
//   DBOSMaxStepRetriesExceeded: RuntimeError: Volcengine ASR query failed:
//   45000006 [Invalid audio URI] OperatorWrapper Process failed: internal
//   error,audio download failed
//
// which reads like "the platform / my network is broken". This maps the known
// failure shapes to a plain-English message (+ optional hint), and keeps the
// raw string available for a "Details" affordance so nothing is lost.
//
// PATTERN-TABLE DRIVEN: add a new failure shape = add one row to PATTERNS.

export interface HumanizedError {
  /** Plain-English, user-facing message. Never empty for a non-empty input. */
  message: string;
  /** Optional actionable follow-up ("try again in a moment"), when we have one. */
  hint?: string;
}

interface ErrorPattern {
  /** Matched against the (DBOS-unwrapped) raw string. */
  test: RegExp;
  message: string;
  hint?: string;
}

// Order matters: earlier, more specific rows win.
const PATTERNS: ReadonlyArray<ErrorPattern> = [
  {
    // Session-channel publish that LANDED but could not file the post into the
    // requested 合集 (collection). The browser service degrades here instead of
    // discarding a finished upload for an archival field (see
    // publish_distribution.collection_note) — so this row is a caveat on a
    // success, not a failure. First in the table so the generic "not found"
    // rule below can never claim it.
    test: /\[collection_(not_found|control_missing|error)\]/i,
    message: 'Published, but the collection was not applied.',
    hint: 'Check the collection name, or file the post from the app.',
  },
  {
    // The Codex backend rejected the ORCHESTRATOR model our catalog row names
    // (`mediahub_models.actual_model` for codex-image / codex-local-image is
    // the model gpt-image-2-skill passes as `--model`). 2026-09-05: OpenAI
    // dropped gpt-5.4 for ChatGPT-account Codex and every canvas image run
    // failed with "HTTP 400" until that row was repointed. Retrying cannot
    // help; the row is what has to change.
    test: /model.*is not supported when using codex|not supported when using codex with a chatgpt account/i,
    message: 'The Codex model configured for this account is not accepted.',
    hint: 'Point this catalog row at a model Codex currently accepts (Settings → AI → the row\'s model), then run again.',
  },
  {
    // The user's Codex provider card is switched off. The Providers page is
    // the one management entry (2026-09-06): an application never runs what
    // that page does not offer, and the fix is exactly one toggle.
    test: /\[provider_card_disabled\]/i,
    message: 'Codex (Local CLI) is switched off for this account.',
    hint: 'Turn the card on under Settings → AI → Providers, then run again.',
  },
  {
    // The image model DECLINED the prompt on content grounds: it answered with
    // prose (why, plus a rewrite that would work) instead of calling the image
    // tool. Two raw shapes reach here and both must match:
    //
    //   [content_refused] …      daemon >= 0.5.0 + the typed backend path
    //   …image_generation_call…  what a 0.4.0 daemon still produces, and what
    //                            every failure already in the history says —
    //                            `gpt-image-2-skill`'s own `missing_image_result`
    //                            envelope after dbos_error_to_text shredded the
    //                            multi-line JSON down to its longest line.
    //
    // No "try again" in the hint: a refusal is deterministic, the same words
    // get declined the same way. The prompt is what has to change.
    test: /\[content_refused\]|missing_image_result|image_generation_call/i,
    message: 'The image model declined this prompt.',
    hint: "Open Details for the model's own explanation and the rewrite it suggests, then edit the wording and run again.",
  },
  {
    // ffmpeg audio extraction produced no stream — the source video has no
    // audio track (e.g. a B站 DASH clip merged without sound). The
    // extract→transcribe chain (manual Transcribe on a no-audio video) fails
    // here; surface WHY instead of a bare "extraction failed".
    test: /does not contain any stream|no audio track|stream ?map.*matches/i,
    message: 'This video has no audio track — it may have been downloaded without sound.',
  },
  {
    // Volcengine ASR 45000006 — the audio URI the ASR service was handed could
    // not be fetched. For image galleries this is the background-music download
    // still being in flight when transcription fired.
    test: /invalid audio uri|audio download failed|\b45000006\b/i,
    message: "Transcription couldn't read this media's audio.",
    hint: 'For image galleries this needs the background music download to finish — try again in a moment.',
  },
  {
    // Provider wallet empty.
    test: /insufficient balance|\b402\b|payment required/i,
    message: 'The AI provider account is out of balance.',
  },
  {
    // Model / ASR resource not enabled for the configured provider credentials.
    // Covers Volcengine 45000030 ("resource not granted") and OpenAI-style
    // 404 model_not_found ("no active grant for service '…' on this key").
    // MUST sit before the generic 404 rule below, otherwise a model-not-found
    // 404 would be mislabeled as "source media couldn't be found".
    test: /resource not granted|no active grant|model_not_found|\b45000030\b/i,
    message: "This model isn't enabled for the configured provider account.",
  },
  {
    // Volcengine Ark "SetLimitExceeded" — an inference cap CONFIGURED on the
    // account for that model, not a burst limit. It returns 429 like a burst
    // limit does, but waiting never clears it: doubao-seed-2-0-pro failed this
    // way on every single call for five days (2026-08-14 → 19) and took every
    // ai_summary run down with it. MUST sit above the generic 429 row, whose
    // "try again shortly" is actively wrong advice here.
    test: /setlimitexceeded|reached the set inference limit/i,
    message: 'The provider account has hit its configured limit for this model.',
    hint: 'Raise the limit in the provider console, or point this task at another model in Settings → AI.',
  },
  {
    // Rate limited.
    test: /\b429\b|rate.?limit|too many requests/i,
    message: 'The AI provider is rate-limiting — try again shortly.',
  },
  {
    // Source track / media gone (Soda / player info missing).
    test: /no url_player_info|sodaapierror/i,
    message: "This media's audio track is no longer available.",
  },
  {
    // Source not found / removed.
    test: /\b404\b|not\s*found|does not exist|been removed|\bdeleted\b/i,
    message: "The source media couldn't be found.",
  },
  {
    // Network timeout — retrying usually clears it.
    test: /timeout|timed out/i,
    message: 'The task timed out — retry usually works.',
  },
];

// Peel engine/retry wrappers so pattern matching sees the underlying cause.
// `DBOSMaxStepRetriesExceeded:` is prepended by the DBOS engine after a step
// exhausts its retries; the real error is nested inside.
function unwrap(raw: string): string {
  let s = raw.trim();
  s = s.replace(/^DBOSMaxStepRetriesExceeded:\s*/i, '');
  return s.trim();
}

// A raw string "looks technical" when it carries an exception class name, a
// numeric provider error code, engine wrapper noise, or is simply very long —
// i.e. it would confuse a user. Short, already-clean strings (e.g. a platform's
// "upload rejected") are passed through unchanged instead of being flattened
// into the generic fallback.
function looksTechnical(s: string): boolean {
  return (
    /[A-Za-z_]+(Error|Exception|Wrapper):/i.test(s) ||
    /\b\d{5,}\b/.test(s) ||
    /Process failed|internal error|Traceback/i.test(s) ||
    s.length > 140
  );
}

export function humanizeTaskError(raw?: string | null): HumanizedError {
  if (!raw || !raw.trim()) return { message: 'Processing failed.' };

  const unwrapped = unwrap(raw);

  for (const p of PATTERNS) {
    if (p.test.test(unwrapped)) {
      return p.hint ? { message: p.message, hint: p.hint } : { message: p.message };
    }
  }

  // Unknown shape. Preserve information: if it reads like a raw exception dump,
  // show a neutral message and let the caller surface the raw text under
  // "Details". If it is already a short clean sentence, keep it as the message.
  if (looksTechnical(unwrapped)) {
    return { message: 'Processing failed — see details.' };
  }
  return { message: unwrapped };
}


/**
 * Split a failing party's explanation into display paragraphs. The model
 * writes for a chat window — blank-line paragraphs and `**emphasis**` — and
 * neither the Task Center modal nor a canvas node is a markdown surface:
 * literal `**` around the rewrite it offers reads as noise. Shared so the
 * two surfaces can never drift apart on this.
 */
export function explanationParagraphs(text: string): string[] {
  return text
    .replace(/\*\*/g, '')
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter(Boolean);
}
