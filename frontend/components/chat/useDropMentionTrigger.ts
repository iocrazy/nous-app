import { useCallback } from 'react';
import type { RefObject } from 'react';
import type { Editor } from '@tiptap/core';

/**
 * Delete the "@query" the writer typed to open the mention picker.
 *
 * Mandatory on EVERY tab of the picker, whatever it does with the pick:
 *
 * - assets / outputs STAGE above the composer, so the typed text stands for
 *   nothing and is POSTED as message body — an issue reply whose body was just
 *   "@" (真机验收 run 348429859900467);
 * - the resource tab inserts a chip, but `insertResourceRef` is a bare
 *   `insertContent` AT THE CARET that replaces nothing, so the query survives
 *   next to the chip ("hello @me@story.md").
 *
 * ⚠️ Callers that also insert must call this FIRST and insert second. Run
 * afterwards, the scan starts from a caret behind the fresh chip, reads the
 * chip's own "@name" as the query and deletes what was just inserted.
 *
 * Shared by the two composers that both grew the same staging tabs
 * (`AIChatPanel` and `Todolist/IssueReplyBox`) so they cannot disagree about
 * when the query goes away. The deletion itself lives in the tiptap command
 * `removeMentionTrigger` (`ChatInputResourceMention.ts`) — this is only the
 * hook that reaches it through the editor ref.
 *
 * The optional chaining is not defensive noise: the ref is null until the
 * editor mounts, and the command is absent from a composer wired without
 * `createResourceMentionExtension`.
 */
export function useDropMentionTrigger(
  editorRef: RefObject<Editor | null>,
): () => void {
  return useCallback(() => {
    (editorRef.current?.commands as unknown as {
      removeMentionTrigger?: () => boolean;
    } | undefined)?.removeMentionTrigger?.();
  }, [editorRef]);
}
