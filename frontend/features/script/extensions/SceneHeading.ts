import { Node, mergeAttributes } from '@tiptap/core';

export const SceneHeading = Node.create({
  name: 'sceneHeading',
  group: 'block',
  content: 'inline*',
  defining: true,

  parseHTML() {
    return [
      {
        tag: 'h2',
        getAttrs: (element) => {
          const text = (element as HTMLElement).textContent || '';
          return /^场景\s*\d/.test(text) ? {} : false;
        },
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ['h2', mergeAttributes(HTMLAttributes, { class: 'scene-heading' }), 0];
  },
});
