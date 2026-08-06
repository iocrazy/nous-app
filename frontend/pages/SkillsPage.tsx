import React from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { SkillsTab } from '../components/AILibrary/SkillsTab';

/**
 * Routes handled:
 *   /team/:teamId/ai-library/skills
 *   /team/:teamId/ai-library/skills/:slug
 *   /team/:teamId/ai-library/skills/:slug/files/<relative file path>
 *
 * The file path after ``/files/`` is parsed off the pathname here because
 * it contains slashes (e.g. ``references/examples.md``) which React
 * Router can't capture cleanly into a single param.
 */
export const SkillsPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const params = useParams();
  const teamId = params.teamId as string | undefined;
  const urlSlug = (params.slug as string | undefined) ?? null;

  const prefix = teamId ? `/team/${teamId}` : '';
  // Stay under /ai-library/skills when nested in the new secondary-sidebar
  // layout; legacy /skills keeps working for old bookmarks.
  const skillsBase = location.pathname.includes('/ai-library/')
    ? '/ai-library/skills'
    : '/skills';

  let filePath: string | null = null;
  if (urlSlug) {
    const marker = `/${urlSlug}/files/`;
    const idx = location.pathname.indexOf(marker);
    if (idx !== -1) {
      filePath = decodeURIComponent(
        location.pathname.slice(idx + marker.length),
      );
    }
  }

  return (
    // Same fix as AgentsPage: `h-full … overflow-hidden` pinned this box to
    // the viewport and clipped, so SkillEditor's own `overflow-y-auto` became
    // a second scroll container nested inside AILibraryLayout's. Flow instead.
    <div className="flex-1 min-w-0">
      <SkillsTab
        slug={urlSlug}
        filePath={filePath}
        onSlugChange={(next) =>
          navigate(
            next ? `${prefix}${skillsBase}/${next}` : `${prefix}${skillsBase}`,
          )
        }
        onFilePathChange={(s, p) =>
          navigate(
            `${prefix}${skillsBase}/${encodeURIComponent(s)}/files/${p
              .split('/')
              .map(encodeURIComponent)
              .join('/')}`,
          )
        }
      />
    </div>
  );
};

export default SkillsPage;
