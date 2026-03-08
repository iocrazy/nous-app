-- 099_daily_point_gifts.sql
-- Daily point gifts tracking table

CREATE TABLE IF NOT EXISTS daily_point_gifts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES auth.users(id),
    team_id UUID NOT NULL,
    gift_date DATE NOT NULL,
    amount_granted INT NOT NULL DEFAULT 0,
    amount_consumed INT NOT NULL DEFAULT 0,
    amount_reclaimed INT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'granted' CHECK (status IN ('granted', 'reclaimed')),
    granted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    reclaimed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, gift_date)
);

CREATE INDEX idx_daily_point_gifts_date_status ON daily_point_gifts(gift_date, status);
CREATE INDEX idx_daily_point_gifts_user ON daily_point_gifts(user_id);

-- RLS: admin-only via service_role
ALTER TABLE daily_point_gifts ENABLE ROW LEVEL SECURITY;
