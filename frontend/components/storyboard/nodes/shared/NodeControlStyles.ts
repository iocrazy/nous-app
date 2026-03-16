// ─── NodeControlStyles ────────────────────────────────────────────────────────
// Shared Tailwind CSS class strings for consistent node control styling.

export const nodeControlStyles = {
  modelSelector: [
    'w-full bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5',
    'text-sm text-gray-200 focus:outline-none focus:border-blue-500',
    'transition-colors cursor-pointer',
  ].join(' '),

  sizeDropdown: [
    'bg-gray-800 border border-gray-600 rounded-md px-2 py-1',
    'text-xs text-gray-300 focus:outline-none focus:border-blue-500',
    'transition-colors cursor-pointer',
  ].join(' '),

  generateButton: [
    'w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg',
    'bg-blue-600 hover:bg-blue-700 disabled:bg-blue-800 disabled:opacity-50',
    'text-sm font-medium text-white transition-colors disabled:cursor-not-allowed',
  ].join(' '),

  progressBar: {
    container: 'w-full h-1.5 bg-gray-700 rounded-full overflow-hidden',
    fill: 'h-full bg-blue-500 rounded-full transition-all duration-300',
  },

  label: 'block text-xs font-medium text-gray-400 mb-1',

  textarea: [
    'w-full bg-gray-800 border border-gray-600 rounded-lg px-2 py-1.5',
    'text-sm text-gray-200 placeholder-gray-500 resize-none',
    'focus:outline-none focus:border-blue-500 transition-colors',
  ].join(' '),

  section: 'space-y-1.5',
} as const;
