import { getSupabaseClient } from '../supabaseClient';
import { Notification } from '../types';

export interface NotificationWithRead extends Notification {
  read: boolean;
}

export const fetchNotifications = async (): Promise<NotificationWithRead[]> => {
  const supabase = getSupabaseClient();
  if (!supabase) return [];

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) return [];

  const { data: memberships } = await supabase
    .from('team_members')
    .select('team_id')
    .eq('user_id', claims.sub);

  const teamIds = memberships?.map(m => m.team_id) || [];

  let query = supabase
    .from('notifications')
    .select('*')
    .order('created_at', { ascending: false })
    .limit(50);

  if (teamIds.length > 0) {
    query = query.or(`type.eq.system,team_id.in.(${teamIds.join(',')})`);
  } else {
    query = query.eq('type', 'system');
  }

  const { data: notifications, error } = await query;
  if (error) throw error;
  if (!notifications?.length) return [];

  const notificationIds = notifications.map(n => n.id);
  const { data: readStatus } = await supabase
    .from('user_notifications')
    .select('notification_id, read_at')
    .eq('user_id', claims.sub)
    .in('notification_id', notificationIds);

  const readMap = new Map(readStatus?.map(r => [r.notification_id, !!r.read_at]) || []);

  return notifications.map(n => ({
    ...n,
    read: readMap.get(n.id) || false,
  }));
};

export const markAsRead = async (notificationId: string): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  await supabase
    .from('user_notifications')
    .upsert({
      user_id: claims.sub,
      notification_id: notificationId,
      read_at: new Date().toISOString(),
    });
};

export const markAllAsRead = async (): Promise<void> => {
  const supabase = getSupabaseClient();
  if (!supabase) throw new Error('Supabase not configured');

  const { data: { claims } } = await supabase.auth.getClaims();
  if (!claims) throw new Error('Not authenticated');

  const notifications = await fetchNotifications();
  const unreadIds = notifications.filter(n => !n.read).map(n => n.id);

  if (unreadIds.length === 0) return;

  const upserts = unreadIds.map(id => ({
    user_id: claims.sub,
    notification_id: id,
    read_at: new Date().toISOString(),
  }));

  await supabase.from('user_notifications').upsert(upserts);
};

export const getUnreadCount = async (): Promise<number> => {
  const notifications = await fetchNotifications();
  return notifications.filter(n => !n.read).length;
};
