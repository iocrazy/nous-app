import { InMemoryCanvasEventBus } from './eventBus';
import { nodeCatalog } from './nodeCatalog';
import { CanvasNodeFactory } from './nodeFactory';
import { uuidGenerator } from '../infrastructure/idGenerator';

export const canvasEventBus = new InMemoryCanvasEventBus();
export const canvasNodeFactory = new CanvasNodeFactory(uuidGenerator, nodeCatalog);

// TODO: migrate — these require backend integration to replace Tauri gateways
// export const graphImageResolver = ...
// export const canvasToolProcessor = ...
// export const canvasAiGateway = ...
