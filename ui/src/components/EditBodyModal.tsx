import { useState } from 'react';

interface Props {
  initialValue: unknown;
  onSave: (value: unknown) => void;
  onClose: () => void;
}

export default function EditBodyModal({ initialValue, onSave, onClose }: Props) {
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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-lg mx-4">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
          <span className="text-sm font-medium text-gray-900">Edit body</span>
          <button
            onClick={onClose}
            className="text-gray-400 hover:text-gray-600 text-sm leading-none"
          >
            ✕
          </button>
        </div>
        <div className="p-4">
          <textarea
            className="w-full h-64 text-xs font-mono border border-gray-200 rounded p-2 resize-none focus:outline-none focus:ring-1 focus:ring-gray-400"
            value={text}
            onChange={(e) => setText(e.target.value)}
            spellCheck={false}
          />
        </div>
        <div className="flex justify-end gap-2 px-4 py-3 border-t border-gray-200">
          <button
            onClick={onClose}
            className="text-xs px-3 py-1.5 rounded border border-gray-200 text-gray-500 hover:bg-gray-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            className="text-xs px-3 py-1.5 rounded bg-gray-900 text-white hover:bg-gray-700 transition-colors"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  );
}
