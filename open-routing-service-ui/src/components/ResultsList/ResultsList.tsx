import { useSearchStore } from '@/store';
import { rankColor } from '@/utils/rankColors';
import { humanizeCategory } from '@/utils/categoryIcon';

/** Ranked card list of facilities; click to select / isolate on the map. */
export function ResultsList(): JSX.Element {
  const results = useSearchStore((s) => s.results);
  const selectedRank = useSearchStore((s) => s.selectedRank);
  const setSelectedRank = useSearchStore((s) => s.setSelectedRank);
  const cacheHit = useSearchStore((s) => s.cacheHit);

  if (results.length === 0) {
    return (
      <div className="px-4 py-3 text-sm text-slate-400" data-testid="results-empty">
        No results yet — pick an incident and click <strong>Find</strong>.
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col" data-testid="results-list">
      <div className="flex items-center justify-between px-4 py-2 text-xs text-slate-400">
        <span>
          {results.length} result{results.length === 1 ? '' : 's'}
        </span>
        {cacheHit ? <span className="text-emerald-400">served from cache</span> : null}
      </div>
      <ul className="flex-1 overflow-y-auto">
        {results.map((r) => (
          <li key={r.facility_id}>
            <button
              type="button"
              aria-pressed={selectedRank === r.rank}
              onClick={() =>
                setSelectedRank(selectedRank === r.rank ? null : r.rank)
              }
              data-testid={`result-${r.rank}`}
              className={
                'w-full border-b border-slate-800 px-4 py-3 text-left hover:bg-slate-800 ' +
                (selectedRank === r.rank ? 'bg-slate-800' : '')
              }
            >
              <div className="flex items-center gap-3">
                <span
                  aria-label={`Rank ${r.rank}`}
                  className="flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold text-white"
                  style={{ backgroundColor: rankColor(r.rank) }}
                >
                  {r.rank}
                </span>
                <div className="text-sm">
                  <div className="font-medium text-slate-100">
                    {r.name != null && r.name.length > 0
                      ? r.name
                      : `Facility #${r.facility_id}`}
                  </div>
                  {r.category != null && r.category.length > 0 ? (
                    <div className="text-xs text-sky-300">
                      {humanizeCategory(r.category)}
                    </div>
                  ) : null}
                  <div className="text-slate-400">
                    {Math.round(r.total_distance_m)} m · {Math.round(r.total_time_s)} s
                  </div>
                </div>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
