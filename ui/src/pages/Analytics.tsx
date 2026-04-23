import {
  LineChart, Line, BarChart, Bar, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { DAILY_RUNS, APPROVAL_RATES, SUMMARY_STATS, AGENTS } from '../mocks';
import StubBadge from '../components/shared/StubBadge';

const AGENT_COLORS = Object.fromEntries(AGENTS.map((a) => [a.name, a.color]));

const RANGE_OPTS = ['7 days', '30 days', '90 days', 'Custom'] as const;

export default function Analytics() {
  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Analytics</h1>
          <p className="text-sm text-slate-400 mt-0.5">Agent performance overview</p>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex gap-1 bg-slate-900 border border-slate-800 rounded-lg p-1">
            {RANGE_OPTS.map((r) => (
              <button
                key={r}
                className={`px-3 py-1 rounded text-xs font-medium transition-colors ${r === '30 days' ? 'bg-slate-700 text-slate-100' : 'text-slate-500 hover:text-slate-300'}`}
                onClick={() => console.log('range', r)}
              >
                {r}
              </button>
            ))}
          </div>
          <StubBadge />
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        {[
          { label: 'Accounts researched', value: SUMMARY_STATS.accountsResearched },
          { label: 'Outreach sent', value: SUMMARY_STATS.outreachSent },
          { label: 'Proposals generated', value: SUMMARY_STATS.proposalsGenerated },
          { label: 'Approval rate', value: `${SUMMARY_STATS.approvalRate}%` },
        ].map(({ label, value }) => (
          <div key={label} className="bg-slate-900 border border-slate-800 rounded-xl p-5">
            <p className="text-xs text-slate-500 mb-1">{label}</p>
            <p className="text-3xl font-bold text-slate-100">{value}</p>
          </div>
        ))}
      </div>

      {/* Line chart: runs per day */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4">Agent Runs per Day (last 30 days)</h2>
        <ResponsiveContainer width="100%" height={260}>
          <LineChart data={DAILY_RUNS} margin={{ top: 0, right: 16, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
            <XAxis
              dataKey="date"
              tick={{ fill: '#64748b', fontSize: 10 }}
              tickFormatter={(v: string) => v.slice(5)}
              interval={4}
            />
            <YAxis tick={{ fill: '#64748b', fontSize: 10 }} />
            <Tooltip
              contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 8 }}
              labelStyle={{ color: '#94a3b8', fontSize: 11 }}
              itemStyle={{ fontSize: 11 }}
            />
            <Legend wrapperStyle={{ fontSize: 11, color: '#64748b', paddingTop: 12 }} />
            {AGENTS.map((a) => (
              <Line
                key={a.id}
                type="monotone"
                dataKey={a.name}
                stroke={a.color}
                strokeWidth={1.5}
                dot={false}
                activeDot={{ r: 3 }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>

      {/* Bar chart: approval rate */}
      <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 className="text-sm font-semibold text-slate-300 mb-4">Approval Rate by Agent</h2>
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={APPROVAL_RATES} margin={{ top: 0, right: 16, left: -10, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis dataKey="agent" tick={{ fill: '#64748b', fontSize: 10 }} />
            <YAxis domain={[0, 100]} tick={{ fill: '#64748b', fontSize: 10 }} unit="%" />
            <Tooltip
              contentStyle={{ backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: 8 }}
              labelStyle={{ color: '#94a3b8', fontSize: 11 }}
              itemStyle={{ fontSize: 11 }}
              formatter={(v) => [`${v}%`, 'Approval rate']}
            />
            <Bar dataKey="rate" radius={[4, 4, 0, 0]}>
              {APPROVAL_RATES.map((entry) => (
                <Cell key={entry.agent} fill={AGENT_COLORS[entry.agent] ?? '#3b82f6'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* Extra stat cards */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <p className="text-xs text-slate-500 mb-1">Avg time to approve</p>
          <p className="text-2xl font-bold text-slate-100">{SUMMARY_STATS.avgTimeToApprove}</p>
        </div>
        <div className="bg-slate-900 border border-slate-800 rounded-xl p-5">
          <p className="text-xs text-slate-500 mb-1">Most active agent</p>
          <p className="text-2xl font-bold text-slate-100">{SUMMARY_STATS.mostActiveAgent}</p>
        </div>
      </div>
    </div>
  );
}
