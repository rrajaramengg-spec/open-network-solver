import type { CostMode, FacilityType } from '@/types/routing';

export interface FacilityTypeSelectProps {
  value: FacilityType;
  onChange: (value: FacilityType) => void;
}

const OPTIONS: { value: FacilityType; label: string }[] = [
  { value: 'all', label: 'All facilities' },
  { value: 'fire_station', label: 'Fire station' },
  { value: 'hospital', label: 'Hospital' },
  { value: 'police', label: 'Police' },
  { value: 'school', label: 'School' },
];

/** Curated facility-type dropdown (spec scenario defaults). */
export function FacilityTypeSelect({ value, onChange }: FacilityTypeSelectProps): JSX.Element {
  return (
    <div>
      <label htmlFor="facility-type" className="block text-sm font-medium text-slate-200">
        Facility type
      </label>
      <select
        id="facility-type"
        value={value}
        onChange={(e) => onChange(e.target.value as FacilityType)}
        className="mt-1 w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100"
      >
        {OPTIONS.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </div>
  );
}

export interface CostModeToggleProps {
  value: CostMode;
  onChange: (value: CostMode) => void;
}

export function CostModeToggle({ value, onChange }: CostModeToggleProps): JSX.Element {
  return (
    <div>
      <span className="block text-sm font-medium text-slate-200">Cost mode</span>
      <div role="group" aria-label="Cost mode" className="mt-1 flex gap-1 text-sm">
        {(['distance', 'time'] as const).map((m) => (
          <button
            key={m}
            type="button"
            aria-pressed={value === m}
            className={
              'flex-1 rounded px-3 py-2 ' +
              (value === m
                ? 'bg-sky-600 text-white'
                : 'bg-slate-800 text-slate-300 hover:bg-slate-700')
            }
            onClick={() => onChange(m)}
          >
            {m === 'distance' ? 'Distance' : 'Time'}
          </button>
        ))}
      </div>
    </div>
  );
}
