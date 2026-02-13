import { supabase } from '../supabaseClient';
import { Folder, Resource, ResourceItem } from '../types';

// ─── Folders ────────────────────────────────────────────

export async function fetchFolders(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<Folder[]> {
  const { data, error } = await supabase
    .from('folders')
    .select('*')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId)
    .eq('is_trashed', false)
    .order('sort_order', { ascending: true });

  if (error) throw error;
  return data || [];
}

export async function createFolder(folder: {
  name: string;
  parent_id?: string | null;
  scope_type: 'personal' | 'team';
  scope_id: string;
}): Promise<Folder> {
  const user = (await supabase.auth.getUser()).data.user;
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('folders')
    .insert({ ...folder, created_by: user.id })
    .select()
    .single();

  if (error) throw error;
  return data;
}

export async function renameFolder(id: string, name: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ name })
    .eq('id', id);

  if (error) throw error;
}

export async function trashFolder(id: string): Promise<void> {
  const { error } = await supabase
    .from('folders')
    .update({ is_trashed: true, trashed_at: new Date().toISOString() })
    .eq('id', id);

  if (error) throw error;
}

// Build folder tree from flat list
export function buildFolderTree(folders: Folder[]): Folder[] {
  const map = new Map<string, Folder>();
  const roots: Folder[] = [];

  folders.forEach(f => map.set(f.id, { ...f, children: [] }));

  map.forEach(folder => {
    if (folder.parent_id && map.has(folder.parent_id)) {
      map.get(folder.parent_id)!.children!.push(folder);
    } else {
      roots.push(folder);
    }
  });

  return roots;
}

// ─── Resources ──────────────────────────────────────────

export async function fetchResources(
  scopeType: 'personal' | 'team',
  scopeId: string,
  folderId?: string | null
): Promise<ResourceItem[]> {
  let query = supabase
    .from('resource_items')
    .select('*, resource:resources(*)')
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  if (folderId) {
    query = query.eq('folder_id', folderId);
  } else {
    query = query.is('folder_id', null);
  }

  const { data, error } = await query.order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
}

export async function fetchResourceCount(
  scopeType: 'personal' | 'team',
  scopeId: string
): Promise<number> {
  const { count, error } = await supabase
    .from('resource_items')
    .select('*', { count: 'exact', head: true })
    .eq('scope_type', scopeType)
    .eq('scope_id', scopeId);

  if (error) throw error;
  return count || 0;
}
