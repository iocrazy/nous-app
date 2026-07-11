/**
 * CanvasGenResultBody (②-5): renders the durable result from task metadata
 * — image vs video by kind, prompt subtitle, fan-out position, and the
 * Open Canvas jump. No resource fetch involved.
 */
import { render, screen, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';

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
  it('renders an image result with prompt and canvas jump', () => {
    render(
      <CanvasGenResultBody
        task={task({ result_url: '/gm/1/cover', canvas_id: '9', kind: 'image', index: 2, count: 4 })}
      />,
    );
    expect(screen.getByRole('img')).toHaveProperty('src', expect.stringContaining('/gm/1/cover') as never);
    expect(screen.getByText(/a lighthouse at dawn/)).toBeTruthy();
    expect(screen.getByText('Item 2 / 4')).toBeTruthy();
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
