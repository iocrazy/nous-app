import { render, screen, waitFor, fireEvent, cleanup } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';

// i18n: return the key so assertions are stable and language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listEpisodes: vi.fn(),
}));
vi.mock('../sceneService', () => svc);

import { EditorShell } from '../components/EditorShell';

const scene = (over: Partial<SceneDoc>): SceneDoc => ({
  id: '324520385049690',
  script_id: '1',
  chapter_id: null,
  heading_int_ext: 'INT',
  location_text: 'Blank Studio',
  time_of_day: 'NIGHT',
  content_version: 1,
  sort_order: 0,
  elements: [{ id: 'el_00000001', type: 'action', text: 'One pool of light.' }],
  ...over,
});

const twoScenes: SceneDoc[] = [
  scene({ id: '111', sort_order: 0, heading_int_ext: 'INT', location_text: 'Blank Studio' }),
  scene({ id: '222', sort_order: 1, heading_int_ext: 'EXT', location_text: 'Rooftop Access' }),
];

afterEach(() => {
  cleanup();
  svc.listScenes.mockReset();
  svc.listEpisodes.mockReset();
});

describe('EditorShell', () => {
  it('renders three landmark zones after loading scenes', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);

    await waitFor(() => expect(screen.getByRole('navigation')).toBeInTheDocument());
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('complementary')).toBeInTheDocument();
    // Loaded scenes surface in the rail.
    expect(screen.getByText('Rooftop Access')).toBeInTheDocument();
  });

  it('exposes the save-indicator slot', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('save-indicator-slot')).toBeInTheDocument());
  });

  it('toggles data-theme and persists to localStorage', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    const { container } = render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument());

    const root = container.querySelector('.mh-editor-shell') as HTMLElement;
    expect(root.getAttribute('data-theme')).toBe('light');

    fireEvent.click(screen.getByRole('button', { name: 'editor.toggleTheme' }));

    expect(root.getAttribute('data-theme')).toBe('dark');
    expect(localStorage.getItem('editor.theme')).toBe('dark');
  });

  it('switches the active tab and panel content on click', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument());

    const scriptTab = screen.getByRole('tab', { name: 'editor.tabScript' });
    const outlineTab = screen.getByRole('tab', { name: 'editor.tabOutline' });
    expect(scriptTab).toHaveAttribute('aria-selected', 'true');
    expect(outlineTab).toHaveAttribute('aria-selected', 'false');

    fireEvent.click(outlineTab);

    expect(outlineTab).toHaveAttribute('aria-selected', 'true');
    expect(scriptTab).toHaveAttribute('aria-selected', 'false');
    // Outline mode shows the read-only outline placeholder.
    expect(screen.getByTestId('outline-placeholder')).toBeInTheDocument();
  });

  it('collapses the right panel without losing the navigation landmark', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('complementary')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'editor.collapseLeft' }));
    // Navigation must NOT disappear — the collapsed strip is still a landmark.
    expect(screen.getByRole('navigation')).toBeInTheDocument();
  });

  it('shows an error state and logs when the fetch fails', async () => {
    const err = new Error('boom');
    svc.listScenes.mockRejectedValue(err);
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<EditorShell scriptId="1" />);

    await waitFor(() => expect(screen.getByText('editor.loadError')).toBeInTheDocument());
    expect(spy).toHaveBeenCalled();
  });

  it('scrolls the matching scene block into view when a rail row is clicked', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    // jsdom does not implement scrollIntoView — install a mock to observe the call.
    const scrollSpy = vi.fn();
    HTMLElement.prototype.scrollIntoView = scrollSpy;
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('scene-rail')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Rooftop Access'));
    expect(scrollSpy).toHaveBeenCalled();
  });
});
