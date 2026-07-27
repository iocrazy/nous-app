/**
 * parseOptionsInput / formatOptionsInput (M3 PR-I §2) — pure comma-separated
 * options text <-> string[] conversion backing the Form tab's `select` row.
 */
import { describe, expect, it } from 'vitest';

import { formatOptionsInput, parseOptionsInput } from './formFieldOptions';

describe('parseOptionsInput', () => {
  it('splits a simple comma-separated list and trims whitespace', () => {
    expect(parseOptionsInput('a, b, c')).toEqual(['a', 'b', 'c']);
  });

  it('drops blank segments from leading/trailing/double commas', () => {
    expect(parseOptionsInput(',a,,b,')).toEqual(['a', 'b']);
  });

  it('drops whitespace-only segments', () => {
    expect(parseOptionsInput('a,   ,b')).toEqual(['a', 'b']);
  });

  it('returns an empty array for blank input', () => {
    expect(parseOptionsInput('')).toEqual([]);
    expect(parseOptionsInput('   ')).toEqual([]);
  });

  it('returns a single-element array for input with no comma', () => {
    expect(parseOptionsInput('solo')).toEqual(['solo']);
  });
});

describe('formatOptionsInput', () => {
  it('joins options with ", "', () => {
    expect(formatOptionsInput(['a', 'b', 'c'])).toBe('a, b, c');
  });

  it('renders an empty string for undefined or empty options', () => {
    expect(formatOptionsInput(undefined)).toBe('');
    expect(formatOptionsInput([])).toBe('');
  });

  it('round-trips through parseOptionsInput for a well-formed list', () => {
    const options = ['Red', 'Green', 'Blue'];
    expect(parseOptionsInput(formatOptionsInput(options))).toEqual(options);
  });
});
