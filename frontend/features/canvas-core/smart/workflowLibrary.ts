// features/canvas-core/smart/workflowLibrary.ts
//
// Workflow ↔ resource library glue (②-4, user-directed): instead of
// Infinite's separate asset manager, workflow templates live in MediaHub's
// EXISTING resource library (Supabase-storage backed) — saved through the
// normal upload endpoint into the team scope, read back through the
// authenticated file endpoint for import.

import {
  getResourceFileUrl,
  uploadResource,
} from '../../../services/resourceService';
import { getSupabaseClient } from '../../../supabaseClient';
import { workflowFilename, type WorkflowPayload } from './workflowIO';
import type { Resource } from '../../../types';

/** Upload a serialized workflow into the team library as a JSON file. */
export async function saveWorkflowToLibrary(
  payload: WorkflowPayload,
  scopeId: string,
): Promise<Resource> {
  const file = new File(
    [JSON.stringify(payload, null, 2)],
    workflowFilename(payload.nodes.length),
    { type: 'application/json' },
  );
  return uploadResource(file, scopeId);
}

/** Read a library workflow file back as text (the /file endpoint only
 *  accepts a JWT — memory: file token contract). */
export async function fetchWorkflowText(resourceId: string): Promise<string> {
  let token: string | undefined;
  try {
    const supabase = getSupabaseClient();
    const { data } = await supabase.auth.getSession();
    token = data.session?.access_token;
  } catch {
    token = undefined;
  }
  const res = await fetch(getResourceFileUrl(resourceId, token));
  if (!res.ok) {
    throw new Error(`Failed to read workflow from the library (${res.status})`);
  }
  return res.text();
}
