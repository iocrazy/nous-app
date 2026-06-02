import { describe, it, expect, afterEach } from 'vitest';
import { Editor } from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import { createResourceMentionExtension } from './ChatInputResourceMention';
import type { ResourceSearchResult } from '../../types';

const FAKE_ITEM: ResourceSearchResult = {
  id: '1', name: 'story.md', kind: 'doc', mime: 'text/markdown', size: 100,
  scope: { type: 'personal', id: 'u' }, updated_at: '2026-05-24T00:00:00Z',
  thumbnail_url: null,
};

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

  it('inserts a resourceRef node when commands.insertResourceRef is called', () => {
    editor = new Editor({
      extensions: [StarterKit, createResourceMentionExtension({ onPick: async () => null })],
      content: '',
    });
    (editor as any).commands.insertResourceRef(FAKE_ITEM);
    const json = editor.getJSON();
    const found = JSON.stringify(json).includes('"resourceRef"');
    expect(found).toBe(true);
  });

  it('renderText returns @name for resourceRef chips', () => {
    editor = new Editor({
      extensions: [StarterKit, createResourceMentionExtension({ onPick: async () => null })],
      content: '',
    });
    (editor as any).commands.insertResourceRef(FAKE_ITEM);
    const text = editor.getText();
    expect(text).toContain('story.md');
  });
});
