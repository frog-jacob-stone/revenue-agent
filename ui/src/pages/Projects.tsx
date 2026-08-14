import { FolderKanban } from 'lucide-react';
import PlaceholderPage from '../components/shared/PlaceholderPage';

/**
 * Projects — placeholder.
 *
 * The only project data in this system is `harvest_projects`, a read cache of
 * Harvest's own list. There is no `projects` table that this system owns, and
 * no concept of a project being "complete" beyond Harvest's archived flag —
 * see "Revenue Reporting & Project Tracking" in PROGRESS.md.
 */
export default function Projects() {
  return (
    <PlaceholderPage
      title="Projects"
      subtitle="Engagements, their delivery status, and how each one is billed."
      icon={FolderKanban}
    >
      <p>
        The only project data here today is <code>harvest_projects</code>, a read cache of
        Harvest's list. Nothing tracks delivery status: there is no concept of a project being
        complete beyond Harvest's own archived flag.
      </p>
      <p>
        This screen needs a project record this system owns — one that can hold completion
        state and tie an engagement to its billing group and revenue — before there is anything
        to show.
      </p>
    </PlaceholderPage>
  );
}
