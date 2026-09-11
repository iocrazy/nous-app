// Pure logic for the Push tab: rating + AI-intent controls, the Pipeline tag
// filter, and turning an error response body into a readable message.
//
// Classic script (no bundler): popup.html loads it BEFORE popup.js, which reads
// it from globalThis.MediaHubIntents. It also exports via module.exports so
// scripts/extension-intents.test.cjs can require it under Node. Keep it free of
// DOM and chrome.* access — anything here must run in both places.
//
// Mirrors frontend/pages/ShortcutsTagsPage.tsx (the iOS Shortcuts tag picker)
// and frontend/utils/aiIntents.ts; change them together.
(function (root) {
  'use strict';

  // Tag group holding the Transcript / Summary / Analyze system tags. Those AI
  // intents are explicit request fields now, so the popup hides this group
  // instead of offering its tags as ordinary choices.
  const PIPELINE_TAG_GROUP = 'Pipeline';

  const DEFAULT_OPTIONS = Object.freeze({
    rating: null,
    transcribe: false,
    summarize: false,
    analyze: false,
  });

  const INTENT_KEYS = Object.freeze(['transcribe', 'summarize', 'analyze']);

  // Next options after setting one key. Summary/analysis run after
  // transcription, so enabling either lights transcribe, and turning transcribe
  // off turns both off — the state never breaks that dependency. Always
  // returns a new object; `prev` is never mutated.
  function applyIntentDependencies(prev, key, value) {
    const next = { ...prev, [key]: value };
    if ((key === 'summarize' || key === 'analyze') && value === true) {
      return { ...next, transcribe: true };
    }
    if (key === 'transcribe' && value === false) {
      return { ...next, summarize: false, analyze: false };
    }
    return next;
  }

  // Only the fields to merge into the POST /media/fetch body: `rating` when it
  // is a number, each intent flag only when true. Omitting the rest keeps the
  // request identical to pre-1.4.0 pushes when nothing is set.
  function buildIntentFields(options) {
    const fields = {};
    if (typeof options.rating === 'number') fields.rating = options.rating;
    for (const key of INTENT_KEYS) {
      if (options[key] === true) fields[key] = true;
    }
    return fields;
  }

  // The tags the popup may list, search, or rank. New array; input untouched.
  function filterPickableTags(tags) {
    return tags.filter((tag) => tag.group_name !== PIPELINE_TAG_GROUP);
  }

  // How many 422 validation entries to spell out before "(+N more)".
  const MAX_VALIDATION_ITEMS = 2;

  const nonEmptyString = (value) =>
    typeof value === 'string' && value.trim() ? value : '';

  // "rating: Input should be ..." for one FastAPI validation entry; the
  // leading "body" segment is noise to a user.
  function describeValidationItem(item) {
    if (!item || typeof item !== 'object') return '';
    const msg = nonEmptyString(item.msg);
    if (!msg) return '';
    const loc = Array.isArray(item.loc)
      ? item.loc.filter((part, i) => !(i === 0 && part === 'body')).join('.')
      : '';
    return loc ? `${loc}: ${msg}` : msg;
  }

  function summarizeValidation(items) {
    const parts = items.map(describeValidationItem).filter(Boolean);
    if (parts.length === 0) return '';
    const shown = parts.slice(0, MAX_VALIDATION_ITEMS).join('; ');
    const rest = parts.length - MAX_VALIDATION_ITEMS;
    return rest > 0 ? `${shown} (+${rest} more)` : shown;
  }

  // Message for a failed request. Production wraps every error in the
  // ErrorResponse envelope ({success:false, error, code, details} — see
  // backend/app/core/exceptions.py), which has no `detail`; reading only
  // `detail` rendered every failure as empty. Order: `error`, then a string
  // `detail` (bare FastAPI shape), then "HTTP <status>". A 422 appends a short
  // summary of its {loc,msg} list from `details` (or a bare `detail` array).
  function describeErrorBody(body, status) {
    const fallback = `HTTP ${status}`;
    if (!body || typeof body !== 'object') return fallback;
    const message =
      nonEmptyString(body.error) || nonEmptyString(body.detail) || fallback;
    if (status !== 422) return message;
    const items = Array.isArray(body.details)
      ? body.details
      : Array.isArray(body.detail)
        ? body.detail
        : null;
    const summary = items ? summarizeValidation(items) : '';
    return summary ? `${message}: ${summary}` : message;
  }

  const api = Object.freeze({
    PIPELINE_TAG_GROUP,
    DEFAULT_OPTIONS,
    applyIntentDependencies,
    buildIntentFields,
    filterPickableTags,
    describeErrorBody,
  });

  root.MediaHubIntents = api;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
})(globalThis);
