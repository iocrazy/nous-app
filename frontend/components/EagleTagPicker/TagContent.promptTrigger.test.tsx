import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { TagContent } from './TagContent';
import type { Tag } from '../../types';

// Mock unifiedTagService
vi.mock('../../services/unifiedTagService', () => ({
  updateTag: vi.fn(),
}));

const tag = (over: Partial<Tag>): Tag => ({
  id: '1',
  name: 'test-tag',
  name_zh: '测试',
  color: null,
  icon: null,
  type: 'user',
  created_at: '2026-01-01',
  prompt_trigger: false,
  ...over,
});

const baseProps = {
  allTags: [],
  selectedIds: new Set<string>(),
  starredIds: [] as string[],
  settings: {
    columnWidth: 'medium' as const,
    layout: 'grid' as const,
    sort: 'name' as const,
    showStarred: false,
    showRecently: false,
    showRecommended: false,
    showCount: false,
  },
  selectedGroup: null,
  search: '',
  onToggleTag: vi.fn(),
  onToggleStar: vi.fn(),
};

describe('TagContent prompt trigger context menu', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('shows "Show Prompt Panel" menu item for user tags', async () => {
    const userTag = tag({ type: 'user' });
    render(<TagContent {...baseProps} allTags={[userTag]} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Menu should appear with the prompt panel option
    expect(screen.getByText('Show Prompt Panel')).toBeInTheDocument();
  });

  it('does not show "Show Prompt Panel" menu item for system tags', async () => {
    const systemTag = tag({ type: 'system' });
    render(<TagContent {...baseProps} allTags={[systemTag]} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Menu should NOT have the prompt panel option
    expect(screen.queryByText('Show Prompt Panel')).not.toBeInTheDocument();
  });

  it('does not show "Show Prompt Panel" menu item for time tags', async () => {
    const timeTag = tag({ type: 'time' });
    render(<TagContent {...baseProps} allTags={[timeTag]} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Menu should NOT have the prompt panel option
    expect(screen.queryByText('Show Prompt Panel')).not.toBeInTheDocument();
  });

  it('shows checkmark when prompt_trigger is true', async () => {
    const userTag = tag({ type: 'user', prompt_trigger: true });
    render(<TagContent {...baseProps} allTags={[userTag]} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Menu item should have checkmark class or icon
    const menuItem = screen.getByText('Show Prompt Panel').closest('button');
    expect(menuItem).toHaveClass('text-[var(--accent-text)]');
  });

  it('calls updateTag with toggled prompt_trigger when clicked', async () => {
    const { updateTag } = await import('../../services/unifiedTagService');
    (updateTag as any).mockResolvedValue({ ...tag({ type: 'user' }), prompt_trigger: true });

    const userTag = tag({ type: 'user', prompt_trigger: false });
    const onToggleTag = vi.fn();
    render(<TagContent {...baseProps} allTags={[userTag]} onToggleTag={onToggleTag} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Click the "Show Prompt Panel" menu item
    const menuItem = screen.getByText('Show Prompt Panel');
    fireEvent.click(menuItem);

    // updateTag should be called with the tag ID and toggled prompt_trigger
    expect(updateTag).toHaveBeenCalledWith('1', { prompt_trigger: true });
  });

  it('toggles prompt_trigger from true to false', async () => {
    const { updateTag } = await import('../../services/unifiedTagService');
    (updateTag as any).mockResolvedValue({ ...tag({ type: 'user' }), prompt_trigger: false });

    const userTag = tag({ type: 'user', prompt_trigger: true });
    const onToggleTag = vi.fn();
    render(<TagContent {...baseProps} allTags={[userTag]} onToggleTag={onToggleTag} />);

    // Right-click on the tag button
    const tagButton = screen.getByRole('button', { name: /test-tag/ });
    fireEvent.contextMenu(tagButton, { clientX: 100, clientY: 100 });

    // Click the "Show Prompt Panel" menu item
    const menuItem = screen.getByText('Show Prompt Panel');
    fireEvent.click(menuItem);

    // updateTag should be called with prompt_trigger toggled to false
    expect(updateTag).toHaveBeenCalledWith('1', { prompt_trigger: false });
  });
});
