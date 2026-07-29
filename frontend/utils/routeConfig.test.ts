// frontend/utils/routeConfig.test.ts
// Coverage for the URL ↔ ViewState mapping — added with the canvas entry
// (Infinite-Canvas parity Phase 0 G11), which is prefix-matched and easy to
// break silently when reordering pathnameToView.

import { describe, expect, it } from 'vitest';
import { pathnameToView, viewToModule, VIEW_PATH_MAP } from './routeConfig';

describe('routeConfig canvas mapping', () => {
  it('maps the canvas view to /canvas', () => {
    expect(VIEW_PATH_MAP.canvas).toBe('/canvas');
  });

  it('resolves the canvas list route to the canvas view', () => {
    expect(pathnameToView('/team/t1/canvas')).toBe('canvas');
  });

  it('resolves the canvas editor route to the canvas view', () => {
    expect(pathnameToView('/team/t1/canvas/123456')).toBe('canvas');
  });

  it('still resolves neighbouring views correctly', () => {
    expect(pathnameToView('/team/t1/resources')).toBe('resources');
    expect(pathnameToView('/team/t1/projects')).toBe('mediatrack');
    expect(pathnameToView('/team/t1/chat')).toBe('chat');
  });
});

// K1.5 (2026-07-29) — per-module accent scoping (docs/superpowers/specs/
// 2026-07-29-warm-paper-palette-design.md §5). AppLayout stamps
// data-module from viewToModule(pathnameToView(...), location.pathname); pin
// the mapping here so a future ViewState reshuffle can't silently detach a
// module from its accent (or worse, leak an accent onto an unrelated view).
describe('viewToModule (K1.5 per-module accent)', () => {
  it('maps the AI Library / Agent surface to "ai"', () => {
    expect(viewToModule('ailibrary', '/team/t1/ai-library/agents')).toBe('ai');
    expect(viewToModule('agents', '/team/t1/agents')).toBe('ai');
    expect(viewToModule('skills', '/team/t1/skills')).toBe('ai');
    expect(viewToModule('chat', '/team/t1/chat')).toBe('ai');
  });

  it('maps Topic Inspiration (/parser) to "inspiration"', () => {
    expect(viewToModule('parser', '/team/t1/parser')).toBe('inspiration');
    expect(viewToModule('parser', '/parser')).toBe('inspiration'); // legacy no-team-prefix form
  });

  it('maps Resources + Distribution to "resources"', () => {
    expect(viewToModule('resources', '/team/t1/resources')).toBe('resources');
    expect(viewToModule('distribution', '/team/t1/distribution/accounts')).toBe('resources');
  });

  it('maps Projects (mediatrack) and other small modules to null (global default green)', () => {
    expect(viewToModule('mediatrack', '/team/t1/projects')).toBeNull();
    expect(viewToModule('dashboard', '/team/t1/dashboard')).toBeNull();
    expect(viewToModule('settings', '/team/t1/settings')).toBeNull();
    expect(viewToModule('todolist', '/team/t1/todolist')).toBeNull();
    expect(viewToModule('shared', '/team/t1/shared')).toBeNull();
    expect(viewToModule('points', '/team/t1/points')).toBeNull();
    expect(viewToModule('canvas', '/team/t1/canvas')).toBeNull();
  });

  it('minor fix: a genuinely unrecognized route must NOT inherit the inspiration accent', () => {
    // pathnameToView's trailing `return 'parser'` is a catch-all for ANY
    // unmatched path, not just the real /parser route (see its own comment).
    // Before this fix, viewToModule('parser') unconditionally returned
    // 'inspiration', so an unrecognized route would silently paint itself
    // ochre. It must fall back to the global default (null) instead.
    const unknownPath = '/team/t1/some-totally-unknown-route';
    expect(pathnameToView(unknownPath)).toBe('parser'); // confirms the fallback actually fires
    expect(viewToModule('parser', unknownPath)).toBeNull();
  });
});
