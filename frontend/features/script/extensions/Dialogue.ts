import { Node, mergeAttributes } from '@tiptap/core';

export const Dialogue = Node.create({
  name: 'dialogue',
  group: 'block',
  content: 'inline*',
  defining: true,

  parseHTML() {
    return [
      {
        tag: 'p',
        getAttrs: (element) => {
          const el = element as HTMLElement;
          const strong = el.querySelector('strong');
          if (!strong) return false;
          const text = el.textContent || '';
          return /：/.test(text) ? {} : false;
        },
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['p', mergeAttributes(HTMLAttributes, { class: 'dialogue-line' }), 0];
  },
});
