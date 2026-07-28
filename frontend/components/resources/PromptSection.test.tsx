import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PromptSection } from './PromptSection';
import type { Resource } from '../../types';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

// SendToCanvasModal only mounts once the button is clicked, but its module
// (and its transitive imports — react-router-dom, projectsService,
// canvasService, resourceService) load eagerly with PromptSection. Stub
// them so the un-clicked tests below stay hermetic and the clicked test
// gets a deterministic, empty project list.
const fetchProjects = vi.fn().mockResolvedValue([]);
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../services/projectsService', () => ({
  fetchProjects: (...a: unknown[]) => fetchProjects(...a),
}));
vi.mock('../../features/canvas-core/services/canvasService', () => ({
  listCanvases: vi.fn().mockResolvedValue([]),
}));
vi.mock('../../services/resourceService', () => ({
  getResourceCoverUrl: (id: string) => `https://api.test/cover/${id}`,
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
  onEnsureTriggerTag: vi.fn().mockResolvedValue(undefined),
  canGenerate: true, generating: false, onGenerate: noop,
  translating: false, onTranslate: noop,
  ...over,
});

describe('PromptSection', () => {
  it('shows collapsed preview card when prompt data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'masterpiece, 1girl' }) })} />);
    // Section header (Sparkles + "Prompt" caption) is always present.
    expect(screen.getByText('Prompt')).toBeTruthy();
    expect(screen.getByText(/masterpiece, 1girl/)).toBeTruthy();
    expect(screen.queryByPlaceholderText(/negative/i)).toBeNull(); // not expanded yet
  });

  it('shows section header + Add Prompt pill when no data and no trigger tag', () => {
    render(<PromptSection {...props()} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
    const pill = screen.getByText(/\+ Add Prompt/).closest('button');
    expect(pill).not.toBeNull();
    expect(pill?.className).toContain('border-dashed');
  });

  it('collapsed preview card shows only the negative row when only negative data exists', () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt_negative: 'lowres, bad hands' }) })} />);
    expect(screen.getByText('Prompt')).toBeTruthy();
    expect(screen.getByText(/lowres, bad hands/)).toBeTruthy();
    // No positive text was supplied, so no font-mono positive line renders.
    expect(document.querySelector('.font-mono.text-\\[11px\\]')).toBeNull();
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

  it('shows the Send to Canvas button only when the expanded view has prompt data', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'p' }) })} />);
    fireEvent.click(screen.getByText(/^p$/));
    expect(await screen.findByText('Send to Canvas')).toBeTruthy();
  });

  it('omits Send to Canvas when expanded with no prompt data yet (trigger-tag row)', () => {
    render(<PromptSection {...props()} />);
    fireEvent.click(screen.getByText(/\+ Add Prompt/));
    expect(screen.queryByText('Send to Canvas')).toBeNull();
  });

  it('clicking Send to Canvas opens the modal with the current lang side as positive', async () => {
    render(<PromptSection {...props({ resource: base({ gen_prompt: 'a hero shot' }) })} />);
    fireEvent.click(screen.getByText(/a hero shot/));
    fireEvent.click(await screen.findByText('Send to Canvas'));

    expect(await screen.findByText(/No projects yet/)).toBeTruthy();
    expect(fetchProjects).toHaveBeenCalled();
  });
});
