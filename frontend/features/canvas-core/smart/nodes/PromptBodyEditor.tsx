// The prompt body input for a canvas prompt node.
//
// Replaces a <textarea>. The reason is not styling: an image chip is a DOM
// element and a textarea holds only characters, so with a textarea the
// reference-image list has to live in a side field that the text merely
// annotates — and the two drift. Here the document IS the list
// (promptImageRefs.ts), which is also how Infinite-Canvas does it.
//
// Two things this component must get right, both verified by its tests:
//   - `nodrag`/`nowheel` on the contenteditable element ITSELF. React Flow
//     reads them off the event target; on a wrapper they do nothing and the
//     canvas pans while you select text.
//   - IME safety. A store round-trip that re-seeds the document mid-
//     composition rips half-typed pinyin out of the input method (#1898).

import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
} from 'react';
import type { JSONContent } from '@tiptap/core';
import type { EditorState } from '@tiptap/pm/state';
import { EditorContent, useEditor } from '@tiptap/react';
import Document from '@tiptap/extension-document';
import Paragraph from '@tiptap/extension-paragraph';
import Placeholder from '@tiptap/extension-placeholder';
import Text from '@tiptap/extension-text';

import { PromptAssetChipNode } from './PromptAssetChipNode';
import { PromptImageChipNode } from './PromptImageChipNode';
import { type MentionedAsset } from '../mentionedAssets';
import {
  PROMPT_ASSET_REF,
  PROMPT_IMAGE_REF,
  collectAssetRefs,
  collectImageRefs,
  docToPromptText,
  mentionQueryFromText,
  seedPromptDoc,
  type PromptImageRef,
} from './promptImageRefs';

/**
 * How an insert treats the text before the caret.
 *
 * `consumeMention` defaults to TRUE, which is the `@` picker's contract: the
 * user typed `@que`, the picker matched on it, and the chip has to REPLACE that
 * token or the query is left stranded in the body.
 *
 * Every other caller wants `false`. The deletion is not "remove the query I
 * matched" — it is "delete from the last literal `@` within 80 characters back
 * to the caret", which cannot tell a pending query from an email address the
 * user typed an hour ago. A `⌥` library drop has no pending query at all, so
 * on a body ending `contact me at foo@bar.com` the default silently ate
 * `@bar.com`, and with two literal `@` in the window a second insert ate the
 * first chip along with them.
 */
export interface MentionInsertOptions {
  consumeMention?: boolean;
}

export interface PromptBodyEditorHandle {
  /** Insert an image chip at the caret. Replaces a pending `@query` token
   *  unless `opts.consumeMention` is false. */
  insertImage: (image: PromptImageRef, opts?: MentionInsertOptions) => void;
  /** Insert an asset chip at the caret. Replaces a pending `@query` token
   *  unless `opts.consumeMention` is false. */
  insertAsset: (asset: MentionedAsset, opts?: MentionInsertOptions) => void;
  /**
   * Insert plain text at the caret, replacing a pending `@query` token unless
   * `opts.consumeMention` is false — the same contract as the two chip
   * inserters, and it takes the option for the same reason: the consuming
   * step deletes back to the last literal `@` within 80 characters, which is
   * a text heuristic that cannot tell a pending query from prose.
   *
   * The `⌥` library drop used to be a caller — `insertText('@' + title + ' ')`
   * per item — and final review ruling R31 replaced it with
   * `mentionLibraryItems.ts`, which inserts real chips: the characters
   * `@Harbour` are prose a run never reads. The Prompts page's Insert
   * positive is the plain-text caller, and it passes `consumeMention: false`.
   * Anything reaching for this to write a MENTION wants `insertAsset` /
   * `insertImage` instead.
   */
  insertText: (text: string, opts?: MentionInsertOptions) => void;
  focus: () => void;
}

interface Props {
  /** Plain-text mirror of the body (chips render as `@alias`). */
  value: string;
  onChange: (text: string) => void;
  /** Fires whenever the set of image chips in the document changes. */
  onRefsChange?: (refs: PromptImageRef[]) => void;
  /** Fires whenever the set of ASSET chips in the document changes. Separate
   *  callback, not a merged one: the two lists mean different things and land
   *  in different node-data fields. */
  onAssetRefsChange?: (assets: MentionedAsset[]) => void;
  /** Fires when the user types `@`, so the caller can open the picker. */
  onAtTyped?: () => void;
  /**
   * Keys the open picker wants first — Escape to dismiss, arrows to move the
   * active row. Return true to say "handled", which stops the editor from
   * also acting on it (Escape must not blur, ArrowDown must not move the
   * caret out from under the list).
   */
  onKeyDown?: (event: KeyboardEvent) => boolean;
  /**
   * The live `@query` sitting before the caret, or null when there is none.
   * Emitted on every document change so the caller can narrow the picker as
   * the user types and dismiss it when the token is deleted.
   */
  onMentionQueryChange?: (query: string | null) => void;
  readOnly?: boolean;
  placeholder?: string;
  /** Chips to seed the document with on mount (restored from persistence). */
  initialChips?: PromptImageRef[];
  /**
   * Known asset mentions, used to turn `@[asset:id]` tokens in `value` back
   * into chips — on mount AND on any later external re-seed.
   *
   * This is a NAME TABLE, not a position list: where each chip goes is decided
   * by where its token sits in the text, so a reload restores the chip exactly
   * where the user put it. (Image chips have no such token and are therefore
   * appended, which is why they cannot survive an external re-seed.)
   */
  knownAssets?: MentionedAsset[];
  /** Distinguishes the two places this editor renders (node vs attached panel). */
  testId?: string;
  /** Accessible name. Each host has its own — do not collapse them into one:
   *  a screen-reader user meets two different prompt boxes. */
  ariaLabel?: string;
}

/** Reads the text before the caret and asks `mentionQueryFromText` whether a
 *  live mention token is sitting there. */
function mentionQueryBeforeCaret(ed: { view: { state: EditorState } }): string | null {
  const { state } = ed.view;
  const from = state.selection.from;
  return mentionQueryFromText(state.doc.textBetween(Math.max(0, from - 80), from, '\n', '\n'));
}

export const PromptBodyEditor = forwardRef<PromptBodyEditorHandle, Props>(
  function PromptBodyEditor(
    {
      value,
      onChange,
      onRefsChange,
      onAssetRefsChange,
      onAtTyped,
      onMentionQueryChange,
      onKeyDown,
      readOnly = false,
      placeholder = 'What should the model generate? Type @ to reference an asset',
      initialChips = [],
      knownAssets = [],
      testId = 'prompt-body-editor',
      ariaLabel = 'Prompt body',
    },
    ref,
  ) {
    // Guard against re-seeding the document while an IME is composing.
    const composingRef = useRef(false);
    // What we last told the parent — so its echo is not mistaken for an
    // external edit worth overwriting the document with.
    const lastEmittedRef = useRef(value);
    // Callbacks change identity every render; read them through a ref so the
    // editor is created once rather than torn down and rebuilt.
    const cbRef = useRef({
      onChange,
      onRefsChange,
      onAssetRefsChange,
      onAtTyped,
      onMentionQueryChange,
      onKeyDown,
    });
    cbRef.current = {
      onChange,
      onRefsChange,
      onAssetRefsChange,
      onAtTyped,
      onMentionQueryChange,
      onKeyDown,
    };

    /**
     * Every asset name this editor has ever seen, id → snapshot.
     *
     * Accumulated rather than replaced: a re-seed happens with whatever
     * `value` the parent last pushed, and the parent's `knownAssets` prop may
     * lag one render behind a chip the user just inserted. Forgetting a name
     * would turn that chip back into a raw token in front of the user.
     */
    const assetIndexRef = useRef(
      new Map<string, MentionedAsset>(knownAssets.map((a) => [a.asset_id, a])),
    );
    for (const a of knownAssets) assetIndexRef.current.set(a.asset_id, a);

    /** Publish the document outward: text, chips, live mention query. */
    const emit = useCallback((ed: { getJSON: () => JSONContent; view: { state: EditorState } }) => {
      const json = ed.getJSON();
      const text = docToPromptText(json);
      lastEmittedRef.current = text;
      const assets = collectAssetRefs(json);
      for (const a of assets) assetIndexRef.current.set(a.asset_id, a);
      cbRef.current.onChange(text);
      cbRef.current.onRefsChange?.(collectImageRefs(json));
      cbRef.current.onAssetRefsChange?.(assets);
      cbRef.current.onMentionQueryChange?.(mentionQueryBeforeCaret(ed));
    }, []);
    // The composition listener is bound once; reach `emit` through a ref so
    // it never needs re-binding.
    const emitRef = useRef(emit);
    emitRef.current = emit;

    const editor = useEditor({
      // A deliberately small schema: one paragraph type, text, and chips.
      // StarterKit would add headings, lists, code blocks and their keymaps —
      // none of which belong in a prompt box.
      extensions: [
        Document,
        Paragraph,
        Text,
        Placeholder.configure({ placeholder }),
        PromptImageChipNode,
        PromptAssetChipNode,
      ],
      content: seedPromptDoc(value, initialChips, assetIndexRef.current),
      editable: !readOnly,
      editorProps: {
        attributes: {
          // These two must be here, on the contenteditable element.
          class: [
            'nodrag nowheel',
            'min-h-[3.5rem] max-h-[240px] w-full overflow-y-auto bg-transparent',
            // No focus ring: the box already sits inside a bordered card, and
            // a ring around the whole field reads as an error state. IC draws
            // none either.
            'text-[13px] text-ink-200 outline-none',
            'prompt-body-editor',
          ].join(' '),
          'data-testid': testId,
          'aria-label': ariaLabel,
          // A contenteditable=false div is NOT focusable by default, so a
          // read-only viewer could neither tab to the prompt nor select and
          // copy it. tiptap drops tabindex when it makes the view
          // non-editable, so we put it back. (The editable case is focusable
          // on its own.)
          tabindex: '0',
          role: 'textbox',
          'aria-readonly': readOnly ? 'true' : 'false',
        },
        handleKeyDown(_view, event) {
          // The picker gets first refusal: while it is open, Escape and the
          // arrow keys belong to it, not to the text.
          if (cbRef.current.onKeyDown?.(event)) {
            event.preventDefault();
            return true;
          }
          // An '@' produced by an IME is part of a composition, not a
          // mention trigger — opening the picker there steals the keystrokes
          // the input method is still using.
          if (composingRef.current) return false;
          if (event.key === '@' && cbRef.current.onAtTyped) {
            // Let the character land first, then notify — the picker wants to
            // read the query that follows it.
            setTimeout(() => cbRef.current.onAtTyped?.(), 0);
          }
          return false;
        },
      },
      onUpdate({ editor: ed }) {
        // While an IME is composing, nothing leaves this component: the
        // store round-trip is asynchronous, and a controlled rewrite landing
        // mid-composition commits raw pinyin as text (2026-08-18 incident).
        // compositionend flushes once, below.
        if (composingRef.current) return;
        emit(ed);
      },
    });

    // Report the seeded state once the editor exists, so a restored chip is
    // known to the parent without waiting for the first keystroke.
    useEffect(() => {
      if (!editor) return;
      emit(editor);
      // Intentionally once per editor instance.
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [editor]);

    // Track composition on the DOM directly: the guard has to be up before
    // React re-renders from the store echo, which is earlier than a React
    // synthetic handler would fire.
    useEffect(() => {
      const dom = editor?.view.dom;
      if (!dom) return;
      const start = () => {
        composingRef.current = true;
      };
      const end = () => {
        composingRef.current = false;
        // The single commit for the whole composition.
        if (editor) emitRef.current(editor);
      };
      dom.addEventListener('compositionstart', start);
      dom.addEventListener('compositionend', end);
      return () => {
        dom.removeEventListener('compositionstart', start);
        dom.removeEventListener('compositionend', end);
      };
    }, [editor]);

    // External changes (undo, a reset, the Library loader) re-seed the
    // document — but never mid-composition, and never for our own echo.
    //
    // Re-seeding necessarily DROPS the chips: `value` is plain text and holds
    // no chip information, so there is nothing to restore them from. That is
    // correct for a genuine external replacement (loading a different prompt
    // really should clear its images) but catastrophic on mount, where the
    // chips we just seeded would be wiped before they were ever seen. Hence
    // the mount guard — the seeded document is not an external change.
    const syncedOnceRef = useRef(false);
    useEffect(() => {
      if (!editor) return;
      if (!syncedOnceRef.current) {
        syncedOnceRef.current = true;
        return;
      }
      if (composingRef.current) return;
      if (value === lastEmittedRef.current) return;
      if (value === docToPromptText(editor.getJSON())) return;
      editor.commands.setContent(seedPromptDoc(value, [], assetIndexRef.current), {
        emitUpdate: false,
      });
      lastEmittedRef.current = value;
    }, [value, editor]);

    useEffect(() => {
      editor?.setEditable(!readOnly);
    }, [editor, readOnly]);

    /**
     * Insert `content` at the caret, optionally consuming a pending `@query`.
     *
     * The consuming half belongs to ONE caller — the `@` picker, whose whole
     * gesture is "the user typed `@que` and chose a row". It searches the 80
     * characters before the caret for the last `@` and deletes from there, so
     * it is a text-shaped heuristic, not a record of what the picker matched:
     * it cannot tell a pending query from an address the user typed earlier.
     * A caller with no pending query must opt out — see
     * {@link MentionInsertOptions}.
     */
    const insertAtMention = useCallback(
      (
        content: Parameters<typeof editor.commands.insertContent>[0],
        consumeMention = true,
      ) => {
        if (!editor) return;
        const { state } = editor.view;
        const from = state.selection.from;
        // A caller with no pending `@query` (the Library panel, the ⌥ drop)
        // reaches an editor nobody is typing in. Its "caret" is then whatever
        // ProseMirror seeded — position 0 on a never-focused card — so the
        // chip landed in FRONT of the prose (seen on prod, 2026-09-05). No
        // focus means no caret intent: append instead. The `@` picker keeps
        // the real caret: its popover re-claims focus for its search box, and
        // the caret it must consume back to is the one the user left.
        const atEnd = !consumeMention && !editor.isFocused;
        const chain = editor.chain().focus(atEnd ? 'end' : undefined);
        if (consumeMention) {
          const textBefore = state.doc.textBetween(Math.max(0, from - 80), from, '\n', '\n');
          const at = textBefore.lastIndexOf('@');
          if (at >= 0) {
            chain.deleteRange({ from: from - (textBefore.length - at), to: from });
          }
        }
        chain.insertContent(content).run();
      },
      [editor],
    );

    const insertImage = useCallback(
      (image: PromptImageRef, opts?: MentionInsertOptions) => {
        insertAtMention(
          [
            { type: PROMPT_IMAGE_REF, attrs: { ...image } },
            { type: 'text', text: ' ' },
          ],
          opts?.consumeMention ?? true,
        );
      },
      [insertAtMention],
    );

    const insertAsset = useCallback(
      (asset: MentionedAsset, opts?: MentionInsertOptions) => {
        // Remember the name BEFORE the chip exists: `insertContent` triggers an
        // update, whose re-seed guard reads this index.
        assetIndexRef.current.set(asset.asset_id, asset);
        insertAtMention(
          [
            { type: PROMPT_ASSET_REF, attrs: { ...asset } },
            { type: 'text', text: ' ' },
          ],
          opts?.consumeMention ?? true,
        );
      },
      [insertAtMention],
    );

    const insertText = useCallback(
      (text: string, opts?: MentionInsertOptions) => {
        insertAtMention([{ type: 'text', text }], opts?.consumeMention ?? true);
      },
      [insertAtMention],
    );

    useImperativeHandle(
      ref,
      () => ({
        insertImage,
        insertAsset,
        insertText,
        focus: () => editor?.commands.focus(),
      }),
      [insertImage, insertAsset, insertText, editor],
    );

    return <EditorContent editor={editor} />;
  },
);
