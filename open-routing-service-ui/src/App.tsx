import { useEffect } from 'react';
import { useSearchStore } from '@/store';
import {
  ErrorToast,
  MapView,
  ResultsList,
  RunbookBadge,
  SearchWidget,
} from '@/components';

export interface AppProps {
  routingApiUrl: string;
  photonUrl: string;
  tileUrl: string;
}

/** Top-level layout: header · widget · map · results · toast. */
export function App({ routingApiUrl, photonUrl, tileUrl }: AppProps): JSX.Element {
  const loadCategories = useSearchStore((s) => s.loadCategories);

  // Populate the data-driven facility-type dropdown on first render.
  useEffect(() => {
    void loadCategories();
  }, [loadCategories]);

  return (
    <div className="flex h-screen flex-col bg-slate-950 text-slate-100">
      <header className="flex shrink-0 items-center justify-between border-b border-slate-800 px-4 py-2">
        <h1 className="text-sm font-semibold tracking-wide text-slate-200">
          open-network-solver · closest facility
        </h1>
        <RunbookBadge routingApiUrl={routingApiUrl} />
      </header>
      <main className="flex min-h-0 flex-1">
        <SearchWidget />
        <div className="relative flex-1">
          <MapView tileUrl={tileUrl} photonUrl={photonUrl} />
        </div>
        <div className="w-72 shrink-0 overflow-y-auto border-l border-slate-800 bg-slate-900/95">
          <ResultsList />
        </div>
      </main>
      <ErrorToast />
    </div>
  );
}
