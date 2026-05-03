-- 197 (P12): touch user_mcp_servers.updated_at on every UPDATE.
--
-- Mig 194 created the column with DEFAULT NOW() but no trigger, so a
-- row's updated_at stayed at created_at forever. Audit / debug ("when
-- did the user last touch this server?") needed updated_at to actually
-- update.
--
-- Pattern mirrors mig 169 (dbos_workflow_routing_touch trigger).

CREATE OR REPLACE FUNCTION public.touch_user_mcp_servers_updated_at()
RETURNS trigger AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_mcp_servers_touch ON public.user_mcp_servers;
CREATE TRIGGER user_mcp_servers_touch
  BEFORE UPDATE ON public.user_mcp_servers
  FOR EACH ROW EXECUTE FUNCTION public.touch_user_mcp_servers_updated_at();
