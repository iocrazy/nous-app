/**
 * Wire-shape fixtures for the /api/v1/ai domain.
 *
 * Every field the generated schema marks required is present, and Snowflake
 * ids are JSON numbers exactly as the backend sends them (CLAUDE.md,
 * "边界 mock 必须用真实 JSON 形状"). Tests override only what they assert on.
 */
import type { AIGovernanceFlags, NousModelPublic } from '../../types/api';

let nextNousModelId = 7_300_000_000_000_101;

/** A `GET /ai/nous-models` row: an ok-probed platform LLM. */
export function makeNousModel(overrides: Partial<NousModelPublic> = {}): NousModelPublic {
  return {
    id: nextNousModelId++,
    name: 'nous-llm',
    display_name: 'Nous LLM',
    actual_model: '',
    type: 'llm',
    pricing_type: 'per_token',
    pricing_value: 1,
    sort_order: 0,
    last_test_status: null,
    last_tested_at: null,
    last_test_code: null,
    is_local: false,
    ...overrides,
  };
}

/** `GET /ai/governance` with every module open and Nous off. */
export function makeGovernance(overrides: Partial<AIGovernanceFlags> = {}): AIGovernanceFlags {
  return {
    caption: true,
    chat: true,
    classification: true,
    embedding: true,
    summarization: true,
    topic_scorer: true,
    transcription: true,
    translation: true,
    visual_analysis: true,
    nous_enabled: false,
    nous_modules: {},
    ...overrides,
  };
}
