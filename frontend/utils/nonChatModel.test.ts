/**
 * nonChatModel — the naming heuristic behind the Settings guard.
 *
 * The BYOK model catalog carries no type information (it is the provider's own
 * `GET /models` output), so the id string is the only signal. These tests pin
 * both directions of the guess: it must catch the model that actually broke
 * production, and it must stay silent on ordinary chat models — a false red
 * line teaches users to ignore red lines.
 */
import { describe, it, expect } from 'vitest';
import { suspectedNonChatKind, nonChatKindKey } from './nonChatModel';

describe('suspectedNonChatKind', () => {
  it('flags the model from the 2026-08-16 incident', () => {
    // The user left this as doubao's only enabled model; summarization takes
    // the provider's selected_model, so every summary POSTed an embedding
    // model to /chat/completions and failed.
    expect(suspectedNonChatKind('doubao-embedding-vision-251215')).toBe('embedding');
  });

  it.each([
    ['text-embedding-3-large', 'embedding'],
    ['bge-m3', 'embedding'],
    ['gte-rerank-v2', 'rerank'],
    ['doubao-seedream-4-0-t2i-250828', 'image'],
    ['gpt-image-1', 'image'],
    ['dall-e-3', 'image'],
    ['doubao-seedance-1-0-pro-250528', 'video'],
    ['whisper-1', 'speech'],
    ['gpt-4o-transcribe', 'speech'],
    ['doubao-tts-2-0', 'speech'],
  ] as const)('classifies %s as %s', (modelId, kind) => {
    expect(suspectedNonChatKind(modelId)).toBe(kind);
  });

  it.each([
    'doubao-seed-2-0-pro-260215',
    'doubao-lite',
    'gpt-4o',
    'deepseek-chat',
    'qwen-max',
    'claude-opus-5',
    'kimi-k2-turbo-preview',
  ])('stays silent on the chat model %s', (modelId) => {
    expect(suspectedNonChatKind(modelId)).toBeNull();
  });

  it('does not flag chat models that merely see or hear', () => {
    // `vision` and `audio` are the tempting-but-wrong tokens: both of these
    // answer on /chat/completions. Adding them to the vocabulary would put a
    // permanent false warning on working models.
    expect(suspectedNonChatKind('doubao-vision-pro-32k')).toBeNull();
    expect(suspectedNonChatKind('gpt-4o-audio-preview')).toBeNull();
    expect(suspectedNonChatKind('gpt-4o-realtime-preview')).toBeNull();
  });

  it('matches whole tokens, not substrings', () => {
    // Substring matching would make `tts` fire on "attsu" and `embed` on
    // "unembedded" — the kind of false positive this guard cannot afford.
    expect(suspectedNonChatKind('attsu-chat')).toBeNull();
    expect(suspectedNonChatKind('imagenary-chat-7b')).toBeNull();
  });

  it('is case- and separator-insensitive', () => {
    expect(suspectedNonChatKind('Text_Embedding_3_Small')).toBe('embedding');
    expect(suspectedNonChatKind('BAAI/bge-large-zh')).toBe('embedding');
  });

  it('says nothing about an empty or missing id', () => {
    expect(suspectedNonChatKind('')).toBeNull();
    expect(suspectedNonChatKind(null)).toBeNull();
    expect(suspectedNonChatKind(undefined)).toBeNull();
  });
});

describe('nonChatKindKey', () => {
  it('namespaces the kind under the aiSettings copy', () => {
    expect(nonChatKindKey('embedding')).toBe('aiSettings.nonChatKind.embedding');
  });
});
