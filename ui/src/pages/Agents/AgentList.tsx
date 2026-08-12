import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { listAgents } from '../../api';

/**
 * `/settings/agents` has no list view of its own — it redirects to the first
 * agent's detail page, which carries the roster in its sidebar.
 *
 * The target used to be hardcoded to `sdr-researcher`, a prototype slug that was
 * never in the registry, so this route landed on a permanent "Loading…". It now
 * asks the backend which agents exist.
 */
export default function AgentList() {
  const [slug, setSlug] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    listAgents()
      .then((rows) => {
        if (rows.length > 0) setSlug(rows[0].slug);
        else setFailed(true);
      })
      .catch(() => setFailed(true));
  }, []);

  if (slug) return <Navigate to={`/settings/agents/${slug}`} replace />;

  return (
    <div className="p-6">
      <p className="text-sm text-slate-500">
        {failed ? 'No agents registered.' : 'Loading agents…'}
      </p>
    </div>
  );
}
