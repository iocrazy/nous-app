/**
 * AttachedComposerPanel — @-mention for the node's own input images.
 *
 * The panel already shows the node's images as a chip row ("N inputs"), but
 * its prompt box had no way to REFER to one of them. Typing @ now offers
 * them, and picking one drops an inline chip carrying the thumbnail — IC's
 * mention token, which is also what makes the reference visible in the text
 * rather than hidden in a side field.
 *
 * The library tab is deliberately absent here: the asset library is being
 * reworked, and input images are the ones that cost nothing to mention
 * because the node already owns them.
 */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const rerunPrompt = vi.fn();
const createPromptFromNode = vi.fn((_nodeId: string, _seed: unknown) => 'p-new');
vi.mock('../regenerate', () => ({
  rerunPrompt: (...a: unknown[]) => rerunPrompt(...a),
  promptIdForOutput: () => null,
  regenerateForOutput: vi.fn(),
}));
vi.mock('../recreate', () => ({
  createPromptFromNode: (nodeId: string, seed: unknown) =>
    createPromptFromNode(nodeId, seed),
}));
vi.mock('./useGenerationModels', () => ({
  useGenerationModels: () => [{ name: 'codex-image', display_name: 'GPT Image 2' }],
}));

import { useCanvasCoreStore } from '../../store/canvasCoreStore';
import type { CanvasNode } from '../../types';
import { AttachedComposerPanel } from './AttachedComposerPanel';

const URL_A = '/api/v1/generated-media/11/cover';
const URL_B = '/api/v1/generated-media/22/cover';

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

function renderPanel(inputUrls: string[] = [URL_A, URL_B]) {
  return render(<AttachedComposerPanel nodeId="m1" inputUrls={inputUrls} pinned />);
}

/** Type '@' the way the editor sees it. */
function typeAt() {
  fireEvent.keyDown(screen.getByTestId('attached-prompt-editor'), { key: '@' });
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  useCanvasCoreStore.getState().reset();
});

describe('AttachedComposerPanel — @ input images', () => {
  it('offers the node images when @ is typed', async () => {
    seed();
    renderPanel();
    typeAt();

    const options = await screen.findAllByTestId('mention-image-option');
    expect(options).toHaveLength(2);
    expect(options[0].querySelector('img')?.getAttribute('src')).toContain('11');
  });

  it('labels the candidates positionally, as the input row does', async () => {
    seed();
    renderPanel();
    typeAt();

    const options = await screen.findAllByTestId('mention-image-option');
    expect(options[0]).toHaveTextContent('Image 1');
    expect(options[1]).toHaveTextContent('Image 2');
  });

  it('shows nothing to pick when the node has no images', async () => {
    seed();
    renderPanel([]);
    typeAt();

    await waitFor(() => {
      expect(screen.queryAllByTestId('mention-image-option')).toHaveLength(0);
    });
  });

  it('picking an image drops a chip into the prompt', async () => {
    seed();
    renderPanel();
    typeAt();

    fireEvent.mouseDown((await screen.findAllByTestId('mention-image-option'))[1]);

    const chip = await screen.findByTestId('prompt-image-chip');
    expect(chip).toHaveTextContent('Image 2');
    expect(chip.querySelector('img')?.getAttribute('src')).toContain('22');
  });

  it('a picked image becomes the i2i source', async () => {
    seed();
    renderPanel();
    typeAt();
    fireEvent.mouseDown((await screen.findAllByTestId('mention-image-option'))[1]);
    await screen.findByTestId('prompt-image-chip');

    fireEvent.click(screen.getByTestId('composer-run'));
    await waitFor(() => {
      expect(createPromptFromNode).toHaveBeenCalledWith(
        'm1',
        expect.objectContaining({ source_ref: URL_B }),
      );
    });
  });

  it('Run carries the chip through as @alias in the prompt body', async () => {
    seed();
    renderPanel();
    typeAt();
    fireEvent.mouseDown((await screen.findAllByTestId('mention-image-option'))[0]);
    await screen.findByTestId('prompt-image-chip');

    fireEvent.click(screen.getByTestId('composer-run'));
    await waitFor(() => {
      expect(createPromptFromNode).toHaveBeenCalledWith(
        'm1',
        expect.objectContaining({ body: expect.stringContaining('@Image 1') }),
      );
    });
  });

  it('removing the chip takes it back out of the body', async () => {
    seed();
    renderPanel();
    typeAt();
    fireEvent.mouseDown((await screen.findAllByTestId('mention-image-option'))[0]);
    await screen.findByTestId('prompt-image-chip');

    fireEvent.click(screen.getByRole('button', { name: /remove image 1/i }));
    await waitFor(() => expect(screen.queryByTestId('prompt-image-chip')).toBeNull());

    fireEvent.click(screen.getByTestId('composer-run'));
    await waitFor(() => {
      expect(createPromptFromNode).toHaveBeenCalledWith(
        'm1',
        expect.objectContaining({ body: expect.not.stringContaining('@Image 1') }),
      );
    });
  });

  it('keeps the prompt editable and typed text intact', async () => {
    seed();
    renderPanel();
    const editor = screen.getByTestId('attached-prompt-editor');
    expect(editor.getAttribute('contenteditable')).toBe('true');
    // React Flow must keep its hands off the editor inside a canvas node.
    expect(editor.classList.contains('nodrag')).toBe(true);
    expect(editor.classList.contains('nowheel')).toBe(true);
  });
});
