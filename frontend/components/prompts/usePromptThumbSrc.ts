// components/prompts/usePromptThumbSrc.ts
//
// One place that binds `thumbSrc` to the session's media token, so the three
// thumbnail renderers (shared thumbs strip, panel preview slides, resource
// library album card) cannot disagree about how an album slide is fetched.
import { useCallback } from 'react';

import { useOptionalAuth } from '../../contexts/AuthContext';
import { thumbSrc } from '../../services/promptsService';

export function usePromptThumbSrc(): (url: string | null | undefined) => string {
  const mediaToken = useOptionalAuth()?.mediaToken ?? null;
  return useCallback((url: string | null | undefined) => thumbSrc(url, mediaToken), [mediaToken]);
}
