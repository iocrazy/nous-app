import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import React from 'react';
import { SegmentedTypeFilter } from './SegmentedTypeFilter';

describe('SegmentedTypeFilter', () => {
  const buttons = () =>
    within(screen.getByTestId('segmented-type-filter')).getAllByRole('button');

  it('renders All + the five media types', () => {
    render(<SegmentedTypeFilter selected={[]} onChange={() => {}} />);
    expect(buttons()).toHaveLength(6); // All, image, video, audio, document, other
  });

  it('marks All as pressed when nothing is selected', () => {
    render(<SegmentedTypeFilter selected={[]} onChange={() => {}} />);
    const [all] = buttons();
    expect(all).toHaveAttribute('aria-pressed', 'true');
  });

  it('toggles a type on when clicked (multi-select add)', () => {
    const onChange = vi.fn();
    render(<SegmentedTypeFilter selected={['image']} onChange={onChange} />);
    // index 2 = video (order: All, image, video, audio, document, other)
    fireEvent.click(buttons()[2]);
    expect(onChange).toHaveBeenCalledWith(['image', 'video']);
  });

  it('toggles a selected type off without dropping the others (multi-select remove)', () => {
    const onChange = vi.fn();
    render(<SegmentedTypeFilter selected={['image', 'video']} onChange={onChange} />);
    fireEvent.click(buttons()[1]); // image
    expect(onChange).toHaveBeenCalledWith(['video']);
  });

  it('clears the selection when All is clicked', () => {
    const onChange = vi.fn();
    render(<SegmentedTypeFilter selected={['image', 'video']} onChange={onChange} />);
    fireEvent.click(buttons()[0]); // All
    expect(onChange).toHaveBeenCalledWith([]);
  });
});
