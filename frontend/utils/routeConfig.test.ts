// frontend/utils/routeConfig.test.ts
// Coverage for the URL ↔ ViewState mapping — added with the canvas entry
// (Infinite-Canvas parity Phase 0 G11), which is prefix-matched and easy to
// break silently when reordering pathnameToView.

import { describe, expect, it } from 'vitest';
import { pathnameToView, VIEW_PATH_MAP } from './routeConfig';

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
