/**
 * CanvasGenResultBody (②-5): renders the durable result from task metadata
 * — image vs video by kind, prompt subtitle, fan-out position, and the
 * Open Canvas jump. No resource fetch involved.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));

import { CanvasGenResultBody } from './CanvasGenResultBody';
import type { UnifiedTask } from '../../../contexts/TaskManagerContext';

afterEach(() => cleanup());

const task = (metadata: Record<string, unknown>, extra: Partial<UnifiedTask> = {}): UnifiedTask =>
  ({
    id: 't1',
    task_type: 'canvas_gen',
    status: 'completed',
    subtitle: 'a lighthouse at dawn',
    metadata,
    ...extra,
  }) as unknown as UnifiedTask;

describe('CanvasGenResultBody', () => {
  it('renders an image result with the canvas jump — the prompt is NOT repeated here', () => {
    // Reading order in the modal is description → error → result; the prompt
    // and "Item i / n" live in TaskDescriptionBlock above every body, so the
    // body only shows the result. Repeating them here put the prompt twice.
    render(
      <CanvasGenResultBody
        task={task({ result_url: '/gm/1/cover', canvas_id: '9', kind: 'image', index: 2, count: 4 })}
      />,
    );
    expect(screen.getByRole('img')).toHaveProperty('src', expect.stringContaining('/gm/1/cover') as never);
    expect(screen.queryByText(/a lighthouse at dawn/)).toBeNull();
    expect(screen.queryByText('Item 2 / 4')).toBeNull();
    expect((screen.getByText('Open Canvas') as HTMLAnchorElement).href).toContain('/canvas/9');
  });

  it('renders a video element for video kind', () => {
    const { container } = render(
      <CanvasGenResultBody task={task({ result_url: '/gm/2/stream', kind: 'video' })} />,
    );
    expect(container.querySelector('video')?.src).toContain('/gm/2/stream');
  });

  it('shows the pending placeholder while running', () => {
    render(<CanvasGenResultBody task={task({}, )} />);
    expect(screen.getByText(/No result attached|Result appears here/)).toBeTruthy();
  });
});


describe('CanvasGenResultBody — result_url host', () => {
  it('prefixes an API-relative result_url with the API origin', () => {
    const { container } = render(
      <CanvasGenResultBody task={{ id: 't', task_type: 'cover_gen', status: 'completed', phase: 'completed', title: 'AI cover', metadata: { result_url: '/api/v1/generated-media/1/cover' } } as unknown as UnifiedTask} />,
    );
    expect(container.querySelector('img')?.getAttribute('src')).toBe('https://api.test/api/v1/generated-media/1/cover');
  });
});
