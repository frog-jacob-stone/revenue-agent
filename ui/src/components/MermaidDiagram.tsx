import { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';

let initialized = false;

function ensureInitialized() {
  if (initialized) return;
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'strict',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    flowchart: { useMaxWidth: true, htmlLabels: true, curve: 'basis' },
  });
  initialized = true;
}

let renderCounter = 0;

interface Props {
  source: string;
  className?: string;
}

export default function MermaidDiagram({ source, className }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    ensureInitialized();
    const id = `mmd-${++renderCounter}`;
    mermaid
      .render(id, source)
      .then(({ svg }) => {
        if (cancelled || !ref.current) return;
        ref.current.innerHTML = svg;
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
      });
    return () => {
      cancelled = true;
    };
  }, [source]);

  if (error) {
    return (
      <div className={`text-xs text-red-400 font-mono whitespace-pre-wrap ${className ?? ''}`}>
        Diagram error: {error}
      </div>
    );
  }

  return <div ref={ref} className={className} />;
}
