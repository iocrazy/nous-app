# Phone + Password Auth Design

**Date:** 2026-03-06
**Status:** Approved

## Problem

Frontend has phone login UI shell but no real auth logic. Need phone+password login at zero cost (no SMS).

## Solution: Phone-to-Email Conversion

Convert phone number to a deterministic email address and use standard Supabase email auth.

```
Phone: 13800138000 → Email: 86_13800138000@phone.mediahub.internal
```

### Why This Approach

- **Zero Supabase config changes** — no need to enable phone provider on GoTrue
- **Zero backend changes** — frontend calls Supabase JS directly (same as current email flow)
- **Zero cost** — no SMS provider needed
- **Reversible** — can add real phone auth later without breaking existing accounts

### Phone-to-Email Format

```
{country_code}_{phone_number}@phone.mediahub.internal
```

- Country code default: `86` (China)
- Phone stored in `user_metadata.phone` for display
- `user_metadata.login_type` = `"phone"` to distinguish from email users

## Changes

### Frontend Only: `AuthOverlay.tsx`

1. **Phone login**: convert phone → email, call `supabase.auth.signInWithPassword({ email })`
2. **Phone register**: convert phone → email, call `supabase.auth.signUp({ email, options: { data: { phone, login_type: 'phone' } } })`
3. **Phone mode UI**: show Login/Register toggle (same as email mode), hide SMS tab
4. **Validation**: 11-digit China mobile number

### Backend (optional, for API completeness)

Add to `supabase_auth_router.py`:
- `POST /auth/signup-phone` — phone+password signup
- `POST /auth/signin-phone` — phone+password signin
- Same phone→email conversion logic
- Handles team quota creation and event logging

## Data Flow

```
Register:
  User enters phone=13800138000, password=xxx
  → Frontend: email = "86_13800138000@phone.mediahub.internal"
  → supabase.auth.signUp({ email, password, data: { phone: "+8613800138000", login_type: "phone" } })
  → Supabase creates user with auto-confirm (email confirm disabled for self-hosted)
  → Returns JWT session

Login:
  User enters phone=13800138000, password=xxx
  → Frontend: email = "86_13800138000@phone.mediahub.internal"
  → supabase.auth.signInWithPassword({ email, password })
  → Returns JWT session

Display:
  user_metadata.login_type === "phone"
    → Show phone number from user_metadata.phone
  else
    → Show email as before
```

## Future: SMS Support

When needed, add SMS verification without breaking existing accounts:
1. Configure Twilio/Aliyun SMS on GoTrue
2. Add `/auth/send-otp` and `/auth/verify-otp` endpoints
3. Enable SMS Login tab in frontend
4. Existing phone+password users unaffected
