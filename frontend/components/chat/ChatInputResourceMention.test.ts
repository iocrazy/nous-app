import { describe, it, expect, afterEach } from 'vitest';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { createResourceMentionExtension } from './ChatInputResourceMention';
import type { ResourceSearchResult } from '../../types';

// Real `/resources/search` row shape (Task 1 contract) — including the
// RELATIVE thumbnail path and both status columns.
const FAKE_ITEM: ResourceSearchResult = {
  id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
  scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
  thumbnail_url: null, transcript_status: null, summary_status: null,
};

const VIDEO_ITEM: ResourceSearchResult = {
  id: '77', name: 'pitch.mp4', kind: 'video', mime: 'video/mp4', size: 18000000,
  scope: { type: 'team', id: 't1' }, updated_at: '2026-05-20T00:00:00Z',
  thumbnail_url: '/api/v1/resources/77/cover',
  transcript_status: 'processing', summary_status: 'none',
};

interface JsonNode { type?: string; attrs?: Record<string, unknown>; content?: JsonNode[] }

function chipAttrs(editor: Editor): Record<string, unknown> | undefined {
  const doc = editor.getJSON() as JsonNode;
  return (doc.content ?? [])
    .flatMap((n) => n.content ?? [])
    .find((n) => n.type === 'resourceRef')?.attrs;
}

describe('ChatInputResourceMention', () => {
  // Track the editor so we can tear down its prosemirror EditorView. Without
  // destroy(), prosemirror-view's DOMObserver schedules a setTimeout flush that
  // fires AFTER the test (and after jsdom tears down `document`) →
  // "ReferenceError: document is not defined" unhandled error → vitest exits 1
  // even though every test passed (flaked CI on #438 / #446).
  let editor: Editor | null = null;

  afterEach(() => {
    editor?.destroy();
    editor = null;
  });

  function makeEditor(): Editor {
    editor = new Editor({
      extensions: [StarterKit, createResourceMentionExtension({ onPick: async () => null })],
      content: '',
    });
    return editor;
  }

  it('inserts a resourceRef node when commands.insertResourceRef is called', () => {
    const ed = makeEditor();
    (ed as any).commands.insertResourceRef(FAKE_ITEM);
    const json = ed.getJSON();
    const found = JSON.stringify(json).includes('"resourceRef"');
    expect(found).toBe(true);
  });

  it('renderText returns @name for resourceRef chips', () => {
    const ed = makeEditor();
    (ed as any).commands.insertResourceRef(FAKE_ITEM);
    const text = ed.getText();
    expect(text).toContain('story.md');
  });

  it('carries the thumbnail and status snapshot onto the chip', () => {
    const ed = makeEditor();
    (ed as any).commands.insertResourceRef(VIDEO_ITEM);
    expect(chipAttrs(ed)).toMatchObject({
      resourceId: '77',
      thumbnailUrl: '/api/v1/resources/77/cover',
      transcriptStatus: 'processing',
      summaryStatus: 'none',
    });
  });

  it('normalises the null/absent case to empty strings', () => {
    // The context-menu path (Task 4) hands over a resource that has no
    // search row behind it, so the enriched fields simply are not there.
    const ed = makeEditor();
    (ed as any).commands.insertResourceRef({
      id: '5', name: 'note.md', kind: 'doc', scope: { type: 'personal', id: 'u' },
    });
    expect(chipAttrs(ed)).toMatchObject({
      resourceId: '5',
      name: 'note.md',
      mime: '',
      thumbnailUrl: '',
      transcriptStatus: '',
      summaryStatus: '',
    });
  });
});
