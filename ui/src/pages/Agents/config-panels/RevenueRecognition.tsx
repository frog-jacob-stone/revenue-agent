import StubBadge from '../../../components/shared/StubBadge';

export default function RevenueRecognitionConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">Revenue Recognition Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Run schedule <StubBadge /></label>
          <input type="text" defaultValue="0 6 1 * *" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 font-mono focus:outline-none" />
          <p className="text-xs text-slate-600 mt-1">Cron: 1st of each month at 06:00 UTC</p>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Alert if not complete by (UTC) <StubBadge /></label>
          <input type="time" defaultValue="09:00" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">HubSpot pipeline to reconcile <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>All pipelines</option>
            <option>Services Pipeline</option>
            <option>Retainer Pipeline</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Report recipients <StubBadge /></label>
          <input type="text" defaultValue="jacob@frogslayer.com" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
      </div>
    </div>
  );
}
