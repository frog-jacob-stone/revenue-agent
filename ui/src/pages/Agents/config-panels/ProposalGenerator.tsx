import StubBadge from '../../../components/shared/StubBadge';

export default function ProposalGeneratorConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">Proposal Generator Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Default template <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>modernization-v3</option>
            <option>enterprise-v2</option>
            <option>assessment-v1</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Enterprise threshold ($) <StubBadge /></label>
          <input type="number" defaultValue={500000} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Max autonomous discount % <StubBadge /></label>
          <input type="number" defaultValue={10} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Trigger stage <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>Proposal Requested</option>
            <option>Demo Completed</option>
            <option>Manual only</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-slate-400 mb-1.5">Default sections <StubBadge /></label>
          <div className="flex flex-wrap gap-3">
            {['Executive Summary', 'Scope', 'Timeline', 'Pricing', 'References'].map((s) => (
              <label key={s} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input type="checkbox" defaultChecked className="accent-cyan-400" />
                {s}
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
