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
    .select('*, video_collections(count)')
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
    id: String(c.id), // Convert bigint to string
    video_count: c.video_collections?.[0]?.count || 0,
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
  return { ...data, id: String(data.id), video_count: 0, is_shared: !!teamId };
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

export const addVideoToCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  // Get video_id from aweme_id
  const { data: video, error: videoError } = await supabase
    .from('douyin_videos')
    .select('id')
    .eq('aweme_id', videoAwemeId)
    .single();

  if (videoError || !video) throw new Error('Video not found');

  const { error } = await supabase
    .from('video_collections')
    .insert({
      collection_id: parseInt(collectionId),
      video_id: video.id,
      added_by: user.id,
    });

  if (error && error.code !== '23505') throw error;
};

export const removeVideoFromCollection = async (collectionId: string, videoAwemeId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  // Get video_id from aweme_id
  const { data: video, error: videoError } = await supabase
    .from('douyin_videos')
    .select('id')
    .eq('aweme_id', videoAwemeId)
    .single();

  if (videoError || !video) throw new Error('Video not found');

  const { error } = await supabase
    .from('video_collections')
    .delete()
    .eq('collection_id', parseInt(collectionId))
    .eq('video_id', video.id);

  if (error) throw error;
};

export const fetchVideoCollections = async (videoAwemeId: string): Promise<string[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  // Get video_id from aweme_id
  const { data: video, error: videoError } = await supabase
    .from('douyin_videos')
    .select('id')
    .eq('aweme_id', videoAwemeId)
    .single();

  if (videoError || !video) return [];

  const { data, error } = await supabase
    .from('video_collections')
    .select('collection_id')
    .eq('video_id', video.id);

  if (error) throw error;
  return (data || []).map(cv => String(cv.collection_id));
};
