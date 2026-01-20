# Team Settings Feature Design

## Overview

Design for Team management features including Settings Modal, team switching behavior, invite system, and deletion logic.

## Features

### 1. Team Dropdown Behavior

**Selected Team moves to top:**
- When user selects a team from the dropdown, that team moves to the top of the list
- Order: [Selected Team] → [Other Teams sorted by name] → [Personal]
- Visual indicator (checkmark) shows current selection

### 2. Settings Modal (Full-Screen)

**Layout:**
- Full-screen modal with dark overlay
- Left sidebar navigation
- Right content area
- Close button (X) in top-right corner

**Navigation Sections:**
1. Personal Settings
2. Team Settings (only visible when a team is selected)

### 3. Personal Settings

| Field | Type | Description |
|-------|------|-------------|
| Avatar | Image Upload | Profile picture with upload/change option |
| Username | Text Input | Editable display name |
| Bio | Textarea | Short user description |
| Email | Text (readonly) | From Supabase Auth, display only |
| Change Password | Button/Form | Opens password change flow via Supabase Auth |

**Password Change Flow:**
1. Click "Change Password" button
2. Show inline form: Current Password, New Password, Confirm Password
3. Use Supabase Auth `updateUser({ password })` API
4. Show success/error message

### 4. Team Settings

**Header Section:**
- Team avatar/icon
- Team name (editable by owner)
- Team description (editable by owner)

**Members Table:**

| Column | Description |
|--------|-------------|
| Avatar | Member profile picture |
| Name | Member display name |
| Email | Member email |
| Role | Owner / Admin / Member (dropdown for role change) |
| Actions | Remove button (not for owner) |

**Actions:**
- "Invite Members" button → Opens invite modal
- "Leave Team" button (for non-owners)
- "Delete Team" button (owner only, danger zone)

### 5. Invite Members Modal

**Design (based on reference screenshots):**

```
┌─────────────────────────────────────────┐
│  Invite Members                      X  │
├─────────────────────────────────────────┤
│                                         │
│  Share this link to invite members:     │
│  ┌─────────────────────────────────┐    │
│  │ https://app.com/invite/abc123   │ 📋 │
│  └─────────────────────────────────┘    │
│                                         │
│  Link Settings                          │
│  ┌─────────────────────────────────┐    │
│  │ Expires: [7 days ▼]             │    │
│  │ Max uses: [No limit ▼]          │    │
│  └─────────────────────────────────┘    │
│                                         │
│  [Generate New Link]                    │
│                                         │
└─────────────────────────────────────────┘
```

**Link Settings Options:**
- Expires: 30 minutes, 1 hour, 6 hours, 12 hours, 1 day, 7 days, Never
- Max uses: 1, 5, 10, 25, 50, 100, No limit

**Copy Button Behavior:**
- Click to copy link to clipboard
- Show "Copied!" tooltip briefly

### 6. Team Deletion Logic

**Owner deletes team:**
1. Click "Delete Team" button (in danger zone)
2. Confirmation modal appears:
   - Warning message about permanent deletion
   - Text input: "Type team name to confirm"
   - Delete button (disabled until name matches)
3. On confirm:
   - Delete all team data (members, invites, etc.)
   - All members are automatically removed
   - Owner redirects to Personal space
   - Show success toast

**Member leaves team:**
1. Click "Leave Team" button
2. Simple confirmation dialog: "Are you sure you want to leave [Team Name]?"
3. On confirm:
   - Remove member from team
   - Redirect to Personal space
   - Show success toast

## Database Schema

### New Tables

```sql
-- Team invite links
CREATE TABLE team_invites (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  team_id UUID REFERENCES teams(id) ON DELETE CASCADE,
  code VARCHAR(20) UNIQUE NOT NULL,
  created_by UUID REFERENCES auth.users(id),
  expires_at TIMESTAMPTZ,
  max_uses INTEGER,
  use_count INTEGER DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for quick lookup
CREATE INDEX idx_team_invites_code ON team_invites(code);
```

### RLS Policies

```sql
-- Team invites: only team admins/owners can create
CREATE POLICY "Team admins can manage invites"
ON team_invites
FOR ALL
USING (
  EXISTS (
    SELECT 1 FROM team_members
    WHERE team_members.team_id = team_invites.team_id
    AND team_members.user_id = auth.uid()
    AND team_members.role IN ('owner', 'admin')
  )
);
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/users/me` | PUT | Update user profile (avatar, username, bio) |
| `/api/v1/users/me/password` | PUT | Change password |
| `/api/v1/teams/{id}` | PUT | Update team info |
| `/api/v1/teams/{id}` | DELETE | Delete team (owner only) |
| `/api/v1/teams/{id}/members` | GET | List team members |
| `/api/v1/teams/{id}/members/{user_id}` | DELETE | Remove member |
| `/api/v1/teams/{id}/members/{user_id}/role` | PUT | Change member role |
| `/api/v1/teams/{id}/leave` | POST | Leave team |
| `/api/v1/teams/{id}/invites` | POST | Create invite link |
| `/api/v1/teams/{id}/invites` | GET | List active invites |
| `/api/v1/teams/{id}/invites/{invite_id}` | DELETE | Revoke invite |
| `/api/v1/invites/{code}` | GET | Get invite info (public) |
| `/api/v1/invites/{code}/accept` | POST | Accept invite |

## Component Structure

```
components/
├── SettingsModal/
│   ├── SettingsModal.tsx        # Main modal wrapper
│   ├── SettingsSidebar.tsx      # Left navigation
│   ├── PersonalSettings.tsx     # Personal settings form
│   ├── TeamSettings.tsx         # Team settings page
│   ├── MembersTable.tsx         # Team members table
│   ├── InviteMembersModal.tsx   # Invite modal
│   └── DeleteTeamModal.tsx      # Delete confirmation
```

## Implementation Order

1. Settings Modal shell (layout, navigation)
2. Personal Settings (avatar, username, bio, email)
3. Change Password functionality
4. Team Settings header (name, description)
5. Members table (list, role display)
6. Role management (change roles, remove members)
7. Leave team functionality
8. Invite system (create link, copy, settings)
9. Accept invite flow
10. Delete team functionality
11. Team dropdown reordering

## Notes

- Notification preferences will be added later with the notification system
- Language switcher not needed (global setting exists)
- Billing/subscription features not included in this version
