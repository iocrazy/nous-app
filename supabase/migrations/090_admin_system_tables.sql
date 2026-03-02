-- Admin System Tables Migration
-- Date: 2026-02-03
-- Description: Creates tables for user credits, transactions, pricing, system settings, and audit logs

-- ============================================
-- 1. User Credits Table
-- ============================================
CREATE TABLE IF NOT EXISTS user_credits (
    user_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    balance INTEGER NOT NULL DEFAULT 0,
    total_earned INTEGER NOT NULL DEFAULT 0,
    total_spent INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE user_credits IS 'User credit accounts for tracking balance and usage';
COMMENT ON COLUMN user_credits.balance IS 'Current available credits';
COMMENT ON COLUMN user_credits.total_earned IS 'Total credits earned (recharges, gifts, etc.)';
COMMENT ON COLUMN user_credits.total_spent IS 'Total credits spent on actions';

CREATE INDEX IF NOT EXISTS idx_user_credits_balance ON user_credits(balance);

-- ============================================
-- 2. Credit Transactions Table
-- ============================================
CREATE TABLE IF NOT EXISTS credit_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    amount INTEGER NOT NULL,
    type VARCHAR(50) NOT NULL CHECK (type IN ('recharge', 'consume', 'refund', 'gift', 'adjustment')),
    description TEXT,
    related_id VARCHAR(255),
    admin_id UUID REFERENCES auth.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE credit_transactions IS 'Log of all credit transactions';
COMMENT ON COLUMN credit_transactions.type IS 'Transaction type: recharge, consume, refund, gift, adjustment';
COMMENT ON COLUMN credit_transactions.related_id IS 'Related entity ID (e.g., video aweme_id for consumption)';
COMMENT ON COLUMN credit_transactions.admin_id IS 'Admin who performed the action (for adjustments/gifts)';

CREATE INDEX IF NOT EXISTS idx_credit_transactions_user_id ON credit_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_type ON credit_transactions(type);
CREATE INDEX IF NOT EXISTS idx_credit_transactions_created_at ON credit_transactions(created_at);

-- ============================================
-- 3. Credit Pricing Configuration Table
-- ============================================
CREATE TABLE IF NOT EXISTS credit_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action VARCHAR(100) UNIQUE NOT NULL,
    cost INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE credit_pricing IS 'Configurable pricing for different actions';
COMMENT ON COLUMN credit_pricing.action IS 'Action identifier (e.g., parse, download)';
COMMENT ON COLUMN credit_pricing.cost IS 'Credit cost for this action';
COMMENT ON COLUMN credit_pricing.is_active IS 'Whether this pricing rule is active';

-- Insert default pricing
INSERT INTO credit_pricing (action, cost, description) VALUES
    ('parse', 1, 'Parse a single video link'),
    ('download', 2, 'Download video after parsing'),
    ('ai_analysis', 5, 'AI content analysis'),
    ('storage_gb', 10, 'Storage per GB per month')
ON CONFLICT (action) DO NOTHING;

-- ============================================
-- 4. System Settings Table
-- ============================================
CREATE TABLE IF NOT EXISTS system_settings (
    key VARCHAR(100) PRIMARY KEY,
    value JSONB NOT NULL,
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by UUID REFERENCES auth.users(id)
);

COMMENT ON TABLE system_settings IS 'Global system configuration settings';
COMMENT ON COLUMN system_settings.key IS 'Setting key identifier';
COMMENT ON COLUMN system_settings.value IS 'Setting value in JSON format';
COMMENT ON COLUMN system_settings.updated_by IS 'Admin who last updated this setting';

-- Insert default system settings
INSERT INTO system_settings (key, value, description) VALUES
    ('site_name', '"MediaHub"', 'Site display name'),
    ('registration_enabled', 'true', 'Allow new user registration'),
    ('download_enabled', 'true', 'Allow video downloads'),
    ('ai_analysis_enabled', 'true', 'Allow AI content analysis'),
    ('new_user_credits', '100', 'Credits given to new users'),
    ('free_storage_gb', '5', 'Free storage quota in GB')
ON CONFLICT (key) DO NOTHING;

-- ============================================
-- 5. Add is_banned field to user_profiles
-- ============================================
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'user_profiles' AND column_name = 'is_banned'
    ) THEN
        ALTER TABLE user_profiles ADD COLUMN is_banned BOOLEAN NOT NULL DEFAULT false;
        COMMENT ON COLUMN user_profiles.is_banned IS 'Whether the user is banned from the platform';
    END IF;
END $$;

-- ============================================
-- 6. Audit Logs Table
-- ============================================
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    admin_id UUID NOT NULL REFERENCES auth.users(id),
    action VARCHAR(100) NOT NULL,
    target_type VARCHAR(50) NOT NULL,
    target_id VARCHAR(255) NOT NULL,
    details JSONB,
    ip_address VARCHAR(45),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE audit_logs IS 'Audit trail for admin actions';
COMMENT ON COLUMN audit_logs.admin_id IS 'Admin who performed the action';
COMMENT ON COLUMN audit_logs.action IS 'Action performed (e.g., ban_user, adjust_credits)';
COMMENT ON COLUMN audit_logs.target_type IS 'Type of target entity (e.g., user, video, setting)';
COMMENT ON COLUMN audit_logs.target_id IS 'ID of the target entity';
COMMENT ON COLUMN audit_logs.details IS 'Additional details about the action';
COMMENT ON COLUMN audit_logs.ip_address IS 'IP address of the admin';

CREATE INDEX IF NOT EXISTS idx_audit_logs_admin_id ON audit_logs(admin_id);
CREATE INDEX IF NOT EXISTS idx_audit_logs_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_audit_logs_target_type ON audit_logs(target_type);
CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at ON audit_logs(created_at);

-- ============================================
-- 7. Enable RLS
-- ============================================
ALTER TABLE user_credits ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE credit_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_logs ENABLE ROW LEVEL SECURITY;

-- ============================================
-- 8. RLS Policies
-- ============================================

-- User Credits RLS
CREATE POLICY "Users can view own credits" ON user_credits
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Admins can manage all credits" ON user_credits
    FOR ALL USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- Credit Transactions RLS
CREATE POLICY "Users can view own transactions" ON credit_transactions
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Admins can manage all transactions" ON credit_transactions
    FOR ALL USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- Credit Pricing RLS (anyone can view, admins can modify)
CREATE POLICY "Anyone can view pricing" ON credit_pricing
    FOR SELECT USING (true);

CREATE POLICY "Admins can manage pricing" ON credit_pricing
    FOR ALL USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- System Settings RLS (anyone can view, admins can modify)
CREATE POLICY "Anyone can view settings" ON system_settings
    FOR SELECT USING (true);

CREATE POLICY "Admins can manage settings" ON system_settings
    FOR ALL USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- Audit Logs RLS (admins only)
CREATE POLICY "Admins can view audit logs" ON audit_logs
    FOR SELECT USING (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

CREATE POLICY "Admins can create audit logs" ON audit_logs
    FOR INSERT WITH CHECK (
        EXISTS (SELECT 1 FROM user_profiles WHERE id = auth.uid() AND role = 'admin')
    );

-- ============================================
-- 9. Updated_at Triggers
-- ============================================

-- Note: update_updated_at_column() function already exists from 001_initial_schema.sql

DROP TRIGGER IF EXISTS update_user_credits_updated_at ON user_credits;
CREATE TRIGGER update_user_credits_updated_at
    BEFORE UPDATE ON user_credits
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_credit_pricing_updated_at ON credit_pricing;
CREATE TRIGGER update_credit_pricing_updated_at
    BEFORE UPDATE ON credit_pricing
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS update_system_settings_updated_at ON system_settings;
CREATE TRIGGER update_system_settings_updated_at
    BEFORE UPDATE ON system_settings
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ============================================
-- 10. Helper Functions
-- ============================================

-- Function to initialize user credits (can be called from trigger or service)
CREATE OR REPLACE FUNCTION initialize_user_credits()
RETURNS TRIGGER AS $$
DECLARE
    initial_credits INTEGER;
BEGIN
    -- Get initial credits from system settings
    SELECT COALESCE((value)::integer, 100)
    INTO initial_credits
    FROM system_settings
    WHERE key = 'new_user_credits';

    -- Create credit account with initial balance
    INSERT INTO user_credits (user_id, balance, total_earned)
    VALUES (NEW.id, initial_credits, initial_credits)
    ON CONFLICT (user_id) DO NOTHING;

    -- Log the initial credit gift if credits were given
    IF initial_credits > 0 THEN
        INSERT INTO credit_transactions (user_id, amount, type, description)
        VALUES (NEW.id, initial_credits, 'gift', 'Welcome bonus for new user');
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql SECURITY DEFINER;

-- Trigger to auto-initialize credits when user profile is created
DROP TRIGGER IF EXISTS user_profiles_init_credits ON user_profiles;
CREATE TRIGGER user_profiles_init_credits
    AFTER INSERT ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION initialize_user_credits();

-- ============================================
-- Done!
-- ============================================
-- After running this migration, verify by checking:
-- SELECT * FROM user_credits LIMIT 1;
-- SELECT * FROM credit_transactions LIMIT 1;
-- SELECT * FROM credit_pricing;
-- SELECT * FROM system_settings;
-- SELECT * FROM audit_logs LIMIT 1;
