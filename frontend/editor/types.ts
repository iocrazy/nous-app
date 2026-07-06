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
