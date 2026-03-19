import { InMemoryCanvasEventBus } from './eventBus';
import { nodeCatalog } from './nodeCatalog';
import { CanvasNodeFactory } from './nodeFactory';
import { CanvasToolProcessor } from './toolProcessor';
import { uuidGenerator } from '../infrastructure/idGenerator';
import { HttpAiGateway } from '../infrastructure/httpAiGateway';
import { HttpToolGateway } from '../infrastructure/httpToolGateway';

export const canvasEventBus = new InMemoryCanvasEventBus();
export const canvasNodeFactory = new CanvasNodeFactory(uuidGenerator, nodeCatalog);

// Image split gateway — call configure(projectId, assetId) before using split.
export const canvasToolGateway = new HttpToolGateway();

// Tool processor for crop, annotate, and split operations.
export const canvasToolProcessor = new CanvasToolProcessor(canvasToolGateway, uuidGenerator);

// AI gateway for image/video generation (Phase 3 will add real usage).
export const canvasAiGateway = new HttpAiGateway();
