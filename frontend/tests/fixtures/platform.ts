/**
 * Wire-shape fixtures for the platform model list (spec 2026-09-25 §3.1/§3.3).
 *
 * Shapes are copied from the backend wire tests
 * (`backend/tests/api/test_ai_settings_wire.py`: `test_settings_carry_the_platform_view`,
 * `test_unreachable_engine_keeps_the_list`, `test_platform_status`), not
 * invented: `ai_providers.nous` carries `enabled / managed / models /
 * enabled_models / disabled_models`; `platform_models[name]` carries exactly
 * the eight mapping keys (no key, host, display name or sort order); the
 * status endpoint answers `{models: {name: {status, local_ready, superseded}},
 * engine}`. `aiService.getAISettings` passes all of these through unchanged,
 * so the same objects serve as the normalized settings a component receives.
 */
import type { AISettings } from '../../types';
import type {
  PlatformEngineState,
  PlatformModelEntry,
  PlatformModelRuntime,
  PlatformStatusResponse,
} from '../../types/api';

export interface PlatformRowSpec extends Partial<PlatformModelEntry> {
  name: string;
  /** In the user's blacklist (listed, not enabled). */
  disabled?: boolean;
}

export function platformEntry(overrides: Partial<PlatformModelEntry> = {}): PlatformModelEntry {
  const type = overrides.type ?? 'llm';
  return {
    actual_model: 'qwen3-8b',
    type,
    status: 'ok',
    is_local: false,
    pricing_type: 'per_token',
    pricing_value: 1,
    context_window_tokens: 32768,
    // The server's default answer for an ordinary row: image/video rows can
    // generate, everything else cannot. Upscale-only rows override to false.
    generatable: type === 'image' || type === 'video',
    ...overrides,
  };
}

export const ENGINE_OK: PlatformEngineState = {
  reachable: true,
  stale: false,
  checked_at: '2026-09-25T08:00:00Z',
};

/** `test_unreachable_engine_keeps_the_list`: every row stays, unknown. */
export const ENGINE_DOWN: PlatformEngineState = {
  reachable: false,
  stale: false,
  checked_at: null,
};

export interface PlatformWireOptions {
  /** The user's master switch (stored value). */
  enabled?: boolean;
  engine?: PlatformEngineState | null;
}

/** The platform part of a `GET /ai/settings` body. */
export function platformSettingsWire(
  rows: PlatformRowSpec[],
  { enabled = true, engine = ENGINE_OK }: PlatformWireOptions = {},
): {
  ai_providers: { nous: Record<string, unknown> };
  platform_models: Record<string, PlatformModelEntry>;
  platform_engine: PlatformEngineState | null;
} {
  const models = rows.map((r) => r.name);
  const disabled = rows.filter((r) => r.disabled).map((r) => r.name);
  return {
    ai_providers: {
      nous: {
        enabled,
        managed: true,
        models,
        enabled_models: models.filter((n) => !disabled.includes(n)),
        disabled_models: disabled,
      },
    },
    platform_models: Object.fromEntries(
      rows.map(({ name, disabled: _d, ...entry }) => [name, platformEntry(entry)]),
    ),
    platform_engine: engine,
  };
}

/** `settings` with the platform card, mapping and engine state set. */
export function withPlatform(
  settings: AISettings,
  rows: PlatformRowSpec[],
  options: PlatformWireOptions = {},
): AISettings {
  const wire = platformSettingsWire(rows, options);
  return {
    ...settings,
    providers: {
      ...settings.providers,
      nous: wire.ai_providers.nous as unknown as AISettings['providers'][string],
    },
    platform_models: wire.platform_models,
    platform_engine: wire.platform_engine,
  };
}

/** A `GET /ai/platform-status` body. */
export function platformStatusWire(
  models: Record<string, Partial<PlatformModelRuntime>>,
  engine: PlatformEngineState | null = ENGINE_OK,
): PlatformStatusResponse {
  return {
    models: Object.fromEntries(
      Object.entries(models).map(([name, m]) => [
        name,
        { status: 'ok', local_ready: null, superseded: false, ...m },
      ]),
    ),
    engine,
  };
}

/** Minimal AISettings every component test can extend. */
export function baseAISettings(overrides: Partial<AISettings> = {}): AISettings {
  return {
    ai_enabled: true,
    auto_transcribe: false,
    auto_summarize: false,
    preferred_language: 'auto',
    providers: {},
    task_assignment: {
      transcription: '',
      summarization: '',
      visual_analysis: '',
      translation: '',
      caption: '',
      classification: '',
      image_generation: '',
      script_generation: '',
    },
    ...overrides,
  };
}
