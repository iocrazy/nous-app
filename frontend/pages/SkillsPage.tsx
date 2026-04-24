import React from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { SkillsTab } from '../components/AILibrary/SkillsTab';

export const SkillsPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const urlSlug = (params.slug as string | undefined) ?? null;

  const prefix = teamId ? `/team/${teamId}` : '';
  // Stay under /ai-library/skills when nested in the new secondary-sidebar
  // layout; otherwise fall back to the legacy /skills path.
  const skillsBase = location.pathname.includes('/ai-library/')
    ? '/ai-library/skills'
    : '/skills';

  return (
    <div className="flex-1 h-full min-h-0 overflow-hidden">
      <SkillsTab
        slug={urlSlug}
        onSlugChange={(next) => navigate(`${prefix}${skillsBase}/${next}`)}
      />
    </div>
  );
};

export default SkillsPage;
