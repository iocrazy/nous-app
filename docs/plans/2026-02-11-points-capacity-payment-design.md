# Points, Capacity & Payment System Design

> Date: 2026-02-11
> Branch: feature/Points-capacity-payment-system

## Overview

Add a points-based monetization system to MediaHub:

- **Points System**: Unified currency for all consumable actions
- **Team Management Enhancement**: Team points pool, roles (owner/admin/member), member quotas
- **Capacity Limits**: Storage quota enforcement per team
- **Payment System**: WeChat Pay + Alipay integration for purchasing point packages

## Key Decisions

| Decision | Choice |
|----------|--------|
| Business model | Points-based (credits) |
| Point consumption | Video parsing, AI features, storage |
| Team model | Team-level points pool |
| Payment methods | WeChat Pay + Alipay |
| Free tier | 500 points + 5GB storage per new user |
| Team enhancement | Roles (owner/admin/member) + member quotas |

## Data Model

### New Tables

#### `point_packages` - Purchasable Point Packages

```sql
CREATE TABLE point_packages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,                    -- e.g., "Starter Pack"
    description TEXT,
    points_amount INTEGER NOT NULL,        -- e.g., 100
    price_cents INTEGER NOT NULL,          -- e.g., 1000 (= ¥10.00)
    currency TEXT NOT NULL DEFAULT 'CNY',
    is_active BOOLEAN NOT NULL DEFAULT true,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `team_quotas` - Team Points Balance & Storage Quota

```sql
CREATE TABLE team_quotas (
    team_id UUID PRIMARY KEY REFERENCES teams(id) ON DELETE CASCADE,
    points_balance INTEGER NOT NULL DEFAULT 0,
    storage_limit_bytes BIGINT NOT NULL DEFAULT 5368709120, -- 5GB
    storage_used_bytes BIGINT NOT NULL DEFAULT 0,
    free_points_granted BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `member_quotas` - Per-Member Monthly Limits

```sql
CREATE TABLE member_quotas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    monthly_points_limit INTEGER,          -- NULL = unlimited
    points_used_this_month INTEGER NOT NULL DEFAULT 0,
    reset_at TIMESTAMPTZ NOT NULL DEFAULT date_trunc('month', now()) + interval '1 month',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(team_id, user_id)
);
```

#### `point_transactions` - Points Ledger (All Changes)

```sql
CREATE TABLE point_transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID REFERENCES auth.users(id),
    amount INTEGER NOT NULL,               -- positive=credit, negative=debit
    balance_after INTEGER NOT NULL,
    type TEXT NOT NULL,                     -- purchase/consume/refund/gift/admin_adjust
    reference_type TEXT,                    -- video_parse/ai_transcription/ai_analysis/ai_summary/storage
    reference_id TEXT,                      -- related entity ID
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `point_pricing` - Action Cost Configuration

```sql
CREATE TABLE point_pricing (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action_type TEXT NOT NULL UNIQUE,      -- video_parse, ai_transcription, etc.
    points_cost INTEGER NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

#### `orders` - Payment Orders

```sql
CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES auth.users(id),
    package_id UUID NOT NULL REFERENCES point_packages(id),
    points_amount INTEGER NOT NULL,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'CNY',
    payment_method TEXT NOT NULL,           -- wechat/alipay
    payment_status TEXT NOT NULL DEFAULT 'pending', -- pending/paid/failed/expired/refunded
    trade_no TEXT,                          -- third-party transaction ID
    payment_url TEXT,                       -- payment redirect URL or QR code data
    paid_at TIMESTAMPTZ,
    expired_at TIMESTAMPTZ NOT NULL DEFAULT now() + interval '30 minutes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### Modified Tables

#### `team_members` - Add Role Field

```sql
ALTER TABLE team_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member';
-- Values: owner, admin, member
```

### Default Pricing Data

| action_type | points_cost | Description |
|-------------|-------------|-------------|
| video_parse | 5 | Parse a single video |
| video_parse_batch | 4 | Parse a video in batch mode |
| ai_transcription | 20 | AI speech-to-text |
| ai_summary | 15 | AI text summarization |
| ai_visual_analysis | 15 | AI visual analysis |
| storage_gb_month | 50 | Storage per GB per month |

### Default Package Data

| name | points | price | unit price |
|------|--------|-------|------------|
| Starter Pack | 100 | ¥10 | ¥0.10/pt |
| Standard Pack | 500 | ¥45 | ¥0.09/pt |
| Pro Pack | 2,000 | ¥160 | ¥0.08/pt |
| Team Pack | 10,000 | ¥700 | ¥0.07/pt |

## Points Consumption Flow

```
User initiates action (e.g., parse video)
  → Determine active team
  → Query point_pricing for action cost
  → Check team_quotas.points_balance >= required points
  → Check member_quotas (if set) for monthly limit
  → If storage action: check storage_used < storage_limit
  → Deduct points (atomic PostgreSQL transaction)
  → Write point_transactions record
  → Execute actual operation
  → On failure: refund points (write refund transaction)
```

## Team Roles & Permissions

| Role | Buy Points | Manage Members | Set Quotas | View Team Stats | Use Points | Delete Team |
|------|-----------|----------------|------------|-----------------|------------|-------------|
| owner | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| admin | ❌ | ✅ (invite/remove member) | ❌ | ✅ | ✅ | ❌ |
| member | ❌ | ❌ | ❌ | Own stats only | ✅ (within quota) | ❌ |

## API Endpoints

### Points

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/points/balance` | GET | Team points balance |
| `/points/transactions` | GET | Points transaction history |
| `/points/pricing` | GET | Points pricing table |
| `/points/usage-stats` | GET | Usage statistics |

### Payment

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/payment/packages` | GET | Available point packages |
| `/payment/create-order` | POST | Create payment order |
| `/payment/order/{id}/status` | GET | Query order status |
| `/payment/callback/wechat` | POST | WeChat payment callback |
| `/payment/callback/alipay` | POST | Alipay payment callback |
| `/payment/orders` | GET | Team order history |

### Team Quota

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/teams/{id}/quota` | GET | Team quota info |
| `/teams/{id}/quota` | PUT | Update quota settings |
| `/teams/{id}/members/usage` | GET | Member usage stats |
| `/teams/{id}/members/{uid}/limit` | PUT | Set member monthly limit |

### Admin

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/admin/points/adjust` | POST | Manual points adjustment |
| `/admin/points/overview` | GET | Platform-wide points stats |

## Frontend Components

### New Components

| Component | Description |
|-----------|-------------|
| `PointsCenter.tsx` | Main points dashboard with balance, packages, transaction history |
| `PaymentModal.tsx` | Payment flow modal (WeChat QR / Alipay redirect) |
| `PointsBadge.tsx` | Header badge showing current points balance |
| `QuotaBar.tsx` | Storage usage progress bar |
| `PointsConfirmDialog.tsx` | Pre-action confirmation ("This will cost 5 points") |

### Modified Components

| Component | Changes |
|-----------|---------|
| `TeamSettings.tsx` | Add role management, member quotas, team consumption stats |
| `Header.tsx` | Add PointsBadge, QuotaBar |
| `App.tsx` | Integrate PointsCenter route |
| `types.ts` | Add Points, Order, Quota TypeScript types |

### New Services

| Service | Description |
|---------|-------------|
| `pointsService.ts` | Points balance, transactions, pricing API calls |
| `paymentService.ts` | Order creation, status polling, package listing |

## Backend Architecture

### New Files

```
backend/app/schemas/points.py          # Pydantic models
backend/app/schemas/payment.py
backend/app/repositories/points_repository.py
backend/app/repositories/payment_repository.py
backend/app/services/points_service.py  # Core points logic
backend/app/services/quota_service.py   # Capacity checking
backend/app/services/payment_service.py # Payment integration
backend/app/api/points_router.py
backend/app/api/payment_router.py
```

### Modified Files (Inject Points Checks)

```
backend/app/api/videos_router.py       # Check points before parse
backend/app/api/ai_router.py           # Check points before AI ops
backend/app/services/video_service.py
backend/app/services/llm_analysis_service.py
backend/app/services/whisper_service.py
```

### PointsService Core Interface

```python
class PointsService:
    async def consume_points(self, team_id, user_id, action_type, reference_id=None):
        """Atomic debit + write transaction record"""

    async def check_quota(self, team_id, user_id, action_type):
        """Pre-check balance and member quota"""

    async def refund_points(self, transaction_id, reason):
        """Refund on operation failure"""

    async def add_points(self, team_id, amount, type, description):
        """Credit points (purchase, gift, admin adjust)"""
```

## Security Considerations

- Payment callbacks: **verify signatures**, prevent forgery
- Order expiry: 30-minute timeout, auto-close expired orders
- Idempotency: Same trade_no callback processed only once
- Amount validation: Backend verifies price, never trusts frontend
- Points deduction: PostgreSQL transaction ensures atomicity
- RLS policies: Team members can only see their own team's data

## Reference: 分秒帧 (MediaTrack) Platform Insights

> Explored via Playwright browser on 2026-02-11. Key observations relevant to this implementation:

### Pricing Model
- **Seat-based + storage-based pricing** (Studio: 3-6 seats, 69-149/month; Enterprise: 10-30 seats, 3999-13999/year)
- Storage tiers: 100GB - 3TB
- Per-project member limits
- Our model adapts this to **points-based** (simpler, more flexible for a video parsing tool vs. full review platform)

### Team & Role Management
- Roles: Owner, Admin, Member (same as our design)
- Owner has full control over billing, member management, quota settings
- Admins can manage members but not billing
- Members operate within assigned quotas
- Sidebar navigation: Projects, Resource Library, To-do, Upload

### Storage & Resource Management
- File metadata display: codec, resolution, bitrate, FPS, file size
- Storage usage clearly visible in team settings
- Resource library for centralized asset management
- We should show **file metadata** alongside storage quota to give users context on what's consuming storage

### UI Patterns (Applicable to PointsCenter)
- Clean card-based dashboard layout
- Progress bars for storage usage (similar to our QuotaBar design)
- Status badges on items (we'll use transaction type badges)
- Grid layout for browseable items (we'll use for package cards)

### Future Features (Not in Current Scope)
These 分秒帧 features are noted for potential future implementation:
- Video review with frame-accurate annotations
- Version management (v1, v2... stacking)
- Review status workflow (Pending Review → In Review → Feedback Collected → Approved)
- Annotation tools (pen, arrow, rectangle, text, tags, @mentions)
- Process/workflow management with stage advancement
- To-do/task management integrated with projects

## Implementation Phases

### Phase 1: Database + Points Core
- Database migrations (all new tables + seed data)
- team_members role field extension
- Auto-create personal team + grant 500 points on signup
- RLS policies for all new tables

### Phase 2: Backend Points & Quota System
- PointsService, QuotaService
- Points check middleware in existing operations
- Points API routes
- Celery Beat task for monthly storage billing

### Phase 3: Payment System
- PaymentService (WeChat + Alipay integration)
- Payment callback handlers (signature verification, idempotency)
- Order management API
- Frontend PaymentModal

### Phase 4: Frontend UI
- PointsCenter page
- Team settings enhancement (roles, quotas)
- Global points/capacity indicators
- Pre-action points confirmation dialog
