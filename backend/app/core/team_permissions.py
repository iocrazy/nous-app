"""Team role and permission definitions.

Static constants — no DB table needed.
"""

# All granular permission keys
TEAM_PERMISSIONS = [
    "manage_team",       # Edit team name/description, delete team
    "manage_members",    # Invite/remove members, change roles
    "manage_collections",  # Create/edit/delete collections
    "manage_videos",     # Add/remove videos from collections
    "edit_tags",         # Create/edit/delete tags on videos
    "edit_analysis",     # Edit AI analysis / notes
    "comment",           # Add comments
    "view_analytics",    # View team analytics
    "export",            # Export / download data
    "view",              # View team content
]

# Ordered from highest to lowest privilege
TEAM_ROLES = ["owner", "admin", "editor", "reviewer", "viewer"]

# Role -> set of permissions  (owner uses wildcard)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "owner": {"*"},
    "admin": {
        "manage_members",
        "manage_collections",
        "manage_videos",
        "edit_tags",
        "edit_analysis",
        "comment",
        "view_analytics",
        "export",
        "view",
    },
    "editor": {
        "manage_collections",
        "manage_videos",
        "edit_tags",
        "edit_analysis",
        "comment",
        "view_analytics",
        "export",
        "view",
    },
    "reviewer": {
        "comment",
        "view_analytics",
        "export",
        "view",
    },
    "viewer": {
        "view",
    },
}

# Roles that can be assigned via the admin panel (owner is excluded)
ASSIGNABLE_ROLES = ["admin", "editor", "reviewer", "viewer"]


def get_role_info() -> list[dict]:
    """Return a list of role definitions suitable for API responses."""
    return [
        {
            "role": role,
            "permissions": sorted(ROLE_PERMISSIONS[role]),
            "is_assignable": role in ASSIGNABLE_ROLES,
        }
        for role in TEAM_ROLES
    ]
