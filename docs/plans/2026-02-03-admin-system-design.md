# MediaHub Admin System Design

> Date: 2026-02-03
> Status: Approved

## Overview

Build an independent admin backend system for MediaHub to manage users, teams, content, credits, and system settings.

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│                       User Access                          │
├─────────────────────────┬─────────────────────────────────┤
│  mediahub.vercel.app    │  nas-ip:3097 (Admin Panel)      │
│  (Main Site - Vercel)   │  NAS Docker Container           │
├─────────────────────────┴─────────────────────────────────┤
│              MediaHub Backend (NAS:8080)                  │
│                    /api/v1/*                              │
│              + NEW: /api/v1/admin/*                       │
├───────────────────────────────────────────────────────────┤
│                  Supabase (Cloud)                         │
│           Shared Auth + Database                          │
└───────────────────────────────────────────────────────────┘
```

## Key Decisions

| Item | Decision |
|------|----------|
| Deployment | Independent (separate from main site) |
| Tech Stack | React + Refine + Supabase |
| Auth | Shared Supabase Auth, check `user_profiles.role = 'admin'` |
| Hosting | NAS Docker, port 3097 |
| Payment | Credits system first, payment integration later |

## Feature Modules

### 1. User Management

| Feature | Description |
|---------|-------------|
| User List | Paginated table: email, role, registration date, status |
| Search/Filter | Filter by email, role, date |
| Role Assignment | Dropdown: admin / user / test |
| Ban Account | Set `is_banned` flag, prevent login |
| Delete User | Soft delete, keep data but mark as deleted |

### 2. Team Management

| Feature | Description |
|---------|-------------|
| Team List | Name, owner, member count, created date |
| View Members | Expand to see all members and roles |
| Dissolve Team | Delete team and all related data |
| Transfer Ownership | Transfer to another member |

### 3. Content Management

| Feature | Description |
|---------|-------------|
| Video List | Title, author, tags, download time |
| Tag Management | Edit/delete all tags (including system tags) |
| Bulk Operations | Bulk delete, bulk tagging |

### 4. Credits System

#### Database Schema

```sql
-- User credits account
user_credits (
  user_id UUID PRIMARY KEY,
  balance INTEGER DEFAULT 0,
  total_earned INTEGER DEFAULT 0,
  total_spent INTEGER DEFAULT 0,
  updated_at TIMESTAMPTZ
)

-- Credit transaction log
credit_transactions (
  id UUID PRIMARY KEY,
  user_id UUID,
  amount INTEGER,                   -- positive=income, negative=expense
  type VARCHAR,                     -- 'recharge'/'consume'/'refund'/'gift'
  description TEXT,
  related_id VARCHAR,
  created_at TIMESTAMPTZ
)

-- Credit pricing config
credit_pricing (
  id UUID PRIMARY KEY,
  action VARCHAR UNIQUE,            -- 'parse', 'download', 'ai_analysis', 'storage_gb'
  cost INTEGER,
  is_active BOOLEAN DEFAULT true,
  updated_at TIMESTAMPTZ
)
```

#### Pricing (Default)

| Action | Credits | Note |
|--------|---------|------|
| Parse Link | 1 | Per parse |
| Download Video | 2 | After parsing |
| AI Analysis | 5 | GPT content analysis |
| Storage | 10/GB/month | Over free quota |
| Monthly Membership | 100/month | Unlimited parse+download |

#### Admin Features

- View all user credit balances
- Manual recharge/deduct credits
- View transaction history
- Configure pricing per action

### 5. System Settings

| Feature | Description |
|---------|-------------|
| Global Config | Site name, logo, announcements |
| Feature Toggles | Enable/disable registration, download, AI analysis |
| Credit Pricing | Adjust credits cost per action |
| Free Quota | New user gift credits, free storage |
| Email Templates | Welcome, low balance alerts |

### 6. Statistics Dashboard

| Chart | Description |
|-------|-------------|
| User Growth | Daily/weekly/monthly new users |
| Active Users | DAU / WAU / MAU |
| Download Stats | Parse count, downloads, success rate |
| Credit Flow | Total recharge, spending, balance distribution |
| Storage Usage | Total usage, per-user breakdown |

### 7. Audit Logs

```sql
audit_logs (
  id UUID PRIMARY KEY,
  admin_id UUID,
  action VARCHAR,          -- 'user.ban', 'team.delete', 'credit.add'
  target_type VARCHAR,     -- 'user', 'team', 'video', 'tag'
  target_id VARCHAR,
  details JSONB,
  ip_address VARCHAR,
  created_at TIMESTAMPTZ
)
```

Features:
- Record all admin operations
- Filter by time, admin, action type
- Export logs

### 8. API Management

| Feature | Description |
|---------|-------------|
| API Key List | View all users' API keys |
| Revoke Key | Immediately disable a key |
| Usage Stats | Call count, last used time per key |
| Rate Limiting | Set per-minute/daily call limits |

## Project Structure

### Admin Frontend

```
mediahub-admin/
├── src/
│   ├── providers/
│   │   ├── authProvider.ts      # Supabase Auth adapter
│   │   └── dataProvider.ts      # Supabase Data adapter
│   ├── pages/
│   │   ├── dashboard/           # Stats overview
│   │   ├── users/               # User management
│   │   ├── teams/               # Team management
│   │   ├── videos/              # Content management
│   │   ├── tags/                # Tag management
│   │   ├── credits/             # Credits management
│   │   ├── audit-logs/          # Audit logs
│   │   ├── api-keys/            # API management
│   │   └── settings/            # System settings
│   ├── components/
│   └── App.tsx
├── Dockerfile
├── docker-compose.yml
└── package.json
```

### Backend New APIs

```
/api/v1/admin/
├── users/                  # User CRUD
├── teams/                  # Team management
├── videos/                 # Content management
├── tags/                   # Tag management (including system tags)
├── credits/
│   ├── GET    /            # All user credits
│   ├── POST   /{user_id}   # Manual recharge
│   └── GET    /transactions # Transaction history
├── audit-logs/             # Audit logs
├── api-keys/               # API Key management
├── stats/                  # Statistics data
└── settings/               # System config
```

### New Database Tables

| Table | Purpose |
|-------|---------|
| `user_credits` | User credit accounts |
| `credit_transactions` | Credit transaction log |
| `credit_pricing` | Credit pricing config |
| `audit_logs` | Audit logs |
| `system_settings` | Global system config |

### Docker Deployment

```yaml
# Add to docker/docker-compose.yml
mediahub-admin:
  image: imheygo/mediahub-admin:latest
  container_name: mediahub-admin
  restart: unless-stopped
  ports:
    - "3097:80"
  environment:
    - VITE_SUPABASE_URL=${SUPABASE_URL}
    - VITE_SUPABASE_ANON_KEY=${SUPABASE_ANON_KEY}
    - VITE_API_URL=http://mediahub:8080
  depends_on:
    - mediahub
```

## Implementation Phases

### Phase 1: Foundation
1. Create `mediahub-admin` project with Refine
2. Setup Supabase Auth provider (admin role check)
3. Create database migrations for new tables
4. Implement admin permission middleware in backend

### Phase 2: Core Modules
5. User management (list, search, role, ban)
6. Team management (list, members, dissolve)
7. Content management (videos, tags)
8. Audit logging

### Phase 3: Credits System
9. Credits database tables
10. Credits API endpoints
11. Admin credits management UI
12. Integrate credits check into main app

### Phase 4: Advanced Features
13. Statistics dashboard
14. System settings
15. API key management
16. Docker build & deployment

## Security Considerations

1. **Admin Role Check**: Every `/api/v1/admin/*` endpoint must verify `user_profiles.role = 'admin'`
2. **Audit Everything**: Log all admin actions with IP address
3. **Rate Limiting**: Prevent brute force on admin login
4. **Network Isolation**: Admin panel on internal network, access via VPN or port forwarding
5. **Soft Delete**: Never hard delete user data, always soft delete with audit trail
