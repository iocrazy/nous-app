// scripts/extension-intents.test.cjs
//
// Unit tests for chrome-extension/intents.js — the pure logic behind the
// MediaHub Push popup's rating + AI-intent controls. Lives outside
// chrome-extension/ so the packaged extension never ships it.
//
// Run: node --test scripts/extension-intents.test.cjs
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const EXT_DIR = path.join(__dirname, '..', 'chrome-extension');
const intents = require(path.join(EXT_DIR, 'intents.js'));

const {
  PIPELINE_TAG_GROUP,
  DEFAULT_OPTIONS,
  applyIntentDependencies,
  buildIntentFields,
  filterPickableTags,
  describeErrorBody,
} = intents;

test('PIPELINE_TAG_GROUP matches the web app constant', () => {
  assert.equal(PIPELINE_TAG_GROUP, 'Pipeline');
});

test('DEFAULT_OPTIONS is all-off with no rating, and cannot be mutated', () => {
  assert.deepEqual(DEFAULT_OPTIONS, {
    rating: null,
    transcribe: false,
    summarize: false,
    analyze: false,
  });
  assert.ok(Object.isFrozen(DEFAULT_OPTIONS));
});

test('turning Summary on also turns Transcribe on', () => {
  const next = applyIntentDependencies(DEFAULT_OPTIONS, 'summarize', true);
  assert.deepEqual(next, { rating: null, transcribe: true, summarize: true, analyze: false });
});

test('turning Analyze on also turns Transcribe on', () => {
  const next = applyIntentDependencies(DEFAULT_OPTIONS, 'analyze', true);
  assert.deepEqual(next, { rating: null, transcribe: true, summarize: false, analyze: true });
});

test('turning Transcribe off also turns Summary and Analyze off', () => {
  const prev = { rating: 4, transcribe: true, summarize: true, analyze: true };
  const next = applyIntentDependencies(prev, 'transcribe', false);
  assert.deepEqual(next, { rating: 4, transcribe: false, summarize: false, analyze: false });
});

test('turning Summary or Analyze off leaves Transcribe on', () => {
  const prev = { rating: null, transcribe: true, summarize: true, analyze: true };
  assert.deepEqual(applyIntentDependencies(prev, 'summarize', false), {
    rating: null, transcribe: true, summarize: false, analyze: true,
  });
  assert.deepEqual(applyIntentDependencies(prev, 'analyze', false), {
    rating: null, transcribe: true, summarize: true, analyze: false,
  });
});

test('rating passes through without touching intents', () => {
  const prev = { rating: null, transcribe: true, summarize: false, analyze: true };
  assert.deepEqual(applyIntentDependencies(prev, 'rating', 3), { ...prev, rating: 3 });
  assert.deepEqual(applyIntentDependencies({ ...prev, rating: 3 }, 'rating', null), prev);
});

test('applyIntentDependencies returns a new object and never mutates prev', () => {
  const prev = { rating: 2, transcribe: true, summarize: true, analyze: false };
  const snapshot = { ...prev };
  const next = applyIntentDependencies(prev, 'transcribe', false);
  assert.notEqual(next, prev);
  assert.deepEqual(prev, snapshot);
});

test('buildIntentFields omits false flags and a null rating', () => {
  assert.deepEqual(buildIntentFields(DEFAULT_OPTIONS), {});
});

test('buildIntentFields includes true flags and a numeric rating', () => {
  assert.deepEqual(
    buildIntentFields({ rating: 5, transcribe: true, summarize: true, analyze: true }),
    { rating: 5, transcribe: true, summarize: true, analyze: true },
  );
  assert.deepEqual(
    buildIntentFields({ rating: null, transcribe: true, summarize: false, analyze: false }),
    { transcribe: true },
  );
  assert.deepEqual(
    buildIntentFields({ rating: 1, transcribe: false, summarize: false, analyze: false }),
    { rating: 1 },
  );
});

test('filterPickableTags drops Pipeline-group tags and keeps the rest in order', () => {
  const tags = [
    { id: '1', name: 'Transcript', group_name: 'Pipeline' },
    { id: '2', name: 'Cyberpunk', group_name: 'Style' },
    { id: '3', name: 'Summary', group_name: 'Pipeline' },
    { id: '4', name: 'Loose', group_name: null },
  ];
  assert.deepEqual(filterPickableTags(tags).map((t) => t.id), ['2', '4']);
  assert.equal(tags.length, 4, 'input list must not be mutated');
});

test('describeErrorBody reads the production ErrorResponse envelope', () => {
  const body = { success: false, error: 'Media not found', code: 'http_404', details: null };
  assert.equal(describeErrorBody(body, 404), 'Media not found');
});

test('describeErrorBody falls back to a string detail, then to HTTP <status>', () => {
  assert.equal(describeErrorBody({ detail: 'Invalid API key' }, 401), 'Invalid API key');
  assert.equal(describeErrorBody({ detail: { code: 'x' } }, 409), 'HTTP 409');
  assert.equal(describeErrorBody({}, 500), 'HTTP 500');
  assert.equal(describeErrorBody(null, 502), 'HTTP 502');
  assert.equal(describeErrorBody('<html>Bad Gateway</html>', 502), 'HTTP 502');
});

test('describeErrorBody summarizes 422 validation details', () => {
  const body = {
    success: false,
    error: 'Request validation failed',
    code: 'validation_error',
    details: [
      { loc: ['body', 'rating'], msg: 'Input should be less than or equal to 5', type: 'less_than_equal' },
      { loc: ['body', 'url'], msg: 'Field required', type: 'missing' },
      { loc: ['body', 'transcribe'], msg: 'Input should be a valid boolean', type: 'bool_type' },
    ],
  };
  assert.equal(
    describeErrorBody(body, 422),
    'Request validation failed: rating: Input should be less than or equal to 5; url: Field required (+1 more)',
  );
});

test('describeErrorBody handles a bare FastAPI 422 and non-array details', () => {
  const bare = { detail: [{ loc: ['body', 'rating'], msg: 'Input should be a valid integer' }] };
  assert.equal(describeErrorBody(bare, 422), 'HTTP 422: rating: Input should be a valid integer');
  const odd = { error: 'Request validation failed', details: { reason: 'weird' } };
  assert.equal(describeErrorBody(odd, 422), 'Request validation failed');
});

test('popup.html loads intents.js before popup.js', () => {
  const html = fs.readFileSync(path.join(EXT_DIR, 'popup.html'), 'utf8');
  const intentsAt = html.indexOf('<script src="intents.js">');
  const popupAt = html.indexOf('<script src="popup.js">');
  assert.ok(intentsAt !== -1, 'popup.html must load intents.js');
  assert.ok(popupAt !== -1, 'popup.html must load popup.js');
  assert.ok(intentsAt < popupAt, 'intents.js must load before popup.js');
});
