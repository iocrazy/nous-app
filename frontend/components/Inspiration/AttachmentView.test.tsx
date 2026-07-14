import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrlWithToken: (id: string, token?: string) =>
    `http://api.test/att/${id}${token ? `?token=${token}` : ''}`,
}));
vi.mock('../../contexts/AuthContext', () => ({
  useAuth: () => ({ mediaToken: 'tok' }),
}));

import { AttachmentView } from './AttachmentView';

const att = (id: string, mime: string, name: string) => ({
  id,
  mime,
  size_bytes: 1024,
  original_name: name,
});

describe('AttachmentView', () => {
  it('renders nothing for empty list', () => {
    const { container } = render(<AttachmentView attachments={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders images as token-authed thumbnails that open an in-place lightbox', () => {
    render(<AttachmentView attachments={[att('1', 'image/png', 'pic.png')]} />);
    const img = screen.getByRole('img');
    expect(img.getAttribute('src')).toBe('http://api.test/att/1?token=tok');
    // No navigation away: the thumbnail is a button, not a link.
    expect(img.closest('a')).toBeNull();
    fireEvent.click(img.closest('button')!);
    const dialog = screen.getByRole('dialog');
    expect(dialog).not.toBeNull();
    // Escape closes the lightbox in place.
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).toBeNull();
  });

  it('renders audio with native controls', () => {
    const { container } = render(
      <AttachmentView attachments={[att('2', 'audio/mpeg', 'memo.mp3')]} />,
    );
    expect(container.querySelector('audio')).not.toBeNull();
  });

  it('renders video with native controls', () => {
    const { container } = render(
      <AttachmentView attachments={[att('3', 'video/mp4', 'clip.mp4')]} />,
    );
    expect(container.querySelector('video')).not.toBeNull();
  });

  it('renders other files as a download chip with name and size', () => {
    render(
      <AttachmentView attachments={[att('4', 'application/pdf', 'report.pdf')]} />,
    );
    expect(screen.getByText('report.pdf')).toBeTruthy();
    expect(screen.getByText(/1(\.0)? KB/)).toBeTruthy();
  });
});
