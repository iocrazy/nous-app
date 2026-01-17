import { getSupabaseClient } from '../supabaseClient';
import { Collection } from '../types';

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
    .select('*, collection_videos(count)')
    .order('created_at', { ascending: false });

  if (teamIds.length > 0) {
    query = query.or(`owner_id.eq.${user.id},team_id.in.(${teamIds.join(',')})`);
  } else {
    query = query.eq('owner_id', user.id);
  }

  const { data, error } = await query;
  if (error) throw error;

  return (data || []).map(c => ({
    ...c,
    video_count: c.collection_videos?.[0]?.count || 0,
    is_shared: !!c.team_id,
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
  return { ...data, video_count: 0, is_shared: !!teamId };
};

export const deleteCollection = async (collectionId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('collections')
    .delete()
    .eq('id', collectionId);

  if (error) throw error;
};

export const addVideoToCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { error } = await supabase
    .from('collection_videos')
    .insert({
      collection_id: collectionId,
      video_aweme_id: videoAwemeId,
      added_by: user.id,
    });

  if (error && error.code !== '23505') throw error;
};

export const removeVideoFromCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('collection_videos')
    .delete()
    .eq('collection_id', collectionId)
    .eq('video_aweme_id', videoAwemeId);

  if (error) throw error;
};

export const fetchVideoCollections = async (videoAwemeId: string): Promise<string[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('collection_videos')
    .select('collection_id')
    .eq('video_aweme_id', videoAwemeId);

  if (error) throw error;
  return (data || []).map(cv => cv.collection_id);
};
