/**
 * Wire-shape fixtures for the episode routes.
 *
 * `GET /projects/{id}/episodes/progress` always carries `workflow` and
 * `surface_state` (backend `episode_repository._progress_row`), and
 * stringifies `episode_id` / `workflow.current_node_id`. Tests override only
 * what they assert on.
 */
import type { EpisodeProgress, EpisodeWorkflowRollup } from '../../types/api';

/** A workflow rollup for an episode with no workflow nodes yet. */
export function makeEpisodeWorkflow(
  overrides: Partial<EpisodeWorkflowRollup> = {},
): EpisodeWorkflowRollup {
  return {
    nodes_total: 0,
    nodes_done: 0,
    current_node_id: null,
    needs_input_count: 0,
    ...overrides,
  };
}

/** One `episodes/progress` row. */
export function makeEpisodeProgress(overrides: Partial<EpisodeProgress> = {}): EpisodeProgress {
  return {
    episode_id: '1',
    title: 'Ep 1',
    sort_order: 1,
    owner_id: null,
    script_count: 0,
    scene_count: 0,
    shots_total: 0,
    shots_done: 0,
    renders_count: 0,
    status: 'planned',
    workflow: makeEpisodeWorkflow(),
    surface_state: { script: false, storyboard: false },
    ...overrides,
  };
}
