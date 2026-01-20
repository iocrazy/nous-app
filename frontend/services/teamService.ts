import { getSupabaseClient } from '../supabaseClient';
import { Team, TeamMember } from '../types';

export const fetchMyTeams = async (): Promise<Team[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id');

  if (!memberships?.length) return [];

  const teamIds = memberships.map(m => m.team_id);
  const { data, error } = await supabase
    .from('teams')
    .select('*')
    .in('id', teamIds)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
};

export const createTeam = async (name: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user }, error: authError } = await supabase.auth.getUser();
  if (authError) {
    console.error('Auth error:', authError);
    throw new Error('Authentication failed: ' + authError.message);
  }
  if (!user) throw new Error('Not authenticated - please log in again');

  console.log('Creating team for user:', user.id);

  const { data, error } = await supabase
    .from('teams')
    .insert({ name, owner_id: user.id })
    .select()
    .single();

  if (error) {
    console.error('Create team error:', error);
    throw new Error(error.message || 'Failed to create team');
  }

  console.log('Team created:', data);
  return data;
};

export const joinTeamByCode = async (inviteCode: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { data: team, error: teamError } = await supabase
    .from('teams')
    .select('*')
    .eq('invite_code', inviteCode.toUpperCase())
    .single();

  if (teamError || !team) throw new Error('Invalid invite code');

  const { error: memberError } = await supabase
    .from('team_members')
    .insert({ team_id: team.id, user_id: user.id, role: 'member' });

  if (memberError) {
    if (memberError.code === '23505') throw new Error('Already a member');
    throw memberError;
  }

  return team;
};

export const leaveTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { user } } = await supabase.auth.getUser();
  if (!user) throw new Error('Not authenticated');

  const { error } = await supabase
    .from('team_members')
    .delete()
    .eq('team_id', teamId)
    .eq('user_id', user.id);

  if (error) throw error;
};

export const deleteTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('teams')
    .delete()
    .eq('id', teamId);

  if (error) throw error;
};

export const fetchTeamMembers = async (teamId: string): Promise<TeamMember[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('team_members')
    .select('*')
    .eq('team_id', teamId)
    .order('joined_at', { ascending: true });

  if (error) throw error;
  return data || [];
};

export const updateTeam = async (teamId: string, updates: { name?: string; description?: string }): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data, error } = await supabase
    .from('teams')
    .update(updates)
    .eq('id', teamId)
    .select()
    .single();

  if (error) throw error;
  return data;
};

export const updateMemberRole = async (
  teamId: string,
  userId: string,
  role: 'admin' | 'member'
): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_members')
    .update({ role })
    .eq('team_id', teamId)
    .eq('user_id', userId);

  if (error) throw error;
};

export const removeMember = async (teamId: string, userId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_members')
    .delete()
    .eq('team_id', teamId)
    .eq('user_id', userId);

  if (error) throw error;
};
