/**
 * M0 spike: a bare TipTap editor over one scene's `ScriptElement[]`, rendering
 * today's row DOM (spec D5) via a NodeView. Editing works (typing is not
 * blocked), but the sync/debounce/remote-apply wiring is explicitly M1 scope
 * (spec phases) — every `onUpdate` emits ops immediately, with no debounce
 * and no `useSceneSync` integration. `elements` is read only on MOUNT: this
 * component does not yet react to the prop changing after that (no external
 * re-sync, no remote-ops meta guard) — that lands with M1's collab wiring.
 */
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from 'react';
import {
  EditorContent,
  NodeViewContent,
  NodeViewWrapper,
  ReactNodeViewRenderer,
  useEditor,
  type NodeViewProps,
} from '@tiptap/react';
import { ScriptDocument, ScriptElementNode, ScriptText, type ScriptElementAttrs } from './schema';
import { docToElements, elementsToDoc } from './docModel';
import { mapDocChange } from './opsMapper';
import type { ElementOp, ScriptElement } from '../types';
import { HOLLYWOOD_LINE_CLASS } from '../render/HollywoodLayout';
import { ASIAN_LINE_CLASS } from '../render/AsianLayout';

export type SceneFormat = 'hollywood' | 'asian';

export interface TipTapSceneEditorProps {
  elements: ScriptElement[];
  format: SceneFormat;
  onOps?: (ops: ElementOp[]) => void;
  /** A1 continuous numbering base (see HollywoodLayout/AsianLayout's prop of
   *  the same name) — the heading row consumed `blockIndexBase + 1`, so the
   *  first element here displays as `blockIndexBase + 2`. Defaults to 0. */
  blockIndexBase?: number;
}

interface ScriptElementViewRefs {
  formatRef: MutableRefObject<SceneFormat>;
  blockIndexBaseRef: MutableRefObject<number>;
}

/** The row DOM (spec D5): gutter [num + 4-dot drag handle] + tick + content. */
function ScriptElementView({ node, editor, getPos }: NodeViewProps, refs: ScriptElementViewRefs) {
  const attrs = node.attrs as ScriptElementAttrs;

  // No reliable static hook into "this node's sibling index changed" — the
  // safe, simple-for-M0 approach (per spec: "keep simple") is to re-render
  // on every editor transaction and recompute from getPos() fresh.
  const [, bumpTick] = useState(0);
  useEffect(() => {
    const rerender = () => bumpTick((t) => t + 1);
    editor.on('transaction', rerender);
    return () => {
      editor.off('transaction', rerender);
    };
  }, [editor]);

  let siblingIndex = 0;
  if (typeof getPos === 'function') {
    const pos = getPos();
    if (typeof pos === 'number') {
      siblingIndex = editor.state.doc.resolve(pos).index(0);
    }
  }
  const displayIndex = refs.blockIndexBaseRef.current + 2 + siblingIndex;

  const lineClass =
    refs.formatRef.current === 'asian'
      ? ASIAN_LINE_CLASS[attrs.elType]
      : HOLLYWOOD_LINE_CLASS[attrs.elType];

  return (
    <NodeViewWrapper as="div" className="mh-el-row">
      <div className="mh-el-gutter" contentEditable={false}>
        <span className="mh-el-num">{displayIndex}</span>
        <button type="button" className="mh-el-drag" contentEditable={false} tabIndex={-1}>
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
          <span className="mh-el-dot" />
        </button>
      </div>
      <span className={`mh-el-tick t-${attrs.elType}`} contentEditable={false} aria-hidden="true" />
      <NodeViewContent
        as="div"
        className={`mh-el-editable mh-el-line ${lineClass}`}
        data-el-type={attrs.elType}
        data-el-id={attrs.id}
      />
    </NodeViewWrapper>
  );
}

export function TipTapSceneEditor({
  elements,
  format,
  onOps,
  blockIndexBase = 0,
}: TipTapSceneEditorProps) {
  // Latest-callback / latest-value refs — read fresh inside the NodeView and
  // onUpdate closures captured once at editor creation (NoteEditor.tsx's
  // established pattern in this repo).
  const onOpsRef = useRef(onOps);
  onOpsRef.current = onOps;
  const formatRef = useRef<SceneFormat>(format);
  formatRef.current = format;
  const blockIndexBaseRef = useRef(blockIndexBase);
  blockIndexBaseRef.current = blockIndexBase;

  // The mapper's diff base: the last element list this component itself
  // emitted (or the initial mount snapshot). Updated after every onUpdate.
  const lastEmittedRef = useRef<ScriptElement[]>(elements);

  const initialContent = useMemo(() => elementsToDoc(elements), []); // eslint-disable-line react-hooks/exhaustive-deps

  const extensions = useMemo(() => {
    const refs: ScriptElementViewRefs = { formatRef, blockIndexBaseRef };
    const ViewNode = ScriptElementNode.extend({
      addNodeView() {
        return ReactNodeViewRenderer((props: NodeViewProps) => ScriptElementView(props, refs));
      },
    });
    return [ScriptDocument, ScriptText, ViewNode];
  }, []);

  const editor = useEditor({
    extensions,
    content: initialContent,
    editable: true,
    editorProps: {
      attributes: { class: 'mh-tiptap-scene-editor' },
    },
    onUpdate: ({ editor: ed }) => {
      const nextElements = docToElements(ed.state.doc);
      const ops = mapDocChange(lastEmittedRef.current, nextElements);
      if (ops.length > 0) {
        onOpsRef.current?.(ops);
      }
      lastEmittedRef.current = nextElements;
    },
  });

  useEffect(() => {
    if (import.meta.env.MODE !== 'test') return;
    // Test-only backdoor (mirrors NoteEditor.tsx) — jsdom can't drive real
    // keyboard/IME input into ProseMirror, so tests reach the editor
    // instance directly to exercise commands.
    (window as unknown as Record<string, unknown>).__tipTapSceneEditorInstance = editor;
  }, [editor]);

  return (
    <div className="mh-tiptap-scene-editor-root">
      <EditorContent editor={editor} />
    </div>
  );
}
