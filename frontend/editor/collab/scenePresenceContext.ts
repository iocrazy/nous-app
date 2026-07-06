import { createContext } from 'react';
import type { PresenceUser } from './useScriptPresence';

/** scene id → other participants focused on that scene (self excluded upstream). */
export type ScenePresenceMap = Record<string, PresenceUser[]>;

/**
 * Feeds per-scene presence to node components (SceneFlowNode) without threading
 * it through the pure sceneNodeMapper — mirrors how ChapterActionContext feeds
 * chapter actions across the React Flow boundary. Defaults to empty so a node
 * rendered without a provider (tests, storyboard) simply shows no badge.
 */
export const ScenePresenceContext = createContext<ScenePresenceMap>({});
