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

  describe('removeMentionTrigger', () => {
    // Picking an asset now stages it ABOVE the composer, so the "@query"
    // the user typed to open the picker is left behind with nothing to
    // stand for. Unremoved, it is sent as message body.
    function typed(text: string): Editor {
      const ed = makeEditor();
      ed.commands.setContent(`<p>${text}</p>`);
      ed.commands.focus('end');
      return ed;
    }

    it('deletes the @query the picker was opened with', () => {
      const ed = typed('look at @pitch');
      expect((ed as any).commands.removeMentionTrigger()).toBe(true);
      expect(ed.getText()).toBe('look at ');
    });

    it('deletes a bare @ with nothing typed after it', () => {
      const ed = typed('hello @');
      (ed as any).commands.removeMentionTrigger();
      expect(ed.getText()).toBe('hello ');
    });

    it('leaves prose alone once the query has whitespace in it', () => {
      // "@ " is no longer a picker session — it is just an at sign.
      const ed = typed('email me @ home');
      expect((ed as any).commands.removeMentionTrigger()).toBe(false);
      expect(ed.getText()).toBe('email me @ home');
    });

    it('does nothing when there is no @ at all', () => {
      const ed = typed('plain text');
      expect((ed as any).commands.removeMentionTrigger()).toBe(false);
      expect(ed.getText()).toBe('plain text');
    });

    it('deletes only the query, never back into an existing chip', () => {
      // A chip renders as "@pitch.mp4" in getText() but occupies ONE
      // position, and it carries an "@" of its own. A delete range measured
      // from the wrong origin would swallow the chip along with the query.
      const ed = makeEditor();
      ed.commands.setContent('<p>start</p>');
      ed.commands.focus('end');
      (ed as any).commands.insertResourceRef(VIDEO_ITEM);
      ed.commands.insertContent(' then @pit');
      ed.commands.focus('end');

      (ed as any).commands.removeMentionTrigger();

      expect(ed.getText()).toBe('start@pitch.mp4 then ');
      expect(JSON.stringify(ed.getJSON())).toContain('"resourceRef"');
    });

    it('does not reach into an earlier paragraph for its @', () => {
      // The scan is confined to the caret's own text block. Scanning the
      // whole document would find the "@" one paragraph up and delete the
      // paragraph break plus everything between.
      const ed = makeEditor();
      ed.commands.setContent('<p>ping @alice</p><p>second line</p>');
      ed.commands.focus('end');

      expect((ed as any).commands.removeMentionTrigger()).toBe(false);
      expect(ed.getText()).toContain('ping @alice');
      expect(ed.getText()).toContain('second line');
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
