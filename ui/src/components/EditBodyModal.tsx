import { useState } from 'react';

interface Props {
  title?: string;
  initialValue: unknown;
  onSave: (value: unknown) => void;
  onClose: () => void;
}

export default function EditBodyModal({ title = 'Edit payload', initialValue, onSave, onClose }: Props) {
  const [text, setText] = useState(
    typeof initialValue === 'string'
      ? initialValue
      : JSON.stringify(initialValue, null, 2),
  );

  const handleSave = () => {
    try {
      onSave(JSON.parse(text));
    } catch {
      onSave(text);
    }
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30">
      <div className="bg-white border border-slate-200 rounded-lg shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
          <span className="text-sm font-medium text-slate-800">{title}</span>
          <button
            onClick={onClose}
            className="text-slate-500 hover:text-slate-700 text-sm leading-none"
          >
            ✕
          </button>
        </div>
        <div className="p-4">
          <textarea
            className="w-full h-64 text-xs font-mono bg-slate-50 border border-slate-300 text-emerald-700 rounded p-2 resize-none focus:outline-none focus:ring-1 focus:ring-slate-400"
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
          />
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-slate-200">
          <button
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded border border-slate-300 text-slate-600 hover:bg-slate-100 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="text-xs px-3 py-1.5 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
