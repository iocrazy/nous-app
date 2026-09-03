// features/canvas-core/ui/canvasShortcuts.ts
//
// The canvas keyboard-shortcut reference (the data behind the ? help panel).
// Single source of truth — every entry here is a shortcut actually wired in
// useCanvasShortcuts or a documented canvas gesture, so the panel can never
// drift into advertising keys that don't exist.
//
// `keys` are logical tokens; the panel formats `mod` per platform (⌘ on
// macOS, Ctrl elsewhere). Non-keyboard gestures (drag combos) carry a
// `gesture: true` flag so the panel can group them apart.

export interface ShortcutEntry {
  /** Logical key tokens, e.g. ['mod', 'K'] or ['Alt', 'drag']. */
  keys: string[];
  label: string;
  /** True for pointer gestures (Alt-drag etc.) rather than pure key combos. */
  gesture?: boolean;
}

export interface ShortcutGroup {
  title: string;
  entries: ShortcutEntry[];
}

export const CANVAS_SHORTCUT_GROUPS: ShortcutGroup[] = [
  {
    title: 'Edit',
    entries: [
      { keys: ['mod', 'Z'], label: 'Undo' },
      { keys: ['mod', 'Shift', 'Z'], label: 'Redo' },
      { keys: ['mod', 'C'], label: 'Copy selection' },
      { keys: ['mod', 'V'], label: 'Paste' },
      { keys: ['mod', 'D'], label: 'Duplicate in place' },
      { keys: ['Delete'], label: 'Delete selection' },
    ],
  },
  {
    title: 'Select',
    entries: [
      { keys: ['mod', 'A'], label: 'Select all' },
      { keys: ['Shift', 'drag'], label: 'Marquee select', gesture: true },
      { keys: ['Esc'], label: 'Clear selection' },
    ],
  },
  {
    title: 'Group',
    entries: [
      { keys: ['mod', 'G'], label: 'Group selection' },
      { keys: ['mod', 'Shift', 'G'], label: 'Ungroup' },
    ],
  },
  {
    title: 'Tools',
    entries: [
      { keys: ['mod', 'K'], label: 'Command palette' },
      { keys: ['X'], label: 'Knife (cut edges)' },
      { keys: ['L'], label: 'Library panel' },
      { keys: ['?'], label: 'This help' },
    ],
  },
  {
    title: 'Build',
    entries: [
      { keys: ['Double-click'], label: 'Add-node menu', gesture: true },
      { keys: ['Alt', 'drag'], label: 'Snap-connect a node', gesture: true },
    ],
  },
];

/** Format a logical key token for display on the caller's platform. */
export function formatKey(token: string, isMac: boolean): string {
  if (token === 'mod') return isMac ? '⌘' : 'Ctrl';
  if (token === 'Shift') return isMac ? '⇧' : 'Shift';
  if (token === 'Alt') return isMac ? '⌥' : 'Alt';
  if (token === 'drag') return 'drag';
  return token;
}
