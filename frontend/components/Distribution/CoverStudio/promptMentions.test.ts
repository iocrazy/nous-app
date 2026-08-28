import { describe, expect, it } from 'vitest';

import type { CoverReference } from './coverReferences';
import { expandMentions, insertMention, mentionLabel, mentionToken } from './promptMentions';

const person: CoverReference = { kind: 'person', genId: '1', url: '/p' };
const frame: CoverReference = { kind: 'frame', genId: '2', url: '/f', timestampSeconds: 3.1 };
const tpl: CoverReference = { kind: 'template', genId: '3', url: '/t', label: 'Bold {headline}', templateId: 'r3' };

describe('promptMentions', () => {
  it('labels are human and brace-free', () => {
    expect(mentionLabel(person)).toBe('person');
    expect(mentionLabel(frame)).toBe('frame 0:03.1');
    expect(mentionLabel(tpl)).toBe('Bold headline');
    expect(mentionToken(tpl)).toBe('@{Bold headline}');
  });

  it('inserting replaces the @query the user was typing and leaves a space', () => {
    const out = insertMention('look at @bol please', 'look at @bol'.length, '@{Bold headline}');
    expect(out.text).toBe('look at @{Bold headline}  please');
    expect(out.caret).toBe('look at @{Bold headline} '.length);
  });

  it('expands by POSITION in send order, person first', () => {
    const text = 'copy @{Bold headline}, keep @{person}, ignore @{frame 0:03.1}';
    expect(expandMentions(text, [person, frame, tpl])).toBe(
      'copy reference image 3 (Bold headline), keep reference image 1 (person), ignore reference image 2 (frame 0:03.1)',
    );
  });

  it('leaves a mention of a picture that is no longer in the pool as written', () => {
    expect(expandMentions('see @{gone}', [person])).toBe('see @{gone}');
  });
});
