export interface FacilityCountInputProps {
  /** Current K value (1..10). */
  k: number;
  onChange: (k: number) => void;
}

/** Stepper input clamped to [1, 10] per spec. */
export function FacilityCountInput({ k, onChange }: FacilityCountInputProps): JSX.Element {
  const dec = () => onChange(Math.max(1, k - 1));
  const inc = () => onChange(Math.min(10, k + 1));
  return (
    <div>
      <label htmlFor="k-input" className="block text-sm font-medium text-slate-200">
        Number of facilities (K)
      </label>
      <div className="mt-1 flex items-center gap-2">
        <button
          type="button"
          aria-label="Decrement K"
          onClick={dec}
          disabled={k <= 1}
          className="rounded bg-slate-800 px-3 py-2 text-slate-100 hover:bg-slate-700 disabled:opacity-50"
        >
          −
        </button>
        <input
          id="k-input"
          type="number"
          min={1}
          max={10}
          step={1}
          value={k}
          onChange={(e) => {
            const v = parseInt(e.target.value || '1', 10);
            onChange(Math.max(1, Math.min(10, Number.isNaN(v) ? 1 : v)));
          }}
          className="w-16 rounded border border-slate-700 bg-slate-900 px-2 py-2 text-center text-sm text-slate-100"
        />
        <button
          type="button"
          aria-label="Increment K"
          onClick={inc}
          disabled={k >= 10}
          className="rounded bg-slate-800 px-3 py-2 text-slate-100 hover:bg-slate-700 disabled:opacity-50"
        >
          +
        </button>
      </div>
    </div>
  );
}
