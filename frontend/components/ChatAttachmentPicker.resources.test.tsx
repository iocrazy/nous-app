/**
 * The user's actual complaint: a picked library asset used to be a tiptap
 * node inside the input line, so it ate a whole row and shoved the text
 * aside. It now stages where the image attachments already stage.
 *
 * These tests pin the three properties that make that true — the chip is
 * inside the attachment row, that row wraps, and the row exists even when
 * the only thing staged is a resource — plus the one that keeps the OTHER
 * consumer of this component (Todolist/IssueReplyBox) unaffected.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

// The upload pipeline pulls in supabase + Toast; this suite is about layout.
vi.mock('../hooks/useChatAttachmentUpload', () => ({
  useChatAttachmentUpload: () => ({ handleFiles: vi.fn(), uploading: false }),
}));

const taskManager = { current: null as { tasks: unknown[] } | null };
vi.mock('../hooks/useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => taskManager.current,
}));

import { ChatAttachmentPicker, type StagedAttachment } from './ChatAttachmentPicker';
import type { StagedResourceRef } from './chat/stagedResources';

const RESOURCE: StagedResourceRef = {
  resource_id: '339710259795355',
  name: 'pitch.mp4',
  kind: 'video',
  mime: 'video/mp4',
  scope: { type: 'personal', id: 'u' },
  thumbnail_url: '',
  transcript_status: 'completed',
  summary_status: 'completed',
};

const IMAGE: StagedAttachment = {
  kind: 'image',
  url: 'https://cdn.example.test/a.png',
  filename: 'shot.png',
  size_bytes: 2048,
  mime: 'image/png',
  resource_id: '77',
};

function renderPicker(over: Partial<React.ComponentProps<typeof ChatAttachmentPicker>> = {}) {
  const onChange = vi.fn();
  const onResourcesChange = vi.fn();
  render(
    <ChatAttachmentPicker
      attachments={[]}
      onChange={onChange}
      resources={[]}
      onResourcesChange={onResourcesChange}
      {...over}
    />,
  );
  return { onChange, onResourcesChange };
}

describe('staged resources in the attachment row', () => {
  beforeEach(() => {
    taskManager.current = null;
  });

  it('puts the resource chip inside the attachment row, not somewhere of its own', () => {
    renderPicker({ resources: [RESOURCE] });
    const row = screen.getByTestId('composer-attachment-row');
    expect(within(row).getByTestId('staged-resource-chip')).toBeVisible();
    expect(within(row).getByText('pitch.mp4')).toBeVisible();
  });

  it('lets the row wrap, so several assets fall onto a second line', () => {
    // "可以错行" — the user asked for this explicitly. A row without
    // flex-wrap would push the composer sideways instead.
    renderPicker({ resources: [RESOURCE] });
    expect(screen.getByTestId('composer-attachment-row').className).toContain('flex-wrap');
  });

  it('mixes resources and uploaded files in the SAME row', () => {
    renderPicker({ resources: [RESOURCE], attachments: [IMAGE] });
    const row = screen.getByTestId('composer-attachment-row');
    expect(within(row).getByTestId('staged-resource-chip')).toBeVisible();
    expect(within(row).getByText('shot.png')).toBeVisible();
  });

  it('shows the row for a resource alone, with no file staged', () => {
    // The early return that paints only the paperclip button reads
    // `attachments.length === 0`; forgetting the resources half here would
    // swallow the chip entirely — the asset would look like it never landed.
    renderPicker({ resources: [RESOURCE] });
    expect(screen.getByTestId('composer-attachment-row')).toBeVisible();
  });

  it('falls back to just the picker button when nothing is staged', () => {
    renderPicker();
    expect(screen.queryByTestId('composer-attachment-row')).toBeNull();
  });

  it('removes one asset without touching the others', () => {
    const second: StagedResourceRef = { ...RESOURCE, resource_id: '42', name: 'b.mp4' };
    const { onResourcesChange } = renderPicker({ resources: [RESOURCE, second] });

    const chip = screen.getAllByTestId('staged-resource-chip')[0];
    fireEvent.click(within(chip).getByLabelText('remove'));

    expect(onResourcesChange).toHaveBeenCalledWith([second]);
  });

  it('keeps the file chip removal on its own callback', () => {
    const { onChange, onResourcesChange } = renderPicker({
      resources: [RESOURCE],
      attachments: [IMAGE],
    });

    fireEvent.click(screen.getByTitle('chat.attachments.remove'));

    expect(onChange).toHaveBeenCalledWith([]);
    expect(onResourcesChange).not.toHaveBeenCalled();
  });

  it('shows the live processing dot from the Task Center', () => {
    // Same refinement the inline chip had — a staged asset is exactly when
    // the user wants to know whether the agent will be able to read it.
    taskManager.current = {
      tasks: [{
        task_type: 'ai_transcription',
        resource_id: 339710259795355,   // JSON number, as the router returns it
        status: 'in_progress',
        created_at: '2026-08-18T00:00:00Z',
      }],
    };
    renderPicker({ resources: [RESOURCE] });

    expect(screen.getByTestId('staged-resource-chip-status'))
      .toHaveAttribute('data-status', 'processing');
  });
});

describe('the other consumer (IssueReplyBox) is untouched', () => {
  it('renders no resource chips when the props are omitted entirely', () => {
    render(<ChatAttachmentPicker attachments={[IMAGE]} onChange={vi.fn()} />);
    expect(screen.queryByTestId('staged-resource-chip')).toBeNull();
    expect(screen.getByText('shot.png')).toBeVisible();
  });
});
