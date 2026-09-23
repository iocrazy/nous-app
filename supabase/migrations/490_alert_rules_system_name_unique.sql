-- 490: system anchor alert_rules rows are unique by name.
--
-- System alert_history writers (the hourly agent-cost anomaly sweep, the
-- scope resolver's denied audit) hang their history rows off an "anchor" rule
-- that ensure_anchor_rule (backend/app/services/alerting/anchor_rule.py)
-- looks up by name and creates on first use. Anchor rows are exactly the ones
-- with created_by IS NULL: the helper never sets it, and the admin create
-- endpoint always stamps the calling admin's id.
--
-- Get-then-insert let two first-ever writers race and create two anchors with
-- the same name. This partial UNIQUE index is the arbiter the helper's
-- INSERT ... ON CONFLICT (name) WHERE created_by IS NULL DO NOTHING targets.
-- Rules created by a user (created_by set) keep free naming: the predicate
-- leaves them out.
--
-- Safe to add: production alert_rules had 0 rows when this was written.
-- Idempotent (IF NOT EXISTS). The ORM mirror is AlertRules.__table_args__ in
-- backend/app/models/alerting.py; the schema-drift gate compares the two.

CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_rules_system_name
    ON public.alert_rules (name)
    WHERE created_by IS NULL;
