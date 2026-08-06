/**
 * Personal-override predicates (mig 341).
 *
 * One place decides "does this agent carry the caller's own customization",
 * because two surfaces ask it — the Persona banner and the header overflow
 * menu's Reset item — and they must never disagree: a banner saying "you
 * have 3 personal changes" with no way to undo them is exactly the state
 * this feature spent a release in (backend shipped, zero UI).
 */
import { describe, expect, it } from 'vitest';
import type { AILibraryAgent } from '../../types';
import { hasPersonalOverride, overrideFieldCount } from './agentOverride';

const agent = (over: Partial<AILibraryAgent>): AILibraryAgent =>
  ({ id: 'a1', slug: 'script-ai', name: 'Script AI', ...over }) as AILibraryAgent;

describe('hasPersonalOverride', () => {
  it('is true for a system preset with overridden fields', () => {
    expect(
      hasPersonalOverride(
        agent({
          is_system_preset: true,
          override_scope: 'user',
          override_fields: ['soul_md', 'model'],
        }),
      ),
    ).toBe(true);
  });

  it('is false for a preset the caller never customized', () => {
    expect(
      hasPersonalOverride(
        agent({ is_system_preset: true, override_scope: null, override_fields: [] }),
      ),
    ).toBe(false);
  });

  it('is false when the backend omits the override fields entirely', () => {
    // Older payloads (and every non-merged read path — background pipelines,
    // admin routes) carry no override_* keys at all. Absent must read as
    // "no override", not as undefined leaking into a `.length`.
    expect(hasPersonalOverride(agent({ is_system_preset: true }))).toBe(false);
  });

  it('is false for a user-owned agent even if override_fields somehow arrives', () => {
    // A non-preset agent IS the user's own row — edits land on it directly,
    // so there is nothing to reset back to. Guarding on is_system_preset
    // keeps a stray payload from offering a reset that would 404.
    expect(
      hasPersonalOverride(
        agent({ is_system_preset: false, override_fields: ['soul_md'] }),
      ),
    ).toBe(false);
  });
});

describe('overrideFieldCount', () => {
  it('counts the replaced fields', () => {
    expect(
      overrideFieldCount(agent({ override_fields: ['soul_md', 'model', 'temperature'] })),
    ).toBe(3);
  });

  it('is 0 when absent or empty', () => {
    expect(overrideFieldCount(agent({}))).toBe(0);
    expect(overrideFieldCount(agent({ override_fields: [] }))).toBe(0);
  });
});
