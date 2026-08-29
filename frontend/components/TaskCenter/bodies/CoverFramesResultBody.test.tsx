import { cleanup, render, screen } from '@testing-library/react';
import React from 'react';
import { afterEach, describe, expect, it } from 'vitest';

import type { UnifiedTask } from '../../../contexts/TaskManagerContext';
import { CoverFramesResultBody } from './CoverFramesResultBody';

afterEach(cleanup);

const base = { id: 't1', task_type: 'cover_frames', status: 'completed', phase: 'completed', title: 'Cover frames' } as unknown as UnifiedTask;

describe('CoverFramesResultBody', () => {
  it('shows the sampled frames as pictures with their timestamps, not as JSON', () => {
    render(
      <CoverFramesResultBody
        task={{ ...base, metadata: { cover_frames: { source_resource_id: '900', candidates: [
          { index: 0, timestamp_seconds: 3, preview_data_url: 'data:image/jpeg;base64,AAA', preview_width: 240, preview_height: 427 },
          { index: 1, timestamp_seconds: 65, preview_data_url: 'data:image/jpeg;base64,BBB', preview_width: 240, preview_height: 427 },
        ] } } } as unknown as UnifiedTask}
      />,
    );
    const imgs = screen.getByTestId('cover-frames-grid').querySelectorAll('img');
    expect(imgs).toHaveLength(2);
    expect(imgs[0].getAttribute('src')).toBe('data:image/jpeg;base64,AAA');
    expect(screen.getByText('1:05')).toBeTruthy();
    expect(document.body.textContent).not.toContain('preview_data_url');
  });

  it('shows the server sentence when sampling failed', () => {
    render(<CoverFramesResultBody task={{ ...base, metadata: { cover_frames: { source_resource_id: '900', candidates: [], error: 'video too short' } } } as unknown as UnifiedTask} />);
    expect(screen.getByTestId('cover-frames-error').textContent).toContain('video too short');
  });
});
