/**
 * AttachedComposerPanel — IC's full per-node panel (⑥). Run spawns a wired
 * prompt seeded with the panel values and dispatches it immediately.
 */

import { fireEvent, render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const rerunPrompt = vi.fn();
vi.mock('../regenerate', () => ({
  rerunPrompt: (...a: unknown[]) => rerunPrompt(...a),
  promptIdForOutput: () => null,
  regenerateForOutput: vi.fn(),
}));
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [
    { name: 'codex-image', display_name: 'GPT Image 2 (Codex)' },
  ],
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { AttachedComposerPanel } from './AttachedComposerPanel';

const URL_A = '/api/v1/generated-media/a.png';
const URL_B = '/api/v1/generated-media/b.png';

function seed(): void {
  useCanvasCoreStore.getState().reset();
  useCanvasCoreStore.setState({
    kind: 'smart',
    nodes: [
      {
        id: 'm1',
        type: 'media',
        position: { x: 0, y: 0 },
        data: { title: 'Media', items: [] },
      } as unknown as CanvasNode,
    ],
    connections: [],
    selection: [],
  });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('AttachedComposerPanel', () => {
  it('renders kind tabs, input chips, prompt, model/ratio/count and Run', () => {
    seed();
    render(
      <AttachedComposerPanel nodeId="m1" inputUrls={[URL_A, URL_B]} pinned />,
    );
    expect(screen.getByTestId('composer-kind-image')).toBeInTheDocument();
    expect(screen.getByTestId('composer-kind-video')).toBeInTheDocument();
    expect(screen.getAllByTestId('composer-input-thumb')).toHaveLength(2);
    expect(screen.getByTestId('composer-input-row').textContent).toContain(
      '2 inputs',
    );
    expect(screen.getByRole('textbox', { name: 'Attached prompt' })).toBeInTheDocument();
    expect(screen.getByTestId('pill-model')).toBeTruthy();
  });

  it('Run spawns a wired, seeded prompt and dispatches it', () => {
    seed();
    render(<AttachedComposerPanel nodeId="m1" inputUrls={[URL_A, URL_B]} pinned />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Attached prompt' }), {
      target: { value: 'make it rainy' },
    });
    // Second thumbnail becomes the source.
    fireEvent.click(screen.getAllByTestId('composer-input-thumb')[1]);
    fireEvent.click(screen.getByTestId('composer-run'));

    const s = useCanvasCoreStore.getState();
    const prompt = s.nodes.find(
      (n) => (n as { type: string }).type === 'prompt',
    ) as unknown as {
      id: string;
      data: { body: string; source_ref?: string; gen?: { kind: string } };
    };
    expect(prompt.data.body).toBe('make it rainy');
    expect(prompt.data.source_ref).toBe(URL_B);
    expect(prompt.data.gen?.kind).toBe('image');
    expect(
      s.connections.some(
        (c) => String(c.source) === 'm1' && String(c.target) === prompt.id,
      ),
    ).toBe(true);
    expect(rerunPrompt).toHaveBeenCalledWith(prompt.id);
  });

  it('video tab produces a video gen', () => {
    seed();
    render(<AttachedComposerPanel nodeId="m1" inputUrls={[URL_A]} pinned />);
    fireEvent.click(screen.getByTestId('composer-kind-video'));
    fireEvent.click(screen.getByTestId('composer-run'));
    const prompt = useCanvasCoreStore
      .getState()
      .nodes.find((n) => (n as { type: string }).type === 'prompt') as unknown as {
      data: { gen?: { kind: string } };
    };
    expect(prompt.data.gen?.kind).toBe('video');
  });

  it('read-only renders nothing', () => {
    seed();
    render(<AttachedComposerPanel nodeId="m1" inputUrls={[]} readOnly />);
    expect(screen.queryByTestId('attached-composer')).toBeNull();
  });
});

it('video Run carries video_mode + resolution picked in the duration panel', () => {
  seed();
  render(<AttachedComposerPanel nodeId="m1" inputUrls={[URL_A, URL_B]} pinned />);
  fireEvent.click(screen.getByTestId('composer-kind-video'));
  fireEvent.click(screen.getByTestId('pill-duration'));
  fireEvent.click(screen.getAllByTestId('video-resolution-option')[0]); // 720p
  fireEvent.click(screen.getByTestId('pill-duration'));
  fireEvent.click(screen.getByText('First & Last'));
  fireEvent.change(screen.getByRole('textbox', { name: 'Attached prompt' }), {
    target: { value: 'morph' },
  });
  fireEvent.click(screen.getByTestId('composer-run'));
  const s = useCanvasCoreStore.getState();
  const prompt = s.nodes.find(
    (n) => (n as { type: string }).type === 'prompt',
  ) as unknown as { data: { gen: Record<string, unknown> } };
  expect(prompt.data.gen.video_mode).toBe('frames');
  expect(prompt.data.gen.resolution).toBe('720p');
});
