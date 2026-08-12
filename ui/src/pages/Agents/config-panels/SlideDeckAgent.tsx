import StubBadge from '../../../components/shared/StubBadge';

export default function SlideDeckAgentConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">Slide Deck Agent Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Default slide count <StubBadge /></label>
          <input type="number" defaultValue={12} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Executive deck max slides <StubBadge /></label>
          <input type="number" defaultValue={10} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Output format <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>Google Slides</option>
            <option>PowerPoint (.pptx)</option>
            <option>PDF</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Brand template <StubBadge /></label>
          <select className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none">
            <option>Frogslayer 2026</option>
            <option>Minimal</option>
            <option>Custom</option>
          </select>
        </div>
      </div>
    </div>
  );
}
