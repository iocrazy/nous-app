import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { HotwordChipInput, parseHotwords } from './HotwordChipInput';

describe('parseHotwords', () => {
  it('splits on commas and newlines, trims, drops empties', () => {
    expect(parseHotwords('krea2, DBOS\nSupabase, ,')).toEqual([
      'krea2',
      'DBOS',
      'Supabase',
    ]);
  });

  it('dedupes case-insensitively keeping first spelling', () => {
    expect(parseHotwords('DBOS, dbos, Krea')).toEqual(['DBOS', 'Krea']);
  });
});

describe('HotwordChipInput', () => {
  it('renders one chip per word with its own remove button', () => {
    const onChange = vi.fn();
    render(<HotwordChipInput value="krea2, DBOS" onChange={onChange} />);
    expect(screen.getByText('krea2')).toBeInTheDocument();
    expect(screen.getByText('DBOS')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Remove krea2'));
    expect(onChange).toHaveBeenCalledWith('DBOS');
  });

  it('adds a word on Enter and serializes comma-separated', () => {
    const onChange = vi.fn();
    render(<HotwordChipInput value="krea2" onChange={onChange} />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Supabase' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).toHaveBeenCalledWith('krea2, Supabase');
  });

  it('ignores duplicate adds', () => {
    const onChange = vi.fn();
    render(<HotwordChipInput value="krea2" onChange={onChange} />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'KREA2' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(onChange).not.toHaveBeenCalled();
  });

  it('removes last chip on Backspace in empty input', () => {
    const onChange = vi.fn();
    render(<HotwordChipInput value="krea2, DBOS" onChange={onChange} />);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Backspace' });
    expect(onChange).toHaveBeenCalledWith('krea2');
  });

  it('commits pending draft on blur', () => {
    const onChange = vi.fn();
    render(<HotwordChipInput value="" onChange={onChange} />);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: 'Supabase' } });
    fireEvent.blur(input);
    expect(onChange).toHaveBeenCalledWith('Supabase');
  });
});
