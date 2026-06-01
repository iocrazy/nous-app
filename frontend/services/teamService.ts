import { getSupabaseClient } from '../supabaseClient';
import { Team, TeamMember } from '../types';

// PostgREST returns BIGINT as JSON number; ensure IDs are always strings.
// teams.is_personal was dropped in PR-E (mig 243) in favour of teams.kind;
// derive the legacy boolean from kind so downstream consumers (types.Team,
// SettingsPage, useNavigation) keep working unchanged.
function normalizeTeam(t: any): Team {
  return { ...t, id: String(t.id), is_personal: t.kind === 'personal' };
}

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
    .neq('kind', 'personal')
    .order('created_at', { ascending: false });

  if (error) throw error;
  return (data || []).map(normalizeTeam);
};

export const fetchPersonalTeam = async (): Promise<Team | null> => {
  const supabase = getSupabaseClient();
  if (!supabase) return null;

  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id');

  if (!memberships?.length) return null;

  const teamIds = memberships.map(m => m.team_id);
  const { data, error } = await supabase
    .from('teams')
    .select('*')
    .in('id', teamIds)
    .eq('kind', 'personal')
    .limit(1)
    .maybeSingle();

  if (error || !data) return null;
  return normalizeTeam(data);
};

export const createTeam = async (name: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims }, error: authError } = await supabase.auth.getClaims();
  if (authError) {
    console.error('Auth error:', authError);
    throw new Error('Authentication failed: ' + authError.message);
  }
  if (!claims) throw new Error('Not authenticated - please log in again');

  const { data, error } = await supabase
    .from('teams')
    .insert({ name, owner_id: claims.sub })
    .select()
    .single();

  if (error) {
    console.error('Create team error:', error);
    throw new Error(error.message || 'Failed to create team');
  }

  return normalizeTeam(data);
};

export const joinTeamByCode = async (inviteCode: string): Promise<Team> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  const { data: team, error: teamError } = await supabase
    .from('teams')
    .select('*')
    .eq('invite_code', inviteCode.toUpperCase())
    .single();

  if (teamError || !team) throw new Error('Invalid invite code');

  const { error: memberError } = await supabase
    .from('team_members')
    .insert({ team_id: team.id, user_id: claims.sub, role: 'member' });

  if (memberError) {
    if (memberError.code === '23505') throw new Error('Already a member');
    throw memberError;
  }

  return normalizeTeam(team);
};

export const leaveTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  const { error } = await supabase
    .from('team_members')
    .delete()
    .eq('team_id', teamId)
    .eq('user_id', claims.sub);

  if (error) throw error;
};

export const deleteTeam = async (teamId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase.rpc('delete_team_with_cleanup', {
    target_team_id: teamId,
  });

  if (error) {
    console.error('[teamService] deleteTeam RPC failed:', error);
    throw error;
  }
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
  return normalizeTeam(data);
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
