import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ReviewStatusBadge } from './ReviewStatusBadge';

describe('ReviewStatusBadge', () => {
  it('labels a known status', () => {
    render(<ReviewStatusBadge status="needs_changes" />);
    expect(screen.getByText('Needs Changes')).toBeTruthy();
  });

  it('renders an unknown status as Pending instead of crashing', () => {
    // review_status.status has no CHECK constraint; the wire type is string.
    render(<ReviewStatusBadge status="legacy_value" />);
    expect(screen.getByText('Pending')).toBeTruthy();
  });
});
