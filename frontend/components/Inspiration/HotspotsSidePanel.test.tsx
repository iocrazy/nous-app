import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (_k: string, f: string) => f }) }));

import { HotspotsSidePanel } from './HotspotsSidePanel';

const HS = (id: string, over = {}) => ({ id, title: `T${id}`, tags: [], heat: 90 - Number(id), source_label: 'WEIBO', ...over });

describe('HotspotsSidePanel', () => {
  it('shows top 3 ranked rows from the hotspots prop', () => {
    render(
      <HotspotsSidePanel
        hotspots={[HS('1'), HS('2'), HS('3'), HS('4')]}
        day={null}
        onSaveAsNote={vi.fn()}
        onOpenAll={vi.fn()}
      />,
    );
    expect(screen.getByText('T1')).toBeTruthy();
    expect(screen.getByText('T3')).toBeTruthy();
    expect(screen.queryByText('T4')).toBeNull();
  });

  it('+ button saves the row as a note', () => {
    const onSaveAsNote = vi.fn();
    render(
      <HotspotsSidePanel
        hotspots={[HS('1'), HS('2'), HS('3')]}
        day={null}
        onSaveAsNote={onSaveAsNote}
        onOpenAll={vi.fn()}
      />,
    );
    fireEvent.click(screen.getAllByLabelText('Save as note')[0]);
    expect(onSaveAsNote).toHaveBeenCalledWith(expect.objectContaining({ id: '1' }));
  });

  it('All hotspots link opens the tab', () => {
    const onOpenAll = vi.fn();
    render(
      <HotspotsSidePanel
        hotspots={[HS('1'), HS('2'), HS('3')]}
        day={null}
        onSaveAsNote={vi.fn()}
        onOpenAll={onOpenAll}
      />,
    );
    fireEvent.click(screen.getByText(/all hotspots/i));
    expect(onOpenAll).toHaveBeenCalled();
  });

  it('hides already-hidden hotspots from the top-3 ranking', () => {
    render(
      <HotspotsSidePanel
        hotspots={[HS('1', { is_hidden: true }), HS('2'), HS('3'), HS('4')]}
        day={null}
        onSaveAsNote={vi.fn()}
        onOpenAll={vi.fn()}
      />,
    );
    expect(screen.queryByText('T1')).toBeNull();
    expect(screen.getByText('T4')).toBeTruthy();
  });

  it('empty hotspots list renders nothing', () => {
    const { container } = render(
      <HotspotsSidePanel hotspots={[]} day={null} onSaveAsNote={vi.fn()} onOpenAll={vi.fn()} />,
    );
    expect(container.firstChild).toBeNull();
  });
});
