import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('./InspirationPage', () => ({
  InspirationPage: () => <div data-testid="new-page">new</div>,
}));
vi.mock('../hooks/useTopicModuleEnabled', () => ({
  useTopicModuleStatus: () => ({ visible: true, enabled: true }),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (_k: string, fallback?: string) => fallback ?? _k }),
}));
vi.mock('../components/Toast', () => ({ useToast: () => ({ addToast: vi.fn() }) }));
vi.mock('../services/topicService', () => ({
  getHotspots: vi.fn().mockResolvedValue([]),
  getHotspotDates: vi.fn().mockResolvedValue([]),
  getInterest: vi.fn().mockResolvedValue({ interest_text: '', has_embedding: false }),
  setInterest: vi.fn(),
  setHotspotState: vi.fn(),
}));

describe('TopicInspirationPage flag switch', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it('renders the new InspirationPage when flag is on', async () => {
    vi.stubEnv('VITE_FEATURE_INSPIRATION_NOTES', 'true');
    const { TopicInspirationPage } = await import('./TopicInspirationPage');
    render(<TopicInspirationPage />);
    expect(screen.getByTestId('new-page')).toBeTruthy();
  });

  it('renders the legacy page when flag is off', async () => {
    vi.stubEnv('VITE_FEATURE_INSPIRATION_NOTES', 'false');
    const { TopicInspirationPage } = await import('./TopicInspirationPage');
    render(<TopicInspirationPage />);
    expect(screen.queryByTestId('new-page')).toBeNull();
  });
});
