import { Circle, Edit2 } from 'lucide-react';
import StubBadge from '../components/shared/StubBadge';

interface Integration {
  name: string;
  description: string;
  connected: boolean;
  key: string;
}

const INTEGRATIONS: Integration[] = [
  { name: 'HubSpot', description: 'CRM for contacts, companies, and deals', connected: true, key: 'hubspot' },
  { name: 'Apollo.io', description: 'Contact enrichment and prospecting data', connected: true, key: 'apollo' },
  { name: 'Anthropic API', description: 'Claude LLM for all agent reasoning', connected: true, key: 'anthropic' },
  { name: 'Slack', description: 'Notifications and alert delivery', connected: false, key: 'slack' },
];

const SCHEDULES = [
  { agent: 'SDR Researcher', cron: '0 8 * * 1-5', description: 'Weekdays at 08:00 UTC' },
  { agent: 'Outreach Agent', cron: '0 9 * * 1-5', description: 'Weekdays at 09:00 UTC' },
  { agent: 'Content Writer', cron: '0 10 * * 1', description: 'Mondays at 10:00 UTC' },
  { agent: 'Proposal Generator', cron: 'On trigger', description: 'Runs on HubSpot stage change' },
  { agent: 'Slide Deck Agent', cron: 'On trigger', description: 'Runs when proposal approved' },
  { agent: 'Revenue Recognition', cron: '0 6 1 * *', description: '1st of month at 06:00 UTC' },
];

export default function Settings() {
  return (
    <div className="p-6 space-y-6 max-w-3xl mx-auto">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Settings</h1>
        <p className="text-sm text-slate-400 mt-0.5">Integrations, schedules, and preferences</p>
      </div>

      {/* Integrations */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Integrations</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          {INTEGRATIONS.map((int, i) => (
            <div key={int.key} className={`flex items-center justify-between px-5 py-4 ${i < INTEGRATIONS.length - 1 ? 'border-b border-slate-800' : ''}`}>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5">
                  <Circle className={`w-2 h-2 ${int.connected ? 'fill-emerald-400 text-emerald-400' : 'fill-slate-600 text-slate-600'}`} />
                  <span className={`text-xs font-medium ${int.connected ? 'text-emerald-400' : 'text-slate-500'}`}>
                    {int.connected ? 'Connected' : 'Not configured'}
                  </span>
                </div>
                <div>
                  <p className="text-sm font-medium text-slate-200">{int.name}</p>
                  <p className="text-xs text-slate-500">{int.description}</p>
                </div>
              </div>
              <button
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
                onClick={() => console.log('connect/edit', int.key)}
              >
                <Edit2 className="w-3 h-3" />
                {int.connected ? 'Edit' : 'Connect'}
                <StubBadge />
              </button>
            </div>
          ))}
        </div>
      </section>

      {/* Agent Scheduling */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Agent Scheduling</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-xs text-slate-500 uppercase tracking-wide">
                <th className="text-left px-5 py-3 font-medium">Agent</th>
                <th className="text-left px-5 py-3 font-medium">Cron</th>
                <th className="text-left px-5 py-3 font-medium">Description</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody>
              {SCHEDULES.map((s, i) => (
                <tr key={s.agent} className={`${i < SCHEDULES.length - 1 ? 'border-b border-slate-800' : ''}`}>
                  <td className="px-5 py-3 text-slate-200 text-sm font-medium">{s.agent}</td>
                  <td className="px-5 py-3 font-mono text-xs text-cyan-400">{s.cron}</td>
                  <td className="px-5 py-3 text-slate-500 text-xs">{s.description}</td>
                  <td className="px-5 py-3">
                    <button
                      className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-300 transition-colors"
                      onClick={() => console.log('edit schedule', s.agent)}
                    >
                      <Edit2 className="w-3 h-3" />
                      Edit
                      <StubBadge />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {/* Timezone */}
      <section className="space-y-3">
        <h2 className="text-xs font-semibold text-slate-400 uppercase tracking-wide">Preferences</h2>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-slate-200">Timezone</p>
              <p className="text-xs text-slate-500 mt-0.5">Used for scheduled agent runs and display times</p>
            </div>
            <div className="flex items-center gap-2">
              <select className="bg-slate-800 border border-slate-700 text-slate-300 text-sm rounded-lg px-3 py-1.5 focus:outline-none">
                <option>UTC</option>
                <option>America/Chicago</option>
                <option>America/New_York</option>
                <option>America/Los_Angeles</option>
              </select>
              <StubBadge />
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}
