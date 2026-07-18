/**
 * selectionContext — pure extraction for the "select text → AI chat" pill.
 *
 * Given a live {@link Selection}, decide whether it is a quotable run of script
 * text (a non-empty text selection anchored inside a `.mh-el-editable` that
 * lives within the `.mh-sheet` paper) and, if so, pull out the plain text plus
 * the structural context the chat quote is tagged with: the anchor element's
 * id/type, the anchor scene, a display label, and whether the selection spilled
 * across more than one scene.
 *
 * Kept pure (no React, no store, no DOM mutation) so it can be unit-tested
 * against a plain jsdom sheet and reused by the button wrapper unchanged.
 */

export interface SelectionQuoteContext {
  /** The selected plain text (trimmed). */
  text: string;
  /** Short display label for the anchor scene (heading text), if any. */
  sceneLabel?: string;
  /** Anchor scene id (the scene where the selection STARTS). */
  sceneId?: string;
  /** Anchor element id + type. */
  elementId?: string;
  elementType?: string;
  /** True when the selection spanned more than one scene. */
  crossScene?: boolean;
}

export interface ResolveSelectionOptions {
  /** Paper root a selection must live within to count. Default `.mh-sheet`. */
  sheetSelector?: string;
  /** Script text element the anchor must resolve to. Default `.mh-el-editable`. */
  editableSelector?: string;
  /** Scene block carrying `data-scene-id`. Default `[data-scene-id]`. */
  sceneSelector?: string;
}

const DEFAULTS: Required<ResolveSelectionOptions> = {
  sheetSelector: '.mh-sheet',
  editableSelector: '.mh-el-editable',
  sceneSelector: '[data-scene-id]',
};

/** Resolve the nearest Element for a DOM node (text nodes → parentElement). */
function elementOf(node: Node | null): Element | null {
  if (!node) return null;
  return node.nodeType === Node.ELEMENT_NODE
    ? (node as Element)
    : node.parentElement;
}

/**
 * Label the anchor scene by its ordinal within the paper ("S2"). The heading
 * text is unreliable — it is composed from input-based `HeadingSelect` widgets
 * that carry no `textContent` — whereas DOM order gives a stable, human number
 * matching the scene rail.
 */
function deriveSceneLabel(
  scene: Element | null,
  sheetSelector: string,
  sceneSelector: string,
): string | undefined {
  if (!scene) return undefined;
  const sheet = scene.closest(sheetSelector) ?? scene.ownerDocument;
  const scenes = Array.from(sheet.querySelectorAll(sceneSelector));
  const idx = scenes.indexOf(scene);
  return idx >= 0 ? `S${idx + 1}` : undefined;
}

export function resolveSelectionQuote(
  sel: Selection | null,
  opts?: ResolveSelectionOptions,
): SelectionQuoteContext | null {
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;

  const text = sel.toString().trim();
  if (!text) return null;

  const cfg = { ...DEFAULTS, ...opts };
  const range = sel.getRangeAt(0);

  // Anchor = where the selection STARTS. A selection that begins outside a
  // script line (margins, chrome, prose outside the sheet) is not quotable.
  const startEl = elementOf(range.startContainer)?.closest(cfg.editableSelector) ?? null;
  if (!startEl) return null;
  if (!startEl.closest(cfg.sheetSelector)) return null;

  const startScene = startEl.closest(cfg.sceneSelector);
  const endEl = elementOf(range.endContainer)?.closest(cfg.editableSelector) ?? null;
  const endScene = endEl?.closest(cfg.sceneSelector) ?? null;

  const sceneId = startScene?.getAttribute('data-scene-id') ?? undefined;
  const crossScene = Boolean(startScene && endScene && startScene !== endScene);

  return {
    text,
    sceneId,
    sceneLabel: deriveSceneLabel(startScene, cfg.sheetSelector, cfg.sceneSelector),
    elementId: (startEl as HTMLElement).dataset.elId,
    elementType: (startEl as HTMLElement).dataset.elType,
    crossScene,
  };
}
