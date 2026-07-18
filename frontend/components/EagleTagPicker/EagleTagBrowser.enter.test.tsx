import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent, screen } from '@testing-library/react';
import { EagleTagBrowser } from './EagleTagBrowser';
import type { Tag } from '../../types';

const tag = (over: Partial<Tag>): Tag => ({
  id: '1', name: 'copywriting', name_zh: '文案', color: null, icon: null,
  type: 'user', created_at: '2026-01-01', ...over,
});

const baseProps = {
  allTags: [tag({})],
  selectedIds: new Set<string>(),
  starredIds: [] as string[],
  settings: { sort: 'name', show_counts: false } as never,
  onToggleTag: vi.fn(),
  onToggleStar: vi.fn(),
  onUpdateSettings: vi.fn(),
  forceMobileLayout: false,
};

// jsdom's synthetic KeyboardEvent does not forward `isComposing` through
// fireEvent options in every version, so dispatch a native event when the
// composition flag needs to reach `e.nativeEvent.isComposing`.
const keyDownComposing = (input: HTMLElement) => {
  const ev = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
  Object.defineProperty(ev, 'isComposing', { value: true });
  input.dispatchEvent(ev);
};

describe('EagleTagBrowser enter-to-create', () => {
  it('does not create while IME composition is active', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '新标签' } });
    keyDownComposing(input);
    expect(onCreate).not.toHaveBeenCalled();
  });

  it('creates on Enter after composition ends', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '新标签' } });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).toHaveBeenCalledWith('新标签', expect.any(String));
  });

  it('treats a name_zh exact match as existing (no create row)', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '文案' } });
    expect(screen.queryByText(/Create "文案"/)).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).not.toHaveBeenCalled();
  });
});
