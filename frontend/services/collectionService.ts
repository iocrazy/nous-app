import { getSupabaseClient } from '../supabaseClient';
import { Collection } from '../types';

// NOTE: media_collections table has been dropped (076 migration).
// Collection-media associations are now managed via resource_items + folders.
// The collections table itself still exists for metadata.
export const fetchMyCollections = async (): Promise<Collection[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return [];

  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id')
    .eq('user_id', user.id);

  const teamIds = memberships?.map(m => m.team_id) || [];

  let query = supabase
    .from('collections')
    .select('*')
    .order('created_at', { ascending: false });

  if (teamIds.length > 0) {
    query = query.or(`owner_id.eq.${user.id},team_id.in.(${teamIds.join(',')})`);
  } else {
    query = query.eq('owner_id', user.id);
  }

  const { data, error } = await query;
  if (error) throw error;

  return (data || []).map((c) => ({
    ...c,
    id: String(c.id),
    media_count: 0,
    video_count: 0,
    is_shared: !!c.team_id,
    thumbnail_url: undefined,
  }));
};

export const createCollection = async (name: string, teamId?: string): Promise<Collection> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data, error } = await supabase
    .from('collections')
    .insert({
      name,
      owner_id: user.id,
      team_id: teamId || null,
    })
    .select()
    .single();

  if (error) throw error;
  return { ...data, id: String(data.id), media_count: 0, video_count: 0, is_shared: !!teamId };
};

export const deleteCollection = async (collectionId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('collections')
    .delete()
    .eq('id', parseInt(collectionId));

  if (error) throw error;
};

// NOTE: media_collections table dropped. This is a no-op stub until
// collection-video association is rebuilt on resource_items.
export const addVideoToCollection = async (_collectionId: string, _videoAwemeId: string): Promise<void> => {
  console.warn('addVideoToCollection: media_collections table has been dropped, operation skipped');
};

// NOTE: media_collections table dropped. This is a no-op stub.
export const removeVideoFromCollection = async (_collectionId: string, _videoAwemeId: string): Promise<void> => {
  console.warn('removeVideoFromCollection: media_collections table has been dropped, operation skipped');
};

// NOTE: media_collections table dropped. Returns empty until rebuilt on resource_items.
export const fetchVideoCollections = async (_videoAwemeId: string): Promise<string[]> => {
  return [];
};
