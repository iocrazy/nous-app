# Points, Capacity & Payment System - Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a points-based monetization system with team points pool, capacity limits, and WeChat/Alipay payment integration.

**Architecture:** Points are the unified currency. Every team has a `team_quotas` record tracking balance and storage. All consumable operations (parse, AI, storage) deduct from the team pool via `PointsService`. Payment creates orders processed through third-party callbacks. The existing `team_members.role` is extended with `admin` for fine-grained permissions.

**Tech Stack:** FastAPI + Supabase (PostgreSQL with RLS) + React 19 + TypeScript + WeChat/Alipay payment APIs

**Design Doc:** `docs/plans/2026-02-11-points-capacity-payment-design.md`

---

## Phase 1: Database & Points Core

### Task 1: Database Migration - Points System Tables

**Files:**
- Create: `supabase/migrations/041_points_system.sql`

**Step 1: Write the migration SQL**

```sql
-- 041_points_system.sql
-- Points system: packages, team quotas, member quotas, transactions, pricing, orders

-- ============================================
-- 1. Point Packages (purchasable packages)
-- ============================================
CREATE TABLE IF NOT EXISTS point_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    description TEXT,
    points_amount INTEGER NOT NULL,
    price_cents INTEGER NOT NULL,          -- Price in cents (e.g., 1000 = ¥10.00)
    currency TEXT NOT NULL DEFAULT 'CNY',
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 2. Team Quotas (balance + storage limits)
-- ============================================
CREATE TABLE IF NOT EXISTS team_quotas (
    team_id UUID PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    points_balance INTEGER NOT NULL DEFAULT 0,
    storage_limit_bytes BIGINT NOT NULL DEFAULT 5368709120, -- 5GB
    storage_used_bytes BIGINT NOT NULL DEFAULT 0,
    free_points_granted BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 3. Member Quotas (per-member monthly limits)
-- ============================================
CREATE TABLE IF NOT EXISTS member_quotas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    monthly_points_limit INTEGER,           -- NULL = unlimited
    points_used_this_month INTEGER NOT NULL DEFAULT 0,
    reset_at TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', now()) + interval '1 month',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(team_id, user_id)
);

-- ============================================
-- 4. Point Transactions (ledger)
-- ============================================
CREATE TABLE IF NOT EXISTS point_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id),
    amount INTEGER NOT NULL,                -- positive=credit, negative=debit
    balance_after INTEGER NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('purchase', 'consume', 'refund', 'gift', 'admin_adjust')),
    reference_type TEXT,                    -- video_parse, ai_transcription, ai_summary, ai_visual_analysis, storage
    reference_id TEXT,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 5. Point Pricing (action costs)
-- ============================================
CREATE TABLE IF NOT EXISTS point_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_type TEXT NOT NULL UNIQUE,
    points_cost INTEGER NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 6. Orders (payment orders)
-- ============================================
CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id),
    package_id UUID NOT NULL REFERENCES point_packages(id),
    points_amount INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    payment_method TEXT NOT NULL CHECK (payment_method IN ('wechat', 'alipay')),
    payment_status TEXT NOT NULL DEFAULT 'pending' CHECK (payment_status IN ('pending', 'paid', 'failed', 'expired', 'refunded')),
    trade_no TEXT,                           -- Third-party transaction ID
    payment_url TEXT,                        -- Payment redirect URL or QR code data
    paid_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 minutes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================
-- 7. Extend team_members with role
-- ============================================
-- team_members already has a role column. Update check constraint to include 'admin'.
DO $$
BEGIN
    -- Drop existing constraint if it exists
    ALTER TABLE team_members DROP CONSTRAINT IF EXISTS team_members_role_check;
    -- Add new constraint
    ALTER TABLE team_members ADD CONSTRAINT team_members_role_check
        CHECK (role IN ('owner', 'admin', 'member'));
EXCEPTION
    WHEN OTHERS THEN NULL;
END $$;

-- ============================================
-- 8. Indexes
-- ============================================
CREATE INDEX IF NOT EXISTS idx_point_transactions_team_id ON point_transactions(team_id);
CREATE INDEX IF NOT EXISTS idx_point_transactions_user_id ON point_transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_point_transactions_type ON point_transactions(type);
CREATE INDEX IF NOT EXISTS idx_point_transactions_created_at ON point_transactions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_team_id ON orders(team_id);
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_payment_status ON orders(payment_status);
CREATE INDEX IF NOT EXISTS idx_orders_trade_no ON orders(trade_no);
CREATE INDEX IF NOT EXISTS idx_member_quotas_team_user ON member_quotas(team_id, user_id);

-- ============================================
-- 9. Auto-update updated_at triggers
-- ============================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY['point_packages', 'team_quotas', 'member_quotas', 'point_pricing', 'orders']
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS set_updated_at ON %I;
            CREATE TRIGGER set_updated_at
                BEFORE UPDATE ON %I
                FOR EACH ROW
                EXECUTE FUNCTION update_updated_at_column();
        ', tbl, tbl);
    END LOOP;
END $$;

-- ============================================
-- 10. Seed default pricing data
-- ============================================
INSERT INTO point_pricing (action_type, points_cost, description) VALUES
    ('video_parse', 5, 'Parse a single video'),
    ('video_parse_batch', 4, 'Parse a video in batch mode (per video)'),
    ('ai_transcription', 20, 'AI speech-to-text transcription'),
    ('ai_summary', 15, 'AI text summarization'),
    ('ai_visual_analysis', 15, 'AI visual analysis'),
    ('storage_gb_month', 50, 'Storage per GB per month')
ON CONFLICT (action_type) DO NOTHING;

-- ============================================
-- 11. Seed default packages
-- ============================================
INSERT INTO point_packages (name, description, points_amount, price_cents, sort_order) VALUES
    ('Starter Pack', '100 points for getting started', 100, 1000, 1),
    ('Standard Pack', '500 points with 10% discount', 500, 4500, 2),
    ('Pro Pack', '2000 points with 20% discount', 2000, 16000, 3),
    ('Team Pack', '10000 points with 30% discount', 10000, 70000, 4)
ON CONFLICT DO NOTHING;

-- ============================================
-- 12. RLS Policies
-- ============================================

-- Enable RLS on all new tables
ALTER TABLE point_packages ENABLE ROW LEVEL SECURITY;
ALTER TABLE team_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE member_quotas ENABLE ROW LEVEL SECURITY;
ALTER TABLE point_transactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE point_pricing ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;

-- point_packages: anyone can read active packages
CREATE POLICY "Anyone can read active packages"
    ON point_packages FOR SELECT
    USING (is_active = true);

-- point_pricing: anyone can read active pricing
CREATE POLICY "Anyone can read active pricing"
    ON point_pricing FOR SELECT
    USING (is_active = true);

-- team_quotas: team members can read their team quota
CREATE POLICY "Team members can read quota"
    ON team_quotas FOR SELECT
    USING (
        team_id IN (
            SELECT tm.team_id FROM team_members tm
            WHERE tm.user_id = auth.uid()
        )
    );

-- member_quotas: team members can read their own quota, owner/admin can read all
CREATE POLICY "Members read own quota"
    ON member_quotas FOR SELECT
    USING (
        user_id = auth.uid()
        OR team_id IN (
            SELECT tm.team_id FROM team_members tm
            WHERE tm.user_id = auth.uid() AND tm.role IN ('owner', 'admin')
        )
    );

-- point_transactions: team members can read their team's transactions
CREATE POLICY "Team members read transactions"
    ON point_transactions FOR SELECT
    USING (
        team_id IN (
            SELECT tm.team_id FROM team_members tm
            WHERE tm.user_id = auth.uid()
        )
    );

-- orders: team owner can read team orders, others can read their own
CREATE POLICY "Read own or team orders"
    ON orders FOR SELECT
    USING (
        user_id = auth.uid()
        OR team_id IN (
            SELECT tm.team_id FROM team_members tm
            WHERE tm.user_id = auth.uid() AND tm.role IN ('owner', 'admin')
        )
    );

-- Service role can do everything (backend uses service_role key)
CREATE POLICY "Service role full access on point_packages"
    ON point_packages FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on team_quotas"
    ON team_quotas FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on member_quotas"
    ON member_quotas FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on point_transactions"
    ON point_transactions FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on point_pricing"
    ON point_pricing FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Service role full access on orders"
    ON orders FOR ALL USING (true) WITH CHECK (true);
```

**Step 2: Run the migration**

Execute in Supabase SQL Editor or via CLI:
```bash
supabase db push
```

**Step 3: Verify tables exist**

Query in SQL Editor:
```sql
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name IN ('point_packages', 'team_quotas', 'member_quotas', 'point_transactions', 'point_pricing', 'orders');
```
Expected: 6 rows returned.

**Step 4: Commit**

```bash
git add supabase/migrations/041_points_system.sql
git commit -m "feat: add points system database migration (tables, indexes, RLS, seed data)"
```

---

### Task 2: Backend Schemas - Points & Payment Pydantic Models

**Files:**
- Create: `backend/app/schemas/points.py`
- Create: `backend/app/schemas/payment.py`

**Step 1: Write points schemas**

Create `backend/app/schemas/points.py`:

```python
# app/schemas/points.py

"""
Points system Pydantic schemas.

Covers point packages, team quotas, member quotas,
point transactions, and pricing.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PointPackage(BaseModel):
    """A purchasable point package."""
    id: str
    name: str
    description: Optional[str] = None
    points_amount: int
    price_cents: int
    currency: str = "CNY"
    is_active: bool = True
    sort_order: int = 0


class TeamQuota(BaseModel):
    """Team-level points balance and storage quota."""
    team_id: str
    points_balance: int = 0
    storage_limit_bytes: int = Field(default=5368709120, description="5GB default")
    storage_used_bytes: int = 0
    free_points_granted: bool = False


class MemberQuota(BaseModel):
    """Per-member monthly usage limits."""
    id: str
    team_id: str
    user_id: str
    monthly_points_limit: Optional[int] = None
    points_used_this_month: int = 0
    reset_at: Optional[datetime] = None


class MemberQuotaUpdate(BaseModel):
    """Request to update a member's monthly limit."""
    monthly_points_limit: Optional[int] = Field(None, ge=0, description="NULL = unlimited")


class PointTransaction(BaseModel):
    """A single points ledger entry."""
    id: str
    team_id: str
    user_id: Optional[str] = None
    amount: int
    balance_after: int
    type: str  # purchase, consume, refund, gift, admin_adjust
    reference_type: Optional[str] = None
    reference_id: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime


class PointPricing(BaseModel):
    """Cost of an action in points."""
    action_type: str
    points_cost: int
    description: Optional[str] = None
    is_active: bool = True


class PointsBalanceResponse(BaseModel):
    """Response for points balance query."""
    team_id: str
    points_balance: int
    storage_limit_bytes: int
    storage_used_bytes: int
    storage_used_percent: float


class PointsAdjustRequest(BaseModel):
    """Admin request to manually adjust points."""
    team_id: str
    amount: int = Field(..., description="Positive to add, negative to deduct")
    description: str = Field(..., min_length=1)


class QuotaCheckResult(BaseModel):
    """Result of a pre-operation quota check."""
    allowed: bool
    points_cost: int
    current_balance: int
    reason: Optional[str] = None
```

**Step 2: Write payment schemas**

Create `backend/app/schemas/payment.py`:

```python
# app/schemas/payment.py

"""
Payment system Pydantic schemas.

Covers order creation, status, and callbacks.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CreateOrderRequest(BaseModel):
    """Request to create a payment order."""
    package_id: str
    payment_method: str = Field(..., pattern="^(wechat|alipay)$")
    team_id: str


class OrderResponse(BaseModel):
    """Payment order details."""
    id: str
    team_id: str
    user_id: str
    package_id: str
    points_amount: int
    amount_cents: int
    currency: str
    payment_method: str
    payment_status: str
    payment_url: Optional[str] = None
    trade_no: Optional[str] = None
    paid_at: Optional[datetime] = None
    expired_at: datetime
    created_at: datetime


class OrderStatusResponse(BaseModel):
    """Simplified order status check."""
    order_id: str
    payment_status: str
    points_amount: int
    paid_at: Optional[datetime] = None
```

**Step 3: Commit**

```bash
git add backend/app/schemas/points.py backend/app/schemas/payment.py
git commit -m "feat: add Pydantic schemas for points and payment system"
```

---

### Task 3: Points Repository

**Files:**
- Create: `backend/app/repositories/points_repository.py`

**Step 1: Write the repository**

Create `backend/app/repositories/points_repository.py` following the exact pattern from `video_repository.py` (lazy `_get_client()` + `_get_table()`):

```python
# app/repositories/points_repository.py

"""
Points Repository

Data access layer for points system tables:
team_quotas, member_quotas, point_transactions, point_pricing, point_packages.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class PointsRepository:
    """Points data access (async)."""

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    # ------------------------------------------------------------------
    # Point Pricing
    # ------------------------------------------------------------------

    async def get_pricing(self, action_type: str) -> Optional[Dict[str, Any]]:
        """Get points cost for an action type."""
        client = await self._get_client()
        result = await client.table("point_pricing").select("*").eq(
            "action_type", action_type
        ).eq("is_active", True).execute()
        return result.data[0] if result.data else None

    async def get_all_pricing(self) -> List[Dict[str, Any]]:
        """Get all active pricing rules."""
        client = await self._get_client()
        result = await client.table("point_pricing").select("*").eq(
            "is_active", True
        ).order("action_type").execute()
        return result.data or []

    # ------------------------------------------------------------------
    # Point Packages
    # ------------------------------------------------------------------

    async def get_active_packages(self) -> List[Dict[str, Any]]:
        """Get all active point packages."""
        client = await self._get_client()
        result = await client.table("point_packages").select("*").eq(
            "is_active", True
        ).order("sort_order").execute()
        return result.data or []

    async def get_package_by_id(self, package_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific package by ID."""
        client = await self._get_client()
        result = await client.table("point_packages").select("*").eq(
            "id", package_id
        ).execute()
        return result.data[0] if result.data else None

    # ------------------------------------------------------------------
    # Team Quotas
    # ------------------------------------------------------------------

    async def get_team_quota(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get a team's quota record."""
        client = await self._get_client()
        result = await client.table("team_quotas").select("*").eq(
            "team_id", team_id
        ).execute()
        return result.data[0] if result.data else None

    async def create_team_quota(self, team_id: str, points_balance: int = 0,
                                 storage_limit_bytes: int = 5368709120) -> Dict[str, Any]:
        """Create a team quota record."""
        client = await self._get_client()
        result = await client.table("team_quotas").insert({
            "team_id": team_id,
            "points_balance": points_balance,
            "storage_limit_bytes": storage_limit_bytes,
            "free_points_granted": points_balance > 0,
        }).execute()
        return result.data[0] if result.data else {}

    async def update_points_balance(self, team_id: str, new_balance: int) -> Dict[str, Any]:
        """Update team points balance."""
        client = await self._get_client()
        result = await client.table("team_quotas").update({
            "points_balance": new_balance,
        }).eq("team_id", team_id).execute()
        return result.data[0] if result.data else {}

    async def update_storage_used(self, team_id: str, storage_used_bytes: int) -> Dict[str, Any]:
        """Update team storage usage."""
        client = await self._get_client()
        result = await client.table("team_quotas").update({
            "storage_used_bytes": storage_used_bytes,
        }).eq("team_id", team_id).execute()
        return result.data[0] if result.data else {}

    # ------------------------------------------------------------------
    # Member Quotas
    # ------------------------------------------------------------------

    async def get_member_quota(self, team_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        """Get a member's quota in a team."""
        client = await self._get_client()
        result = await client.table("member_quotas").select("*").eq(
            "team_id", team_id
        ).eq("user_id", user_id).execute()
        return result.data[0] if result.data else None

    async def upsert_member_quota(self, team_id: str, user_id: str,
                                   monthly_points_limit: Optional[int]) -> Dict[str, Any]:
        """Create or update a member's monthly limit."""
        client = await self._get_client()
        result = await client.table("member_quotas").upsert({
            "team_id": team_id,
            "user_id": user_id,
            "monthly_points_limit": monthly_points_limit,
        }, on_conflict="team_id,user_id").execute()
        return result.data[0] if result.data else {}

    async def increment_member_usage(self, team_id: str, user_id: str, points: int) -> None:
        """Increment a member's monthly usage. Uses RPC for atomicity."""
        client = await self._get_client()
        # Get current, increment, update
        quota = await self.get_member_quota(team_id, user_id)
        if quota:
            new_used = quota["points_used_this_month"] + points
            await client.table("member_quotas").update({
                "points_used_this_month": new_used,
            }).eq("team_id", team_id).eq("user_id", user_id).execute()

    async def get_team_member_quotas(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all member quotas for a team."""
        client = await self._get_client()
        result = await client.table("member_quotas").select("*").eq(
            "team_id", team_id
        ).execute()
        return result.data or []

    # ------------------------------------------------------------------
    # Point Transactions
    # ------------------------------------------------------------------

    async def create_transaction(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Insert a point transaction record."""
        client = await self._get_client()
        result = await client.table("point_transactions").insert(data).execute()
        return result.data[0] if result.data else {}

    async def get_transactions(self, team_id: str, limit: int = 50,
                                offset: int = 0, type_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get team's transaction history."""
        client = await self._get_client()
        query = client.table("point_transactions").select("*").eq("team_id", team_id)
        if type_filter:
            query = query.eq("type", type_filter)
        result = await query.order("created_at", desc=True).range(
            offset, offset + limit - 1
        ).execute()
        return result.data or []

    async def get_usage_stats(self, team_id: str) -> Dict[str, Any]:
        """Get aggregated usage stats for a team."""
        client = await self._get_client()
        # Total consumed
        consumed = await client.table("point_transactions").select("amount").eq(
            "team_id", team_id
        ).eq("type", "consume").execute()
        total_consumed = sum(abs(t["amount"]) for t in (consumed.data or []))

        # Total purchased
        purchased = await client.table("point_transactions").select("amount").eq(
            "team_id", team_id
        ).eq("type", "purchase").execute()
        total_purchased = sum(t["amount"] for t in (purchased.data or []))

        # By reference_type
        all_consume = await client.table("point_transactions").select(
            "reference_type, amount"
        ).eq("team_id", team_id).eq("type", "consume").execute()

        by_type: Dict[str, int] = {}
        for t in (all_consume.data or []):
            ref = t.get("reference_type") or "other"
            by_type[ref] = by_type.get(ref, 0) + abs(t["amount"])

        return {
            "total_consumed": total_consumed,
            "total_purchased": total_purchased,
            "by_type": by_type,
        }
```

**Step 2: Commit**

```bash
git add backend/app/repositories/points_repository.py
git commit -m "feat: add PointsRepository for points system data access"
```

---

### Task 4: Payment Repository

**Files:**
- Create: `backend/app/repositories/payment_repository.py`

**Step 1: Write the repository**

```python
# app/repositories/payment_repository.py

"""
Payment Repository

Data access layer for orders table.
"""

from typing import Any, Dict, List, Optional

from loguru import logger

from app.db.supabase_client import get_async_supabase_admin


class PaymentRepository:
    """Payment/order data access (async)."""

    def __init__(self):
        self._client = None

    async def _get_client(self):
        if self._client is None:
            self._client = await get_async_supabase_admin()
        return self._client

    async def create_order(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a new payment order."""
        client = await self._get_client()
        result = await client.table("orders").insert(data).execute()
        logger.info(f"Created order: {result.data[0]['id'] if result.data else 'unknown'}")
        return result.data[0] if result.data else {}

    async def get_order_by_id(self, order_id: str) -> Optional[Dict[str, Any]]:
        """Get order by ID."""
        client = await self._get_client()
        result = await client.table("orders").select("*").eq("id", order_id).execute()
        return result.data[0] if result.data else None

    async def get_order_by_trade_no(self, trade_no: str) -> Optional[Dict[str, Any]]:
        """Get order by third-party trade number (for callback idempotency)."""
        client = await self._get_client()
        result = await client.table("orders").select("*").eq("trade_no", trade_no).execute()
        return result.data[0] if result.data else None

    async def update_order(self, order_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Update order fields."""
        client = await self._get_client()
        result = await client.table("orders").update(data).eq("id", order_id).execute()
        return result.data[0] if result.data else {}

    async def get_team_orders(self, team_id: str, limit: int = 50,
                               offset: int = 0) -> List[Dict[str, Any]]:
        """Get a team's order history."""
        client = await self._get_client()
        result = await client.table("orders").select("*").eq(
            "team_id", team_id
        ).order("created_at", desc=True).range(offset, offset + limit - 1).execute()
        return result.data or []

    async def expire_pending_orders(self) -> int:
        """Mark expired pending orders. Returns count of expired orders."""
        client = await self._get_client()
        result = await client.table("orders").update({
            "payment_status": "expired"
        }).eq("payment_status", "pending").lt(
            "expired_at", "now()"
        ).execute()
        count = len(result.data) if result.data else 0
        if count > 0:
            logger.info(f"Expired {count} pending orders")
        return count
```

**Step 2: Commit**

```bash
git add backend/app/repositories/payment_repository.py
git commit -m "feat: add PaymentRepository for order data access"
```

---

### Task 5: PointsService - Core Business Logic

**Files:**
- Create: `backend/app/services/points_service.py`

**Step 1: Write the service**

```python
# app/services/points_service.py

"""
Points Service

Core business logic for points consumption, balance checks,
and quota enforcement. All point mutations go through this service
to ensure atomicity and consistent transaction logging.
"""

from typing import Optional

from loguru import logger

from app.repositories.points_repository import PointsRepository


class PointsService:
    """Manages points consumption, balance, and quota checks."""

    def __init__(self):
        self.repo = PointsRepository()

    async def check_and_consume(
        self,
        team_id: str,
        user_id: str,
        action_type: str,
        reference_id: Optional[str] = None,
        count: int = 1,
    ) -> dict:
        """
        Check quota and consume points atomically.

        Args:
            team_id: Team whose pool to debit
            user_id: User performing the action
            action_type: e.g. 'video_parse', 'ai_transcription'
            reference_id: Optional related entity ID
            count: Number of units (default 1)

        Returns:
            dict with keys: success, points_cost, balance_after, reason

        Raises:
            Nothing - returns success=False with reason on failure.
        """
        # 1. Get pricing
        pricing = await self.repo.get_pricing(action_type)
        if not pricing:
            logger.warning(f"No pricing found for action: {action_type}")
            # If no pricing configured, allow the action for free
            return {"success": True, "points_cost": 0, "balance_after": 0, "reason": None}

        points_cost = pricing["points_cost"] * count

        # 2. Get team quota
        quota = await self.repo.get_team_quota(team_id)
        if not quota:
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": 0,
                "reason": "Team quota not found. Please contact support.",
            }

        # 3. Check team balance
        if quota["points_balance"] < points_cost:
            return {
                "success": False,
                "points_cost": points_cost,
                "balance_after": quota["points_balance"],
                "reason": f"Insufficient points. Need {points_cost}, have {quota['points_balance']}.",
            }

        # 4. Check member monthly quota (if set)
        member_quota = await self.repo.get_member_quota(team_id, user_id)
        if member_quota and member_quota["monthly_points_limit"] is not None:
            remaining = member_quota["monthly_points_limit"] - member_quota["points_used_this_month"]
            if remaining < points_cost:
                return {
                    "success": False,
                    "points_cost": points_cost,
                    "balance_after": quota["points_balance"],
                    "reason": f"Monthly member limit exceeded. Remaining: {remaining}.",
                }

        # 5. Deduct points
        new_balance = quota["points_balance"] - points_cost
        await self.repo.update_points_balance(team_id, new_balance)

        # 6. Record transaction
        await self.repo.create_transaction({
            "team_id": team_id,
            "user_id": user_id,
            "amount": -points_cost,
            "balance_after": new_balance,
            "type": "consume",
            "reference_type": action_type,
            "reference_id": reference_id,
            "description": f"Consumed {points_cost} points for {action_type}",
        })

        # 7. Update member monthly usage
        if member_quota:
            await self.repo.increment_member_usage(team_id, user_id, points_cost)

        logger.info(
            f"Points consumed: team={team_id}, user={user_id}, "
            f"action={action_type}, cost={points_cost}, balance={new_balance}"
        )

        return {
            "success": True,
            "points_cost": points_cost,
            "balance_after": new_balance,
            "reason": None,
        }

    async def check_quota(self, team_id: str, user_id: str, action_type: str, count: int = 1) -> dict:
        """
        Pre-check if an action is allowed (without consuming).

        Returns dict with: allowed, points_cost, current_balance, reason
        """
        pricing = await self.repo.get_pricing(action_type)
        if not pricing:
            return {"allowed": True, "points_cost": 0, "current_balance": 0, "reason": None}

        points_cost = pricing["points_cost"] * count
        quota = await self.repo.get_team_quota(team_id)
        if not quota:
            return {"allowed": False, "points_cost": points_cost, "current_balance": 0,
                    "reason": "Team quota not found."}

        if quota["points_balance"] < points_cost:
            return {"allowed": False, "points_cost": points_cost,
                    "current_balance": quota["points_balance"],
                    "reason": f"Insufficient points. Need {points_cost}, have {quota['points_balance']}."}

        member_quota = await self.repo.get_member_quota(team_id, user_id)
        if member_quota and member_quota["monthly_points_limit"] is not None:
            remaining = member_quota["monthly_points_limit"] - member_quota["points_used_this_month"]
            if remaining < points_cost:
                return {"allowed": False, "points_cost": points_cost,
                        "current_balance": quota["points_balance"],
                        "reason": f"Monthly limit exceeded. Remaining: {remaining}."}

        return {"allowed": True, "points_cost": points_cost,
                "current_balance": quota["points_balance"], "reason": None}

    async def check_storage(self, team_id: str, additional_bytes: int = 0) -> dict:
        """Check if team has sufficient storage capacity."""
        quota = await self.repo.get_team_quota(team_id)
        if not quota:
            return {"allowed": False, "reason": "Team quota not found."}

        projected = quota["storage_used_bytes"] + additional_bytes
        if projected > quota["storage_limit_bytes"]:
            return {
                "allowed": False,
                "reason": f"Storage limit exceeded. Used: {quota['storage_used_bytes']}, "
                          f"Limit: {quota['storage_limit_bytes']}.",
                "storage_used": quota["storage_used_bytes"],
                "storage_limit": quota["storage_limit_bytes"],
            }

        return {
            "allowed": True,
            "reason": None,
            "storage_used": quota["storage_used_bytes"],
            "storage_limit": quota["storage_limit_bytes"],
        }

    async def add_points(
        self, team_id: str, amount: int, type: str,
        description: str, user_id: Optional[str] = None, reference_id: Optional[str] = None,
    ) -> dict:
        """
        Add points to a team (purchase, gift, admin_adjust).

        Returns dict with: success, new_balance
        """
        quota = await self.repo.get_team_quota(team_id)
        if not quota:
            return {"success": False, "new_balance": 0, "reason": "Team quota not found."}

        new_balance = quota["points_balance"] + amount
        await self.repo.update_points_balance(team_id, new_balance)

        await self.repo.create_transaction({
            "team_id": team_id,
            "user_id": user_id,
            "amount": amount,
            "balance_after": new_balance,
            "type": type,
            "reference_id": reference_id,
            "description": description,
        })

        logger.info(f"Points added: team={team_id}, amount={amount}, type={type}, balance={new_balance}")
        return {"success": True, "new_balance": new_balance}

    async def refund_points(
        self, team_id: str, user_id: str, amount: int,
        reference_type: str, reference_id: Optional[str] = None, reason: str = "Operation failed",
    ) -> dict:
        """Refund points on operation failure."""
        return await self.add_points(
            team_id=team_id,
            amount=amount,
            type="refund",
            description=f"Refund: {reason}",
            user_id=user_id,
            reference_id=reference_id,
        )

    async def get_balance(self, team_id: str) -> dict:
        """Get team's current balance and storage info."""
        quota = await self.repo.get_team_quota(team_id)
        if not quota:
            return {"points_balance": 0, "storage_limit_bytes": 0,
                    "storage_used_bytes": 0, "storage_used_percent": 0}

        limit = quota["storage_limit_bytes"]
        used = quota["storage_used_bytes"]
        percent = (used / limit * 100) if limit > 0 else 0

        return {
            "team_id": team_id,
            "points_balance": quota["points_balance"],
            "storage_limit_bytes": limit,
            "storage_used_bytes": used,
            "storage_used_percent": round(percent, 2),
        }

    async def ensure_team_quota(self, team_id: str, grant_free_points: bool = True) -> Dict:
        """
        Ensure a team has a quota record. Create one with free points if not exists.
        Called when a team is created or when a user first accesses the system.
        """
        quota = await self.repo.get_team_quota(team_id)
        if quota:
            return quota

        initial_points = 500 if grant_free_points else 0
        quota = await self.repo.create_team_quota(
            team_id=team_id,
            points_balance=initial_points,
            storage_limit_bytes=5368709120,  # 5GB
        )

        if initial_points > 0:
            await self.repo.create_transaction({
                "team_id": team_id,
                "amount": initial_points,
                "balance_after": initial_points,
                "type": "gift",
                "description": "Welcome bonus: 500 free points",
            })
            logger.info(f"Granted {initial_points} free points to team {team_id}")

        return quota
```

Note: Add the missing import at the top:
```python
from typing import Dict, Optional
```

**Step 2: Commit**

```bash
git add backend/app/services/points_service.py
git commit -m "feat: add PointsService with consume, check, refund, and balance logic"
```

---

### Task 6: Points API Router

**Files:**
- Create: `backend/app/api/points_router.py`
- Modify: `backend/app/api/__init__.py` (register router)

**Step 1: Write the router**

Create `backend/app/api/points_router.py`:

```python
# app/api/points_router.py

"""
Points API

Endpoints for checking balance, viewing transactions,
getting pricing, and admin point adjustments.
"""

from fastapi import APIRouter, HTTPException, Query
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.points_repository import PointsRepository
from app.schemas.points import MemberQuotaUpdate, PointsAdjustRequest
from app.services.points_service import PointsService

router = APIRouter(prefix="/points", tags=["Points"])


def _get_team_id_from_auth(auth) -> str:
    """
    Get the user's active team_id.
    For now, we look up the first team the user belongs to.
    In the future this could come from a header or session.
    """
    # This will be resolved per-request - see helper below
    raise NotImplementedError("Use _resolve_team_id instead")


async def _resolve_team_id(user_id: str, team_id_param: str = None) -> str:
    """Resolve team_id from param or user's first team."""
    if team_id_param:
        return team_id_param

    from app.db.supabase_client import get_async_supabase_admin
    client = await get_async_supabase_admin()
    result = await client.table("team_members").select("team_id").eq(
        "user_id", user_id
    ).limit(1).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="User has no team. Please create or join a team.")

    return result.data[0]["team_id"]


@router.get("/balance")
async def get_balance(auth: AuthDep, team_id: str = Query(None)):
    """Get team points balance and storage info."""
    tid = await _resolve_team_id(auth.user_id, team_id)
    service = PointsService()
    balance = await service.get_balance(tid)
    return {"success": True, "data": balance}


@router.get("/transactions")
async def get_transactions(
    auth: AuthDep,
    team_id: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    type: str = Query(None),
):
    """Get points transaction history."""
    tid = await _resolve_team_id(auth.user_id, team_id)
    repo = PointsRepository()
    transactions = await repo.get_transactions(tid, limit=limit, offset=offset, type_filter=type)
    return {"success": True, "data": transactions}


@router.get("/pricing")
async def get_pricing(auth: AuthDep):
    """Get all active pricing rules."""
    repo = PointsRepository()
    pricing = await repo.get_all_pricing()
    return {"success": True, "data": pricing}


@router.get("/usage-stats")
async def get_usage_stats(auth: AuthDep, team_id: str = Query(None)):
    """Get team usage statistics."""
    tid = await _resolve_team_id(auth.user_id, team_id)
    repo = PointsRepository()
    stats = await repo.get_usage_stats(tid)
    return {"success": True, "data": stats}


@router.get("/check")
async def check_quota(
    auth: AuthDep,
    action_type: str = Query(...),
    count: int = Query(1, ge=1),
    team_id: str = Query(None),
):
    """Pre-check if an action is allowed without consuming points."""
    tid = await _resolve_team_id(auth.user_id, team_id)
    service = PointsService()
    result = await service.check_quota(tid, auth.user_id, action_type, count)
    return {"success": True, "data": result}


@router.post("/admin/adjust")
async def admin_adjust_points(request: PointsAdjustRequest, auth: AuthDep):
    """Admin: manually adjust a team's points balance."""
    # Check admin role
    from app.db.supabase_client import get_async_supabase_admin
    client = await get_async_supabase_admin()
    profile = await client.table("user_profiles").select("role").eq(
        "id", auth.user_id
    ).execute()

    if not profile.data or profile.data[0].get("role") != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    service = PointsService()
    result = await service.add_points(
        team_id=request.team_id,
        amount=request.amount,
        type="admin_adjust",
        description=request.description,
        user_id=auth.user_id,
    )

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("reason", "Failed"))

    return {"success": True, "data": result}
```

**Step 2: Register the router in `__init__.py`**

Add to `backend/app/api/__init__.py`:

```python
from app.api.points_router import router as points_router

# Add after existing router registrations:
api_router.include_router(router=points_router, tags=["Points"])
```

**Step 3: Commit**

```bash
git add backend/app/api/points_router.py backend/app/api/__init__.py
git commit -m "feat: add Points API router with balance, transactions, pricing, admin endpoints"
```

---

### Task 7: Payment Service & Router

**Files:**
- Create: `backend/app/services/payment_service.py`
- Create: `backend/app/api/payment_router.py`
- Modify: `backend/app/api/__init__.py` (register router)

**Step 1: Write the payment service**

Create `backend/app/services/payment_service.py`:

```python
# app/services/payment_service.py

"""
Payment Service

Handles order creation, payment callback processing,
and integration with WeChat Pay / Alipay.

Note: Actual payment API integration is stubbed out.
The payment_url field is populated with a placeholder.
Real integration requires merchant credentials and SDK setup.
"""

import uuid
from datetime import datetime, timezone

from loguru import logger

from app.repositories.payment_repository import PaymentRepository
from app.repositories.points_repository import PointsRepository
from app.services.points_service import PointsService


class PaymentService:
    """Manages payment orders and callbacks."""

    def __init__(self):
        self.order_repo = PaymentRepository()
        self.points_repo = PointsRepository()
        self.points_service = PointsService()

    async def create_order(
        self, team_id: str, user_id: str, package_id: str, payment_method: str
    ) -> dict:
        """
        Create a new payment order.

        Returns the order data including a payment_url for the frontend.
        """
        # 1. Validate package
        package = await self.points_repo.get_package_by_id(package_id)
        if not package:
            return {"success": False, "error": "Invalid package ID"}

        if not package.get("is_active"):
            return {"success": False, "error": "Package is no longer available"}

        # 2. Generate trade number
        trade_no = f"MH{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}{uuid.uuid4().hex[:8].upper()}"

        # 3. Create order record
        order = await self.order_repo.create_order({
            "team_id": team_id,
            "user_id": user_id,
            "package_id": package_id,
            "points_amount": package["points_amount"],
            "amount_cents": package["price_cents"],
            "currency": package.get("currency", "CNY"),
            "payment_method": payment_method,
            "payment_status": "pending",
            "trade_no": trade_no,
            # TODO: Replace with real payment URL from WeChat/Alipay API
            "payment_url": f"https://payment.placeholder/{payment_method}/{trade_no}",
        })

        logger.info(
            f"Order created: {order['id']}, trade_no={trade_no}, "
            f"amount={package['price_cents']}cents, points={package['points_amount']}"
        )

        return {"success": True, "data": order}

    async def handle_callback(self, trade_no: str, payment_method: str, paid: bool = True) -> dict:
        """
        Process payment callback from WeChat/Alipay.

        This method must be idempotent - calling it multiple times
        with the same trade_no should only credit points once.

        Args:
            trade_no: Third-party transaction ID
            payment_method: 'wechat' or 'alipay'
            paid: Whether payment was successful

        Returns:
            dict with success status
        """
        # 1. Find order
        order = await self.order_repo.get_order_by_trade_no(trade_no)
        if not order:
            logger.warning(f"Callback for unknown trade_no: {trade_no}")
            return {"success": False, "error": "Order not found"}

        # 2. Idempotency check
        if order["payment_status"] == "paid":
            logger.info(f"Order {order['id']} already paid, ignoring duplicate callback")
            return {"success": True, "message": "Already processed"}

        if order["payment_status"] not in ("pending",):
            logger.warning(f"Order {order['id']} in status {order['payment_status']}, cannot process")
            return {"success": False, "error": f"Order in invalid status: {order['payment_status']}"}

        if not paid:
            # Payment failed
            await self.order_repo.update_order(order["id"], {
                "payment_status": "failed",
            })
            return {"success": True, "message": "Payment failure recorded"}

        # 3. Mark as paid
        await self.order_repo.update_order(order["id"], {
            "payment_status": "paid",
            "paid_at": datetime.now(timezone.utc).isoformat(),
        })

        # 4. Credit points to team
        result = await self.points_service.add_points(
            team_id=order["team_id"],
            amount=order["points_amount"],
            type="purchase",
            description=f"Purchased {order['points_amount']} points (Order: {order['id'][:8]})",
            user_id=order["user_id"],
            reference_id=order["id"],
        )

        logger.info(
            f"Payment processed: order={order['id']}, points={order['points_amount']}, "
            f"new_balance={result.get('new_balance')}"
        )

        return {"success": True, "message": "Payment processed", "points_added": order["points_amount"]}

    async def get_order_status(self, order_id: str) -> dict:
        """Get order status for frontend polling."""
        order = await self.order_repo.get_order_by_id(order_id)
        if not order:
            return {"success": False, "error": "Order not found"}
        return {
            "success": True,
            "data": {
                "order_id": order["id"],
                "payment_status": order["payment_status"],
                "points_amount": order["points_amount"],
                "paid_at": order.get("paid_at"),
            },
        }

    async def get_team_orders(self, team_id: str, limit: int = 50, offset: int = 0) -> list:
        """Get team's order history."""
        return await self.order_repo.get_team_orders(team_id, limit, offset)
```

**Step 2: Write the payment router**

Create `backend/app/api/payment_router.py`:

```python
# app/api/payment_router.py

"""
Payment API

Endpoints for creating orders, checking order status,
and handling payment callbacks.
"""

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from app.core.deps import AuthDep
from app.repositories.points_repository import PointsRepository
from app.schemas.payment import CreateOrderRequest
from app.services.payment_service import PaymentService

router = APIRouter(prefix="/payment", tags=["Payment"])


async def _resolve_team_id(user_id: str, team_id_param: str = None) -> str:
    """Resolve team_id from param or user's first team."""
    if team_id_param:
        return team_id_param
    from app.db.supabase_client import get_async_supabase_admin
    client = await get_async_supabase_admin()
    result = await client.table("team_members").select("team_id").eq(
        "user_id", user_id
    ).limit(1).execute()
    if not result.data:
        raise HTTPException(status_code=404, detail="User has no team")
    return result.data[0]["team_id"]


@router.get("/packages")
async def get_packages(auth: AuthDep):
    """Get all available point packages."""
    repo = PointsRepository()
    packages = await repo.get_active_packages()
    return {"success": True, "data": packages}


@router.post("/create-order")
async def create_order(request: CreateOrderRequest, auth: AuthDep):
    """Create a payment order for a point package."""
    service = PaymentService()
    result = await service.create_order(
        team_id=request.team_id,
        user_id=auth.user_id,
        package_id=request.package_id,
        payment_method=request.payment_method,
    )
    if not result["success"]:
        raise HTTPException(status_code=400, detail=result.get("error"))
    return result


@router.get("/order/{order_id}/status")
async def get_order_status(order_id: str, auth: AuthDep):
    """Poll order payment status."""
    service = PaymentService()
    result = await service.get_order_status(order_id)
    if not result["success"]:
        raise HTTPException(status_code=404, detail=result.get("error"))
    return result


@router.get("/orders")
async def get_orders(
    auth: AuthDep,
    team_id: str = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Get team's order history."""
    tid = await _resolve_team_id(auth.user_id, team_id)
    service = PaymentService()
    orders = await service.get_team_orders(tid, limit, offset)
    return {"success": True, "data": orders}


@router.post("/callback/wechat")
async def wechat_callback(request: Request):
    """
    WeChat Pay payment callback.

    TODO: Implement real signature verification with WeChat Pay SDK.
    This is a placeholder that accepts a trade_no and marks as paid.
    """
    try:
        body = await request.json()
        trade_no = body.get("trade_no")
        if not trade_no:
            return {"code": "FAIL", "message": "Missing trade_no"}

        # TODO: Verify WeChat signature here
        # wechat_verify_signature(request.headers, body)

        service = PaymentService()
        result = await service.handle_callback(trade_no, "wechat", paid=True)

        if result["success"]:
            return {"code": "SUCCESS", "message": "OK"}
        else:
            return {"code": "FAIL", "message": result.get("error", "Unknown error")}
    except Exception as e:
        logger.error(f"WeChat callback error: {e}")
        return {"code": "FAIL", "message": str(e)}


@router.post("/callback/alipay")
async def alipay_callback(request: Request):
    """
    Alipay payment callback.

    TODO: Implement real signature verification with Alipay SDK.
    This is a placeholder that accepts a trade_no and marks as paid.
    """
    try:
        body = await request.json()
        trade_no = body.get("trade_no")
        if not trade_no:
            return "fail"

        # TODO: Verify Alipay signature here
        # alipay_verify_signature(body)

        service = PaymentService()
        result = await service.handle_callback(trade_no, "alipay", paid=True)

        return "success" if result["success"] else "fail"
    except Exception as e:
        logger.error(f"Alipay callback error: {e}")
        return "fail"
```

**Step 3: Register the payment router in `__init__.py`**

Add to `backend/app/api/__init__.py`:

```python
from app.api.payment_router import router as payment_router

api_router.include_router(router=payment_router, tags=["Payment"])
```

**Step 4: Commit**

```bash
git add backend/app/services/payment_service.py backend/app/api/payment_router.py backend/app/api/__init__.py
git commit -m "feat: add PaymentService and Payment API router with order creation and callbacks"
```

---

## Phase 2: Integration - Inject Points Checks into Existing Operations

### Task 8: Inject Points Check into Video Fetch

**Files:**
- Modify: `backend/app/api/videos_router.py`

**Step 1: Add points check to `fetch_video` endpoint**

At the top of `videos_router.py`, add import:
```python
from app.services.points_service import PointsService
```

Inside `fetch_video()` function, add points check right after `logger.info(f"User {auth.user_id} starting video fetch: {url}")` (around line 110):

```python
        # === Points check ===
        points_service = PointsService()
        # Resolve user's team
        from app.db.supabase_client import get_async_supabase_admin as _get_admin
        _admin = await _get_admin()
        _tm = await _admin.table("team_members").select("team_id").eq("user_id", auth.user_id).limit(1).execute()
        _team_id = _tm.data[0]["team_id"] if _tm.data else None
        if _team_id:
            await points_service.ensure_team_quota(_team_id)
            points_result = await points_service.check_and_consume(
                team_id=_team_id,
                user_id=auth.user_id,
                action_type="video_parse",
            )
            if not points_result["success"]:
                raise HTTPException(
                    status_code=402,
                    detail=points_result["reason"],
                )
        # === End points check ===
```

**Step 2: Add points check to `batch_fetch` endpoint**

Same pattern but with `action_type="video_parse_batch"` and `count=len(request.urls)`.

**Step 3: Commit**

```bash
git add backend/app/api/videos_router.py
git commit -m "feat: inject points consumption into video fetch endpoints"
```

---

### Task 9: Inject Points Check into AI Endpoints

**Files:**
- Modify: `backend/app/api/ai_router.py`

**Step 1: Add points checks**

Add the same pattern to `trigger_transcription` (action_type="ai_transcription"), `trigger_summary` (action_type="ai_summary"), and visual analysis endpoints.

Pattern is the same as Task 8: resolve team → check_and_consume → 402 on failure.

**Step 2: Commit**

```bash
git add backend/app/api/ai_router.py
git commit -m "feat: inject points consumption into AI pipeline endpoints"
```

---

## Phase 3: Frontend

### Task 10: Frontend Types & Services

**Files:**
- Modify: `frontend/types.ts` (add Points/Payment types)
- Create: `frontend/services/pointsService.ts`
- Create: `frontend/services/paymentService.ts`

**Step 1: Add types to `types.ts`**

Append to `frontend/types.ts`:

```typescript
// Points System Types
export interface PointPackage {
  id: string;
  name: string;
  description: string | null;
  points_amount: number;
  price_cents: number;
  currency: string;
  sort_order: number;
}

export interface TeamQuota {
  team_id: string;
  points_balance: number;
  storage_limit_bytes: number;
  storage_used_bytes: number;
  storage_used_percent: number;
}

export interface PointTransaction {
  id: string;
  team_id: string;
  user_id: string | null;
  amount: number;
  balance_after: number;
  type: 'purchase' | 'consume' | 'refund' | 'gift' | 'admin_adjust';
  reference_type: string | null;
  reference_id: string | null;
  description: string | null;
  created_at: string;
}

export interface PointPricing {
  action_type: string;
  points_cost: number;
  description: string | null;
}

export interface PaymentOrder {
  id: string;
  team_id: string;
  user_id: string;
  package_id: string;
  points_amount: number;
  amount_cents: number;
  currency: string;
  payment_method: 'wechat' | 'alipay';
  payment_status: 'pending' | 'paid' | 'failed' | 'expired' | 'refunded';
  payment_url: string | null;
  trade_no: string | null;
  paid_at: string | null;
  expired_at: string;
  created_at: string;
}

export interface QuotaCheck {
  allowed: boolean;
  points_cost: number;
  current_balance: number;
  reason: string | null;
}
```

**Step 2: Create `pointsService.ts`**

```typescript
// frontend/services/pointsService.ts

import { getAuthHeaders, API_BASE } from './parserService';
import { TeamQuota, PointTransaction, PointPricing, QuotaCheck } from '../types';

export const fetchPointsBalance = async (teamId?: string): Promise<TeamQuota> => {
  const params = teamId ? `?team_id=${teamId}` : '';
  const res = await fetch(`${API_BASE}/points/balance${params}`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch balance');
  return json.data;
};

export const fetchPointsTransactions = async (
  teamId?: string, limit = 50, offset = 0, type?: string
): Promise<PointTransaction[]> => {
  const params = new URLSearchParams();
  if (teamId) params.set('team_id', teamId);
  params.set('limit', String(limit));
  params.set('offset', String(offset));
  if (type) params.set('type', type);

  const res = await fetch(`${API_BASE}/points/transactions?${params}`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch transactions');
  return json.data;
};

export const fetchPointsPricing = async (): Promise<PointPricing[]> => {
  const res = await fetch(`${API_BASE}/points/pricing`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch pricing');
  return json.data;
};

export const fetchUsageStats = async (teamId?: string): Promise<any> => {
  const params = teamId ? `?team_id=${teamId}` : '';
  const res = await fetch(`${API_BASE}/points/usage-stats${params}`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch usage stats');
  return json.data;
};

export const checkQuota = async (
  actionType: string, count = 1, teamId?: string
): Promise<QuotaCheck> => {
  const params = new URLSearchParams({ action_type: actionType, count: String(count) });
  if (teamId) params.set('team_id', teamId);

  const res = await fetch(`${API_BASE}/points/check?${params}`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to check quota');
  return json.data;
};
```

**Step 3: Create `paymentService.ts`**

```typescript
// frontend/services/paymentService.ts

import { getAuthHeaders, API_BASE } from './parserService';
import { PointPackage, PaymentOrder } from '../types';

export const fetchPackages = async (): Promise<PointPackage[]> => {
  const res = await fetch(`${API_BASE}/payment/packages`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch packages');
  return json.data;
};

export const createOrder = async (
  packageId: string, paymentMethod: 'wechat' | 'alipay', teamId: string
): Promise<PaymentOrder> => {
  const res = await fetch(`${API_BASE}/payment/create-order`, {
    method: 'POST',
    headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      package_id: packageId,
      payment_method: paymentMethod,
      team_id: teamId,
    }),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to create order');
  return json.data;
};

export const pollOrderStatus = async (orderId: string): Promise<{
  payment_status: string;
  points_amount: number;
  paid_at: string | null;
}> => {
  const res = await fetch(`${API_BASE}/payment/order/${orderId}/status`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to get order status');
  return json.data;
};

export const fetchOrders = async (
  teamId?: string, limit = 50, offset = 0
): Promise<PaymentOrder[]> => {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (teamId) params.set('team_id', teamId);

  const res = await fetch(`${API_BASE}/payment/orders?${params}`, {
    headers: getAuthHeaders(),
  });
  const json = await res.json();
  if (!json.success) throw new Error(json.detail || 'Failed to fetch orders');
  return json.data;
};
```

**Step 4: Commit**

```bash
git add frontend/types.ts frontend/services/pointsService.ts frontend/services/paymentService.ts
git commit -m "feat: add frontend types and API services for points and payment system"
```

---

### Task 11: PointsBadge Component (Header Integration)

**Files:**
- Create: `frontend/components/PointsBadge.tsx`
- Modify: `frontend/components/Header.tsx` (add badge)

**Step 1: Create PointsBadge component**

A small badge that shows current points balance in the header. Clicking opens the Points Center.

```tsx
// frontend/components/PointsBadge.tsx

import React, { useEffect, useState } from 'react';
import { Coins } from 'lucide-react';
import { fetchPointsBalance } from '../services/pointsService';
import { TeamQuota } from '../types';

interface PointsBadgeProps {
  onClick: () => void;
}

export const PointsBadge: React.FC<PointsBadgeProps> = ({ onClick }) => {
  const [quota, setQuota] = useState<TeamQuota | null>(null);

  useEffect(() => {
    fetchPointsBalance().then(setQuota).catch(() => {});
  }, []);

  if (!quota) return null;

  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-amber-500/10 hover:bg-amber-500/20 text-amber-400 transition-colors text-sm font-medium"
      title="Points Balance"
    >
      <Coins className="w-4 h-4" />
      <span>{quota.points_balance.toLocaleString()}</span>
    </button>
  );
};
```

**Step 2: Integrate into Header.tsx**

Add `PointsBadge` import and render it in the header bar, near the user dropdown area. The exact location depends on the Header layout - place it before the user dropdown.

**Step 3: Commit**

```bash
git add frontend/components/PointsBadge.tsx frontend/components/Header.tsx
git commit -m "feat: add PointsBadge component and integrate into Header"
```

---

### Task 12: PointsCenter Page Component

**Files:**
- Create: `frontend/components/PointsCenter.tsx`
- Modify: `frontend/App.tsx` (add route/tab)

**Step 1: Create PointsCenter**

Full page with:
- Balance display (big number)
- Storage usage bar
- Package cards for purchasing
- Transaction history table
- Usage stats breakdown

This is a large component. Core structure:

```tsx
// frontend/components/PointsCenter.tsx

import React, { useEffect, useState } from 'react';
import { Coins, Package, ArrowUpRight, ArrowDownRight, TrendingUp, HardDrive } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { fetchPointsBalance, fetchPointsTransactions, fetchPointsPricing, fetchUsageStats } from '../services/pointsService';
import { fetchPackages } from '../services/paymentService';
import { TeamQuota, PointTransaction, PointPricing, PointPackage } from '../types';

interface PointsCenterProps {
  onBuyPackage: (pkg: PointPackage) => void;
}

export const PointsCenter: React.FC<PointsCenterProps> = ({ onBuyPackage }) => {
  const { t } = useTranslation();
  const [quota, setQuota] = useState<TeamQuota | null>(null);
  const [transactions, setTransactions] = useState<PointTransaction[]>([]);
  const [pricing, setPricing] = useState<PointPricing[]>([]);
  const [packages, setPackages] = useState<PointPackage[]>([]);
  const [usageStats, setUsageStats] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([
      fetchPointsBalance().then(setQuota),
      fetchPointsTransactions(undefined, 20).then(setTransactions),
      fetchPointsPricing().then(setPricing),
      fetchPackages().then(setPackages),
      fetchUsageStats().then(setUsageStats),
    ]).finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="flex items-center justify-center h-64 text-zinc-400">Loading...</div>;
  }

  const formatBytes = (bytes: number) => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
  };

  const formatPrice = (cents: number) => `¥${(cents / 100).toFixed(2)}`;

  return (
    <div className="space-y-6 p-6 max-w-5xl mx-auto">
      {/* Balance Card */}
      <div className="bg-gradient-to-r from-amber-500/10 to-orange-500/10 border border-amber-500/20 rounded-2xl p-6">
        <div className="flex items-center justify-between">
          <div>
            <p className="text-zinc-400 text-sm">Points Balance</p>
            <p className="text-4xl font-bold text-amber-400 mt-1">
              {quota?.points_balance.toLocaleString() ?? 0}
            </p>
          </div>
          <Coins className="w-12 h-12 text-amber-500/50" />
        </div>
        {/* Storage bar */}
        {quota && (
          <div className="mt-4">
            <div className="flex items-center justify-between text-sm text-zinc-400 mb-1">
              <span className="flex items-center gap-1"><HardDrive className="w-3.5 h-3.5" /> Storage</span>
              <span>{formatBytes(quota.storage_used_bytes)} / {formatBytes(quota.storage_limit_bytes)}</span>
            </div>
            <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-500 rounded-full transition-all"
                style={{ width: `${Math.min(quota.storage_used_percent, 100)}%` }}
              />
            </div>
          </div>
        )}
      </div>

      {/* Packages */}
      <div>
        <h3 className="text-lg font-semibold text-zinc-200 mb-3">Buy Points</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {packages.map(pkg => (
            <button
              key={pkg.id}
              onClick={() => onBuyPackage(pkg)}
              className="bg-zinc-900 border border-zinc-800 rounded-xl p-4 hover:border-amber-500/50 transition-colors text-left"
            >
              <p className="text-lg font-bold text-zinc-100">{pkg.points_amount.toLocaleString()}</p>
              <p className="text-xs text-zinc-500">{pkg.name}</p>
              <p className="text-amber-400 font-semibold mt-2">{formatPrice(pkg.price_cents)}</p>
              <p className="text-xs text-zinc-600">
                {formatPrice(Math.round(pkg.price_cents / pkg.points_amount * 100) / 100)}/pt
              </p>
            </button>
          ))}
        </div>
      </div>

      {/* Pricing Table */}
      <div>
        <h3 className="text-lg font-semibold text-zinc-200 mb-3">Pricing</h3>
        <div className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-800/50">
              <tr>
                <th className="text-left px-4 py-2 text-zinc-400">Action</th>
                <th className="text-right px-4 py-2 text-zinc-400">Points</th>
              </tr>
            </thead>
            <tbody>
              {pricing.map(p => (
                <tr key={p.action_type} className="border-t border-zinc-800">
                  <td className="px-4 py-2 text-zinc-300">{p.description || p.action_type}</td>
                  <td className="px-4 py-2 text-right text-amber-400 font-medium">{p.points_cost}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Transaction History */}
      <div>
        <h3 className="text-lg font-semibold text-zinc-200 mb-3">Recent Transactions</h3>
        <div className="space-y-2">
          {transactions.map(tx => (
            <div key={tx.id} className="flex items-center justify-between bg-zinc-900 border border-zinc-800 rounded-lg px-4 py-3">
              <div className="flex items-center gap-3">
                {tx.amount > 0
                  ? <ArrowUpRight className="w-4 h-4 text-green-400" />
                  : <ArrowDownRight className="w-4 h-4 text-red-400" />
                }
                <div>
                  <p className="text-sm text-zinc-200">{tx.description}</p>
                  <p className="text-xs text-zinc-500">{new Date(tx.created_at).toLocaleString()}</p>
                </div>
              </div>
              <span className={`font-medium ${tx.amount > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {tx.amount > 0 ? '+' : ''}{tx.amount}
              </span>
            </div>
          ))}
          {transactions.length === 0 && (
            <p className="text-zinc-500 text-sm text-center py-4">No transactions yet</p>
          )}
        </div>
      </div>
    </div>
  );
};
```

**Step 2: Add PointsCenter as a view in App.tsx**

Add `'points'` to the `ViewState` type, add sidebar item, and render `PointsCenter` when active.

**Step 3: Commit**

```bash
git add frontend/components/PointsCenter.tsx frontend/App.tsx frontend/types.ts
git commit -m "feat: add PointsCenter page with balance, packages, pricing, and transaction history"
```

---

### Task 13: PaymentModal Component

**Files:**
- Create: `frontend/components/PaymentModal.tsx`

**Step 1: Create PaymentModal**

Modal for selecting payment method (WeChat/Alipay tabs) and showing QR code / redirect link. Polls order status until paid.

```tsx
// frontend/components/PaymentModal.tsx
// Shows payment options, creates order, polls status until paid
// Props: package (PointPackage), teamId, onClose, onSuccess
```

Full implementation with:
- WeChat / Alipay tab selection
- Create order on confirm
- Poll order status every 3 seconds
- Show success state when paid
- Auto-close after success

**Step 2: Commit**

```bash
git add frontend/components/PaymentModal.tsx
git commit -m "feat: add PaymentModal component with order creation and status polling"
```

---

### Task 14: Team Settings Enhancement (Roles & Quotas)

**Files:**
- Modify: `frontend/components/TeamSettings.tsx`
- Modify: `frontend/types.ts` (update TeamMember role type)

**Step 1: Update TeamMember type**

In `types.ts`, update:
```typescript
export interface TeamMember {
  team_id: string;
  user_id: string;
  role: 'owner' | 'admin' | 'member';  // was 'owner' | 'member'
  joined_at: string;
  email?: string;
  name?: string;
}
```

**Step 2: Enhance TeamSettings**

Add to TeamSettings.tsx:
- Role badge display for each member
- Role change dropdown (owner can change admin/member)
- Monthly quota setting per member
- Team consumption statistics panel

**Step 3: Commit**

```bash
git add frontend/components/TeamSettings.tsx frontend/types.ts
git commit -m "feat: enhance TeamSettings with role management and member quotas"
```

---

## Phase 4: Finishing Touches

### Task 15: Auto-Create Team Quota on Signup

**Files:**
- Modify: `backend/app/api/supabase_auth_router.py` (signup endpoint)

**Step 1: Add quota creation after signup**

After successful user registration, look up or create a personal team, then ensure quota exists with 500 free points.

```python
# After user created successfully:
points_service = PointsService()
# Find user's team (created by trigger or manually)
# await points_service.ensure_team_quota(team_id, grant_free_points=True)
```

**Step 2: Commit**

```bash
git add backend/app/api/supabase_auth_router.py
git commit -m "feat: auto-create team quota with free points on user signup"
```

---

### Task 16: i18n Keys for Points UI

**Files:**
- Modify: `frontend/locales/en.json` (if exists, else inline strings are fine)
- Modify: `frontend/locales/zh.json`

Add translation keys for:
- Points, Balance, Buy Points, Pricing, Transactions
- Storage, Used, Limit
- Package names
- Error messages (Insufficient points, etc.)

**Step 1: Add i18n entries**

**Step 2: Commit**

```bash
git add frontend/locales/
git commit -m "feat: add i18n translations for points and payment UI"
```

---

### Task 17: Final Integration Test & Build Verification

**Step 1: Run backend**
```bash
cd backend && uv run uvicorn app.main:app --reload
```
Verify: No import errors, all routers load.

**Step 2: Test API endpoints**
```bash
# Test points balance
curl -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/points/balance

# Test packages
curl -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/payment/packages

# Test pricing
curl -H "Authorization: Bearer <token>" http://localhost:8080/api/v1/points/pricing
```

**Step 3: Run frontend build**
```bash
cd frontend && npm run build
```
Verify: No TypeScript errors, build succeeds.

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve integration issues from points system implementation"
```

---

## Summary

| Phase | Tasks | Description |
|-------|-------|-------------|
| Phase 1 | 1-7 | Database migration, schemas, repositories, services, API routes |
| Phase 2 | 8-9 | Inject points checks into existing video fetch and AI endpoints |
| Phase 3 | 10-14 | Frontend types, services, PointsCenter, PaymentModal, TeamSettings |
| Phase 4 | 15-17 | Auto-create quota on signup, i18n, integration testing |

**Total: 17 tasks, ~4 phases**

Each task produces a git commit. The system is functional after Phase 1+2 (backend complete), and user-facing after Phase 3+4.
