import StubBadge from '../../../components/shared/StubBadge';

export default function ContentWriterConfig() {
  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-slate-300">Content Writer Configuration</h3>
      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Default blog word count <StubBadge /></label>
          <input type="number" defaultValue={1200} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">LinkedIn post word count <StubBadge /></label>
          <input type="number" defaultValue={280} className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none" />
        </div>
        <div className="col-span-2">
          <label className="block text-xs text-slate-400 mb-1.5">Brand voice summary <StubBadge /></label>
          <textarea
            defaultValue="Direct, technical, no buzzwords, first-person plural ('we'). Never use 'leverage' as a verb."
            rows={3}
            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-300 focus:outline-none resize-none"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1.5">Content types enabled <StubBadge /></label>
          <div className="flex flex-col gap-2">
            {['Blog posts', 'LinkedIn posts', 'Case studies', 'Email copy'].map((t) => (
              <label key={t} className="flex items-center gap-2 text-sm text-slate-300 cursor-pointer">
                <input type="checkbox" defaultChecked className="accent-cyan-400" />
                {t}
              </label>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
