/**
 * `menuBridgeKeymap` — lets the slash menu / mention combobox (both still
 * owned by `SceneBlock`'s state machinery, spec D6/M2) intercept
 * ArrowUp/ArrowDown/Enter/Tab/Escape BEFORE `ScriptKeymap`'s Enter/Tab/
 * Backspace handlers run, so an open menu's keyboard nav never falls through
 * to a real split/retype/backspace.
 *
 * Built with an explicit `priority: 1000` (default extension priority is
 * 100 — see `@tiptap/core`'s `sortExtensions`) rather than relying on
 * extension-array position: `ExtensionManager.plugins` sorts DESCENDING by
 * priority every time regardless of array order/the reverse-then-resort
 * trick used to break ties among EQUAL-priority extensions (see
 * `keymap.ts`'s module doc) — a strictly higher priority is the only order
 * guarantee that doesn't depend on that tie-breaking detail.
 *
 * `TipTapSceneEditor` constructs one instance per mount via `useMemo`,
 * closing over two ref objects it keeps fresh every render
 * (`mentionMenuRef` / `slashMenuRef`) so this extension always reads the
 * LATEST bridge without needing to be re-created (extensions are meant to be
 * stable across the editor's lifetime).
 */
import { Extension } from '@tiptap/core';
import type { MutableRefObject } from 'react';
import { getElementCtx } from './keymap';

/** One open menu's keyboard-nav contract, owned by `SceneBlock`. */
export interface MenuBridge {
  /** The `scriptElement` id this menu is anchored to — a keypress only
   *  routes to the menu when the caret is still inside THIS element. */
  elementId: string;
  onArrowDown: () => void;
  onArrowUp: () => void;
  /** Enter (and Tab, unless `onTab` is given) applies the active option. */
  onApply: () => void;
  /** Tab's behaviour when it differs from Enter (the mention combobox's
   *  character-cue "abandon" semantics) — defaults to `onApply`. Return
   *  `false` to DECLINE the key: Tab then falls through to the real
   *  ScriptKeymap (the transition preset menu does this — Tab must keep its
   *  type-cycle semantics there). */
  onTab?: () => boolean | void;
  onEscape: () => void;
}

export interface MenuBridgeRefs {
  mentionMenuRef: MutableRefObject<MenuBridge | null>;
  slashMenuRef: MutableRefObject<MenuBridge | null>;
}

/** Mention takes priority over slash, mirroring legacy `SceneBlock.handleKeyDown`'s
 *  check order (mention block first, then slash block). */
function resolveMenu(refs: MenuBridgeRefs, elementId: string): MenuBridge | null {
  const mention = refs.mentionMenuRef.current;
  if (mention && mention.elementId === elementId) return mention;
  const slash = refs.slashMenuRef.current;
  if (slash && slash.elementId === elementId) return slash;
  return null;
}

export function createMenuBridgeKeymap(refs: MenuBridgeRefs) {
  return Extension.create({
    name: 'menuBridgeKeymap',
    priority: 1000,
    addKeyboardShortcuts() {
      const resolve = (): MenuBridge | null => {
        if (this.editor.view.composing) return null;
        const ctx = getElementCtx(this.editor.state.selection.$from);
        if (!ctx) return null;
        return resolveMenu(refs, ctx.node.attrs.id as string);
      };
      return {
        ArrowDown: () => {
          const menu = resolve();
          if (!menu) return false;
          menu.onArrowDown();
          return true;
        },
        ArrowUp: () => {
          const menu = resolve();
          if (!menu) return false;
          menu.onArrowUp();
          return true;
        },
        Enter: () => {
          const menu = resolve();
          if (!menu) return false;
          menu.onApply();
          return true;
        },
        Tab: () => {
          const menu = resolve();
          if (!menu) return false;
          if (menu.onTab) return menu.onTab() !== false;
          menu.onApply();
          return true;
        },
        Escape: () => {
          const menu = resolve();
          if (!menu) return false;
          menu.onEscape();
          return true;
        },
      };
    },
  });
}
