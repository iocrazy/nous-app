-- 049_fix_point_transactions_nullable_user.sql
-- Allow NULL user_id in point_transactions for system-generated entries
-- (e.g. welcome bonus, admin adjustments without a specific user context)

ALTER TABLE point_transactions ALTER COLUMN user_id DROP NOT NULL;
