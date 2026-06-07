"""SQLAlchemy 2.0 ORM model package — 106 mapped classes + 1 junction table = 107 tables.

Flat models only: no joined-table inheritance, no relationships, no create_all.
auth.users (GoTrue) is permanently EXCLUDED; FKs targeting users.id are omitted.
Models are REFERENCE METADATA ONLY. Schema is owned by supabase/migrations/*.sql.

``Base`` is re-exported here for convenience (``from app.models import Base``).
Domain modules are kept under 800 lines; large domains are split (agents +
agent_messaging, storyboard + scripts).
"""

# noqa: F401 — intentional re-exports for ``from app.models import *``

from app.db.orm_base import Base  # noqa: F401
from app.models._enums import (  # noqa: F401
    AiTaskStatus,
    ApiKeyStatus,
    DownloadStatus,
    UserRole,
)
from app.models.agent_messaging import (  # noqa: F401
    AgentApprovalRequests,
    AgentCommitments,
    AgentInbox,
    AgentMemories,
    AgentOutbox,
)
from app.models.agents import (  # noqa: F401
    AgentRunEvents,
    AgentRuns,
    AgentSkills,
    AgentStateHistory,
    AgentTasks,
    AgentWorkers,
)
from app.models.ai import (  # noqa: F401
    AiAgents,
    AiAgentVersions,
    AiMessages,
    AiModelPrices,
    AiSessionMemory,
    AiSessions,
    AiUsageLogs,
    NousModels,
    SkillFiles,
    SkillFileVersions,
    Skills,
    SkillVersions,
)
from app.models.billing import (  # noqa: F401
    CreditPricing,
    CreditTransactions,
    DailyPointGifts,
    Orders,
    PointPackages,
    PointPricing,
    PointTransactions,
)
from app.models.library import (  # noqa: F401
    Authors,
    Collections,
    Libraries,
    ProjectCollections,
    SearchLogs,
    Shares,
    ShareViews,
    SmartCollections,
    StyleTemplates,
    TagGroups,
    Tags,
    TempTokens,
)
from app.models.media import (  # noqa: F401
    Folders,
    ParsedMedia,
    ResourceAccessLogs,
    ResourceAnalysis,
    ResourceItems,
    Resources,
    ResourceSummaries,
    ResourceTags,
    ResourceTranscripts,
    ResourceVersions,
)
from app.models.ops import (  # noqa: F401
    AccessOverrides,
    AdminTablePreferences,
    ApiKeyLogs,
    ApiKeys,
    ApiRequestLogs,
    ApplicationLogs,
    AuditLogs,
    BoundaryAudit,
    DeploymentLogs,
    FrontendErrorLogs,
    SystemSettings,
    SystemStatus,
    TaskTracking,
)
from app.models.reviews import (  # noqa: F401
    IssueMessages,
    Issues,
    IssueSequence,
    Notifications,
    ReviewAnnotations,
    ReviewComments,
    ReviewStatus,
    UserNotifications,
)
from app.models.scripts import (  # noqa: F401
    ProjectWorkflows,
    ScriptAssets,
    ScriptChapters,
    ScriptProjects,
    ScriptStoryboardLinks,
    TaskFlows,
    WorkflowNodes,
    WorkflowTimeoutPolicy,
)
from app.models.storyboard import (  # noqa: F401
    StoryboardAssets,
    StoryboardCharacters,
    StoryboardEdges,
    StoryboardFrames,
    StoryboardNodes,
    StoryboardProjects,
    StoryboardVideoAssets,
    UserSchedules,
    t_storyboard_frame_characters,
)
from app.models.teams import (  # noqa: F401
    FileVersions,
    MemberQuotas,
    ProjectFiles,
    ProjectFolders,
    ProjectMembers,
    Projects,
    ProjectTasks,
    TeamInvites,
    TeamMembers,
    TeamPlans,
    TeamQuotas,
    Teams,
)
from app.models.users import (  # noqa: F401
    UserCookies,
    UserCredits,
    UserLogs,
    UserMcpServers,
    UserProfiles,
    UserSettings,
    UserTagPreferences,
)

__all__ = [
    # declarative base
    "Base",
    # enums
    "AiTaskStatus",
    "ApiKeyStatus",
    "DownloadStatus",
    "UserRole",
    # agents
    "AgentApprovalRequests",
    "AgentCommitments",
    "AgentInbox",
    "AgentMemories",
    "AgentOutbox",
    "AgentRunEvents",
    "AgentRuns",
    "AgentSkills",
    "AgentStateHistory",
    "AgentTasks",
    "AgentWorkers",
    # ai
    "AiAgentVersions",
    "AiAgents",
    "AiMessages",
    "AiModelPrices",
    "AiSessionMemory",
    "AiSessions",
    "AiUsageLogs",
    "NousModels",
    "SkillFileVersions",
    "SkillFiles",
    "SkillVersions",
    "Skills",
    # billing
    "CreditPricing",
    "CreditTransactions",
    "DailyPointGifts",
    "Orders",
    "PointPackages",
    "PointPricing",
    "PointTransactions",
    # library
    "Authors",
    "Collections",
    "Libraries",
    "ProjectCollections",
    "SearchLogs",
    "ShareViews",
    "Shares",
    "SmartCollections",
    "StyleTemplates",
    "TagGroups",
    "Tags",
    "TempTokens",
    # media
    "Folders",
    "ParsedMedia",
    "ResourceAccessLogs",
    "ResourceAnalysis",
    "ResourceItems",
    "ResourceSummaries",
    "ResourceTags",
    "ResourceTranscripts",
    "ResourceVersions",
    "Resources",
    # ops
    "AccessOverrides",
    "AdminTablePreferences",
    "ApiKeyLogs",
    "ApiKeys",
    "ApiRequestLogs",
    "ApplicationLogs",
    "AuditLogs",
    "BoundaryAudit",
    "DeploymentLogs",
    "FrontendErrorLogs",
    "SystemSettings",
    "SystemStatus",
    "TaskTracking",
    # reviews
    "IssueMessages",
    "IssueSequence",
    "Issues",
    "Notifications",
    "ReviewAnnotations",
    "ReviewComments",
    "ReviewStatus",
    "UserNotifications",
    # scripts
    "ProjectWorkflows",
    "ScriptAssets",
    "ScriptChapters",
    "ScriptProjects",
    "ScriptStoryboardLinks",
    "TaskFlows",
    "WorkflowNodes",
    "WorkflowTimeoutPolicy",
    # storyboard
    "StoryboardAssets",
    "StoryboardCharacters",
    "StoryboardEdges",
    "StoryboardFrames",
    "StoryboardNodes",
    "StoryboardProjects",
    "StoryboardVideoAssets",
    "UserSchedules",
    "t_storyboard_frame_characters",
    # teams
    "FileVersions",
    "MemberQuotas",
    "ProjectFiles",
    "ProjectFolders",
    "ProjectMembers",
    "ProjectTasks",
    "Projects",
    "TeamInvites",
    "TeamMembers",
    "TeamPlans",
    "TeamQuotas",
    "Teams",
    # users
    "UserCookies",
    "UserMcpServers",
    "UserCredits",
    "UserLogs",
    "UserProfiles",
    "UserSettings",
    "UserTagPreferences",
]
