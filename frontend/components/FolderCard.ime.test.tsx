/**
 * FolderCard.ime.test.tsx — IME composition guard for the inline rename input.
 *
 * Renaming a folder and pressing Enter commits the new name. When Enter commits
 * an IME composition (`nativeEvent.isComposing === true`) the rename must NOT
 * fire, so a Chinese folder name isn't truncated mid-composition. jsdom does not
 * forward `isComposing` through fireEvent options → dispatch a native event.
 */

import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { FolderCard } from './FolderCard';
import type { Folder } from '../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
  initReactI18next: { type: '3rdParty', init: () => {} },
}));

// The card renders a cover-url helper for previews; stub it so render is inert.
vi.mock('../services/resourceService', () => ({
  getResourceCoverUrl: () => '',
}));

const folder = {
  id: 'f1',
  name: 'My Folder',
  created_at: '2026-01-01',
} as unknown as Folder;

describe('FolderCard rename IME composition guard', () => {
  it('does NOT confirm the rename when Enter commits an IME composition', () => {
    const onRenameConfirm = vi.fn();
    render(
      <FolderCard
        folder={folder}
        viewMode="list"
        onClick={vi.fn()}
        renaming
        renameValue="新文件夹"
        onRenameConfirm={onRenameConfirm}
      />,
    );
    const input = screen.getByDisplayValue('新文件夹');

    const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
    Object.defineProperty(ev, 'isComposing', { value: true });
    input.dispatchEvent(ev);

    expect(onRenameConfirm).not.toHaveBeenCalled();
  });

  it('confirms the rename on a normal (non-composing) Enter', () => {
    const onRenameConfirm = vi.fn();
    render(
      <FolderCard
        folder={folder}
        viewMode="list"
        onClick={vi.fn()}
        renaming
        renameValue="新文件夹"
        onRenameConfirm={onRenameConfirm}
      />,
    );
    const input = screen.getByDisplayValue('新文件夹');
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(onRenameConfirm).toHaveBeenCalledOnce();
  });
});
