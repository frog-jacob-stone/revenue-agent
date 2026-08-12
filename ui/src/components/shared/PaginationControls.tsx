import { ChevronLeft, ChevronRight } from 'lucide-react';

interface PaginationControlsProps {
  hasPrev: boolean;
  hasNext: boolean;
  onPrev: () => void;
  onNext: () => void;
  pageIndex: number;
  pageSize: number;
  itemsOnPage: number;
  loading?: boolean;
}

export default function PaginationControls({
  hasPrev,
  hasNext,
  onPrev,
  onNext,
  pageIndex,
  pageSize,
  itemsOnPage,
  loading = false,
}: PaginationControlsProps) {
  const start = itemsOnPage === 0 ? 0 : pageIndex * pageSize + 1;
  const end = pageIndex * pageSize + itemsOnPage;

  const buttonClass =
    'flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-slate-100 hover:bg-slate-200 text-slate-700 transition-colors disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-slate-100';

  return (
    <div className="flex items-center justify-between px-1 py-1">
      <p className="text-xs text-slate-500 tabular-nums">
        {itemsOnPage === 0 ? 'No results' : `Showing ${start.toLocaleString()}–${end.toLocaleString()}`}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          className={buttonClass}
          onClick={onPrev}
          disabled={!hasPrev || loading}
        >
          <ChevronLeft className="w-3.5 h-3.5" />
          Prev
        </button>
        <button
          type="button"
          className={buttonClass}
          onClick={onNext}
          disabled={!hasNext || loading}
        >
          Next
          <ChevronRight className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}
