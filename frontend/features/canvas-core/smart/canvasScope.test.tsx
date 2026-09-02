// features/canvas-core/smart/canvasScope.test.tsx
//
// Two readers of one answer. `useCanvasScope` asks the ROUTER (`useParams`);
// `currentCanvasScopeId` parses `window.location` because the run path —
// chainRun / loopRun / regenerate — is not React and cannot call a hook.
//
// Two readers means they can drift, and a drift here is silent: the asset
// bundle would be fetched with the wrong team and answer 403 `not_a_member`,
// which the card reports as "could not ask" while the URL plainly shows a team
// the user is in. So they are pinned against the same canvas URLs.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { MemoryRouter, Route, Routes } from 'react-router-dom';

import { currentCanvasScopeId, useCanvasScope } from './canvasScope';

const URLS = [
  '/team/727145299382534100/canvas/9',
  '/team/5/canvas/9',
  '/team/5',
  '/canvas/9',
  '/',
];

function Probe() {
  const { scopeId } = useCanvasScope();
  return <div data-testid="scope">{scopeId || '(none)'}</div>;
}

function routerScope(url: string): string {
  const { unmount } = render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/team/:teamId/*" element={<Probe />} />
        <Route path="*" element={<Probe />} />
      </Routes>
    </MemoryRouter>,
  );
  const value = screen.getByTestId('scope').textContent ?? '';
  unmount();
  return value === '(none)' ? '' : value;
}

function locationScope(url: string): string {
  window.history.pushState({}, '', url);
  return currentCanvasScopeId();
}

describe('the hook and the plain function answer the same canvas URLs', () => {
  it.each(URLS)('%s', (url) => {
    expect(locationScope(url)).toBe(routerScope(url));
  });
});
