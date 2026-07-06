import { render, screen, within, cleanup, fireEvent } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ElementToolbar } from '../components/ElementToolbar';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

afterEach(cleanup);

describe('ElementToolbar', () => {
  it('renders 8 script slots in script mode', () => {
    render(
      <ElementToolbar
        mode="script"
        activeType="action"
        onSelectType={vi.fn()}
        onInsertScene={vi.fn()}
      />,
    );
    const bar = screen.getByRole('toolbar');
    expect(within(bar).getAllByRole('button')).toHaveLength(8);
  });

  it('renders 8 outline slots, all disabled, in outline mode', () => {
    render(
      <ElementToolbar
        mode="outline"
        activeType={null}
        onSelectType={vi.fn()}
        onInsertScene={vi.fn()}
      />,
    );
    const buttons = within(screen.getByRole('toolbar')).getAllByRole('button');
    expect(buttons).toHaveLength(8);
    buttons.forEach((b) => expect(b).toBeDisabled());
  });

  it('renders nothing in cover mode', () => {
    const { container } = render(
      <ElementToolbar
        mode="cover"
        activeType={null}
        onSelectType={vi.fn()}
        onInsertScene={vi.fn()}
      />,
    );
    expect(container.querySelector('[role="toolbar"]')).toBeNull();
  });

  it('calls onSelectType when a type slot is clicked', () => {
    const onSelectType = vi.fn();
    render(
      <ElementToolbar
        mode="script"
        activeType="action"
        onSelectType={onSelectType}
        onInsertScene={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /editor\.toolbarCharacter/ }));
    expect(onSelectType).toHaveBeenCalledWith('character');
  });

  it('calls onInsertScene when the Scene slot is clicked', () => {
    const onInsertScene = vi.fn();
    render(
      <ElementToolbar
        mode="script"
        activeType="action"
        onSelectType={vi.fn()}
        onInsertScene={onInsertScene}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /editor\.toolbarScene/ }));
    expect(onInsertScene).toHaveBeenCalledTimes(1);
  });

  it('highlights the slot matching the active element type', () => {
    render(
      <ElementToolbar
        mode="script"
        activeType="character"
        onSelectType={vi.fn()}
        onInsertScene={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: /editor\.toolbarCharacter/ })).toHaveClass('active');
    expect(screen.getByRole('button', { name: /editor\.toolbarAction/ })).not.toHaveClass('active');
  });

  it('supports roving tabindex with arrow keys', () => {
    render(
      <ElementToolbar
        mode="script"
        activeType="action"
        onSelectType={vi.fn()}
        onInsertScene={vi.fn()}
      />,
    );
    const buttons = within(screen.getByRole('toolbar')).getAllByRole('button');
    expect(buttons[0]).toHaveAttribute('tabindex', '0');
    expect(buttons[1]).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(buttons[0], { key: 'ArrowRight' });
    expect(buttons[1]).toHaveAttribute('tabindex', '0');
  });
});
