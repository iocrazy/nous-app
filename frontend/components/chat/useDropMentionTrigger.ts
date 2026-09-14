import { useCallback } from 'react';
import type { RefObject } from 'react';
import type { Editor } from '@tiptap/core';

/**
 * Delete the "@query" the writer typed to open the mention picker.
 *
 * Mandatory on every picker tab that STAGES its pick above the composer
 * (assets, outputs) instead of inserting a chip where the caret is: the typed
 * text then stands for nothing, and what is left behind is POSTED as message
 * body — an issue reply whose body was just "@" (真机验收 run
 * 348429859900467). The resource tab is exempt because `insertResourceRef`
 * replaces the query with a node.
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
