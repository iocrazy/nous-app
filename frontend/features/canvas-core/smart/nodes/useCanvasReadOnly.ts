/**
 * `true` when this canvas session may not write (backend said
 * `can_edit:false` on the load, or a 403 latched mid-session).
 *
 * Every smart node renderer subscribes through this one hook so the
 * question is asked identically everywhere. #1828 shut down the
 * canvas-LEVEL gestures (drag / connect / delete / paste) and the chrome
 * that creates nodes, but each node renderer carries its own write
 * affordances — Run, Generate, Retry, Crop, Add, the inline text fields —
 * and those stayed live. Clicking one did nothing visible: the store
 * drops the revision bump (`markDirty` bails on `readOnly`) and the PUT
 * never leaves, so the user sees a lit button that swallows the click.
 *
 * That is the silent no-op this repo has banned outright: "用户动作 →
 * 触发的每条路径必须返回类型化结果". Withholding the control IS the typed
 * result here — the answer ("you can't write this canvas") is delivered
 * before the click instead of being dropped after it, and the surface's
 * "Read-only" badge says why.
 *
 * Rule of thumb for new node affordances: does it write the canvas
 * document (patchNode / setNodes) or POST anything the write guard would
 * gate? Then it consults this hook. Pure viewing — expand, copy, preview,
 * download, navigate — stays available.
 *
 * ⚠️ HOW to withhold is not a free choice, and `disabled` is the wrong
 * default for half of them:
 *
 *   free-text `<input>` / `<textarea>` → `readOnly={readOnly}`.
 *     `disabled` blocks focus, selection and copy. A read-only viewer's
 *     whole session IS reading — disabling the shot description or the
 *     prompt body takes away the one thing they came for. `readonly` is
 *     the HTML feature for "not editable, still yours to read".
 *     Style it with the `read-only:` Tailwind variant (`disabled:` won't
 *     match), and keep the focus ring: focus is what enables copying.
 *   everything else → `disabled`.
 *     Buttons, `<select>` (HTML has no readonly for it), toggles, chips,
 *     and number steppers (a readonly number input can still be nudged by
 *     the arrow keys). There is no text in them worth copying.
 *
 * The test fence for both halves is `smart-node-readonly.test.tsx`.
 */

import { useCanvasCoreStore } from '../../store/canvasCoreStore';

export function useCanvasReadOnly(): boolean {
  return useCanvasCoreStore((s) => s.readOnly);
}
