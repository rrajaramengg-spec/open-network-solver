import { feetToMetres, metresToFeet, round } from '@/utils/units';

export interface BufferDistanceInputProps {
  /** Current buffer value in metres. */
  metres: number;
  /** Called with the new value in metres. */
  onChange: (metres: number) => void;
  /** Display unit (component-local state — toggle is component-local). */
  unit: 'ft' | 'm';
  /** Called when the user toggles the unit. */
  onUnitChange: (unit: 'ft' | 'm') => void;
}

const FEET_MIN = 33; // ~10 m
const FEET_MAX = 164_042; // ~50 km
const METRES_MIN = 10;
const METRES_MAX = 50_000;

/** Slider + numeric input with ft↔m toggle. */
export function BufferDistanceInput({
  metres,
  onChange,
  unit,
  onUnitChange,
}: BufferDistanceInputProps): JSX.Element {
  const display = unit === 'ft' ? round(metresToFeet(metres)) : round(metres);
  const min = unit === 'ft' ? FEET_MIN : METRES_MIN;
  const max = unit === 'ft' ? FEET_MAX : METRES_MAX;

  const update = (val: number): void => {
    const m = unit === 'ft' ? feetToMetres(val) : val;
    onChange(Math.max(METRES_MIN, Math.min(METRES_MAX, m)));
  };

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <label htmlFor="buffer-num" className="block text-sm font-medium text-slate-200">
          Buffer distance
        </label>
        <div role="group" aria-label="Distance unit" className="flex gap-1 text-xs">
          {(['ft', 'm'] as const).map((u) => (
            <button
              key={u}
              type="button"
              aria-pressed={unit === u}
              className={
                'rounded px-2 py-0.5 ' +
                (unit === u
                  ? 'bg-sky-600 text-white'
                  : 'bg-slate-800 text-slate-300 hover:bg-slate-700')
              }
              onClick={() => onUnitChange(u)}
            >
              {u}
            </button>
          ))}
        </div>
      </div>
      <input
        id="buffer-num"
        type="number"
        className="w-full rounded border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-100 outline-none focus:border-sky-500"
        min={min}
        max={max}
        step={unit === 'ft' ? 10 : 5}
        value={display}
        onChange={(e) => update(parseFloat(e.target.value || '0'))}
      />
      <input
        type="range"
        aria-label="Buffer slider"
        className="w-full"
        min={min}
        max={Math.min(max, unit === 'ft' ? 5000 : 1500)}
        step={unit === 'ft' ? 10 : 5}
        value={Math.min(display, unit === 'ft' ? 5000 : 1500)}
        onChange={(e) => update(parseFloat(e.target.value))}
      />
    </div>
  );
}
