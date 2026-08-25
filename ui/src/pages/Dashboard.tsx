import { LayoutDashboard } from 'lucide-react';
import PlaceholderPage from '../components/shared/PlaceholderPage';

/**
 * Dashboard — placeholder.
 *
 * The previous version rendered agent cards plus a "Recent Activity" feed off
 * `/audit-log`. Neither is what this screen should be: the agent cards restated
 * the Agents tab, and the activity feed restated the Audit Log tab. What a
 * revenue-operations landing page should surface — billing run state, revenue
 * recognition status, exceptions needing attention — has not been designed.
 */
export default function Dashboard() {
  return (
    <PlaceholderPage
      title="Dashboard"
      subtitle="Landing view for revenue operations status."
      icon={LayoutDashboard}
    >
      <p>
        This screen has not been designed. Everything it used to show already lives on a tab
        of its own — agents under Agents, recent events under Audit Log.
      </p>
      <p>
        What belongs here instead — billing run state, revenue recognition status, and the
        exceptions that need a human — is not scoped yet.
      </p>
    </PlaceholderPage>
  );
}
