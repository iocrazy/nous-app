import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { PlaylistIsland } from './PlaylistIsland';

describe('PlaylistIsland', () => {
  it('renders the current track + COMING SOON', () => {
    render(<PlaylistIsland title="My Song" author="Artist" coverUrl={null} />);
    expect(screen.getByText('My Song')).toBeTruthy();
    expect(screen.getByText(/COMING SOON/i)).toBeTruthy();
  });
});
