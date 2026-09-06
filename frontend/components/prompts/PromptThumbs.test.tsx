import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
vi.mock('../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
const authState: { mediaToken: string | null } = { mediaToken: null };
vi.mock('../../contexts/AuthContext', () => ({ useOptionalAuth: () => authState }));
import { PromptThumbs } from './PromptThumbs';

describe('PromptThumbs', () => {
  it('renders a dashed placeholder when there is nothing to show', () => {
    render(<PromptThumbs thumbs={[]} testId="t" />);
    expect(screen.getByTestId('t')).toHaveAttribute('data-empty', 'true');
  });
  it('renders one image for one thumb', () => {
    render(<PromptThumbs thumbs={[{ url: '/api/v1/resources/1/cover', kind: 'image' }]} testId="t" />);
    expect(screen.getAllByRole('img')).toHaveLength(1);
    expect(screen.getByRole('img')).toHaveAttribute('src', 'https://api.test/api/v1/resources/1/cover');
  });
  it('stacks up to three and shows the count', () => {
    const thumbs = [1, 2, 3].map((i) => ({ url: `/api/v1/media/9/slides/00${i}.jpg`, kind: 'image' as const }));
    render(<PromptThumbs thumbs={thumbs} count={6} testId="t" />);
    expect(screen.getAllByRole('img')).toHaveLength(3);
    expect(screen.getByText('6')).toBeInTheDocument();
  });

  it('album slide thumbnails carry the session media token; covers do not', () => {
    authState.mediaToken = 'tok';
    render(
      <PromptThumbs
        thumbs={[
          { url: '/api/v1/media/9/slides/002.jpg', kind: 'image' },
          { url: '/api/v1/resources/1/cover', kind: 'image' },
        ]}
      />,
    );
    const srcs = screen.getAllByRole('img').map((el) => el.getAttribute('src'));
    expect(srcs).toEqual([
      'https://api.test/api/v1/media/9/slides/002.jpg?token=tok',
      'https://api.test/api/v1/resources/1/cover',
    ]);
    authState.mediaToken = null;
  });
});
