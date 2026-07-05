# Chat Group Management (P1 + P2) — 2026-07-04

## Problem

After creating a group chat there is NO entry point to manage it: no member list,
no way to add members/agents post-creation, no roles, no leave/dissolve. The
backend already has `POST /{id}/members` and `POST /{id}/agents`, and
`conversation_members.role` already stores `owner` for the creator — but none of
this is reachable from the UI, and there is no read endpoint for members.

Benchmark: Feishu group settings drawer (member list with 群主/管理员 badges,
set-admin / transfer-owner / remove hover actions, leave/dissolve footer).

## Permission matrix (user-approved 2026-07-04)

| Action                          | member | admin | owner |
|---------------------------------|--------|-------|-------|
| Invite members / add agent      | ✅     | ✅    | ✅    |
| Remove regular member / agent   | ❌     | ✅    | ✅    |
| Remove admin                    | ❌     | ❌    | ✅    |
| Grant/revoke admin              | ❌     | ❌    | ✅    |
| Transfer ownership (self→member)| ❌     | ❌    | ✅    |
| Edit group name/visibility      | ❌     | ✅    | ✅    |
| Leave group                     | ✅     | ✅    | ❌ (must transfer first) |
| Dissolve group (archive)        | ❌     | ❌    | ✅    |

Roles live in `conversation_members.role` (TEXT, no CHECK — values:
`owner` | `admin` | `member`). No migration needed.

## Backend

- `GET    /conversations/{id}/members` — users (name enriched from
  user_profiles.username, service-role) + agents (name/slug from ai_agents),
  each with role + joined_at. Caller must be member.
- `DELETE /conversations/{id}/members/{user_id}` — remove (admin/owner per
  matrix) or leave (self; owner blocked).
- `PATCH  /conversations/{id}/members/{user_id}/role` — owner only; `admin`⇄`member`.
- `POST   /conversations/{id}/transfer-owner` — owner only; old owner → member.
- `DELETE /conversations/{id}/agents/{agent_id}` — admin/owner.
- `PATCH  /conversations/{id}` — name / visibility (group⇄public); admin/owner.
- `DELETE /conversations/{id}` — dissolve = set archived_at; owner only.

Guards: all group-management endpoints reject `direct_agent`/`dm` types.
Roles are enforced in ConversationService (same layer as existing PERM-08).

## Frontend

- `GroupSettingsDrawer.tsx` — header settings button → right drawer:
  group info (inline name edit, visibility), member list (search, role badges,
  hover actions gated by my role), add-member picker (getTeamMembers minus
  current members), agents section (add/remove), footer leave/dissolve.
- ChatPage: wire drawer; refresh sidebar on rename; deselect on leave/dissolve.
- CreateGroupModal: fix `fetchTeamMembers` → `getTeamMembers` (raw-UUID chips bug).
- i18n en/zh; types.ts `ConversationMember`.

## Tests

- Backend: permission-matrix unit tests over ConversationService with a stub
  repo (each cell of the matrix + direct_agent guard + owner-leave block).
- Frontend: build + existing suites; drawer logic kept simple/presentational.
