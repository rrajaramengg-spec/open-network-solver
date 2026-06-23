import { useState } from 'react';
import { useSearchStore } from '@/store';
import { BufferDistanceInput } from './BufferDistanceInput';
import { FacilityCountInput } from './FacilityCountInput';
import { CostModeToggle, FacilityTypeSelect } from './FacilityTypeSelect';

/** Side-panel widget: inputs + Find button. */
export function SearchWidget(): JSX.Element {
  const incident = useSearchStore((s) => s.incident);
  const bufferMeters = useSearchStore((s) => s.bufferMeters);
  const k = useSearchStore((s) => s.k);
  const costMode = useSearchStore((s) => s.costMode);
  const facilityType = useSearchStore((s) => s.facilityType);
  const categories = useSearchStore((s) => s.categories);
  const isLoading = useSearchStore((s) => s.isLoading);

  const setBuffer = useSearchStore((s) => s.setBuffer);
  const setK = useSearchStore((s) => s.setK);
  const setCostMode = useSearchStore((s) => s.setCostMode);
  const setFacilityType = useSearchStore((s) => s.setFacilityType);
  const submit = useSearchStore((s) => s.submit);
  const clear = useSearchStore((s) => s.clear);

  const [unit, setUnit] = useState<'ft' | 'm'>('ft');

  const disabled = isLoading || incident === null;

  return (
    <aside
      className="flex h-full w-80 flex-col gap-4 overflow-y-auto bg-slate-900 p-4 text-slate-100 shadow-xl"
      aria-label="Search widget"
    >
      <h2 className="text-lg font-semibold">Closest Facility</h2>
      <div className="rounded border border-slate-700 bg-slate-950 p-3 text-xs">
        <div className="font-medium text-slate-300">Incident</div>
        {incident === null ? (
          <div className="mt-1 text-slate-400">
            Search an address on the map or click the map.
          </div>
        ) : (
          <div className="mt-1 font-mono text-slate-200">
            {incident.lat.toFixed(5)}, {incident.lon.toFixed(5)}
          </div>
        )}
      </div>
      <BufferDistanceInput
        metres={bufferMeters}
        onChange={setBuffer}
        unit={unit}
        onUnitChange={setUnit}
      />
      <FacilityCountInput k={k} onChange={setK} />
      <FacilityTypeSelect
        value={facilityType}
        onChange={setFacilityType}
        categories={categories}
      />
      <CostModeToggle value={costMode} onChange={setCostMode} />
      <div className="mt-auto flex gap-2">
        <button
          type="button"
          className="flex-1 rounded bg-sky-600 px-4 py-2 font-medium text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-50"
          onClick={submit}
          disabled={disabled}
          data-testid="find-button"
        >
          {isLoading ? (
            <span className="inline-flex items-center gap-2">
              <span
                aria-hidden="true"
                className="h-4 w-4 animate-spin rounded-full border-2 border-white border-r-transparent"
              />
              Finding…
            </span>
          ) : (
            'Find'
          )}
        </button>
        <button
          type="button"
          className="rounded bg-slate-700 px-4 py-2 text-slate-200 hover:bg-slate-600"
          onClick={clear}
        >
          Clear
        </button>
      </div>
    </aside>
  );
}
