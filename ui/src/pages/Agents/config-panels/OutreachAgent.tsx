import StubBadge from '../../../components/shared/StubBadge';

export default function OutreachAgentConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">Outreach Agent Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Default sequence steps <StubBadge /></label>
          <input type="number" defaultValue={3} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Days between steps <StubBadge /></label>
          <input type="number" defaultValue={3} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Send window start <StubBadge /></label>
          <input type="time" defaultValue="09:00" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Send window end <StubBadge /></label>
          <input type="time" defaultValue="17:00" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Primary channel <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>Email</option>
            <option>LinkedIn</option>
            <option>Email + LinkedIn</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Max daily sends <StubBadge /></label>
          <input type="number" defaultValue={50} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
      </div>
    </div>
  );
}
