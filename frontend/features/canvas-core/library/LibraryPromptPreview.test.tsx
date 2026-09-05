import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
// The real `t` interpolates `{{name}}` / `{{count}}` from the options object,
// so a mock that hands back the raw defaultValue would render "Insert slide
// {{name}}" where the app renders a filename — a mock that disagrees with the
// boundary it stands in for. Same shape as LibraryGrid.test.tsx next door.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (k: string, d?: unknown) =>
      typeof d === 'string'
        ? d
        : ((d as { defaultValue?: string } | undefined)?.defaultValue ?? k).replace(
            /\{\{(\w+)\}\}/g,
            (_m: string, n: string) => String((d as Record<string, unknown> | undefined)?.[n] ?? ''),
          ),
  }),
}));
vi.mock('../../../utils/apiConfig', () => ({ getApiUrl: () => 'https://api.test' }));
import { LibraryPromptPreview, activeSlide } from './LibraryPromptPreview';
import type { PromptEntry } from '../../../services/promptsService';

const image: PromptEntry = { key: 'image:10', form: 'image', origin: 'extracted', title: 'Bicycle', tags: [], positive_en: 'cheerful', positive_zh: null, negative_en: 'flare', negative_zh: null, params: { width: 1920, height: 1080, steps: 28 }, thumbs: [{ url: '/api/v1/resources/10/cover', kind: 'image' }], slides: null, source: { store: 'uploads', id: '10' }, updated_at: '' };
const album: PromptEntry = { ...image, key: 'album:7', form: 'album', title: 'Harvest', params: null, slides: [
  { name: '001.jpg', url: null, positive_en: null, positive_zh: null, negative_en: null, negative_zh: null },
  { name: '002.jpg', url: '/api/v1/media/9/slides/002.jpg', positive_en: 'winking', positive_zh: '眨眼', negative_en: 'blur', negative_zh: null },
  { name: '003.jpg', url: null, positive_en: 'leaning', positive_zh: null, negative_en: null, negative_zh: null },
] };
const noop = () => {};
const props = { lang: 'en' as const, onLangChange: noop, slideName: null, onSlideChange: noop, canAct: true, actHint: '', onInsert: noop, onApplyAll: noop, onSaveAsTemplate: noop };

describe('activeSlide', () => {
  it('defaults to the first slide that has text', () => {
    expect(activeSlide(album, null)?.name).toBe('002.jpg');
    expect(activeSlide(album, '003.jpg')?.name).toBe('003.jpg');
    expect(activeSlide(image, null)).toBeNull();
  });
});

describe('LibraryPromptPreview', () => {
  it('image: positive/negative/params blocks and the three actions', () => {
    const onInsert = vi.fn(), onApplyAll = vi.fn(), onSave = vi.fn();
    render(<LibraryPromptPreview {...props} entry={image} onInsert={onInsert} onApplyAll={onApplyAll} onSaveAsTemplate={onSave} />);
    expect(screen.getByTestId('library-prompt-positive')).toHaveTextContent('cheerful');
    expect(screen.getByTestId('library-prompt-negative')).toHaveTextContent('flare');
    expect(screen.getByTestId('library-prompt-params')).toHaveTextContent('16:9');
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' })); expect(onInsert).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' })); expect(onApplyAll).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Save as template…' })); expect(onSave).toHaveBeenCalled();
  });
  it('album: slide rows replace the positive block; textless slides disabled; selected slide names the actions', () => {
    const onSlideChange = vi.fn();
    render(<LibraryPromptPreview {...props} entry={album} slideName="002.jpg" onSlideChange={onSlideChange} />);
    const rows = screen.getAllByTestId('library-prompt-slide');
    expect(rows).toHaveLength(3);
    expect(rows[0].querySelector('button')).toBeDisabled();
    expect(rows[1]).toHaveAttribute('data-active', 'true');
    expect(screen.getByRole('button', { name: 'Insert slide 002.jpg' })).toBeInTheDocument();
    expect(screen.getByTestId('library-prompt-negative')).toHaveTextContent('blur');
    fireEvent.click(rows[2]);
    expect(onSlideChange).toHaveBeenCalledWith('003.jpg');
  });
  it('no target: actions disabled with the hint; template hides Save as template', () => {
    render(<LibraryPromptPreview {...props} entry={{ ...image, form: 'template', origin: 'typed' }} canAct={false} actHint="Pick a prompt node first" />);
    expect(screen.getByRole('button', { name: 'Insert positive' })).toBeDisabled();
    expect(screen.getByText('Pick a prompt node first')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save as template…' })).toBeNull();
  });
  it('language toggle greys a missing side and shows the other with a note', () => {
    const onLangChange = vi.fn();
    render(<LibraryPromptPreview {...props} entry={image} lang="zh" onLangChange={onLangChange} />);
    expect(screen.getByTestId('library-prompt-positive')).toHaveTextContent('cheerful');
    expect(screen.getByText('EN only')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'EN' }));
    expect(onLangChange).toHaveBeenCalledWith('en');
  });
  // The four cases above never mount the empty right column, the captioned
  // rules, the textless entry, or a slide's own Insert — all four are
  // prescribed behaviour, and all four are branches nothing else reaches.
  it('no entry: invites a pick rather than showing an empty frame', () => {
    render(<LibraryPromptPreview {...props} entry={null} />);
    expect(screen.getByText('Pick a prompt to preview it')).toBeInTheDocument();
    expect(screen.queryByTestId('library-prompt-preview')).toBeNull();
  });
  it('captioned: says the text did not make the picture and drops the params', () => {
    render(<LibraryPromptPreview {...props} entry={{ ...image, origin: 'captioned' }} />);
    expect(screen.getByText('This text describes the picture, it did not make it')).toBeInTheDocument();
    expect(screen.queryByTestId('library-prompt-params')).toBeNull();
  });
  it('nothing to insert: both actions stay disabled even with a target', () => {
    render(<LibraryPromptPreview {...props} entry={{ ...image, positive_en: null, positive_zh: null, negative_en: null }} />);
    expect(screen.getByTestId('library-prompt-positive')).toHaveTextContent('No positive prompt');
    expect(screen.getByRole('button', { name: 'Insert positive' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Apply all' })).toBeDisabled();
  });
  it("a slide's own Insert selects that slide as well as inserting it", () => {
    const onSlideChange = vi.fn(), onInsert = vi.fn();
    render(<LibraryPromptPreview {...props} entry={album} slideName="002.jpg" onSlideChange={onSlideChange} onInsert={onInsert} />);
    const rows = screen.getAllByTestId('library-prompt-slide');
    fireEvent.click(rows[2].querySelector('button')!);
    expect(onSlideChange).toHaveBeenCalledWith('003.jpg');
    expect(onInsert).toHaveBeenCalledWith('003.jpg');
  });
  // Ruling R17. The per-slide button selects and acts in one tick, so a parent
  // reading its own `slideName` would act on the slide the user just left.
  // These three assertions are what make the argument the source of truth.
  it('the slide travels through the callbacks — the parent never has to look it up', () => {
    const onInsert = vi.fn(), onApplyAll = vi.fn();
    const { rerender } = render(<LibraryPromptPreview {...props} entry={album} slideName="002.jpg" onInsert={onInsert} onApplyAll={onApplyAll} />);
    fireEvent.click(screen.getAllByTestId('library-prompt-slide')[2].querySelector('button')!);
    expect(onInsert).toHaveBeenLastCalledWith('003.jpg');   // the row acted on, not the row selected
    fireEvent.click(screen.getByRole('button', { name: 'Insert slide 002.jpg' }));
    expect(onInsert).toHaveBeenLastCalledWith('002.jpg');
    fireEvent.click(screen.getByRole('button', { name: 'Apply all from 002.jpg' }));
    expect(onApplyAll).toHaveBeenLastCalledWith('002.jpg');
    rerender(<LibraryPromptPreview {...props} entry={image} onInsert={onInsert} onApplyAll={onApplyAll} />);
    fireEvent.click(screen.getByRole('button', { name: 'Insert positive' }));
    expect(onInsert).toHaveBeenLastCalledWith(undefined);
    fireEvent.click(screen.getByRole('button', { name: 'Apply all' }));
    expect(onApplyAll).toHaveBeenLastCalledWith(undefined);
  });
  // Ruling R18. An album has no Positive block, which is where the note used
  // to live — so this is the case that had no note at all.
  it('album: a one-sided slide gets the shown-language note too', () => {
    render(<LibraryPromptPreview {...props} entry={album} lang="zh" slideName="003.jpg" />);
    expect(screen.getByText('EN only')).toBeInTheDocument();
    expect(screen.getAllByTestId('library-prompt-slide')).toHaveLength(3);
  });
});
