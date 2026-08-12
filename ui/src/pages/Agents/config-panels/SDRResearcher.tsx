import StubBadge from '../../../components/shared/StubBadge';

export default function SDRResearcherConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">SDR Researcher Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Apollo confidence threshold <StubBadge /></label>
          <input type="number" defaultValue={85} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Max contacts per company <StubBadge /></label>
          <input type="number" defaultValue={3} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">ICP employee range <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>50–500</option>
            <option>1–50</option>
            <option>500+</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Primary contact title filter <StubBadge /></label>
          <input type="text" defaultValue="VP Engineering, CTO, Head of Eng" className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-slate-400 mb-1.5">Enrichment sources <StubBadge /></label>
          <div className="flex gap-3">
            {['Apollo.io', 'LinkedIn', 'Clearbit'].map((s) => (
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
