import { useEffect, useRef, useState } from 'react';
import type { NominatimClient, NominatimResult } from '@/api';

export interface AddressSearchProps {
  /** Browser-direct Nominatim client (design D6). */
  client: NominatimClient;
  /** Called when the user selects a suggestion. */
  onSelect: (lat: number, lon: number, displayName: string) => void;
  /** Debounce window in ms; default 300. */
  debounceMs?: number;
}

const MIN_QUERY = 3;

/** Debounced Nominatim autocomplete input. */
export function AddressSearch({
  client,
  onSelect,
  debounceMs = 300,
}: AddressSearchProps): JSX.Element {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<NominatimResult[]>([]);
  const [open, setOpen] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (query.trim().length < MIN_QUERY) {
      setResults([]);
      return;
    }
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const handle = setTimeout(() => {
      client
        .search(query, { signal: controller.signal })
        .then((res) => {
          if (!controller.signal.aborted) {
            setResults(res);
            setOpen(true);
          }
        })
        .catch(() => {
          /* swallow — best-effort suggestions */
        });
    }, debounceMs);
    return () => {
      clearTimeout(handle);
      controller.abort();
    };
  }, [query, debounceMs, client]);

  return (
    <div className="relative">
      <label htmlFor="addr-input" className="block text-sm font-medium text-slate-200">
        Address search
      </label>
      <input
        id="addr-input"
        type="search"
        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-500"
        placeholder="Type an address or place name…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => setOpen(results.length > 0)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
        autoComplete="off"
        aria-autocomplete="list"
        aria-controls="addr-suggestions"
      />
      {open && results.length > 0 ? (
        <ul
          id="addr-suggestions"
          role="listbox"
          className="absolute z-10 mt-1 max-h-60 w-full overflow-auto rounded border border-slate-700 bg-slate-900 text-sm shadow-lg"
        >
          {results.map((r) => (
            <li
              key={`${r.place_id ?? r.display_name}-${r.lat}-${r.lon}`}
              role="option"
              aria-selected="false"
            >
              <button
                type="button"
                className="block w-full px-3 py-2 text-left hover:bg-slate-800"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => {
                  onSelect(parseFloat(r.lat), parseFloat(r.lon), r.display_name);
                  setQuery(r.display_name);
                  setOpen(false);
                }}
              >
                {r.display_name}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
