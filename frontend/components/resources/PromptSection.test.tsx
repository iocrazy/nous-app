import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptSection } from './PromptSection';
import type { Resource } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

const base = (over: Partial<Resource> = {}): Resource =>
  ({
    id: 'r1', filename: 'a.png', file_type: 'image',
    gen_prompt: null, gen_prompt_zh: null,
    gen_prompt_negative: null, gen_prompt_negative_zh: null,
    ...over,
  }) as unknown as Resource;

const noop = () => {};
const props = (over: Partial<Parameters<typeof PromptSection>[0]> = {}) => ({
  resource: base(), onPatch: noop,
  hasTriggerTag: false, onEnsureTriggerTag: vi.fn().mockResolvedValue(undefined),
  canGenerate: true, generating: false, onGenerate: noop,
  translating: false, onTranslate: noop,
  ...over,
});

describe('PromptSection', () => {
  it('shows collapsed preview when prompt data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'masterpiece, 1girl' }) })} />);
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull(); // not expanded yet
  });

  it('shows light Add Prompt entry when no data and no trigger tag', () => {
    render(<PromptSection {...props()} />);
    expect(screen.getByText(/Add Prompt/)).toBeTruthy();
    // The light variant is a bare text button, not the collapsed row —
    // the row's Sparkles-labeled "Prompt" caption must be absent here.
    expect(screen.queryByText('Prompt')).toBeNull();
  });

  it('shows collapsed row (not the light variant) when no data but hasTriggerTag is true', () => {
    render(<PromptSection {...props({ hasTriggerTag: true })} />);
    // Collapsed row variant: Sparkles-labeled "Prompt" caption + inline
    // "+ Add Prompt" text inside the row button.
    expect(screen.getByText('Prompt')).toBeTruthy();
    expect(screen.getByText(/\+ Add Prompt/)).toBeTruthy();
    // Distinguish from the bare light-text variant, which renders
    // "+ Add Prompt" as the button's own (only) label, not alongside a
    // separate "Prompt" caption.
    const row = screen.getByText('Prompt').closest('button');
    expect(row).not.toBeNull();
    expect(row?.className).toContain('border-ink-700/50');
  });

  it('expanding calls onEnsureTriggerTag and reveals both textareas', async () => {
    const p = props({ resource: base({ gen_prompt: 'pos text' }) });
    render(<PromptSection {...p} />);
    fireEvent.click(screen.getByText(/pos text/));
    expect(p.onEnsureTriggerTag).toHaveBeenCalledOnce();
    expect(await screen.findByDisplayValue('pos text')).toBeTruthy();
    expect(screen.getByPlaceholderText(/negative/i)).toBeTruthy();
  });

  it('negative blur patches gen_prompt_negative', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: 'lowres, bad hands' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative: 'lowres, bad hands' });
  });

  it('zh lang patches the _zh columns', async () => {
    const onPatch = vi.fn();
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }), onPatch })} />);
    fireEvent.click(screen.getByText(/^p$/));
    fireEvent.click(screen.getByText('中'));
    const neg = screen.getByPlaceholderText(/negative/i);
    fireEvent.change(neg, { target: { value: '低分辨率' } });
    fireEvent.blur(neg);
    expect(onPatch).toHaveBeenCalledWith({ gen_prompt_negative_zh: '低分辨率' });
  });

  it('defaults to zh when only gen_prompt_zh has content', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt_zh: '杰作, 1girl' }) })} />);
    fireEvent.click(screen.getByText(/杰作, 1girl/));
    expect(await screen.findByDisplayValue('杰作, 1girl')).toBeTruthy();
  });
});
