const STORYBOARD_AT_TAG_REGEX = /@\s*\u56FE\d+/g;
const STORYBOARD_AT_PREFIX_REGEX = /@(?=\s*\u56FE\d+)/g;

export function sanitizeStoryboardText(input: string, ignoreAtTag: boolean): string {
  if (!ignoreAtTag) {
    return input.trim();
  }

  return input
    .replace(STORYBOARD_AT_TAG_REGEX, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}

export function sanitizeStoryboardPromptText(input: string): string {
  return input
    .replace(STORYBOARD_AT_PREFIX_REGEX, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim();
}
