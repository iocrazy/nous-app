/**
 * Legacy storyboard read-only banner (Phase B P3 cutover).
 *
 * The banner is the mandatory piece of the legacy-face lockdown; the deep canvas
 * write surface inside CanvasEditorPage stays interactive (full read-only
 * enforcement / data DROP is P4). This smoke test pins the banner renders with a
 * status role — the CanvasEditorPage wiring itself is verified by build +
 * typecheck (that page pulls in the ReactFlow canvas + zustand stores + router,
 * which are out of scope to mount here).
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

import { StoryboardReadOnlyBanner } from '../../components/storyboard/StoryboardReadOnlyBanner';

afterEach(cleanup);

describe('StoryboardReadOnlyBanner', () => {
  it('renders the read-only notice as a status region', () => {
    render(<StoryboardReadOnlyBanner />);
    expect(screen.getByTestId('storyboard-readonly-banner')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('storyboard.readOnlyBanner');
  });
});
