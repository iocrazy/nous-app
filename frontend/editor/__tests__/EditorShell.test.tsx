import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';

// i18n: return the key so assertions are stable and language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

const svc = vi.hoisted(() => ({
  listScenes: vi.fn(),
  listEpisodes: vi.fn(),
  convertToScenes: vi.fn(),
  moveScene: vi.fn(),
}));
vi.mock('../sceneService', () => svc);

// EditorShell now loads legacy chapters + surfaces convert toasts; stub both so
// the shell can render without a ToastProvider or a real network call.
vi.mock('../../services/scriptService', () => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
}));

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
    // Loaded scenes surface in the scene list (the location also appears as a
    // Locations entity row, so scope to the scene rail).
    const sceneRail = screen.getByTestId('scene-rail');
    expect(within(sceneRail).getByText('Rooftop Access')).toBeInTheDocument();
  });

  it('exposes the save indicator (saved when all scenes are clean)', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('save-indicator')).toBeInTheDocument());
    expect(screen.getByTestId('save-indicator')).toHaveAttribute('data-state', 'saved');
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
    // Outline mode shows the real outline tree (Task 4).
    expect(screen.getByTestId('outline-view')).toBeInTheDocument();
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

    // "Rooftop Access" now also appears as a Locations entity row, so scope to
    // the scene list to click the scene row specifically.
    const sceneRail = screen.getByTestId('scene-rail');
    fireEvent.click(within(sceneRail).getByText('Rooftop Access'));
    expect(scrollSpy).toHaveBeenCalled();
  });

  it('orders the rail: modules nav, then Characters, then the Scenes list', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('navigation')).toBeInTheDocument());

    const modules = screen.getByLabelText('editor.modulesLabel');
    const characters = screen.getByLabelText('editor.charactersLabel');
    const scenesLabel = screen.getByText('editor.scenesLabel');

    // DOCUMENT_POSITION_FOLLOWING (4) means the arg comes after in document order.
    expect(modules.compareDocumentPosition(characters) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(characters.compareDocumentPosition(scenesLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it('persists the chosen format per script and renders the asian engine', async () => {
    localStorage.removeItem('editor.format.42');
    svc.listScenes.mockResolvedValue(twoScenes);
    const { container } = render(<EditorShell scriptId="42" />);
    // Wait for the SCENES (Hollywood lines), not the main landmark — the
    // landmark exists during the loading state too, so slow runners raced
    // the listScenes resolution and flaked on the .hw-action assertion.
    await waitFor(() =>
      expect(container.querySelector('.hw-action')).toBeInTheDocument(),
    );

    // Starts in Hollywood.
    expect(container.querySelector('.as-prefix')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'editor.asian' }));

    // Engine switches and the choice is persisted under the per-script key.
    expect(container.querySelector('.as-prefix')).toBeInTheDocument();
    expect(container.querySelector('.hw-action')).toBeNull();
    expect(localStorage.getItem('editor.format.42')).toBe('asian');
  });
});
