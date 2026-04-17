"""Admin-specific repositories.

Admin routers previously called `get_async_supabase_admin()` inline at
100+ sites across the admin subtree, coupling HTTP handlers to the
database layout. These repositories centralize that access so:

- Admin table names live in one place
- Column-selection and audit decisions are reusable
- Router code becomes thin enough to test without a live Supabase
"""
