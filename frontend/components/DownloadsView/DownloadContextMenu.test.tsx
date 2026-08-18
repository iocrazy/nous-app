/**
 * My Downloads' right-click menu.
 *
 * This menu is a second, independent implementation from the resource
 * library's (`useContextMenuItems`) — which is how "Send to Agent" shipped
 * to one of them and not the other, hiding the feature from the view that
 * holds most of the user's media. The test exists to make that omission
 * fail loudly rather than silently.
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, def?: string) => def ?? _k }),
}));

import { DownloadContextMenu } from './DownloadContextMenu';
import type { Video } from '../../types';

const video = { id: '900001', platform_id: 'abc', title: 'Clip' } as unknown as Video;

function renderMenu(over: Record<string, unknown> = {}) {
  const handlers = {
    onClose: vi.fn(),
    onViewDetails: vi.fn(),
    onOpenNewTab: vi.fn(),
    onDownloadVideo: vi.fn(),
    onDownloadAudio: vi.fn(),
    onSendToAgent: vi.fn(),
    onRename: vi.fn(),
    onShare: vi.fn(),
    onCopyLink: vi.fn(),
    onDelete: vi.fn(),
    onNavigate: vi.fn(),
  };
  render(
    <DownloadContextMenu
      contextMenu={{ x: 10, y: 10, video }}
      {...handlers}
      {...over}
    />,
  );
  return handlers;
}

describe('DownloadContextMenu', () => {
  it('offers Send to Agent', () => {
    renderMenu();
    expect(screen.getByText('Send to Agent')).toBeInTheDocument();
  });

  it('calls onSendToAgent when it is clicked', () => {
    const handlers = renderMenu();
    fireEvent.click(screen.getByText('Send to Agent'));
    expect(handlers.onSendToAgent).toHaveBeenCalledTimes(1);
  });

  it('renders nothing when the menu is closed', () => {
    renderMenu({ contextMenu: null });
    expect(screen.queryByText('Send to Agent')).toBeNull();
  });

  it('uses no emoji in its labels', () => {
    renderMenu();
    expect(document.body.textContent ?? '').not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
