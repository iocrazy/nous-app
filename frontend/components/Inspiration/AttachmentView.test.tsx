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
  // ── onDelete (edit-mode affordance) ──────────────────────────────────────
  // The remove buttons only exist when a caller opts in. Read-only consumers
  // (NoteCard) pass no onDelete and must keep their exact current DOM.

  it('renders no remove buttons when onDelete is omitted', () => {
    render(
      <AttachmentView
        attachments={[
          att('1', 'image/png', 'pic.png'),
          att('2', 'audio/mpeg', 'memo.mp3'),
          att('3', 'video/mp4', 'clip.mp4'),
          att('4', 'application/pdf', 'report.pdf'),
        ]}
      />,
    );
    expect(screen.queryByLabelText(/^Remove /)).toBeNull();
  });

  it('offers a remove button for every mime family when onDelete is given', () => {
    const onDelete = vi.fn();
    render(
      <AttachmentView
        attachments={[
          att('1', 'image/png', 'pic.png'),
          att('2', 'audio/mpeg', 'memo.mp3'),
          att('3', 'video/mp4', 'clip.mp4'),
          att('4', 'application/pdf', 'report.pdf'),
        ]}
        onDelete={onDelete}
      />,
    );
    for (const name of ['pic.png', 'memo.mp3', 'clip.mp4', 'report.pdf']) {
      expect(screen.getByLabelText(`Remove ${name}`)).toBeTruthy();
    }
  });

  it('clicking remove hands the whole attachment back to the caller', () => {
    const onDelete = vi.fn();
    render(
      <AttachmentView attachments={[att('4', 'application/pdf', 'report.pdf')]} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByLabelText('Remove report.pdf'));
    expect(onDelete).toHaveBeenCalledWith(
      expect.objectContaining({ id: '4', original_name: 'report.pdf' }),
    );
  });

  it('removing an image does not also open the lightbox', () => {
    const onDelete = vi.fn();
    render(
      <AttachmentView attachments={[att('1', 'image/png', 'pic.png')]} onDelete={onDelete} />,
    );
    fireEvent.click(screen.getByLabelText('Remove pic.png'));
    expect(onDelete).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog')).toBeNull();
  });
  it('marks the lightbox so a host modal can yield Escape to it', () => {
    render(<AttachmentView attachments={[att('1', 'image/png', 'pic.png')]} />);
    fireEvent.click(screen.getByRole('img').closest('button')!);
    // Cross-component contract: the lightbox stacks ABOVE any modal that
    // embeds this view, and both listen for Escape on window. The host reads
    // this attribute to decide the topmost layer wins. Renaming/removing it
    // silently gives one Escape press two effects.
    expect(document.querySelector('[data-lightbox="attachment"]')).not.toBeNull();
  });
});
