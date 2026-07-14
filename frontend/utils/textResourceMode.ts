// Pure classifier for text-resource preview/editing (spec 2026-07-14).
// Decides which editor mode a resource's detail page should use — never
// mutates anything, no I/O.

export const TEXT_EDIT_MAX_BYTES = 512 * 1024;

const MARKDOWN_EXTS = new Set(['md', 'markdown']);

// Extensions we treat as editable plain text even when the mime isn't text/*.
const PLAINTEXT_EXTS = new Set([
  'txt', 'env', 'json', 'log', 'csv', 'yaml', 'yml', 'ini', 'conf', 'toml',
  'py', 'js', 'jsx', 'ts', 'tsx', 'sh', 'bash', 'html', 'htm', 'css', 'scss',
  'xml', 'svg', 'sql', 'go', 'rs', 'java', 'c', 'cpp', 'h', 'rb', 'php',
]);

// Extension → lowlight language name (lowlight `common` set). Only extensions
// whose grammar ships in `common` map to a name; the rest highlight as plain.
const LOWLIGHT_LANG: Record<string, string> = {
  json: 'json',
  yaml: 'yaml',
  yml: 'yaml',
  py: 'python',
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  html: 'xml',
  htm: 'xml',
  xml: 'xml',
  svg: 'xml',
  css: 'css',
  scss: 'css',
};

function extOf(filename: string | null): string {
  if (!filename) return '';
  const dot = filename.lastIndexOf('.');
  return dot >= 0 ? filename.slice(dot + 1).toLowerCase() : '';
}

export function codeLangForExtension(ext: string): string | null {
  return LOWLIGHT_LANG[ext.toLowerCase()] ?? null;
}

export function classifyTextResource(input: {
  filename: string | null;
  mime: string | null;
  sizeBytes: number | null;
}): 'markdown' | 'plaintext' | 'oversize' | null {
  const mime = (input.mime ?? '').toLowerCase();
  const ext = extOf(input.filename);

  const isMarkdown = MARKDOWN_EXTS.has(ext) || mime === 'text/markdown';
  const isText =
    isMarkdown || mime.startsWith('text/') || PLAINTEXT_EXTS.has(ext);

  if (!isText) return null;

  const size = input.sizeBytes ?? 0;
  if (size > TEXT_EDIT_MAX_BYTES) return 'oversize';

  return isMarkdown ? 'markdown' : 'plaintext';
}
