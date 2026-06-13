import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { CometBack } from './CometBack';

describe('CometBack', () => {
  it('renders a button and fires onClick', () => {
    const onClick = vi.fn();
    render(<CometBack onClick={onClick} title="Back" />);
    const btn = screen.getByRole('button', { name: 'Back' });
    fireEvent.click(btn);
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
