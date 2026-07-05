"""SQLAlchemy 2.0 ORM model package — 104 mapped classes + 1 junction table = 105 tables.

(Was 106/107 before migration 333 retired the ai_sessions/ai_messages models —
Phase 3 Wave 2 legacy-chat table drop; conversations/messages are raw-SQL,
not yet ORM-mapped.)

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
    AgentMemory,
    AiAgents,
    AiAgentVersions,
    AiModelPrices,
    AiSessionMemory,
    AiUsageLogs,
    MediahubModels,
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
from app.models.generated_media import (  # noqa: F401
    GeneratedMedia,
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
    Episodes,
    ProjectWorkflows,
    ScriptAssets,
    ScriptChapters,
    ScriptOps,
    ScriptProjects,
    ScriptScenes,
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
    ProjectFileComments,
    ProjectFiles,
    ProjectFolders,
    ProjectMembers,
    Projects,
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
    "AgentOutbox",
    "AgentRunEvents",
    "AgentRuns",
    "AgentSkills",
    "AgentStateHistory",
    "AgentTasks",
    "AgentWorkers",
    # ai
    "AgentMemory",
    "AiAgentVersions",
    "AiAgents",
    "AiModelPrices",
    "AiSessionMemory",
    "AiUsageLogs",
    "MediahubModels",
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
    "GeneratedMedia",
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
    "Episodes",
    "ProjectWorkflows",
    "ScriptAssets",
    "ScriptChapters",
    "ScriptOps",
    "ScriptProjects",
    "ScriptScenes",
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
    "ProjectFileComments",
    "ProjectFiles",
    "ProjectFolders",
    "ProjectMembers",
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
