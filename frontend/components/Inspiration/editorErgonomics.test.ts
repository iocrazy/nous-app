import { describe, expect, it } from 'vitest';
import { continueListOnEnter, todoShortcutOnSpace } from './editorErgonomics';

describe('continueListOnEnter', () => {
  it('continues a todo item', () => {
    const t = '- [ ] buy milk';
    const r = continueListOnEnter(t, t.length)!;
    expect(r.text).toBe('- [ ] buy milk\n- [ ] ');
    expect(r.caret).toBe(r.text.length);
  });

  it('continues a checked todo as an unchecked one, keeping indent', () => {
    const t = '  - [x] done';
    const r = continueListOnEnter(t, t.length)!;
    expect(r.text).toBe('  - [x] done\n  - [ ] ');
  });

  it('empty todo item + enter exits the list', () => {
    const t = '- [ ] a\n- [ ] ';
    const r = continueListOnEnter(t, t.length)!;
    expect(r.text).toBe('- [ ] a\n');
    expect(r.caret).toBe(8);
  });

  it('continues bullets and increments ordered lists', () => {
    expect(continueListOnEnter('- item', 6)!.text).toBe('- item\n- ');
    expect(continueListOnEnter('3. third', 8)!.text).toBe('3. third\n4. ');
  });

  it('returns null mid-line or on plain text', () => {
    expect(continueListOnEnter('- item', 3)).toBeNull();
    expect(continueListOnEnter('plain', 5)).toBeNull();
  });
});

describe('todoShortcutOnSpace', () => {
  it('turns [] at line start into a todo marker', () => {
    const r = todoShortcutOnSpace('[]', 2)!;
    expect(r.text).toBe('- [ ] ');
    expect(r.caret).toBe(6);
  });

  it('keeps indentation and works on later lines', () => {
    const t = 'x\n  []';
    const r = todoShortcutOnSpace(t, t.length)!;
    expect(r.text).toBe('x\n  - [ ] ');
  });

  it('returns null when [] is not alone at line start', () => {
    expect(todoShortcutOnSpace('a []', 4)).toBeNull();
  });
});
