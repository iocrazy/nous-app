"""SQLAlchemy 2.0 ORM model package — 151 mapped classes + 2 bare Tables = 153 tables.

The bare Tables (``t_storyboard_frame_characters``, ``t_worker_registry``) have
no usable primary key in the DB, so they are mapped as Core Tables rather than
declarative classes — no synthetic PK is invented for them.

Kept honest by ``tests/db/test_schema_drift.py``, which diffs every model here
against a real Postgres built from ``supabase/schema_baseline.sql``. A migration
that adds a table or column must add it here in the same PR, or that gate fails.

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
    AgentMemoryPromotions,
    AgentOverrides,
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
from app.models.alerting import (  # noqa: F401
    AlertHistory,
    AlertRules,
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
from app.models.canvas import (  # noqa: F401
    Canvases,
    CanvasResourceRefs,
)
from app.models.chat import (  # noqa: F401
    ConversationAiMeta,
    ConversationMembers,
    ConversationMemory,
    Conversations,
    MessageAttachments,
    MessageRefs,
    Messages,
)
from app.models.distribution import (  # noqa: F401
    DistributionOauthStates,
    PublishTaskAccounts,
    PublishTasks,
    SocialAccounts,
)
from app.models.generated_media import (  # noqa: F401
    GeneratedMedia,
)
from app.models.inspiration import (  # noqa: F401
    InspirationApiTokens,
    InspirationAttachments,
    InspirationNotes,
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
from app.models.project_library import (  # noqa: F401
    ProjectCharacters,
    ProjectLibEntities,
    ProjectStageHistory,
    ProjectStages,
    ProjectStyleProfile,
)
from app.models.provider_costs import (  # noqa: F401
    CostAuditLog,
    FxRates,
    ProviderByokKeys,
    ProviderContracts,
    ProviderCredits,
    ProviderMonthlySpend,
    ProviderPricing,
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
    ScriptBeats,
    ScriptChapters,
    ScriptCommits,
    ScriptOps,
    ScriptProjects,
    ScriptScenes,
    ScriptShots,
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
from app.models.topics import (  # noqa: F401
    Hotspots,
    HotspotUserState,
    SignalSources,
    TopicGroups,
    UserHiddenSources,
    UserTopicInterests,
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
from app.models.workers import (  # noqa: F401
    t_worker_registry,
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
    "AgentMemoryPromotions",
    "AgentOverrides",
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
    # alerting
    "AlertHistory",
    "AlertRules",
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
    "ScriptBeats",
    "ScriptChapters",
    "ScriptCommits",
    "ScriptOps",
    "ScriptProjects",
    "ScriptScenes",
    "ScriptShots",
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
    # inspiration notes domain
    "InspirationApiTokens",
    "InspirationAttachments",
    "InspirationNotes",
    # canvas
    "CanvasResourceRefs",
    "Canvases",
    # chat / conversations
    "ConversationAiMeta",
    "ConversationMembers",
    "ConversationMemory",
    "Conversations",
    "MessageAttachments",
    "MessageRefs",
    "Messages",
    # distribution
    "DistributionOauthStates",
    "PublishTaskAccounts",
    "PublishTasks",
    "SocialAccounts",
    # project authored library
    "ProjectCharacters",
    "ProjectLibEntities",
    "ProjectStageHistory",
    "ProjectStages",
    "ProjectStyleProfile",
    # provider cost governance
    "CostAuditLog",
    "FxRates",
    "ProviderByokKeys",
    "ProviderContracts",
    "ProviderCredits",
    "ProviderMonthlySpend",
    "ProviderPricing",
    # topics / signal feed
    "Hotspots",
    "HotspotUserState",
    "SignalSources",
    "TopicGroups",
    "UserHiddenSources",
    "UserTopicInterests",
    # users
    "UserCookies",
    "UserMcpServers",
    "UserCredits",
    "UserLogs",
    "UserProfiles",
    "UserSettings",
    "UserTagPreferences",
    # workers
    "t_worker_registry",
]
