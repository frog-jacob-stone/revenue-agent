import { CheckCircle2 } from 'lucide-react';

interface Props {
  title?: string;
  description?: string;
}

export default function EmptyState({ title = 'Nothing here', description = 'No items to display.' }: Props) {
  return (
    <div className="flex flex-col items-center justify-center py-20 text-center">
      <CheckCircle2 className="w-12 h-12 text-slate-600 mb-4" />
      <p className="text-slate-300 font-medium">{title}</p>
      <p className="text-slate-500 text-sm mt-1">{description}</p>
    </div>
  );
}
