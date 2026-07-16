/**
 * Core domain types for the v2 script editor (spec v3 §3, §7).
 *
 * All ids are strings end-to-end — scenes and elements live on Snowflake
 * bigint keys and JS loses precision above 2^53, so ids are NEVER coerced
 * with Number()/parseInt(). Elements carry client-generated `el_<8hex>` ids
 * (see newElementId in sceneService) so anchored ops can reference them
 * before the server has ever seen them.
 */

export type ElementType =
  | 'action'
  | 'dialogue'
  | 'character'
  | 'paren'
  | 'transition'
  | 'comment'
  | 'subtitle';

export interface ScriptElement {
  id: string;
  type: ElementType;
  text: string;
  character_id?: string | null;
}

export interface SceneDoc {
  id: string;
  script_id: string;
  chapter_id: string | null;
  heading_int_ext: string | null;
  location_text: string | null;
  time_of_day: string | null;
  content_version: number;
  elements: ScriptElement[];
  sort_order: number;
  // Node-view canvas coordinates (script_scenes mig 339; null/absent until placed).
  position_x?: number | null;
  position_y?: number | null;
}

/**
 * Anchored element operations (spec §7). Inserts/moves reference a sibling by
 * id (`before_id`/`after_id`) rather than an index, so concurrent edits stay
 * unambiguous. Never a whole-block replace.
 */
export type ElementOp =
  | {
      op: 'insert';
      element_id: string;
      before_id?: string | null;
      after_id?: string | null;
      payload: { type: ElementType; text: string; character_id?: string | null };
    }
  | {
      op: 'update';
      element_id: string;
      payload: Partial<{ type: ElementType; text: string; character_id: string | null }>;
    }
  | { op: 'delete'; element_id: string }
  | {
      op: 'move';
      element_id: string;
      before_id?: string | null;
      after_id?: string | null;
    };

export const ELEMENT_TYPES: ElementType[] = [
  'action',
  'dialogue',
  'character',
  'paren',
  'transition',
  'comment',
  'subtitle',
];

export function isElementType(value: unknown): value is ElementType {
  return typeof value === 'string' && (ELEMENT_TYPES as string[]).includes(value);
}

/** A caret position in the editor: which scene, which element (or a heading
 *  field). The shell tracks it to follow the cursor (toolbar active pill,
 *  type commands). Formerly lived in the retired `editorMachine`. */
export interface CursorState {
  sceneId: string;
  elementId: string | null;
  field: 'element' | 'heading_int_ext' | 'location' | 'time';
}

/**
 * Marks a dragged payload as the editor's OWN reorder drag (a scene handle, the
 * scene move overlay, or an element grip) so a drop can be told apart from text
 * dragged in from anywhere else.
 *
 * Every one of those drags also carries the id as `text/plain` — that's what
 * makes them draggable to other apps at all. But the drop lands on a
 * contentEditable, whose NATIVE default is to insert the dragged text: a scene
 * reorder would fire its move AND write the scene's own id into the script as
 * prose. Skipping ProseMirror's handler doesn't help (returning true from
 * `handleDOMEvents` never preventDefaults — see `runCustomHandler`); only
 * preventDefault on the DROP stops the browser, and by then the drag is done, so
 * unlike a dragstart preventDefault it cancels nothing.
 */
export const MH_DRAG_MIME = 'application/x-mediahub-reorder';
