import { render, screen, waitFor, fireEvent, cleanup, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { SceneDoc } from '../types';

// i18n: return the key so assertions are stable and language-independent.
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));

// Mock the FULL sceneService surface, not just the handful this file's older
// tests happened to touch. EditorShell always mounts WritingPanel → VersionPanel,
// which calls listCommits() on mount; a missing export makes that an
// `undefined is not a function` throw on every render — the stderr noise that
// perturbed effect scheduling and, under parallel CI load, flaked the rail
// scroll test (F5-v2). Keep this in lockstep with sceneService's real exports:
// every network function is a vi.fn(), list-returning ones default to [], and
// the error classes are real (so `instanceof` in catch paths behaves).
const svc = vi.hoisted(() => ({
  // Scenes
  listScenes: vi.fn(),
  getScene: vi.fn(),
  createScene: vi.fn(),
  deleteScene: vi.fn(),
  updateSceneMeta: vi.fn(),
  moveScene: vi.fn(),
  applyOps: vi.fn(),
  convertToScenes: vi.fn(),
  autoStoryboard: vi.fn(),
  updateChapterPosition: vi.fn(),
  newElementId: () => 'el_test0001',
  // Episodes
  listEpisodes: vi.fn().mockResolvedValue([]),
  createEpisode: vi.fn(),
  updateEpisode: vi.fn(),
  deleteEpisode: vi.fn(),
  // Shots
  listShots: vi.fn().mockResolvedValue([]),
  getShot: vi.fn(),
  createShot: vi.fn(),
  updateShot: vi.fn(),
  deleteShot: vi.fn(),
  moveShot: vi.fn(),
  generateShot: vi.fn(),
  generateShotVideo: vi.fn(),
  // Version history (P4) — VersionPanel loads these on mount
  listCommits: vi.fn().mockResolvedValue([]),
  createCommit: vi.fn(),
  deleteCommit: vi.fn(),
  rollbackCommit: vi.fn(),
  diffCommit: vi.fn(),
  // Error classes (real, for instanceof checks in catch paths)
  OpRejectedError: class OpRejectedError extends Error {},
  VersionConflictError: class VersionConflictError extends Error {},
  ShotGenerateDisabledError: class ShotGenerateDisabledError extends Error {},
  ShotVideoDisabledError: class ShotVideoDisabledError extends Error {},
}));
vi.mock('../sceneService', () => svc);

// EditorShell now loads legacy chapters + surfaces convert toasts; stub both so
// the shell can render without a ToastProvider or a real network call.
vi.mock('../../services/scriptService', () => ({
  fetchScriptProject: vi.fn().mockResolvedValue({ chapters: [] }),
}));
vi.mock('../../components/Toast', () => ({
  useToast: () => ({ addToast: vi.fn() }),
  useOptionalToast: () => ({ addToast: vi.fn() }),
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
  // The shell inherits the app theme from <html data-theme> — reset so a
  // theme set inside one test never leaks into the next.
  delete document.documentElement.dataset.theme;
});

describe('EditorShell', () => {
  it('renders three landmark zones after loading scenes', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);

    // Two navigation landmarks now: the rail island (brand / episodes / Views /
    // entities) and the floating scene TOC — so scope the rail assertion by name.
    await waitFor(() =>
      expect(screen.getByRole('navigation', { name: 'editor.railNav' })).toBeInTheDocument(),
    );
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('complementary')).toBeInTheDocument();
    // Loaded scenes surface in the scene list (the location also appears as a
    // Locations entity row, so scope to the scene rail).
    const sceneRail = screen.getByTestId('scene-rail');
    expect(within(sceneRail).getByText('Rooftop Access')).toBeInTheDocument();
  });

  it('embedded (studio) mode: the editor owns scene navigation via the floating scene TOC, still lifting scenes for the slate', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    const onScenesChange = vi.fn();
    const { container } = render(
      <EditorShell scriptId="1" embedded onScenesChange={onScenesChange} />,
    );
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument());

    // Scene navigation is the floating SceneToc (a labelled navigation landmark),
    // NOT a boxed rail and NOT lifted into the workspace tree. Its panel reuses
    // the SceneRail list, so the scene rail renders right here.
    expect(screen.getByRole('complementary')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'editor.scenesNav' })).toBeInTheDocument();
    expect(screen.getByTestId('scene-toc')).toBeInTheDocument();
    const sceneRail = await screen.findByTestId('scene-rail');
    expect(within(sceneRail).getByText('Rooftop Access')).toBeInTheDocument();
    expect(container.querySelector('.mh-editor-shell.mh-embedded')).toBeInTheDocument();

    // …but the rail stays SLIM: the standalone chrome (brand row + episode
    // management selector + module nav) is NOT rendered — the workspace tree
    // owns that navigation (合一终稿, 2026-07-11).
    expect(container.querySelector('.mh-brand-row')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('editor.manageEpisodes')).not.toBeInTheDocument();

    // Scenes are still reported up so the workspace top-bar slate can show the
    // active scene number.
    await waitFor(() => {
      const last = onScenesChange.mock.calls.at(-1)?.[0];
      expect(last).toHaveLength(2);
    });
  });

  it('standalone keeps the rail brand row + episode management selector', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    const { container } = render(<EditorShell scriptId="1" />);
    await waitFor(() =>
      expect(screen.getByRole('navigation', { name: 'editor.railNav' })).toBeInTheDocument(),
    );
    expect(screen.getByLabelText('editor.manageEpisodes')).toBeInTheDocument();
    expect(container.querySelector('.mh-brand-row')).toBeInTheDocument();
    expect(container.querySelector('.mh-editor-shell.mh-embedded')).not.toBeInTheDocument();
  });

  it('exposes the save indicator (saved when all scenes are clean)', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByTestId('save-indicator')).toBeInTheDocument());
    expect(screen.getByTestId('save-indicator')).toHaveAttribute('data-state', 'saved');
  });

  it('follows the app theme (<html data-theme>) with NO local toggle', async () => {
    document.documentElement.dataset.theme = 'light';
    svc.listScenes.mockResolvedValue(twoScenes);
    const { container } = render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument());

    const root = container.querySelector('.mh-editor-shell') as HTMLElement;
    expect(root.getAttribute('data-theme')).toBe('light');

    // Theme is global-only (Settings → Theme): the per-shell ☾/☼ override is
    // gone by user verdict — no such button renders anywhere in the shell.
    expect(screen.queryByRole('button', { name: 'editor.toggleTheme' })).toBeNull();

    // App theme change re-syncs the shell (MutationObserver on <html>).
    document.documentElement.dataset.theme = 'dark';
    await waitFor(() => expect(root.getAttribute('data-theme')).toBe('dark'));
    document.documentElement.dataset.theme = 'light';
    await waitFor(() => expect(root.getAttribute('data-theme')).toBe('light'));
  });

  it('mounts dark when the app theme is dark', async () => {
    document.documentElement.dataset.theme = 'dark';
    svc.listScenes.mockResolvedValue(twoScenes);
    const { container } = render(<EditorShell scriptId="1" />);
    await waitFor(() => expect(screen.getByRole('main')).toBeInTheDocument());
    const root = container.querySelector('.mh-editor-shell') as HTMLElement;
    expect(root.getAttribute('data-theme')).toBe('dark');
    delete document.documentElement.dataset.theme;
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
    expect(screen.getByRole('navigation', { name: 'editor.railNav' })).toBeInTheDocument();
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

    // Settle the rail AND the specific scene row before clicking — findBy retries
    // until they render, killing the pre-click race that flaked under load (F5).
    // "Rooftop Access" also appears as a Locations entity row, so scope to the
    // scene list to click the scene row specifically.
    const sceneRail = await screen.findByTestId('scene-rail');
    const sceneRow = await within(sceneRail).findByText('Rooftop Access');
    fireEvent.click(sceneRow);
    // Retry window on the assertion too (harmless): the scroll can land a tick
    // after the click's state flush.
    await waitFor(() => expect(scrollSpy).toHaveBeenCalled());
  });

  it('toolbar "Scene" inserts the new scene AFTER the active scene, not at the tail', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    svc.createScene.mockReset().mockResolvedValue(scene({ id: '999', sort_order: 2 }));
    svc.moveScene.mockReset().mockResolvedValue(undefined);
    HTMLElement.prototype.scrollIntoView = vi.fn();
    render(<EditorShell scriptId="1" />);

    // Focus the SECOND scene by clicking its rail row → activeSceneId = '222'.
    const sceneRail = await screen.findByTestId('scene-rail');
    fireEvent.click(await within(sceneRail).findByText('Rooftop Access'));

    // Click the toolbar "Scene" button — should create + move next to the cursor.
    fireEvent.click(screen.getByRole('button', { name: 'editor.toolbarScene' }));

    await waitFor(() => expect(svc.createScene).toHaveBeenCalled());
    await waitFor(() =>
      expect(svc.moveScene).toHaveBeenCalledWith('999', { after_scene_id: '222' }),
    );
  });

  it('orders the rail: modules nav, then Characters, then the Scenes list', async () => {
    svc.listScenes.mockResolvedValue(twoScenes);
    render(<EditorShell scriptId="1" />);
    await waitFor(() =>
      expect(screen.getByRole('navigation', { name: 'editor.railNav' })).toBeInTheDocument(),
    );

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

    // The format flip re-keys each scene's TipTap editor, which remounts its
    // ProseMirror view asynchronously — wait for the asian typeset to render.
    await waitFor(() => expect(container.querySelector('.as-prefix')).toBeInTheDocument());
    expect(container.querySelector('.hw-action')).toBeNull();
    // Choice persisted under the per-script key.
    expect(localStorage.getItem('editor.format.42')).toBe('asian');
  });
});
