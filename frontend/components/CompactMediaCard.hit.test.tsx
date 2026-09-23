/**
 * CompactMediaCard — search-hit badge and similarity bar.
 *
 * Both are search-only increments: a card rendered without `hit` (every
 * non-search surface, and every search against a backend that does not tag
 * hits with a layer) must look exactly as it did before.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Video } from '../types';

vi.mock('../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: null }),
}));
vi.mock('../services/resourceService', () => ({
  getPreviewSpriteUrl: () => null,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));

import { CompactMediaCard } from './CompactMediaCard';

const baseVideo = {
  id: '1',
  platform_id: 'pid1',
  title: 'Test Clip',
  author: 'Tester',
  media_type: 'video',
} as unknown as Video;

describe('CompactMediaCard — search hit', () => {
  it('shows the hit badge and similarity bar when hit is given', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 0.71 }} />,
    );
    expect(screen.getByTestId('hit-badge')).toHaveTextContent('Semantic · 0.71');
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '71%' });
    expect(screen.getByTestId('similarity-bar')).toHaveClass('bg-ok');
  });

  it('renders neither without hit', () => {
    render(<CompactMediaCard data={baseVideo} onClick={() => {}} />);
    expect(screen.queryByTestId('hit-badge')).toBeNull();
    expect(screen.queryByTestId('similarity-bar')).toBeNull();
  });

  it('title-only hit reads "Title · 1.00" and bar full', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'text', score: 1 }} />,
    );
    expect(screen.getByTestId('hit-badge')).toHaveTextContent('Title · 1.00');
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '100%' });
  });

  it('clamps an out-of-range score into the bar', () => {
    render(
      <CompactMediaCard data={baseVideo} onClick={() => {}} hit={{ layer: 'semantic', score: 1.4 }} />,
    );
    expect(screen.getByTestId('similarity-bar')).toHaveStyle({ width: '100%' });
  });
});
