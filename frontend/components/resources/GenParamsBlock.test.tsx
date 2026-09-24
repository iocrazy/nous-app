import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { GenParamsBlock } from './GenParamsBlock';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));

// Mirrors what the extractor wrote for the real Krea2/ComfyUI PNG.
const COMFY = {
  tool: 'comfyui',
  model: 'Krea2-redcraft_fp8',
  sampler: 'euler',
  scheduler: 'simple',
  steps: 10,
  cfg: 1,
  denoise: 1,
  seed: 399257458555967,
  width: 1920,
  height: 1080,
  text_encoder: 'qwen3vl_4b_fp8_scaled',
  vae: 'qwen_image_vae',
};

describe('GenParamsBlock', () => {
  it('renders nothing without params', () => {
    const { container } = render(<GenParamsBlock params={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('renders nothing when every key is absent', () => {
    const { container } = render(<GenParamsBlock params={{}} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('shows a one-line summary collapsed, the full grid when opened', () => {
    render(<GenParamsBlock params={COMFY} />);
    const summary = screen.getByTestId('gen-params-summary');
    expect(summary.textContent).toBe('Krea2-redcraft_fp8 · 1920×1080 · 10 steps · cfg 1 · seed 399257458555967');
    expect(screen.queryByTestId('gen-params-grid')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: /Generation Params/ }));
    const grid = screen.getByTestId('gen-params-grid');
    expect(grid).toBeVisible();
    expect(grid.textContent).toContain('ComfyUI');
    expect(grid.textContent).toContain('qwen_image_vae');
    // Absent keys never render a row — no "Provider", no "LoRAs".
    expect(grid.textContent).not.toContain('Provider');
    expect(grid.textContent).not.toContain('LoRAs');
  });

  it('drops values of the wrong type from the open wire object', () => {
    // `gen_params` is JSONB — the resources API types it as an open object.
    render(<GenParamsBlock params={{ model: { nested: true }, steps: '10', cfg: 2, loras: ['a', 3] }} />);
    const summary = screen.getByTestId('gen-params-summary');
    expect(summary.textContent).toBe('cfg 2');
    fireEvent.click(screen.getByRole('button', { name: /Generation Params/ }));
    const grid = screen.getByTestId('gen-params-grid');
    expect(grid.textContent).not.toContain('[object Object]');
    expect(grid.textContent).toContain('LoRAs');
    expect(grid.textContent).not.toContain('Steps');
  });

  it('copies the seed to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<GenParamsBlock params={{ tool: 'a1111', seed: 42, loras: ['detail', 'ink'] }} />);
    fireEvent.click(screen.getByRole('button', { name: /Generation Params/ }));
    fireEvent.click(screen.getByRole('button', { name: 'Copy seed' }));
    expect(writeText).toHaveBeenCalledWith('42');
    expect(screen.getByTestId('gen-params-grid').textContent).toContain('detail, ink');
  });
});
