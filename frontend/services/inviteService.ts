import { getSupabaseClient } from '../supabaseClient';

export interface TeamInvite {
  id: string;
  team_id: string;
  code: string;
  created_by: string;
  expires_at: string | null;
  max_uses: number | null;
  use_count: number;
  created_at: string;
}

export type ExpiryOption = '30m' | '1h' | '6h' | '12h' | '1d' | '7d' | 'never';

export const createInvite = async (
  teamId: string,
  options: {
    expiresIn?: ExpiryOption;
    maxUses?: number | null;
  } = {}
): Promise<TeamInvite> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  let expires_at: string | null = null;
  if (options.expiresIn && options.expiresIn !== 'never') {
    const now = new Date();
    const expiryMap: Record<string, number> = {
      '30m': 30 * 60 * 1000,
      '1h': 60 * 60 * 1000,
      '6h': 6 * 60 * 60 * 1000,
      '12h': 12 * 60 * 60 * 1000,
      '1d': 24 * 60 * 60 * 1000,
      '7d': 7 * 24 * 60 * 60 * 1000,
    };
    expires_at = new Date(now.getTime() + expiryMap[options.expiresIn]).toISOString();
  }

  const { data, error } = await supabase
    .from('team_invites')
    .insert({
      team_id: teamId,
      created_by: claims.sub,
      expires_at,
      max_uses: options.maxUses || null,
    })
    .select()
    .single();

  if (error) throw error;
  return data;
};

export const fetchInvites = async (teamId: string): Promise<TeamInvite[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data, error } = await supabase
    .from('team_invites')
    .select('*')
    .eq('team_id', teamId)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data || [];
};

export const deleteInvite = async (inviteId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { error } = await supabase
    .from('team_invites')
    .delete()
    .eq('id', inviteId);

  if (error) throw error;
};

export const acceptInvite = async (code: string): Promise<{ teamId: string; teamName: string }> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  // Find the invite
  const { data: invite, error: inviteError } = await supabase
    .from('team_invites')
    .select('*, teams(id, name)')
    .eq('code', code)
    .single();

  if (inviteError || !invite) throw new Error('Invalid invite code');

  // Check expiration
  if (invite.expires_at && new Date(invite.expires_at) < new Date()) {
    throw new Error('Invite has expired');
  }

  // Check max uses
  if (invite.max_uses && invite.use_count >= invite.max_uses) {
    throw new Error('Invite has reached max uses');
  }

  // Add user to team
  const { error: memberError } = await supabase
    .from('team_members')
    .insert({ team_id: invite.team_id, user_id: claims.sub, role: 'member' });

  if (memberError) {
    if (memberError.code === '23505') throw new Error('Already a member');
    throw memberError;
  }

  // Increment use count
  await supabase
    .from('team_invites')
    .update({ use_count: invite.use_count + 1 })
    .eq('id', invite.id);

  return {
    teamId: invite.team_id,
    teamName: (invite.teams as { id: string; name: string })?.name || 'Unknown Team',
  };
};

export const getInviteLink = (code: string): string => {
  const baseUrl = window.location.origin;
  return `${baseUrl}/invite/${code}`;
};
