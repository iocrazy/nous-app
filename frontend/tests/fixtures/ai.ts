/**
 * Wire-shape fixtures for the /api/v1/ai domain.
 *
 * Every field the generated schema marks required is present, and Snowflake
 * ids are JSON numbers exactly as the backend sends them (CLAUDE.md,
 * "边界 mock 必须用真实 JSON 形状"). Tests override only what they assert on.
 */
import type { AIGovernanceFlags } from '../../types/api';

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
