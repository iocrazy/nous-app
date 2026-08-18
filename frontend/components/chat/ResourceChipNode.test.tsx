import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import type { NodeViewProps } from '@tiptap/react';
import { ResourceChipView, ResourceRefNode } from './ResourceChipNode';

interface JsonNode { type?: string; attrs?: Record<string, unknown>; content?: JsonNode[] }

// The chip reads task progress through the OPTIONAL hook so it survives the
// fullscreen editor routes, which mount the floating chat outside the
// TaskManagerProvider (RECON#18). Mocking the hook (rather than the context)
// keeps that seam explicit and dodges pulling supabase into a unit test.
const taskManager = { current: null as { tasks: unknown[] } | null };
vi.mock('../../hooks/useOptionalTaskManager', () => ({
  useOptionalTaskManager: () => taskManager.current,
}));

const DEFAULT_ATTRS = {
  resourceId: '77',
  name: 'pitch.mp4',
  kind: 'video',
  mime: 'video/mp4',
  scope: { type: 'personal', id: 'u' },
  thumbnailUrl: '/api/v1/resources/77/cover',
  transcriptStatus: 'none',
  summaryStatus: 'none',
};

function renderChip(attrs: Record<string, unknown> = {}, deleteNode = vi.fn()) {
  const props = {
    node: { attrs: { ...DEFAULT_ATTRS, ...attrs } },
    deleteNode,
  } as unknown as NodeViewProps;
  render(<ResourceChipView {...props} />);
  return deleteNode;
}

describe('ResourceChipView', () => {
  beforeEach(() => {
    taskManager.current = null;
    vi.stubEnv('VITE_API_URL', 'https://api.example.test');
  });
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('renders the resource name and removes on click', () => {
    const deleteNode = renderChip();
    expect(screen.getByText('pitch.mp4')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('remove'));
    expect(deleteNode).toHaveBeenCalled();
  });

  it('paints the cover thumbnail against the API base', () => {
    renderChip();
    const img = screen.getByTestId('resource-chip-thumb') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('https://api.example.test/api/v1/resources/77/cover');
  });

  it('falls back to the kind icon when there is no cover', () => {
    renderChip({ thumbnailUrl: '' });
    expect(screen.queryByTestId('resource-chip-thumb')).toBeNull();
    expect(screen.getByTestId('resource-chip-icon')).toBeInTheDocument();
  });

  it('uses the semantic agent token, never the retired indigo hues', () => {
    // K1 remapped the hue palette, so `indigo-*` no longer carries the
    // meaning it was picked for (CLAUDE.md UI rules, RECON#12).
    renderChip();
    const chip = screen.getByTestId('resource-chip');
    expect(chip.className).not.toMatch(/indigo/);
    expect(chip.className).toMatch(/agent/);
  });

  it('shows a processing dot from the insert-time status snapshot', () => {
    renderChip({ transcriptStatus: 'processing' });
    expect(screen.getByTestId('resource-chip-status').getAttribute('data-status')).toBe('processing');
  });

  it('shows an unprocessed dot when nothing has been generated yet', () => {
    renderChip();
    expect(screen.getByTestId('resource-chip-status').getAttribute('data-status')).toBe('unprocessed');
  });

  it('shows no dot once both steps are done', () => {
    renderChip({ transcriptStatus: 'completed', summaryStatus: 'completed' });
    expect(screen.queryByTestId('resource-chip-status')).toBeNull();
  });

  it('renders a legacy chip that predates the new attrs without a dot or a broken image', () => {
    // Chips already sitting in saved composer content have only the five
    // original attrs. They must keep rendering — tiptap hands them through
    // with whatever defaults the schema declares.
    render(
      <ResourceChipView
        {...({
          node: { attrs: { resourceId: '9', name: 'legacy.md', kind: 'doc', mime: 'text/markdown', scope: { type: 'personal', id: 'u' } } },
          deleteNode: vi.fn(),
        } as unknown as NodeViewProps)}
      />,
    );
    expect(screen.getByText('legacy.md')).toBeInTheDocument();
    expect(screen.queryByTestId('resource-chip-thumb')).toBeNull();
    expect(screen.queryByTestId('resource-chip-status')).toBeNull();
  });

  it('lets a live task override a stale snapshot', () => {
    taskManager.current = {
      tasks: [
        { task_type: 'ai_transcription', resource_id: '77', status: 'completed', created_at: '2026-08-17T02:00:00Z' },
      ],
    };
    renderChip({ transcriptStatus: 'none', summaryStatus: 'completed' });
    expect(screen.queryByTestId('resource-chip-status')).toBeNull();
  });

  it('keeps the snapshot when no TaskManagerProvider is mounted', () => {
    taskManager.current = null;
    renderChip({ transcriptStatus: 'processing' });
    expect(screen.getByTestId('resource-chip-status').getAttribute('data-status')).toBe('processing');
  });
});

describe('ResourceRefNode schema', () => {
  let editor: Editor | null = null;
  afterEach(() => {
    editor?.destroy();
    editor = null;
  });

  it('declares the new attrs with defaults so old documents still parse', () => {
    editor = new Editor({ extensions: [StarterKit, ResourceRefNode], content: '' });
    editor.commands.insertContent({
      type: 'resourceRef',
      attrs: { resourceId: '9', name: 'legacy.md', kind: 'doc' },
    });
    const doc = editor.getJSON() as JsonNode;
    const node = (doc.content ?? [])
      .flatMap((n) => n.content ?? [])
      .find((n) => n.type === 'resourceRef');
    expect(node?.attrs).toMatchObject({
      thumbnailUrl: '',
      transcriptStatus: '',
      summaryStatus: '',
    });
  });

  it('serialises without emoji', () => {
    editor = new Editor({ extensions: [StarterKit, ResourceRefNode], content: '' });
    editor.commands.insertContent({
      type: 'resourceRef',
      attrs: { resourceId: '9', name: 'legacy.md', kind: 'doc' },
    });
    expect(editor.getHTML()).not.toMatch(/\p{Extended_Pictographic}/u);
  });
});
