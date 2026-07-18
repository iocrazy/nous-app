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

describe('EagleTagBrowser hidden shadow tags', () => {
  const shadow = tag({ id: '99', name: 'project-x', name_zh: '项目', origin: 'note' });

  it('exact shadow name → no Create row; Enter toggles the shadow tag (not create)', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    const onToggleTag = vi.fn();
    render(
      <EagleTagBrowser
        {...baseProps}
        onToggleTag={onToggleTag}
        onCreate={onCreate}
        shadowTags={[shadow]}
      />,
    );
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: 'project-x' } });
    expect(screen.queryByText(/Create "project-x"/)).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).not.toHaveBeenCalled();
    expect(onToggleTag).toHaveBeenCalledWith('99');
  });

  it('a name_zh exact shadow match also toggles instead of creating', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    const onToggleTag = vi.fn();
    render(
      <EagleTagBrowser
        {...baseProps}
        onToggleTag={onToggleTag}
        onCreate={onCreate}
        shadowTags={[shadow]}
      />,
    );
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: '项目' } });
    expect(screen.queryByText(/Create "项目"/)).toBeNull();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).not.toHaveBeenCalled();
    expect(onToggleTag).toHaveBeenCalledWith('99');
  });

  it('partial search reveals the shadow tag; empty search hides it', () => {
    render(<EagleTagBrowser {...baseProps} shadowTags={[shadow]} />);
    const input = screen.getByPlaceholderText('Search tags...');
    // Default (empty search): shadow tag is hidden.
    expect(screen.queryByText('project-x')).toBeNull();
    // Partial match reveals it.
    fireEvent.change(input, { target: { value: 'proj' } });
    expect(screen.getByText('project-x')).toBeInTheDocument();
    // Clearing the search hides it again.
    fireEvent.change(input, { target: { value: '' } });
    expect(screen.queryByText('project-x')).toBeNull();
  });

  it('clicking a revealed shadow tag toggles it', () => {
    const onToggleTag = vi.fn();
    render(<EagleTagBrowser {...baseProps} onToggleTag={onToggleTag} shadowTags={[shadow]} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: 'project' } });
    fireEvent.click(screen.getByText('project-x'));
    expect(onToggleTag).toHaveBeenCalledWith('99');
  });

  it('unrelated new word still offers Create and Enter still creates (PR-0 regression)', () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<EagleTagBrowser {...baseProps} onCreate={onCreate} shadowTags={[shadow]} />);
    const input = screen.getByPlaceholderText('Search tags...');
    fireEvent.change(input, { target: { value: 'brandnew' } });
    expect(screen.getByText(/Create "brandnew"/)).toBeInTheDocument();
    fireEvent.keyDown(input, { key: 'Enter', isComposing: false });
    expect(onCreate).toHaveBeenCalledWith('brandnew', expect.any(String));
  });
});
