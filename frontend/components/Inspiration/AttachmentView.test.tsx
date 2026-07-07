import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../../services/inspirationService', () => ({
  attachmentUrl: (id: string) => `http://api.test/att/${id}`,
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

  it('renders images as thumbnails linking to source', () => {
    render(<AttachmentView attachments={[att('1', 'image/png', 'pic.png')]} />);
    const img = screen.getByRole('img');
    expect(img.getAttribute('src')).toBe('http://api.test/att/1');
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
