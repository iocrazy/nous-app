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
// data-module from viewToModule(pathnameToView(...)); pin the mapping here
// so a future ViewState reshuffle can't silently detach a module from its
// accent (or worse, leak an accent onto an unrelated view).
describe('viewToModule (K1.5 per-module accent)', () => {
  it('maps the AI Library / Agent surface to "ai"', () => {
    expect(viewToModule('ailibrary')).toBe('ai');
    expect(viewToModule('agents')).toBe('ai');
    expect(viewToModule('skills')).toBe('ai');
    expect(viewToModule('chat')).toBe('ai');
  });

  it('maps Topic Inspiration (/parser) to "inspiration"', () => {
    expect(viewToModule('parser')).toBe('inspiration');
  });

  it('maps Resources + Distribution to "resources"', () => {
    expect(viewToModule('resources')).toBe('resources');
    expect(viewToModule('distribution')).toBe('resources');
  });

  it('maps Projects (mediatrack) and other small modules to null (global default green)', () => {
    expect(viewToModule('mediatrack')).toBeNull();
    expect(viewToModule('dashboard')).toBeNull();
    expect(viewToModule('settings')).toBeNull();
    expect(viewToModule('todolist')).toBeNull();
    expect(viewToModule('shared')).toBeNull();
    expect(viewToModule('points')).toBeNull();
    expect(viewToModule('canvas')).toBeNull();
  });
});
