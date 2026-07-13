import { describe, expect, it } from 'vitest';
import { EDITOR_SHELL_STYLES } from '../components/editorShellStyles';

/**
 * Guard against the NaN-stylesheet incident (2026-07-13): a backtick inside a
 * CSS comment terminated the template literal early, the remainder parsed as
 * a division expression, and the export evaluated to NaN — rendering
 * <style>{NaN}</style> and stripping EVERY editor style in prod while build
 * and jsdom tests stayed green. This asserts the module evaluates to a real
 * stylesheet, front to back.
 */
describe('EDITOR_SHELL_STYLES integrity', () => {
  it('evaluates to one large CSS string (not NaN from a split template literal)', () => {
    expect(typeof EDITOR_SHELL_STYLES).toBe('string');
    expect(EDITOR_SHELL_STYLES.length).toBeGreaterThan(10_000);
  });

  it('contains selectors from the top, middle and bottom of the sheet', () => {
    // top
    expect(EDITOR_SHELL_STYLES).toContain('.mh-editor-shell{');
    // middle
    expect(EDITOR_SHELL_STYLES).toContain('.mh-el-editable{');
    // appended passes at the bottom (would vanish if the literal split early)
    expect(EDITOR_SHELL_STYLES).toContain('.mh-slash-menu{');
    expect(EDITOR_SHELL_STYLES).toContain('.mh-page-seam{');
  });

  it('has no stray backtick inside the stylesheet text', () => {
    expect(EDITOR_SHELL_STYLES).not.toContain('`');
  });
});
